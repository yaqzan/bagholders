"""
Sharadar bulk pull -- the delisted-equity grab (P2.A "pull" step).

One-month subscription => grab EVERYTHING to disk now, ingest later. This script only
DOWNLOADS + verifies; it never touches MySQL. Ingest is ingest_delisted_equity.py.

API: https://api.sharadar.com/v1.0/data/<table>   (docs: https://sharadar.com/docs/stocks)
NOT Nasdaq Data Link -- data-acquisition.md's "SHARADAR/SEP via data.nasdaq.com" is stale;
that host answers any key anonymously and 429s with QELx06.

Tables pulled (entitled on the 2026-07 one-month sub):
  tickers  metadata: permaticker, isdelisted, firstpricedate/lastpricedate, sector, exchange
  actions  splits / dividends / delistings / ticker changes  (split-reconciliation source)
  sp500    point-in-time S&P 500 membership (added/removed actions)
  stocks   daily OHLCV + closeadj/closeunadj, survivorship-bias-free  (~952 MB zipped)
  funds    daily OHLCV for mutual funds/ETFs, same schema as stocks   (~286 MB zipped)
  metrics  price-based metrics snapshot (beta/52w hi-lo/moving avgs/return); ONE row per
           ticker (its most-recent date), NOT a daily time series -- ~1.4 MB zipped

Entitlement re-probed 2026-08-01 (see .cache/sharadar/ENTITLEMENTS.json for the full
per-table record). NOT entitled on this subscription: `sf1`/`fundamentals` (core financial
statements -- the highest-value gap), `sf2`/`insiders`, `sf3`/`holdings` (+ `holdings-ticker`,
`holdings-investor`), `daily` (daily fundamentals), `events` (8-K material events) -- all 403
"Bulk status requires a subscription that includes this table." `sep` is a bulk-file alias for
`stocks` and `sfp` is an alias for `funds` (identical size/filename/modified in the status
response) -- don't pull both, they're the same file under a different table name.

Adjustment convention (verified on AAPL 2000-01-03):
  close      = split-adjusted only        (1.00)
  closeadj   = split + dividend adjusted  (0.838)   <- matches our yfinance auto_adjust=True
  closeunadj = as-traded                  (111.94)
So ingest_delisted_equity.py's `fac = closeadj/close` applied to OHLC is the right map.

Usage:
  python pull_sharadar.py                       # tickers,actions,sp500,stocks,funds,metrics
  python pull_sharadar.py --tables stocks       # one table
  python pull_sharadar.py --years 10            # shallower pull
  python pull_sharadar.py --force               # re-download even if present

Key: SHARADAR_API_KEY in the repo-root .env (gitignored), same convention as POLYGON_API_KEY.
Never pass the key on the command line.
"""
import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
import urllib.error
import zipfile
from datetime import datetime

REPO = r"C:\Development\Trader"
sys.path.insert(0, REPO)

BASE = "https://api.sharadar.com/v1.0/data/{table}"
# smallest-first so a schema surprise surfaces before the 952 MB download
DEFAULT_TABLES = ["sp500", "tickers", "metrics", "actions", "funds", "stocks"]
OUT_DIR = os.path.join(REPO, ".cache", "sharadar")
PACE_SECONDS = 2.0
MAX_RETRIES = 6
BACKOFF_BASE = 20.0
# Cloudflare in front of api.sharadar.com bans the literal `Python-urllib/*` UA with
# "error code: 1010" (verified 2026-07-29: urllib UA blocked, every other UA passes).
# Any non-urllib UA works, so identify ourselves honestly rather than spoofing a browser.
USER_AGENT = "Trader-DataIngest/1.0"

_last_call = [0.0]


def _req(url):
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT})


def _load_env():
    path = os.path.join(REPO, ".env")
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
    key = os.environ.get("SHARADAR_API_KEY", "").strip()
    if not key:
        sys.exit("SHARADAR_API_KEY not set (repo-root .env)")
    return key


def _pace():
    wait = PACE_SECONDS - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.time()


