# Stage A — component-combination win/loss on v70 75+ CALL signals (funded APEX barrier)
(filtered 33945 -> 33917 rows on finite/sane vol_pct)
ledger: C:\Development\Trader\.cache\component_reweight\ledger_v70_5y.parquet   rows=33917   dates 2016-05-16..2026-05-15
PRIMARY win label = apex15 (funded APEX TP30/SL70/15d, fixed thresholds; provisional —
  knobs being retuned, but lead-class ORDERING is barrier-robust, see robustness table).
Secondary: opt15 (growth-gate scaled barrier), gen15 (dashboard), hold15 (prior proxy).
NOTE win% is the apex TP-before-SL touch rate; funded EV also depends on day-15 expire
  exits (not -70%), so true EV > what the raw win% vs 70% break-even implies.

=== ALL baselines (N, apexTP15 / holdTP15 / optTP15 / genWR15) ===
  70-74   N= 29219   apex  68.1   hold  76.9   opt  46.8   gen  63.8
  75-79   N=  3711   apex  69.8   hold  78.6   opt  47.9   gen  65.7
  80-84   N=   584   apex  68.8   hold  78.3   opt  45.0   gen  67.0
  85+     N=   403   apex  73.7   hold  82.6   opt  48.1   gen  71.2
  75+     N=  4698   apex  70.0   hold  78.9   opt  47.5   gen  66.3

=== 75+ POOL — lead-component (max weighted contribution) vs apex15 ===
  pool baseline apex15 =  70.0  (N=4698)
  lead=RSI    N=  122    77.9   z=+1.87   share= 2.6%
  lead=BB     N=   30    76.7   z=+0.79   share= 0.6%
  lead=MACD   N=  626    73.3   z=+1.70   share=13.3%
  lead=TREND  N= 3912    69.2   z=-0.81   share=83.3%
  lead=STOCH  N=    8    62.5   z=         share= 0.2%
  lead=TA     N=    0     n/a   z=         share= 0.0%

=== 75+ POOL — lead-component (max weighted contribution) vs opt15 ===
  pool baseline opt15 =  47.5  (N=4698)
  lead=STOCH  N=    8    62.5   z=         share= 0.2%
  lead=MACD   N=  626    49.2   z=+0.79   share=13.3%
  lead=TREND  N= 3912    47.3   z=-0.18   share=83.3%
  lead=RSI    N=  122    45.1   z=-0.53   share= 2.6%
  lead=BB     N=   30    43.3   z=-0.46   share= 0.6%
  lead=TA     N=    0     n/a   z=         share= 0.0%

=== 75+ POOL — per-RAW-component tercile vs apex15 (within band) ===
  baseline apex15 =  70.0 (N=4698). Spread = HIGH-tercile minus LOW-tercile WR.
  TREND  [<=83|94<] lo  70.6 (N 1690)  mid  69.7  hi  69.8 (N 1472)   spread  -0.8  z=-0.50
  BB     [<=63|72<] lo  69.8 (N 1627)  mid  71.7  hi  68.5 (N 1523)   spread  -1.3  z=-0.77
  RSI    [<=45|48<] lo  68.6 (N 1658)  mid  68.9  hi  72.7 (N 1542)   spread  +4.1  z=+2.56
  MACD   [<=67|74<] lo  69.2 (N 1600)  mid  69.6  hi  71.4 (N 1504)   spread  +2.2  z=+1.35
  STOCH  [<=17|68<] lo  67.5 (N 1579)  mid  71.7  hi  70.9 (N 1561)   spread  +3.4  z=+2.07
  TA     [<=51|53<] lo  68.7 (N 2161)  mid  70.4  hi  71.9 (N 1245)   spread  +3.2  z=+1.94

