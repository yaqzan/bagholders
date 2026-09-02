# ECERT_SUMMARY -- P1.A E-tier certificates

Pin: `f9fb7b934`  N_std: 2000  N_deep: 1000  Generated: 2026-07-29T08:46:59.968095+00:00

SCREEN, not GATE -- see assessment-backtest.md 'Deep-window screens (SCREEN, not GATE)' + gitnexus 6b155033b. A deep FAIL is a mandatory mechanism investigation, never an automatic revert. A deep PASS is weak comfort, never collapse-proof. Deep windows ride the survivor-only 1995 v74 score+regime+breadth backfill -- every number here reads OPTIMISTICALLY.

## Certificate rules

- Core and Sentinel: p_coll==0 required on every standard cell (CERT FAIL flag if not).
- Apex arms (apex_live, apex_n10): collapse reported with rule-of-three bounds, no hard fail.
- Deep cells: SCREEN only. KNOWN-EXPECTED Apex held-form deep-FAIL: the archived N=300 deep screen (experiments/deep_crash_screen/RESULTS.md, 2026-07-13) found Apex-held collapse=100% on dotcom_crash_2000_2002, 20.7% on gfc_crash_2007_2009, 48.3% on 2007_now (0% on ltcm_1998) -- a documented mechanism (capital-velocity law: 'never make the sprint the held default', concentration_2x FINDINGS), no new investigation required, mitigated live by the 2x watchdog halt-new-entries latch (0b6b778e0). A comparable N=1000 result on these same cells is the expected quantification tightening, NOT a new finding -- flag only if ltcm_1998 (the one archived PASS cell) newly shows collapse>0, or if Core's collapse stops being exactly 0 on any deep cell.

## Standard cells (N=2000)

| arm | window | mean_ret | med_ret | worst_dd | mean_dd | p_coll | ro3_bound(95%) | flag |
|---|---|---:|---:|---:|---:|---:|---:|---|
| sentinel | 2018 | +33.8 | +33.8 | 24.0 | 22.0 | 0.00 | <=0.15% |  |
| sentinel | 2020 | -39.9 | -40.1 | 62.7 | 59.4 | 0.00 | <=0.15% |  |
| sentinel | 2020_crash | -32.6 | -32.7 | 62.3 | 59.4 | 0.00 | <=0.15% |  |
| sentinel | 2021 | +79.8 | +79.0 | 25.1 | 18.8 | 0.00 | <=0.15% |  |
| sentinel | 2022 | +7.5 | +7.2 | 36.7 | 26.2 | 0.00 | <=0.15% |  |
| sentinel | 2023 | +55.5 | +52.8 | 21.5 | 19.4 | 0.00 | <=0.15% |  |
| sentinel | 2024 | +61.6 | +61.6 | 8.5 | 8.3 | 0.00 | <=0.15% |  |
| sentinel | dip | +43.8 | +44.7 | 15.8 | 14.7 | 0.00 | <=0.15% |  |
| sentinel | 22-now | +416.7 | +406.4 | 38.6 | 26.2 | 0.00 | <=0.15% |  |
| sentinel | 2025 | +26.2 | +26.1 | 19.8 | 15.2 | 0.00 | <=0.15% |  |
| sentinel | 5y | +830.6 | +811.8 | 38.2 | 26.3 | 0.00 | <=0.15% |  |
| sentinel | 10y | +1186.8 | +1161.0 | 63.1 | 59.4 | 0.00 | <=0.15% |  |

## Deep cells (SCREEN only, N=1000)

| arm | window | mean_ret | med_ret | worst_dd | p_coll | flag |
|---|---|---:|---:|---:|---:|---|
| sentinel | ltcm_1998 | +2.2 | +2.2 | 32.4 | 0.00 |  |
| sentinel | dotcom_crash_2000_2002 | +97.1 | +95.6 | 44.8 | 0.00 |  |
| sentinel | gfc_crash_2007_2009 | +54.8 | +52.9 | 35.8 | 0.00 |  |
| sentinel | 2007_now | +2013.4 | +1961.6 | 62.6 | 0.00 |  |

## Recipes echoed

