# Sector ETF Alpha — Sector DD Probe

**Date:** 2026-05-11  
**Purpose:** Check whether the V4 sector-ETF alpha failure is really a sector-concentration allocation problem, analogous to the earlier DD-ledger work that led to SAW Put U-curve.

## What Was Tested

New script:

```bash
python experiments/sector_etf_alpha/sector_dd_probe.py --variant V4 --window 2024 --n 120
```

The script:

- runs `monte_carlo.py` with `MC_TRADE_TAPE=1`
- patches signal scores at load time for a sector-ETF variant
- writes isolated tape under `experiments/sector_etf_alpha/dd_probe/`
- joins trades to sector metadata
- attributes trades open during each seed's worst DD episode
- ranks worst-episode losses by `side × sector`

It does **not** overwrite `.cache/dd_ledger/tape_*.parquet`.

For seed-aligned comparisons, run with:

```bash
$env:PYTHONHASHSEED='0'
$env:PYTHONIOENCODING='utf-8'
python experiments/sector_etf_alpha/sector_dd_probe.py --variant BASELINE --window 2024 --n 120
python experiments/sector_etf_alpha/sector_dd_probe.py --variant V4 --window 2024 --n 120
```

## Seed-Aligned Smoke Result

N=120 is still smoke-level, but with `PYTHONHASHSEED=0` baseline and V4 use comparable seeds.

| Variant | 2024 MeanRet | 2024 WorstDD | 2024 MeanDD |
|---|---:|---:|---:|
| Baseline | +26.10B% | 60.2% | 33.3% |
| V4 | +5.99B% | 53.8% | 34.6% |

This seed-aligned smoke differs from the original Phase G smoke where V4 worsened 2024 DD. Treat direction as noisy at N=120. The stable signal is the loss attribution shape.

## Worst-Episode Loss Attribution — 2024 First Pass

Top V4 worst-episode loss rows:

| Rank | Side | Sector | Loss Share |
|---:|---|---|---:|
| 1 | put | technology | 31.4% |
| 2 | put | consumer-cyclical | 18.9% |
| 3 | call | technology | 13.5% |
| 4 | put | healthcare | 9.4% |
| 5 | call | energy | 7.8% |

Top baseline rows:

| Rank | Side | Sector | Loss Share |
|---:|---|---|---:|
| 1 | call | technology | 16.5% |
| 2 | call | industrials | 12.1% |
| 3 | put | basic-materials | 11.0% |
| 4 | call | consumer-cyclical | 8.6% |
| 5 | put | communication-services | 8.4% |

## Interpretation

The V4 DD surface is sectoral, but it is **not just "too many lifted calls in one sector."**

The biggest V4 worst-episode loss concentration is put-side technology and put-side consumer-cyclical. That means a pure call-sector cap may miss the main risk. The sector-specific misfire is broader: when the sector wave is at an inflection, both sides can be wrong at once depending on whether the book is positioned for continuation or reversal.

## Larger DD Windows

Follow-up run fixed `PYTHONHASHSEED=0` and probed `2022`, `22-now`, and `5y` as well as `2024`.

Commands:

```bash
$env:PYTHONHASHSEED='0'
$env:PYTHONIOENCODING='utf-8'
python experiments/sector_etf_alpha/sector_dd_probe.py --variant BASELINE --window 2022 --n 120
python experiments/sector_etf_alpha/sector_dd_probe.py --variant V4       --window 2022 --n 120
python experiments/sector_etf_alpha/sector_dd_probe.py --variant BASELINE --window 22-now --n 120
python experiments/sector_etf_alpha/sector_dd_probe.py --variant V4       --window 22-now --n 120
python experiments/sector_etf_alpha/sector_dd_probe.py --variant BASELINE --window 5y --n 120
python experiments/sector_etf_alpha/sector_dd_probe.py --variant V4       --window 5y --n 120
```

### Side Mix In Worst Episodes

Top-20 sector rows by loss share, grouped by side:

| Window | Variant | Put Share | Call Share | Dominant rows |
|---|---:|---:|---:|---|
| 2022 | Baseline | 78.1% | 21.9% | put consumer-cyclical 23.3%, put financials 16.5%, put technology 13.6% |
| 2022 | V4 | **91.4%** | 8.6% | put technology 22.6%, put unknown 15.8%, put financials 13.8% |
| 2024 | Baseline | 48.5% | 51.0% | call technology 16.5%, call industrials 12.1%, put basic-materials 11.0% |
| 2024 | V4 | **70.9%** | 29.0% | put technology 31.4%, put consumer-cyclical 18.9%, call technology 13.5% |
| 22-now | Baseline | 48.5% | 51.5% | call technology 23.8%, put technology 20.3%, put energy 13.3% |
| 22-now | V4 | **71.7%** | 28.3% | put healthcare 16.4%, put energy 13.2%, put consumer-cyclical 11.8% |
| 5y | Baseline | 73.0% | 26.4% | put healthcare 19.3%, put energy 17.0%, put technology 15.1% |
| 5y | V4 | 70.9% | 29.1% | put healthcare 18.7%, put consumer-cyclical 14.2%, put energy 13.2% |