=== 75+ POOL — lead-class win% ROBUSTNESS across barriers (the ordering is what matters) ===
  lead       TP30/SL70  TP25/SL70  TP35/SL70  TP30/SL50 TP30/SL100  opt(gate)
  TREND (N3912)       69.2       74.2       65.6       63.3       72.5       47.3
  BB    (N  30)       76.7       80.0       76.7       70.0       80.0       43.3
  RSI   (N 122)       77.9       82.0       77.0       67.2       82.0       45.1
  MACD  (N 626)       73.3       75.7       70.4       65.3       76.2       49.2

=== 75+ POOL — pairwise HIGH-component cells (>=65) vs apex15 ===
  MACD x RSI:
    MACD+ / RSI+   N=  422    71.8
    MACD+ / RSI-   N= 2974    69.9
    MACD- / RSI+   N=   58    82.8
    MACD- / RSI-   N= 1244    69.2
  BB x MACD:
    BB+ / MACD+   N= 2230    70.4
    BB+ / MACD-   N=  641    71.3
    BB- / MACD+   N= 1166    69.6
    BB- / MACD-   N=  661    68.4
  TREND x MACD:
    TREND+ / MACD+   N= 2982    69.3
    TREND+ / MACD-   N= 1240    69.4
    TREND- / MACD+   N=  414    75.6
    TREND- / MACD-   N=   62    77.4
  BB x RSI:
    BB+ / RSI+   N=  196    71.9
    BB+ / RSI-   N= 2675    70.5
    BB- / RSI+   N=  284    73.9
    BB- / RSI-   N= 1543    68.3
  STOCH x MACD:
    STOCH+ / MACD+   N=  983    70.3
    STOCH+ / MACD-   N=  711    71.6
    STOCH- / MACD+   N= 2413    70.0
    STOCH- / MACD-   N=  591    67.7
  TREND x RSI:
    TREND+ / RSI+   N=  120    63.3
    TREND+ / RSI-   N= 4102    69.6
    TREND- / RSI+   N=  360    76.4
    TREND- / RSI-   N=  116    74.1

=== 75+ POOL — volume signal vs apex15 ===
  ABSORPTION   N=  201    72.6   z=+0.79
  CONVICTION   N= 3003    69.9   z=-0.09
  REJECTION    N= 1050    69.0   z=-0.69
  THIN_AIR     N=  435    71.7   z=+0.74

=== 75+ POOL — momentum/vol confound (lead-class within c_trend tercile) ===
  c_trend low (>89): N= 2407   70.3
      lead=MACD   N=  619   73.3
      lead=RSI    N=  122   77.9
      lead=BB     N=   30   76.7
      lead=TREND  N= 1628   68.5
  c_trend HIGH (>89): N= 2291   69.8
      lead=TREND  N= 2284   69.7

=== BAND 75-79 — lead-component (max weighted contribution) vs apex15 ===
  pool baseline apex15 =  69.8  (N=3711)
  lead=RSI    N=   92    80.4   z=+2.20   share= 2.5%
  lead=MACD   N=  493    74.0   z=+1.93   share=13.3%
  lead=BB     N=   22    72.7   z=+0.30   share= 0.6%
  lead=STOCH  N=    7    71.4   z=         share= 0.2%
  lead=TREND  N= 3097    68.8   z=-0.90   share=83.5%
  lead=TA     N=    0     n/a   z=         share= 0.0%

=== BAND 75-79 — per-RAW-component tercile vs apex15 (within band) ===
  baseline apex15 =  69.8 (N=3711). Spread = HIGH-tercile minus LOW-tercile WR.
  TREND  [<=82|94<] lo  71.2 (N 1252)  mid  69.0  hi  69.3 (N 1150)   spread  -1.9  z=-1.00
  BB     [<=62|72<] lo  69.5 (N 1251)  mid  71.3  hi  68.5 (N 1182)   spread  -1.0  z=-0.54
  RSI    [<=45|48<] lo  68.6 (N 1326)  mid  68.5  hi  72.4 (N 1205)   spread  +3.8  z=+2.10
  MACD   [<=67|74<] lo  68.8 (N 1339)  mid  69.5  hi  71.3 (N 1134)   spread  +2.6  z=+1.38
  STOCH  [<=17|68<] lo  66.9 (N 1253)  mid  71.5  hi  71.1 (N 1226)   spread  +4.2  z=+2.29
  TA     [<=51|53<] lo  68.7 (N 1790)  mid  69.4  hi  72.4 (N  936)   spread  +3.8  z=+2.04

