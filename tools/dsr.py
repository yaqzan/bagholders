#!/usr/bin/env python
"""Database-Side Rendering (DSR) -- build SVG small-multiples inside DuckDB.

The idea (stolen from @tobilg's "10 million sparklines in DuckDB" spike): don't
ship data to a charting library, ship the *chart*. All the geometry -- scaling,
projection, polyline point strings, ribbon polygons, even the per-cell <g>
wrappers -- is computed by ONE DuckDB query via string_agg. Python only writes
the outer <svg> shell and the file.

Why this repo cares: our sweeps emit thousands of tiny uniform series (N=500 MC
equity curves x 12 windows, 349-cell lattices, per-window DD). Those collapse
into markdown tables in FINDINGS.md because matplotlib small-multiples at that
count is miserable. A contact sheet is a single static file with no JS, no
server, and no notebook -- an agent or a human can open it and see the seed-level
pathology that a median hides.

    # the money shot: 12 windows of N=500 Core MC, one page
    MC_RESULTS_JSON=.cache/mc.json MC_RETURN_PATHS=1 MC_EMIT_CURVE=1 python monte_carlo.py
    python tools/dsr.py mc .cache/mc.json -o .cache/mc_sheet.svg

    # any long-format parquet
    python tools/dsr.py parquet .cache/foo.parquet \
        --facet window --series seed --x bar --y equity -o sheet.svg

Modes
    overlay  (default)  one cell per facet: p5-p95 + p25-p75 quantile ribbons,
                        median bold, every collapse/loss path drawn thin.
                        Collapses are NEVER sampled away -- they are the thing
                        we are hunting.
    grid                one tiny cell per series, sorted worst-final first.
                        The literal contact sheet.
    both                grid page appended under the overlay page.

Scale
    --log is the default for MC equity (compounding is multiplicative; a linear
    axis makes every pre-2020 window look flat). --linear to override.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Layout constants (all in SVG user units)
# ---------------------------------------------------------------------------
OVERLAY_W, OVERLAY_H = 260.0, 150.0     # inner plot box, overlay cell
GRID_W, GRID_H = 46.0, 26.0             # inner plot box, contact-sheet cell
PAD_X, PAD_Y = 34.0, 30.0               # gutter around each inner box
TITLE_H = 46.0

_STYLE = """
:root { --ink:#1c1f26; --dim:#6b7280; --rule:#d4d8e0; --bg:#ffffff;
        --win:#2563eb; --loss:#d97706; --coll:#dc2626; --band:#2563eb; }
@media (prefers-color-scheme: dark) {
:root { --ink:#e6e8ec; --dim:#9aa3b2; --rule:#39404d; --bg:#14171c;
        --win:#60a5fa; --loss:#fbbf24; --coll:#f87171; --band:#60a5fa; }
}
.bg   { fill: var(--bg); }
.ttl  { fill: var(--ink); font: 600 13px ui-sans-serif,system-ui,sans-serif; }
.sub  { fill: var(--dim); font: 400 10px ui-sans-serif,system-ui,sans-serif; }
.lbl  { fill: var(--dim); font: 400  8px ui-monospace,SFMono-Regular,monospace; }
.box  { fill: none; stroke: var(--rule); stroke-width: .6; }
.ref  { stroke: var(--dim); stroke-width: .6; stroke-dasharray: 2 2; fill: none; }
.b95  { fill: var(--band); opacity: .13; stroke: none; }
.b50  { fill: var(--band); opacity: .22; stroke: none; }
.med  { fill: none; stroke: var(--ink); stroke-width: 1.6;
        stroke-linejoin: round; stroke-linecap: round; }
.pw   { fill: none; stroke: var(--win);  stroke-width: .5; opacity: .30; }
.pl   { fill: none; stroke: var(--loss); stroke-width: .6; opacity: .55; }
.pc   { fill: none; stroke: var(--coll); stroke-width: .9; opacity: .90; }
.gw   { fill: none; stroke: var(--win);  stroke-width: .7; }
.gl   { fill: none; stroke: var(--loss); stroke-width: .7; }
.gc   { fill: none; stroke: var(--coll); stroke-width: .9; }
"""


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------
def load_long(con, facets, series, xs, ys):
    """Register the long-format point table. Lists must be equal length."""
    import pyarrow as pa
    tbl = pa.table({'facet': facets, 'series': series, 'i': xs, 'y': ys})
    con.register('_dsr_arrow', tbl)
    con.execute("CREATE OR REPLACE TABLE dsr_pts AS SELECT * FROM _dsr_arrow")
    con.unregister('_dsr_arrow')


def load_mc_json(con, path, windows=None):
    """Adapter for monte_carlo.py's MC_RESULTS_JSON dump.

    Requires the run to have set MC_RETURN_PATHS=1 and MC_EMIT_CURVE=1, which is
    what populates <window>.paths.eq_weeklies (one weekly-downsampled equity
    curve per seed). Returns (facet_order, ref_value, meta).
    """
    with open(path) as f:
        blob = json.load(f)
    meta = blob.get('_meta', {})

    facets, series, xs, ys = [], [], [], []
    cls_f, cls_s, cls_v = [], [], []
    order, ref = [], None
    skipped = []

    # Engine-native collapse definition, so our colouring matches p_coll exactly
    # rather than re-deriving a threshold and quietly disagreeing with it.
    try:
        sys.path.insert(0, str(ROOT))
        from strategy_config import STRATEGY_30DTE as _cfg
        coll_frac = float(getattr(_cfg, 'COLLAPSE_THRESHOLD', 0.20))
    except Exception:
        coll_frac = 0.20

    for label, node in blob.items():
        if label == '_meta' or not isinstance(node, dict):
            continue
        if windows and label not in windows:
            continue
        paths = node.get('paths') or {}
        curves = paths.get('eq_weeklies')
        if not curves or all(c is None for c in curves):
            skipped.append(label)
            continue
        start = float(paths.get('starting_cash') or 0) or None
        if ref is None:
            ref = start
        finals = paths.get('finals') or []
        order.append(label)

        for k, curve in enumerate(curves):
            if not curve:
                continue
            for i, v in enumerate(curve):
                facets.append(label); series.append(k)
                xs.append(i); ys.append(float(v))
            fin = float(finals[k]) if k < len(finals) else float(curve[-1])
            if start and fin <= start * coll_frac:
                c = 'collapse'
            elif start and fin < start:
                c = 'loss'
            else:
                c = 'win'
            cls_f.append(label); cls_s.append(k); cls_v.append(c)

    if not facets:
        raise SystemExit(
            f"No equity curves in {path}. The MC run needs BOTH "
            f"MC_RETURN_PATHS=1 and MC_EMIT_CURVE=1 -- eq_weeklies is absent otherwise."
        )
    if skipped:
        print(f"  [skip] {len(skipped)} window(s) had no eq_weeklies: {', '.join(skipped)}")

    load_long(con, facets, series, xs, ys)

    import pyarrow as pa
    con.register('_dsr_cls_arrow', pa.table({'facet': cls_f, 'series': cls_s, 'cls': cls_v}))
    con.execute("CREATE OR REPLACE TABLE dsr_cls AS SELECT * FROM _dsr_cls_arrow")
    con.unregister('_dsr_cls_arrow')
    return order, ref, meta


def derive_class(con):
    """Fallback classification when the caller supplied no dsr_cls: compare the
    last point to the first. No collapse tier -- we have no ruin threshold for
    an arbitrary parquet, and inventing one would be a lie dressed as a colour."""
    con.execute("""
        CREATE OR REPLACE TABLE dsr_cls AS
        SELECT facet, series,
               CASE WHEN arg_max(y, i) < arg_min(y, i) THEN 'loss' ELSE 'win' END AS cls
        FROM dsr_pts GROUP BY facet, series
    """)


# ---------------------------------------------------------------------------
# The DSR core: geometry + markup, entirely in SQL
# ---------------------------------------------------------------------------
def _prep(con, log_scale, scale_mode):
    """Build the scaled-coordinate table. y-transform and bounds happen here."""
    yexpr = "ln(greatest(y, 1e-9))" if log_scale else "y"
    con.execute(f"CREATE OR REPLACE TABLE dsr_v AS SELECT facet, series, i, {yexpr} AS yv, y AS yraw FROM dsr_pts")

    # Per-series bounds: each cell autoscales to its own range. Magnitude-blind,
    # shape-revealing -- the right choice for a contact sheet you are scanning for
    # a pathological SHAPE (ramp-then-crater), the wrong one for comparing seeds
    # to each other. Grid mode only; ribbons need a shared axis to mean anything.
    con.execute("""
        CREATE OR REPLACE TABLE dsr_sb AS
        SELECT facet, series, min(yv) AS lo,
               CASE WHEN max(yv) <= min(yv) THEN min(yv) + 1e-9 ELSE max(yv) END AS hi,
               max(i) AS imax
        FROM dsr_v GROUP BY facet, series
    """)

    if scale_mode == 'global':
        bounds = """
            SELECT p.facet, g.lo, g.hi, f.imax
            FROM (SELECT DISTINCT facet FROM dsr_v) p
            CROSS JOIN (SELECT min(yv) lo, max(yv) hi FROM dsr_v) g
            JOIN (SELECT facet, max(i) imax FROM dsr_v GROUP BY facet) f USING (facet)
        """
    else:  # per-facet: seeds inside one window stay comparable to each other
        bounds = "SELECT facet, min(yv) lo, max(yv) hi, max(i) imax FROM dsr_v GROUP BY facet"

    con.execute(f"""
        CREATE OR REPLACE TABLE dsr_fb AS
        SELECT facet, lo, CASE WHEN hi <= lo THEN lo + 1e-9 ELSE hi END AS hi, imax
        FROM ({bounds})
    """)


def _paths_sql(w, h, xdp=1, per_series=False):
    """Per-series polyline point string. This is the whole trick, right here.

    Resolution-aware: a series is strided down so it never emits more points
    than the cell has pixels. A 240-point curve in a 46px contact-sheet cell is
    ~200 coordinate pairs nothing can resolve -- that alone was 80% of the file.
    First and last points are always kept so endpoints stay honest.
    """
    xr = f"CAST(ROUND(v.i * ({w}::DOUBLE / NULLIF(b.imax, 0)), {xdp}) AS VARCHAR)" if xdp else \
         f"CAST(CAST(ROUND(v.i * ({w}::DOUBLE / NULLIF(b.imax, 0)), 0) AS INTEGER) AS VARCHAR)"
    src = 'dsr_sb' if per_series else 'dsr_fb'
    keys = 'facet, series' if per_series else 'facet'
    return f"""
        WITH st AS (
            SELECT {keys}, lo, hi, imax,
                   greatest(1, CAST(ceil((imax + 1) / {w}::DOUBLE) AS BIGINT)) AS stride
            FROM {src}
        )
        SELECT v.facet, v.series,
               string_agg(
                   {xr} || ',' ||
                   CAST(ROUND({h} - (v.yv - b.lo) / (b.hi - b.lo) * {h}, 1) AS VARCHAR),
                   ' ' ORDER BY v.i
               ) AS pts
        FROM dsr_v v JOIN st b USING ({keys})
        WHERE (v.i % b.stride) = 0 OR v.i = b.imax OR v.i = 0
        GROUP BY v.facet, v.series
    """


def render_overlay(con, facet_order, ref, cols, sample, title, subtitle):
    w, h = OVERLAY_W, OVERLAY_H
    cell_w, cell_h = w + PAD_X * 2, h + PAD_Y * 2
    cols = max(1, min(cols, len(facet_order)))
    rows = math.ceil(len(facet_order) / cols)

    con.execute(f"CREATE OR REPLACE TABLE dsr_paths AS {_paths_sql(w, h)}")

    # Quantile ribbons: cross-seed distribution at each time index. Computed on
    # the transformed axis so the ribbon lines up with the paths drawn over it.
    con.execute(f"""
        CREATE OR REPLACE TABLE dsr_q AS
        SELECT v.facet, v.i,
               ROUND(v.i * ({w}::DOUBLE / NULLIF(b.imax,0)), 1) AS px,
               ROUND({h} - (quantile_cont(v.yv, 0.05) - b.lo) / (b.hi - b.lo) * {h}, 1) AS p05,
               ROUND({h} - (quantile_cont(v.yv, 0.25) - b.lo) / (b.hi - b.lo) * {h}, 1) AS p25,
               ROUND({h} - (quantile_cont(v.yv, 0.50) - b.lo) / (b.hi - b.lo) * {h}, 1) AS p50,
               ROUND({h} - (quantile_cont(v.yv, 0.75) - b.lo) / (b.hi - b.lo) * {h}, 1) AS p75,
               ROUND({h} - (quantile_cont(v.yv, 0.95) - b.lo) / (b.hi - b.lo) * {h}, 1) AS p95
        FROM dsr_v v JOIN dsr_fb b USING (facet)
        GROUP BY v.facet, v.i, b.imax, b.lo, b.hi
    """)

    # Ribbon polygon = upper edge forward, lower edge reversed. Two string_aggs
    # with opposite ORDER BY, concatenated.
    con.execute("""
        CREATE OR REPLACE TABLE dsr_ribbon AS
        SELECT facet,
               string_agg(CAST(px AS VARCHAR)||','||CAST(p95 AS VARCHAR), ' ' ORDER BY i) || ' ' ||
               string_agg(CAST(px AS VARCHAR)||','||CAST(p05 AS VARCHAR), ' ' ORDER BY i DESC) AS r95,
               string_agg(CAST(px AS VARCHAR)||','||CAST(p75 AS VARCHAR), ' ' ORDER BY i) || ' ' ||
               string_agg(CAST(px AS VARCHAR)||','||CAST(p25 AS VARCHAR), ' ' ORDER BY i DESC) AS r50,
               string_agg(CAST(px AS VARCHAR)||','||CAST(p50 AS VARCHAR), ' ' ORDER BY i) AS med
        FROM dsr_q GROUP BY facet
    """)

    # Which individual paths get drawn: every collapse and every loss (the
    # pathology), plus a deterministic every-Nth stride of the winners so the
    # cloud has texture without a 20 MB file.
    n_coll, n_loss, n_win = con.execute("""
        SELECT count(*) FILTER (WHERE cls='collapse'),
               count(*) FILTER (WHERE cls='loss'),
               count(*) FILTER (WHERE cls='win') FROM dsr_cls
    """).fetchone()
    s_win = max(1, math.ceil(n_win / max(1, sample)))
    s_loss = max(1, math.ceil(n_loss / max(1, sample * 2)))
    d_win = math.ceil(n_win / s_win) if n_win else 0
    d_loss = math.ceil(n_loss / s_loss) if n_loss else 0
    print(f"  overlay: ALL {n_coll} collapse paths drawn; "
          f"loss {d_loss}/{n_loss} (every {s_loss}); win {d_win}/{n_win} (every {s_win}); "
          f"{(n_win - d_win) + (n_loss - d_loss)} paths omitted "
          f"-- the quantile ribbons still reflect every seed")

    con.execute(f"""
        CREATE OR REPLACE TABLE dsr_draw AS
        SELECT p.facet, p.series, c.cls, p.pts,
               CASE c.cls WHEN 'collapse' THEN 'pc' WHEN 'loss' THEN 'pl' ELSE 'pw' END AS klass
        FROM dsr_paths p JOIN dsr_cls c USING (facet, series)
        WHERE c.cls = 'collapse'
           OR (c.cls = 'loss' AND (p.series % {s_loss}) = 0)
           OR (c.cls = 'win'  AND (p.series % {s_win})  = 0)
    """)

    # Per-facet body markup -- one string_agg over the <polyline> elements,
    # ordered so collapses paint last (on top).
    con.execute("""
        CREATE OR REPLACE TABLE dsr_body AS
        SELECT facet,
               string_agg('<polyline class="'||klass||'" points="'||pts||'"/>', ''
                          ORDER BY CASE cls WHEN 'win' THEN 0 WHEN 'loss' THEN 1 ELSE 2 END, series) AS body
        FROM dsr_draw GROUP BY facet
    """)

    stat = con.execute("""
        SELECT facet,
               count(*) FILTER (WHERE cls='collapse') AS n_coll,
               count(*) FILTER (WHERE cls='loss')     AS n_loss,
               count(*)                                AS n_tot
        FROM dsr_cls GROUP BY facet
    """).df().set_index('facet').to_dict('index')

    fb = con.execute("SELECT facet, lo, hi FROM dsr_fb").df().set_index('facet').to_dict('index')
    ribbon = con.execute("SELECT * FROM dsr_ribbon").df().set_index('facet').to_dict('index')
    body = con.execute("SELECT * FROM dsr_body").df().set_index('facet').to_dict('index')

    cells = []
    for k, f in enumerate(facet_order):
        cx = (k % cols) * cell_w + PAD_X
        cy = (k // cols) * cell_h + PAD_Y + TITLE_H
        rb = ribbon.get(f, {})
        st = stat.get(f, {})
        bo = fb.get(f, {})
        lo, hi = bo.get('lo', 0.0), bo.get('hi', 1.0)
        inv = (lambda v: math.exp(v)) if _LOG[0] else (lambda v: v)
        yref = ''
        if ref and lo <= (math.log(ref) if _LOG[0] else ref) <= hi:
            rv = (math.log(ref) if _LOG[0] else ref)
            py = round(h - (rv - lo) / (hi - lo) * h, 1)
            yref = f'<line class="ref" x1="0" x2="{w}" y1="{py}" y2="{py}"/>'
        nc, nl, nt = st.get('n_coll', 0), st.get('n_loss', 0), st.get('n_tot', 0)
        flag = f'  coll {nc}' + (f' / loss {nl}' if nl else '')
        cells.append(
            f'<g transform="translate({cx:.1f},{cy:.1f})">'
            f'<text class="ttl" x="0" y="-14">{_esc(f)}</text>'
            f'<text class="sub" x="0" y="-3">n={nt}{_esc(flag)}</text>'
            f'<rect class="box" x="0" y="0" width="{w}" height="{h}"/>'
            f'{yref}'
            f'<polygon class="b95" points="{rb.get("r95","")}"/>'
            f'<polygon class="b50" points="{rb.get("r50","")}"/>'
            f'{body.get(f, {}).get("body", "")}'
            f'<polyline class="med" points="{rb.get("med","")}"/>'
            f'<text class="lbl" x="{w+3}" y="6">{_fmt(inv(hi))}</text>'
            f'<text class="lbl" x="{w+3}" y="{h}">{_fmt(inv(lo))}</text>'
            f'</g>'
        )

    width = cols * cell_w + PAD_X
    height = rows * cell_h + TITLE_H + PAD_Y
    return _svg(width, height, title, subtitle, "".join(cells))


def render_grid(con, facet_order, cols, max_cells, title, subtitle, per_series=False):
    w, h = GRID_W, GRID_H
    px, py = 6.0, 12.0
    cell_w, cell_h = w + px, h + py

    con.execute(f"CREATE OR REPLACE TABLE dsr_paths AS {_paths_sql(w, h, xdp=0, per_series=per_series)}")

    pages = []
    y_cursor = TITLE_H
    total_dropped = 0
    for f in facet_order:
        n = con.execute("SELECT count(*) FROM dsr_cls WHERE facet = ?", [f]).fetchone()[0]
        keep = min(n, max_cells)
        total_dropped += n - keep
        rows = con.execute("""
            SELECT p.series, c.cls, p.pts,
                   row_number() OVER (ORDER BY s.yN ASC, p.series) - 1 AS slot
            FROM dsr_paths p
            JOIN dsr_cls c USING (facet, series)
            JOIN (SELECT facet, series, arg_max(yv, i) AS yN FROM dsr_v GROUP BY facet, series) s
                 USING (facet, series)
            WHERE p.facet = ?
            QUALIFY slot < ?
            ORDER BY slot
        """, [f, keep]).fetchall()

        gcols = max(1, cols)
        grows = math.ceil(len(rows) / gcols)
        klass = {'collapse': 'gc', 'loss': 'gl'}
        scale_note = 'each cell autoscaled' if per_series else 'shared window scale'
        cells = [f'<text class="ttl" x="{PAD_X}" y="{y_cursor + 12}">{_esc(f)}</text>'
                 f'<text class="sub" x="{PAD_X}" y="{y_cursor + 24}">'
                 f'{len(rows)} of {n} seeds, worst final first, {scale_note}</text>']
        top = y_cursor + 32
        for series, cls, pts, slot in rows:
            gx = PAD_X + (slot % gcols) * cell_w
            gy = top + (slot // gcols) * cell_h
            cells.append(
                f'<polyline class="{klass.get(cls, "gw")}" '
                f'points="{pts}" transform="translate({gx:.1f},{gy:.1f})"/>'
            )
        pages.append("".join(cells))
        y_cursor = top + grows * cell_h + 18

    if total_dropped:
        print(f"  grid: {total_dropped} series dropped by --max-cells (worst-final kept)")

    width = max(1, cols) * cell_w + PAD_X * 2
    return _svg(width, y_cursor + PAD_Y, title, subtitle, "".join(pages))


# ---------------------------------------------------------------------------
# Shell
# ---------------------------------------------------------------------------
_LOG = [True]  # set by main(); read by the label formatter


def _esc(s):
    return (str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def _fmt(v):
    a = abs(v)
    if a >= 1e9:  return f"{v/1e9:.1f}B"
    if a >= 1e6:  return f"{v/1e6:.1f}M"
    if a >= 1e3:  return f"{v/1e3:.0f}k"
    if a >= 1:    return f"{v:.0f}"
    return f"{v:.3g}"


def _svg(width, height, title, subtitle, inner):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {height:.0f}" '
        f'width="{width:.0f}" height="{height:.0f}">'
        f'<style>{_STYLE}</style>'
        f'<rect class="bg" x="0" y="0" width="{width:.0f}" height="{height:.0f}"/>'
        f'<text class="ttl" x="{PAD_X}" y="20">{_esc(title)}</text>'
        f'<text class="sub" x="{PAD_X}" y="34">{_esc(subtitle)}</text>'
        f'{inner}</svg>'
    )


def main():
    ap = argparse.ArgumentParser(description="Database-Side Rendering: SVG small-multiples from DuckDB.")
    sub = ap.add_subparsers(dest='cmd', required=True)

    def common(p):
        p.add_argument('-o', '--out', required=True, help='output .svg path')
        p.add_argument('--mode', choices=['overlay', 'grid', 'both'], default='overlay')
        p.add_argument('--cols', type=int, default=0, help='cells per row (0 = auto)')
        p.add_argument('--sample', type=int, default=120, help='max WIN paths drawn per overlay cell')
        p.add_argument('--max-cells', type=int, default=500, help='max grid cells per facet')
        p.add_argument('--scale', choices=['facet', 'global', 'series'], default='facet',
                       help="y-axis bounds: 'facet' (seeds comparable within a window, default), "
                            "'global' (all windows comparable), 'series' (each cell autoscaled -- "
                            "grid mode only, reveals shape not magnitude)")
        p.add_argument('--linear', action='store_true', help='linear y (default: log)')
        p.add_argument('--title', default=None)

    m = sub.add_parser('mc', help='monte_carlo.py MC_RESULTS_JSON dump')
    m.add_argument('json', help='path to the MC_RESULTS_JSON file')
    m.add_argument('--windows', default='', help='comma-separated subset of window labels')
    common(m)

    q = sub.add_parser('parquet', help='any long-format parquet/csv')
    q.add_argument('path')
    q.add_argument('--facet', required=True)
    q.add_argument('--series', required=True)
    q.add_argument('--x', required=True)
    q.add_argument('--y', required=True)
    common(q)

    a = ap.parse_args()
    _LOG[0] = not a.linear
    con = duckdb.connect()
    ref = None

    if a.cmd == 'mc':
        wanted = {x.strip() for x in a.windows.split(',') if x.strip()}
        order, ref, meta = load_mc_json(con, a.json, wanted or None)
        title = a.title or f"MC equity curves -- {Path(a.json).name}"
        sub_t = (f"N={meta.get('n_iter','?')} seeds/window, hold={meta.get('hold_days','?')}d, "
                 f"{'log' if _LOG[0] else 'linear'} y, ribbons p5-p95 / p25-p75, median bold")
    else:
        reader = 'read_csv_auto' if a.path.endswith(('.csv', '.tsv')) else 'read_parquet'
        # Quote every user-supplied identifier -- real column names collide with
        # DuckDB keywords constantly ('window', 'end', 'order').
        qf, qs, qx, qy = (f'"{c}"' for c in (a.facet, a.series, a.x, a.y))
        con.execute(f"""
            CREATE OR REPLACE TABLE dsr_pts AS
            SELECT CAST({qf} AS VARCHAR) AS facet, CAST({qs} AS BIGINT) AS series,
                   CAST({qx} AS BIGINT) AS i, CAST({qy} AS DOUBLE) AS y
            FROM {reader}('{a.path}') WHERE {qy} IS NOT NULL
        """)
        derive_class(con)
        order = [r[0] for r in con.execute("SELECT DISTINCT facet FROM dsr_pts ORDER BY 1").fetchall()]
        title = a.title or f"{Path(a.path).name}: {a.y} by {a.x}"
        sub_t = f"facet={a.facet}, series={a.series}, {'log' if _LOG[0] else 'linear'} y"

    n_pts, n_ser = con.execute("SELECT count(*), count(DISTINCT (facet, series)) FROM dsr_pts").fetchone()
    print(f"  {n_pts:,} points / {n_ser:,} series / {len(order)} facets")

    per_series = (a.scale == 'series')
    if per_series and a.mode in ('overlay', 'both'):
        print("  [note] --scale series is grid-only; overlay ribbons need a shared "
              "axis, so the overlay page uses facet scale.")
    _prep(con, _LOG[0], 'facet' if per_series else a.scale)

    parts = []
    if a.mode in ('overlay', 'both'):
        parts.append(render_overlay(con, order, ref,
                                    a.cols or min(4, len(order)), a.sample, title, sub_t))
    if a.mode in ('grid', 'both'):
        parts.append(render_grid(con, order, a.cols or 20, a.max_cells,
                                 title + " -- contact sheet", sub_t, per_series=per_series))

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if len(parts) == 1:
        out.write_text(parts[0], encoding='utf-8')
        print(f"  wrote {out}  ({out.stat().st_size/1e3:.0f} KB)")
    else:
        for suffix, svg in zip(('overlay', 'grid'), parts):
            p = out.with_name(f"{out.stem}_{suffix}{out.suffix}")
            p.write_text(svg, encoding='utf-8')
            print(f"  wrote {p}  ({p.stat().st_size/1e3:.0f} KB)")


if __name__ == '__main__':
    main()