### Readout

The bigger DD windows do **not** confirm a pure "V4 call-sector concentration" diagnosis.

They show a stronger pattern:

1. V4 makes worst episodes **more put-heavy** in 2022, 2024, and 22-now.
2. The sector concentration is real, but it rotates by window:
   - 2022: technology / financials / basic-materials puts
   - 2024: technology / consumer-cyclical puts
   - 22-now: healthcare / energy / consumer-cyclical puts
   - 5y: healthcare / consumer-cyclical / energy puts
3. The sector is not stable enough for a fixed sector blacklist or a flat per-sector cap.
4. The side is stable enough to justify a **side-aware sector exposure mechanism**, with put-side handling as first-class.

This is much closer to the SAW lesson than the initial V4 score-stage story: the alpha exists, but portfolio loss happens when side × sector exposure is mis-sized at the wrong sector-wave state.

## Answer To The Open Question

Have we tried limiting allocation per sector at the portfolio level?

**No.** Phase G only tested score-stage dampening and promotion suppression. It did not test a portfolio-level sector allocation cap.

Should we test it?

**Yes, but not as a flat hard cap first.** The probe suggests the better law is likely a sector exposure wave:

- measure open exposure by `side × sector`
- scale new allocation as sector exposure rises
- make the scale depend on sector ETF phase / sector breadth state
- allow sector concentration when the sector wave is confirming
- contract sector concentration near sector inflection / reversal zones

This is analogous to SAW Put U-curve: don't delete the alpha, shape allocation by the wave state.

## Next Experiment Plan

### Stage A — Ledger Mining First

Before sweeping allocation knobs, build a sector-wave ledger over the saved `dd_probe/*_tape.parquet` files:

Features per trade:

- `side`
- `sector`
- `sector_etf`
- `sector_rsi`
- `sector_pct_ema50`
- `sector_ret_5d`
- `sector_phase`
- `stock_rs_5d`
- `open_side_sector_exposure`
- `open_total_sector_exposure`
- `entry_dd`
- `in_worst_episode`
- `dd_contribution_share`

Primary tables:

1. `side × sector_rsi_bin × exposure_bin`
2. `side × sector_phase_bin × exposure_bin`
3. `side × stock_rs_5d_abs_bin × exposure_bin`
4. `side × sector × exposure_bin` only as diagnostic, not as a mechanism candidate

The goal is to find a smooth surface where DD contribution rises with exposure and sector-wave state.

### Stage B — Portfolio Mechanism Sweep

Build a portfolio-stage `SECTOR_WAVE_ALLOC` experiment:

1. Add experiment-local MC runner, not production code.
2. Track open premium exposure by `side × sector`.
3. Apply a continuous scale to new positions:

```text
sector_exposure = open_premium(side, sector) / portfolio_value
phase_risk = f(abs(sector_phase), sector_rsi, sector_pct_ema50)
scale = 1 - alpha * exposure_curve(sector_exposure) * inflection_curve(phase_risk)
```

Candidate mechanism families:

| Family | Rationale |
|---|---|
| SWA-PUT | put-side only, because larger DD windows point put-heavy |
| SWA-BOTH | both sides, but independent call/put params |
| SWA-NET | net sector exposure, calls and puts offset each other |
| SWA-GROSS | gross sector exposure, calls and puts do not offset |
| SWA-U | U-curve on sector RSI/phase, analogous to SAW |
| SWA-INFLECT | contracts only near sector inflection: sector RSI 35-65 and pct_ema50 near zero |

First grid:

| Param | Range |
|---|---|
| exposure_start | 0.20, 0.30, 0.40 |
| exposure_full | 0.45, 0.60, 0.75 |
| floor | 0.50, 0.65, 0.80 |
| side scope | puts, both |
| exposure type | gross, net |
| sector state | flat exposure, sector RSI U-curve, sector phase U-curve, inflection-only |

### Stage C — Gate

Use Stage 3 only, because this is allocation-stage:

- Phase C1: N=150 × 4 windows (`2022`, `2024`, `22-now`, `5y`) to screen.
- Phase C2: top 5 at N=300 × 8 windows.
- Phase C3: winner at N=500 × 8 windows.

Primary target: reduce 5y / 22-now WorstDD without >25% compound loss on 2024 or 2025.

Do **not** ship a flat sector cap unless the ledger shows exposure alone dominates sector-wave state. Current evidence points to a wave-shaped exposure law, not a hard cap.

Ship gate should be Stage 3 only: N=300+ × 8 windows, DD-primary. This is a portfolio allocation mechanism, not a score change.