=== BAND 80-84 — lead-component (max weighted contribution) vs apex15 ===
  pool baseline apex15 =  68.8  (N=584)
  lead=BB     N=    6    83.3   z=         share= 1.0%
  lead=RSI    N=   23    78.3   z=+0.96   share= 3.9%
  lead=MACD   N=   88    71.6   z=+0.52   share=15.1%
  lead=TREND  N=  466    67.8   z=-0.35   share=79.8%
  lead=STOCH  N=    1     0.0   z=         share= 0.2%
  lead=TA     N=    0     n/a   z=         share= 0.0%

=== BAND 80-84 — per-RAW-component tercile vs apex15 (within band) ===
  baseline apex15 =  68.8 (N=584). Spread = HIGH-tercile minus LOW-tercile WR.
  TREND  [<=82|94<] lo  68.8 (N  202)  mid  68.0  hi  69.8 (N  182)   spread  +1.0  z=+0.21
  BB     [<=63|73<] lo  73.8 (N  195)  mid  68.9  hi  63.4 (N  183)   spread -10.5  z=-2.19
  RSI    [<=45|49<] lo  63.5 (N  197)  mid  72.3  hi  70.5 (N  156)   spread  +7.1  z=+1.40
  MACD   [<=69|75<] lo  65.3 (N  216)  mid  70.6  hi  71.3 (N  188)   spread  +6.0  z=+1.29
  STOCH  [<=14|68<] lo  67.2 (N  198)  mid  69.9  hi  69.5 (N  190)   spread  +2.3  z=+0.49
  TA     [<=51|53<] lo  68.2 (N  236)  mid  69.0  hi  69.5 (N  174)   spread  +1.3  z=+0.29

=== BAND 85+ — lead-component (max weighted contribution) vs apex15 ===
  pool baseline apex15 =  73.7  (N=403)
  lead=BB     N=    2   100.0   z=         share= 0.5%
  lead=TREND  N=  349    74.8   z=+0.34   share=86.6%
  lead=MACD   N=   45    68.9   z=-0.69   share=11.2%
  lead=RSI    N=    7    42.9   z=         share= 1.7%
  lead=STOCH  N=    0     n/a   z=         share= 0.0%
  lead=TA     N=    0     n/a   z=         share= 0.0%

=== BAND 85+ — per-RAW-component tercile vs apex15 (within band) ===
  baseline apex15 =  73.7 (N=403). Spread = HIGH-tercile minus LOW-tercile WR.
  TREND  [<=84|95<] lo  72.6 (N  135)  mid  73.5  hi  75.2 (N  117)   spread  +2.6  z=+0.47
  BB     [<=66|73<] lo  71.1 (N  152)  mid  76.3  hi  74.4 (N  133)   spread  +3.4  z=+0.64
  RSI    [<=45|48<] lo  75.6 (N  135)  mid  69.4  hi  76.9 (N  121)   spread  +1.3  z=+0.24
  MACD   [<=70|77<] lo  74.5 (N  137)  mid  73.8  hi  72.8 (N  125)   spread  -1.7  z=-0.30
  STOCH  [<=30|70<] lo  75.6 (N  135)  mid  76.9  hi  68.7 (N  134)   spread  -6.9  z=-1.26
  TA     [<=51|54<] lo  70.4 (N  135)  mid  77.8  hi  70.7 (N   92)   spread  +0.3  z=+0.05


