# RESULTS -- w5dte_minute_real -- SMOKE MODE (2 named weeks only; NOT the full population; NOT a real verdict)

Generated 2026-08-18 12:48:02 (local), seed_base=20260818, mode=smoke
Timezone method: zoneinfo(America/New_York) -- DST-correct
Sessions scanned: 8; raw bar-rows read: 15166864; scan wall-clock: 1.09s
missing_minute_files: 0 []
Events processed: 99 (FAMILY=39, CONTROL0=18, CONTROL1=19, CONTROL2=23)

## Smoke checks

[PASS] 5. Control draws reproduce (draw_idx=0 n_selected) -- draw_idx=0 n_selected: reference=58057 got=58057
[PASS] 1. Event-count reconciliation (full-then-filter vs filter-then-mask) -- full-then-filter=39 filter-then-mask=39
[PASS] 6. Holdout assert on event frame -- no exception raised
[PASS] 2. R0 sanity >= 95% on smoke events (L5) -- L5 r0_ok=99/99 (1.0000); misses=[]
[PASS] 3. Hand-verification (independent manual sum vs pipeline) -- manual: mins=49 vol=331; pipeline: mins=49 vol=331
[PASS] 4. Monotonicity validity_R1 >= R2 >= R3 per arm/L -- FAMILY/L5: R1=1.0000 R2=0.8718 R3=0.7436 OK; FAMILY/L10: R1=1.0000 R2=0.8095 R3=0.5238 OK; CONTROL0/L5: R1=0.9444 R2=0.8333 R3=0.6111 OK; CONTROL0/L10: R1=1.0000 R2=1.0000 R3=1.0000 OK; CONTROL1/L5: R1=0.8947 R2=0.7895 R3=0.4737 OK; CONTROL1/L10: R1=1.0000 R2=0.6000 R3=0.6000 OK; CONTROL2/L5: R1=0.9130 R2=0.8696 R3=0.4783 OK; CONTROL2/L10: R1=0.8333 R2=0.8333 R3=0.6667 OK

## Table A1 -- validity rate by arm x L x tier (denominator = r0_ok events)

| arm | L | n_total | n_r0_ok | r0_fail_n | r0_fail_rate | validity_R1 | validity_R2 | validity_R3 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| FAMILY | 5 | 39 | 39 | 0 | 0 | 1 | 0.871795 | 0.74359 |
| FAMILY | 10 | 21 | 21 | 0 | 0 | 1 | 0.809524 | 0.52381 |
| CONTROL0 | 5 | 18 | 18 | 0 | 0 | 0.944444 | 0.833333 | 0.611111 |
| CONTROL0 | 10 | 6 | 6 | 0 | 0 | 1 | 1 | 1 |
| CONTROL1 | 5 | 19 | 19 | 0 | 0 | 0.894737 | 0.789474 | 0.473684 |
| CONTROL1 | 10 | 5 | 5 | 0 | 0 | 1 | 0.6 | 0.6 |
| CONTROL2 | 5 | 23 | 23 | 0 | 0 | 0.913043 | 0.869565 | 0.478261 |
| CONTROL2 | 10 | 6 | 6 | 0 | 0 | 0.833333 | 0.833333 | 0.666667 |

## Table A2 -- FAMILY validity by expiry_year x L x tier (secondary)

| expiry_year | L | n_total | n_r0_ok | r0_fail_n | r0_fail_rate | validity_R1 | validity_R2 | validity_R3 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2023 | 5 | 17 | 17 | 0 | 0 | 1 | 0.705882 | 0.529412 |
| 2023 | 10 | 7 | 7 | 0 | 0 | 1 | 0.714286 | 0.428571 |
| 2024 | 5 | 22 | 22 | 0 | 0 | 1 | 1 | 0.909091 |
| 2024 | 10 | 14 | 14 | 0 | 0 | 1 | 0.857143 | 0.571429 |

## Table A3 -- FAMILY validity by rule R1..R6 x L x tier (secondary)

