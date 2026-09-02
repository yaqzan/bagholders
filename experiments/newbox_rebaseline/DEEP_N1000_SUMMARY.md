# DEEP_N1000_SUMMARY -- P1.C-2 deep crash screens at N=1000

N=1000  Pin: `f9fb7b934`  Generated: 2026-07-29T16:49:12.787027+00:00

SCREEN, not GATE -- see assessment-backtest.md 'Deep-window screens (SCREEN, not GATE)' + gitnexus 6b155033b. A deep FAIL is a mandatory mechanism investigation, never an automatic revert. A deep PASS is weak comfort, never collapse-proof. Deep windows ride the survivor-only 1995 v74 score+regime+breadth backfill -- every number here reads OPTIMISTICALLY.

KNOWN-EXPECTED Apex held-form deep-FAIL: the archived N=300 deep screen (experiments/deep_crash_screen/RESULTS.md, 2026-07-13) found Apex-held collapse=100% on dotcom_crash_2000_2002, 20.7% on gfc_crash_2007_2009, 48.3% on 2007_now (0% on ltcm_1998) -- a documented mechanism (capital-velocity law: 'never make the sprint the held default', concentration_2x FINDINGS), no new investigation required, mitigated live by the 2x watchdog halt-new-entries latch (0b6b778e0). A comparable N=1000 result on these same cells is the expected quantification tightening, NOT a new finding -- flag only if ltcm_1998 (the one archived PASS cell) newly shows collapse>0, or if Core's collapse stops being exactly 0 on any deep cell.

## Cells

| profile | window | mean_ret | med_ret | worst_dd | p_coll | vs N=300 archived p_coll | flag |
|---|---|---:|---:|---:|---:|---:|---|
| core | ltcm_1998 | -2.4 | -2.2 | 58.7 | 0.0 | 0.0 |  |
| core | dotcom_crash_2000_2002 | +8.3 | +4.0 | 63.0 | 0.0 | 0.0 |  |
| core | gfc_crash_2007_2009 | +28.6 | +26.9 | 68.7 | 0.0 | 0.0 |  |
| core | 2007_now | +5287.2 | +4805.8 | 76.2 | 0.0 | 0.0 |  |
| apex_live | ltcm_1998 | -57.1 | -58.7 | 76.9 | 0.0 | 0.0 |  |
| apex_live | dotcom_crash_2000_2002 | -79.2 | -81.4 | 96.3 | 75.5 | 100.0 | expected (dot-com held-form) |
| apex_live | gfc_crash_2007_2009 | -66.8 | -67.0 | 85.6 | 3.5 | 20.7 | reported, no hard fail |
| apex_live | 2007_now | -49.7 | -81.0 | 93.2 | 68.1 | 48.3 | reported, no hard fail |

## Fingerprint

```json
{
  "timestamp_utc": "2026-07-29T16:49:12.787027+00:00",
  "python_version": "3.11.9 (tags/v3.11.9:de54cf5, Apr  2 2024, 10:12:12) [MSC v.1938 64 bit (AMD64)]",
  "platform": "Windows-10-10.0.26200-SP0",
  "machine": "AMD64",
  "hostname": "Bookmaker",
  "cpu_count": 32,
  "numpy_version": "1.26.4",
  "numpy_blas": "{\"Compilers\": {\"c\": {\"name\": \"msvc\", \"linker\": \"link\", \"version\": \"19.29.30153\", \"commands\": \"cl\"}, \"cython\": {\"name\": \"cython\", \"linker\": \"cython\", \"version\": \"3.0.8\", \"commands\": \"cython\"}, \"c++\": {\"name\": \"msvc\", \"linker\": \"link\", \"version\": \"19.29.30153\", \"commands\": \"cl\"}}, \"Machine Information\": {\"host\": {\"cpu\": \"x86_64\", \"family\": \"x86_64\", \"endian\": \"little\", \"system\": \"windows\"}, \"build\": {\"cpu\": \"x86_64\", \"family\": \"x86_64\", \"endian\": \"little\", \"system\": \"windows\"}}, \"Build Dependencies\": {\"blas\": {\"name\": \"openblas64\", \"found\": true, \"version\": \"0.3.23.dev\", \"detection method\": \"pkgconfig\", \"include directory\": \"/c/opt/64/include\", \"lib directory\": \"/c/opt/64/lib\", \"openblas configuration\": \"USE_64BITINT=1 DYNAMIC_ARCH=1 DYNAMIC_OLDER= NO_CBLAS= NO_LAPACK= NO_LAPACKE= NO_AFFINITY=1 USE_OPENMP= SKYLAKEX MAX_THREADS=2\", \"pc file directory\": \"C:/opt/64/lib/pkgconfig\"}, \"lapack\": {\"name\": \"dep2270588361616\", \"found\": true, \"version\": \"1.26.4\", \"detection method\": \"internal\", \"include directory\": \"unknown\", \"lib directory\": \"unknown\", \"openblas configuration\": \"unknown\", \"pc file directory\": \"unknown\"}}, \"Python Information\": {\"path\": \"C:\\\\Users\\\\runneradmin\\\\AppData\\\\Local\\\\Temp\\\\cibw-run-j442zwj6\\\\cp311-win_amd64\\\\build\\\\venv\\\\Scripts\\\\python.exe\", \"version\": \"3.11\"}, \"SIMD Extensions\": {\"baseline\": [\"SSE\", \"SSE2\", \"SSE3\"], \"found\": [\"SSSE3\", \"SSE41\", \"POPCNT\", \"SSE42\", \"AVX\", \"F16C\", \"FMA3\", \"AVX2\", \"AVX512F\", \"AVX512CD\", \"AVX512_SKX\", \"AVX512_CLX\", \"AVX512_CNL\", \"AVX512_ICL\"]}}",
  "git_commit": "cff8c8922629a170e6b185d88738af05d50962ff",
  "git_dirty": true
}
```
