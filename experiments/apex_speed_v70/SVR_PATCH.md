# SVR monte_carlo patch (apply AFTER calm-barriers C frees the engine)

Cache built: `.cache/apex_speed_v70/semivol_map.parquet` (sym_id=ticker, date, semivol_r).
Driver ENV_MAP + sweep_speed `SVR_B_CANDS` (--mode svrB) already wired.

## Patch 1 — constants + helpers (after the TSL block in monte_carlo.py, near `_exr_eff_cap`)
```python
# SVR — semivol_r (skew-bridge) entry filter (Stage-3 sweep candidate, env-only).
# semivol_r = 60d downside/upside realized-vol ratio (10y MC-computable cousin of option
# put-skew). Cohort (10y): low (~0.5, euphoric/expensive call) WORST; very-high (~1.4,
# crash-mode) weak; middle-high (~0.9-1.1) best. Downweights call alloc toward SVR_FLOOR
# below SVR_LO_FULL and above SVR_HI_FULL; full in the sweet spot. Default OFF => 1.0 => identical.
SVR_ENABLED  = os.environ.get('SVR_ENABLED', '1' if getattr(_cfg, 'SVR_ENABLED', False) else '0') == '1'
SVR_LO_CUT   = float(os.environ.get('SVR_LO_CUT',  str(getattr(_cfg, 'SVR_LO_CUT', 0.60))))
SVR_LO_FULL  = float(os.environ.get('SVR_LO_FULL', str(getattr(_cfg, 'SVR_LO_FULL', 0.80))))
SVR_HI_FULL  = float(os.environ.get('SVR_HI_FULL', str(getattr(_cfg, 'SVR_HI_FULL', 9.0))))
SVR_HI_CUT   = float(os.environ.get('SVR_HI_CUT',  str(getattr(_cfg, 'SVR_HI_CUT', 99.0))))
SVR_FLOOR    = float(os.environ.get('SVR_FLOOR',   str(getattr(_cfg, 'SVR_FLOOR', 0.50))))

_SVR_MAP = None
def _svr_load():
    global _SVR_MAP
    if _SVR_MAP is not None:
        return _SVR_MAP
    _SVR_MAP = {}
    try:
        import polars as _pl
        from datetime import date as _date
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         '.cache', 'apex_speed_v70', 'semivol_map.parquet')
        if os.path.exists(p):
            df = _pl.read_parquet(p)
            for sid, ds, sv in zip(df['sym_id'].to_list(), df['date'].to_list(), df['semivol_r'].to_list()):
                y, m, d = ds[:10].split('-')
                _SVR_MAP[(sid, _date(int(y), int(m), int(d)))] = sv
    except Exception as _e:
        print(f"  [SVR] map load failed ({_e}); SVR inactive")
    return _SVR_MAP

def _svr_scale(svr):
    if not SVR_ENABLED or svr is None:
        return 1.0
    if svr < SVR_LO_FULL:
        t = 0.0 if SVR_LO_FULL <= SVR_LO_CUT else (svr - SVR_LO_CUT) / (SVR_LO_FULL - SVR_LO_CUT)
        if SVR_LO_FULL <= SVR_LO_CUT and svr > SVR_LO_CUT: t = 1.0
        t = 0.0 if t < 0 else 1.0 if t > 1 else t
        return SVR_FLOOR + (1.0 - SVR_FLOOR) * t
    if svr > SVR_HI_FULL:
        t = 0.0 if SVR_HI_CUT <= SVR_HI_FULL else (SVR_HI_CUT - svr) / (SVR_HI_CUT - SVR_HI_FULL)
        if SVR_HI_CUT <= SVR_HI_FULL and svr < SVR_HI_CUT: t = 1.0
        t = 0.0 if t < 0 else 1.0 if t > 1 else t
        return SVR_FLOOR + (1.0 - SVR_FLOOR) * t
    return 1.0
```

## Patch 2 — apply in `_try_fill_call` (monte_carlo.py L2113-2114)
```python
            rxdd_scale = _rxdd_call_scale(dd, rxdd_vix_today)
            svr_scale = _svr_scale(_svr_load().get((sym_id, today))) if SVR_ENABLED else 1.0
            alloc_frac   = TIER_ALLOC[tier] * reg_scale_c * dd_scale * sat_scale * rxdd_scale * svr_scale
```

## Then
- verify (off==baseline; on changes a window): add `verify_svr` to sweep_speed or run a 1-window off/on.
- `python -u sweep_speed.py --mode svrB --n 100 --wins B` (8 variants).
- top-3 -> Phase C N=300x8 incl COVID. Stage if it holds (collapse=0 + compound up).