| rule | L | n_total | n_r0_ok | r0_fail_n | r0_fail_rate | validity_R1 | validity_R2 | validity_R3 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| R1 | 5 | 22 | 22 | 0 | 0 | 1 | 0.954545 | 0.954545 |
| R1 | 10 | 16 | 16 | 0 | 0 | 1 | 0.8125 | 0.5 |
| R2 | 5 | 16 | 16 | 0 | 0 | 1 | 0.9375 | 0.9375 |
| R2 | 10 | 12 | 12 | 0 | 0 | 1 | 0.833333 | 0.5 |
| R3 | 5 | 16 | 16 | 0 | 0 | 1 | 0.9375 | 0.9375 |
| R3 | 10 | 12 | 12 | 0 | 0 | 1 | 0.833333 | 0.5 |
| R4 | 5 | 22 | 22 | 0 | 0 | 1 | 0.954545 | 0.954545 |
| R4 | 10 | 16 | 16 | 0 | 0 | 1 | 0.8125 | 0.5 |
| R5 | 5 | 33 | 33 | 0 | 0 | 1 | 0.848485 | 0.69697 |
| R5 | 10 | 17 | 17 | 0 | 0 | 1 | 0.823529 | 0.529412 |
| R6 | 5 | 33 | 33 | 0 | 0 | 1 | 0.848485 | 0.69697 |
| R6 | 10 | 17 | 17 | 0 | 0 | 1 | 0.823529 | 0.529412 |

## Table B -- gated EV re-read (arm x L x {ungated, R1, R2, R3})

| arm | L | n | ungated_ev | R1_ev | R2_ev | R3_ev |
| --- | --- | --- | --- | --- | --- | --- |
| FAMILY | 5 | 348 | -0.432991 | -0.432991 | -0.504428 | -0.529442 |
| FAMILY | 10 | 348 | -0.27718 | -0.27718 | -0.384061 | -0.476525 |
| CONTROL0 | 5 | 348 | -0.265776 | -0.275493 | -0.278943 | -0.298805 |
| CONTROL0 | 10 | 348 | -0.198147 | -0.198147 | -0.198147 | -0.198147 |
| CONTROL1 | 5 | 348 | -0.159163 | -0.16067 | -0.183322 | -0.107727 |
| CONTROL1 | 10 | 348 | -0.10604 | -0.10604 | -0.0552356 | -0.0552356 |
| CONTROL2 | 5 | 348 | -0.164854 | -0.167287 | -0.164885 | -0.220416 |
| CONTROL2 | 10 | 348 | -0.142364 | -0.131504 | -0.131504 | -0.160239 |

PREREG SURVIVES line (Adjudication, evaluated verbatim):
- verdict: FAIL
- family_R2_ev_L5: -0.5044282439161173
- control_R2_ev_L5: [-0.2789430847476254, -0.18332168923540032, -0.16488471061742196]
- cond_a_positive: False
- cond_b_beats_all_3_controls: False
- note: 3 draws = directional screen per PREREG, not the 100-draw EV gate

## Table C1 -- first-touch time-of-day histogram (30-min ET buckets, r0_ok events)