def _url(table, key, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{BASE.format(table=table)}?api_key={key}&{q}"


def _redact(url):
    return url.split("api_key=")[0] + "api_key=<redacted>&" + url.split("&", 1)[-1]


def status(table, key, years):
    _pace()
    url = _url(table, key, years=years, status="True")
    try:
        with urllib.request.urlopen(_req(url), timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": e.code, "description": e.read().decode(errors="replace")[:300]}


def _download(url, dest, expect_bytes=None):
    tmp = dest + ".part"
    t0 = time.time()
    for attempt in range(MAX_RETRIES):
        try:
            _pace()
            with urllib.request.urlopen(_req(url), timeout=900) as r, open(tmp, "wb") as f:
                total = int(r.headers.get("Content-Length") or expect_bytes or 0)
                done = 0
                last = 0.0
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if time.time() - last > 10:
                        last = time.time()
                        pct = f"{100 * done / total:.1f}%" if total else "?"
                        mbs = done / 1e6 / max(time.time() - t0, 1e-6)
                        eta = ((total - done) / (done / max(time.time() - t0, 1e-6))
                               if total and done else 0)
                        print(f"    {done / 1e6:,.0f} MB ({pct}) {mbs:.1f} MB/s "
                              f"eta {eta / 60:.1f}m", flush=True)
            break
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            if attempt < MAX_RETRIES - 1:
                back = BACKOFF_BASE * (2 ** attempt)
                print(f"  download error ({e}); retry in {back:.0f}s", flush=True)
                time.sleep(back)
                continue
            raise SystemExit(f"download failed after {MAX_RETRIES} attempts: {e}")
    os.replace(tmp, dest)
    return os.path.getsize(dest), time.time() - t0


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify_zip(path):
    """Stream the member CSV: return (members, name, header, data_rows)."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        with z.open(names[0]) as f:
            header = f.readline().decode(errors="replace").rstrip("\r\n")
            rows = 0
            while True:
                buf = f.read(1 << 22)
                if not buf:
                    break
                rows += buf.count(b"\n")
    return len(names), names[0], header, rows


def pull(table, key, years, force=False):
    os.makedirs(OUT_DIR, exist_ok=True)
    suffix = "" if years == "full" else f"-{years}Y"
    dest = os.path.join(OUT_DIR, f"{table}{suffix}.csv.zip")

    st = status(table, key, years)
    if "error" in st:
        print(f"[{table}] NOT AVAILABLE: {st.get('description', st['error'])}")
        return None
    exp = st.get("size")
    print(f"[{table}] remote {st.get('sizeLabel')} modified={st.get('modified')}", flush=True)

    if os.path.exists(dest) and not force and os.path.getsize(dest) == exp:
        print(f"[{table}] already present and size-matched -- skip (--force to re-pull)")
        return None

    print(f"[{table}] downloading...", flush=True)
    nbytes, secs = _download(_url(table, key, years=years), dest, expect_bytes=exp)
    size_ok = (exp is None or nbytes == exp)
    if not size_ok:
        print(f"  !! size mismatch: got {nbytes:,} expected {exp:,}")
    n_members, member, header, rows = _verify_zip(dest)
    rec = {
        "table": table,
        "years": years,
        "file": os.path.basename(dest),
        "bytes": nbytes,
        "expected_bytes": exp,
        "size_match": size_ok,
        "sha256": _sha256(dest),
        "remote_modified": st.get("modified"),
        "downloaded_at": datetime.now().isoformat(timespec="seconds"),
        "download_seconds": round(secs, 1),
        "zip_members": n_members,
        "csv_member": member,
        "csv_header": header,
        "csv_data_rows": rows,
    }
    print(f"[{table}] OK  {nbytes / 1e6:,.0f} MB  {rows:,} rows  in {secs / 60:.1f} min")
    print(f"  header: {header}")
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", default=",".join(DEFAULT_TABLES))
    ap.add_argument("--years", default="full", choices=["5", "10", "full"])
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    key = _load_env()
    tables = [t.strip().lower() for t in a.tables.split(",") if t.strip()]

    manifest_path = os.path.join(OUT_DIR, "manifest.json")
    manifest = {}
    if os.path.exists(manifest_path):
        with open(manifest_path) as f:
            manifest = json.load(f)

    for t in tables:
        rec = pull(t, key, a.years, force=a.force)
        if rec:
            manifest[rec["file"]] = rec
            os.makedirs(OUT_DIR, exist_ok=True)
            with open(manifest_path, "w") as f:
                json.dump(manifest, f, indent=2)

    print(f"\nmanifest -> {manifest_path}")
    for fn, r in sorted(manifest.items()):
        print(f"  {fn:24s} {r['bytes'] / 1e6:9,.0f} MB  {r['csv_data_rows']:12,} rows  "
              f"modified={r['remote_modified']}")


if __name__ == "__main__":
    main()