```json
{
  "sentinel": {
    "TIER_ULTRA_OV": "0.2",
    "TIER_TOP_OV": "0.15",
    "TIER_MID_OV": "0.0",
    "TIER_LOW_OV": "0.0",
    "TIER_OVERFLOW_OV": "0.0",
    "PUT_TIER_TOP_OV": "0.0",
    "PUT_TIER_MID_OV": "0.0",
    "PUT_TIER_LOW_OV": "0.0",
    "MAX_POSITIONS_OVERRIDE": "14",
    "MAX_POSITIONS_CALL": "14",
    "MAX_POSITIONS_PUT": "0",
    "GROSS_PREMIUM_CAP": "0.3",
    "CALL_PREMIUM_CAP": "0.3",
    "PUT_PREMIUM_CAP": "0.0",
    "OPP_SAT_CALL_REF": "16.0",
    "OPP_SAT_PUT_REF": "4.0",
    "OPP_SAT_POWER": "0.5",
    "OPP_SAT_FLOOR": "0.55",
    "PRACTICAL_EXPOSURE_ENABLED": "1",
    "PRACTICAL_CAPITAL_CEILING": "1000000.0",
    "DD_SOFT_BAND_LO": "0.35",
    "DD_SOFT_BAND_HI": "0.55",
    "DD_SOFT_CALL_FLOOR": "0.4"
  }
}
```

## Fingerprint

```json
{
  "timestamp_utc": "2026-07-29T08:46:59.968095+00:00",
  "python_version": "3.11.9 (tags/v3.11.9:de54cf5, Apr  2 2024, 10:12:12) [MSC v.1938 64 bit (AMD64)]",
  "platform": "Windows-10-10.0.26200-SP0",
  "machine": "AMD64",
  "hostname": "Bookmaker",
  "cpu_count": 32,
  "numpy_version": "1.26.4",
  "numpy_blas": "{\"Compilers\": {\"c\": {\"name\": \"msvc\", \"linker\": \"link\", \"version\": \"19.29.30153\", \"commands\": \"cl\"}, \"cython\": {\"name\": \"cython\", \"linker\": \"cython\", \"version\": \"3.0.8\", \"commands\": \"cython\"}, \"c++\": {\"name\": \"msvc\", \"linker\": \"link\", \"version\": \"19.29.30153\", \"commands\": \"cl\"}}, \"Machine Information\": {\"host\": {\"cpu\": \"x86_64\", \"family\": \"x86_64\", \"endian\": \"little\", \"system\": \"windows\"}, \"build\": {\"cpu\": \"x86_64\", \"family\": \"x86_64\", \"endian\": \"little\", \"system\": \"windows\"}}, \"Build Dependencies\": {\"blas\": {\"name\": \"openblas64\", \"found\": true, \"version\": \"0.3.23.dev\", \"detection method\": \"pkgconfig\", \"include directory\": \"/c/opt/64/include\", \"lib directory\": \"/c/opt/64/lib\", \"openblas configuration\": \"USE_64BITINT=1 DYNAMIC_ARCH=1 DYNAMIC_OLDER= NO_CBLAS= NO_LAPACK= NO_LAPACKE= NO_AFFINITY=1 USE_OPENMP= SKYLAKEX MAX_THREADS=2\", \"pc file directory\": \"C:/opt/64/lib/pkgconfig\"}, \"lapack\": {\"name\": \"dep2270588361616\", \"found\": true, \"version\": \"1.26.4\", \"detection method\": \"internal\", \"include directory\": \"unknown\", \"lib directory\": \"unknown\", \"openblas configuration\": \"unknown\", \"pc file directory\": \"unknown\"}}, \"Python Information\": {\"path\": \"C:\\\\Users\\\\runneradmin\\\\AppData\\\\Local\\\\Temp\\\\cibw-run-j442zwj6\\\\cp311-win_amd64\\\\build\\\\venv\\\\Scripts\\\\python.exe\", \"version\": \"3.11\"}, \"SIMD Extensions\": {\"baseline\": [\"SSE\", \"SSE2\", \"SSE3\"], \"found\": [\"SSSE3\", \"SSE41\", \"POPCNT\", \"SSE42\", \"AVX\", \"F16C\", \"FMA3\", \"AVX2\", \"AVX512F\", \"AVX512CD\", \"AVX512_SKX\", \"AVX512_CLX\", \"AVX512_CNL\", \"AVX512_ICL\"]}}",
  "git_commit": "572e41861426b36b44b8027c5301fe7cc5da2ba3",
  "git_dirty": true
}
```