| arm | L | bucket | n |
| --- | --- | --- | --- |
| FAMILY | 5 | 09:30-10:00 | 25 |
| FAMILY | 5 | 10:00-10:30 | 6 |
| FAMILY | 5 | 10:30-11:00 | 2 |
| FAMILY | 5 | 11:00-11:30 | 1 |
| FAMILY | 5 | 11:30-12:00 | 1 |
| FAMILY | 5 | 13:00-13:30 | 1 |
| FAMILY | 5 | 13:30-14:00 | 1 |
| FAMILY | 5 | 14:00-14:30 | 2 |
| FAMILY | 10 | 09:30-10:00 | 11 |
| FAMILY | 10 | 10:00-10:30 | 7 |
| FAMILY | 10 | 11:00-11:30 | 1 |
| FAMILY | 10 | 13:00-13:30 | 1 |
| FAMILY | 10 | 15:30-16:00 | 1 |
| CONTROL0 | 5 | 09:30-10:00 | 11 |
| CONTROL0 | 5 | 10:00-10:30 | 2 |
| CONTROL0 | 5 | 11:00-11:30 | 1 |
| CONTROL0 | 5 | 11:30-12:00 | 1 |
| CONTROL0 | 5 | 12:00-12:30 | 1 |
| CONTROL0 | 5 | 12:30-13:00 | 1 |
| CONTROL0 | 5 | 14:30-15:00 | 1 |
| CONTROL0 | 10 | 10:00-10:30 | 3 |
| CONTROL0 | 10 | 10:30-11:00 | 3 |
| CONTROL1 | 5 | 09:30-10:00 | 10 |
| CONTROL1 | 5 | 10:00-10:30 | 3 |
| CONTROL1 | 5 | 12:00-12:30 | 4 |
| CONTROL1 | 5 | 12:30-13:00 | 2 |
| CONTROL1 | 10 | 09:30-10:00 | 2 |
| CONTROL1 | 10 | 10:30-11:00 | 1 |
| CONTROL1 | 10 | 11:30-12:00 | 1 |
| CONTROL1 | 10 | 13:30-14:00 | 1 |
| CONTROL2 | 5 | 09:30-10:00 | 10 |
| CONTROL2 | 5 | 10:00-10:30 | 4 |
| CONTROL2 | 5 | 10:30-11:00 | 1 |
| CONTROL2 | 5 | 11:30-12:00 | 1 |
| CONTROL2 | 5 | 12:00-12:30 | 2 |
| CONTROL2 | 5 | 12:30-13:00 | 1 |
| CONTROL2 | 5 | 14:00-14:30 | 1 |
| CONTROL2 | 5 | 14:30-15:00 | 2 |
| CONTROL2 | 5 | 15:00-15:30 | 1 |
| CONTROL2 | 10 | 09:30-10:00 | 2 |
| CONTROL2 | 10 | 10:00-10:30 | 3 |
| CONTROL2 | 10 | 15:30-16:00 | 1 |

## Table C2 -- open-auction share (<10:00 ET) + lone-print share (mins_at_above==1)

| arm | L | n | open_auction_share | lone_print_share |
| --- | --- | --- | --- | --- |
| FAMILY | 5 | 39 | 0.641026 | 0.0512821 |
| FAMILY | 10 | 21 | 0.52381 | 0 |
| CONTROL0 | 5 | 18 | 0.611111 | 0.0555556 |
| CONTROL0 | 10 | 6 | 0 | 0 |
| CONTROL1 | 5 | 19 | 0.526316 | 0.157895 |
| CONTROL1 | 10 | 5 | 0.4 | 0 |
| CONTROL2 | 5 | 23 | 0.434783 | 0.0869565 |
| CONTROL2 | 10 | 6 | 0.333333 | 0.166667 |

## Table D -- data quality (r0 failures by arm x L)

| arm | L | n | r0_fail_n | r0_fail_rate |
| --- | --- | --- | --- | --- |
| FAMILY | 5 | 39 | 0 | 0 |
| FAMILY | 10 | 21 | 0 | 0 |
| CONTROL0 | 5 | 18 | 0 | 0 |
| CONTROL0 | 10 | 6 | 0 | 0 |
| CONTROL1 | 5 | 19 | 0 | 0 |
| CONTROL1 | 10 | 5 | 0 | 0 |
| CONTROL2 | 5 | 23 | 0 | 0 |
| CONTROL2 | 10 | 6 | 0 | 0 |

## Table E -- FAMILY vs CONTROL validity gap (R2, secondary)

| L | control_arm | family_validity_R2 | control_validity_R2 | gap |
| --- | --- | --- | --- | --- |
| 5 | CONTROL0 | 0.871795 | 0.833333 | 0.0384615 |
| 5 | CONTROL1 | 0.871795 | 0.789474 | 0.0823212 |
| 5 | CONTROL2 | 0.871795 | 0.869565 | 0.00222965 |
| 10 | CONTROL0 | 0.809524 | 1 | -0.190476 |
| 10 | CONTROL1 | 0.809524 | 0.6 | 0.209524 |
| 10 | CONTROL2 | 0.809524 | 0.833333 | -0.0238095 |

