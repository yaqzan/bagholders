# NOISE_FLOOR_TABLE -- P1.E measured seed-noise dispersion

Supersedes Phase-v32 inherited noise figures (known-issues.md) once adopted; measured on this fingerprint (see bottom). Generated: 2026-07-29T08:53:47.726580+00:00

Windows: ['5y', '22-now', '2020_crash']  Batches: 8  Tiers: [300, 500, 1000, 2000]  Pin: `f9fb7b934`

| window | tier(N) | worst_dd mean | worst_dd std | worst_dd min | worst_dd max | worst_dd range | mean_ret max/min ratio | med_ret max/min ratio | p_coll (per batch) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 5y | 300 | 49.155 | 0.489 | 48.630 | 50.095 | 1.465 | 1.0664524501061319 | 1.0607869980944764 | [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0] |
| 5y | 500 | 49.202 | 0.445 | 48.755 | 50.095 | 1.340 | 1.049513557552197 | 1.05652649982993 | [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0] |
| 5y | 1000 | 49.511 | 0.429 | 48.855 | 50.095 | 1.240 | 1.0353041823858429 | 1.0414306872671448 | [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0] |
| 5y | 2000 | 49.847 | 0.300 | 49.244 | 50.251 | 1.007 | 1.0115103994977164 | 1.0188907946578252 | [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0] |
| 22-now | 300 | 49.019 | 0.329 | 48.753 | 49.819 | 1.067 | 1.0669396696502966 | 1.0904118447634505 | [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0] |
| 22-now | 500 | 49.211 | 0.340 | 48.854 | 49.819 | 0.965 | 1.0381242433445952 | 1.0482641771338905 | [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0] |
| 22-now | 1000 | 49.390 | 0.263 | 48.854 | 49.819 | 0.965 | 1.027548505741208 | 1.0402476065482742 | [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0] |
| 22-now | 2000 | 49.554 | 0.289 | 49.008 | 50.049 | 1.040 | 1.0140465668228689 | 1.026270831825496 | [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0] |
| 2020_crash | 300 | 70.008 | 0.357 | 69.455 | 70.548 | 1.094 | 0.9877449567852409 | 0.9874630048281094 | [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0] |
| 2020_crash | 500 | 70.202 | 0.253 | 69.713 | 70.548 | 0.835 | 0.9898115051107961 | 0.9910674835155147 | [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0] |
| 2020_crash | 1000 | 70.331 | 0.336 | 69.713 | 70.980 | 1.266 | 0.9936573366230691 | 0.9897156857486138 | [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0] |
| 2020_crash | 2000 | 70.384 | 0.293 | 69.907 | 70.980 | 1.072 | 0.9956631977046778 | 0.9918638744581065 | [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0] |

## Fingerprint

```json
{
  "timestamp_utc": "2026-07-29T08:53:47.726580+00:00",
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