##### MARGINAL / REWEIGHT-BOUNDARY INTEGRITY #####
Reweighting moves 70-74 winners UP into 75+ and 75-79 losers DOWN out of it.
The reweight thesis holds ONLY if winning lead-classes win at BOTH bands.

=== PROMOTION RESERVOIR 70-74 — lead-component (max weighted contribution) vs apex15 ===
  pool baseline apex15 =  68.1  (N=29219)
  lead=RSI    N=  400    74.0   z=+2.51   share= 1.4%
  lead=STOCH  N=   43    72.1   z=+0.56   share= 0.1%
  lead=MACD   N= 3302    68.6   z=+0.57   share=11.3%
  lead=TREND  N=25329    68.0   z=-0.37   share=86.7%
  lead=BB     N=  145    65.5   z=-0.67   share= 0.5%
  lead=TA     N=    0     n/a   z=         share= 0.0%

=== PROMOTION RESERVOIR 70-74 — per-RAW-component tercile vs apex15 (within band) ===
  baseline apex15 =  68.1 (N=29219). Spread = HIGH-tercile minus LOW-tercile WR.
  TREND  [<=84|95<] lo  68.9 (N 9977)  mid  68.2  hi  67.1 (N 8947)   spread  -1.9  z=-2.75
  BB     [<=59|71<] lo  65.8 (N 9934)  mid  69.4  hi  69.3 (N 9610)   spread  +3.5  z=+5.19
  RSI    [<=45|48<] lo  67.7 (N10542)  mid  68.6  hi  68.0 (N 8659)   spread  +0.4  z=+0.52
  MACD   [<=65|73<] lo  67.6 (N10084)  mid  68.2  hi  68.6 (N 9468)   spread  +1.0  z=+1.54
  STOCH  [<= 7|58<] lo  67.5 (N 9749)  mid  68.7  hi  68.1 (N 9638)   spread  +0.6  z=+0.93
  TA     [<=50|52<] lo  66.3 (N11593)  mid  69.2  hi  69.3 (N 9476)   spread  +3.0  z=+4.59

=== DEMOTION-PRONE 75-79 — lead-component (max weighted contribution) vs apex15 ===
  pool baseline apex15 =  69.8  (N=3711)
  lead=RSI    N=   92    80.4   z=+2.20   share= 2.5%
  lead=MACD   N=  493    74.0   z=+1.93   share=13.3%
  lead=BB     N=   22    72.7   z=+0.30   share= 0.6%
  lead=STOCH  N=    7    71.4   z=         share= 0.2%
  lead=TREND  N= 3097    68.8   z=-0.90   share=83.5%
  lead=TA     N=    0     n/a   z=         share= 0.0%

=== DEMOTION-PRONE 75-79 — per-RAW-component tercile vs apex15 (within band) ===
  baseline apex15 =  69.8 (N=3711). Spread = HIGH-tercile minus LOW-tercile WR.
  TREND  [<=82|94<] lo  71.2 (N 1252)  mid  69.0  hi  69.3 (N 1150)   spread  -1.9  z=-1.00
  BB     [<=62|72<] lo  69.5 (N 1251)  mid  71.3  hi  68.5 (N 1182)   spread  -1.0  z=-0.54
  RSI    [<=45|48<] lo  68.6 (N 1326)  mid  68.5  hi  72.4 (N 1205)   spread  +3.8  z=+2.10
  MACD   [<=67|74<] lo  68.8 (N 1339)  mid  69.5  hi  71.3 (N 1134)   spread  +2.6  z=+1.38
  STOCH  [<=17|68<] lo  66.9 (N 1253)  mid  71.5  hi  71.1 (N 1226)   spread  +4.2  z=+2.29
  TA     [<=51|53<] lo  68.7 (N 1790)  mid  69.4  hi  72.4 (N  936)   spread  +3.8  z=+2.04