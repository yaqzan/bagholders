"""Contract test for the intraday volume TYPE-stability gate (2026-06-03).

The gate holds a NEUTRAL volume TYPE while intraday confidence is low, killing the
early-session CONVICTION<->THIN_AIR classification flip (the ATI/AMSC ~18pt fakeout).

INVARIANT (why this needs no version bump): every stored historical score, every
assessment WR, and every backtest runs `_analyze_from_cache` with is_historical=True,
which sets confidence=1.0. At confidence=1.0 the gate is dead -> classification is
bit-identical to the pre-gate behavior. The gate only smooths the live partial-day
(confidence<1.0) `trader update` evolution.

Run: python tests/test_volume_intraday_stability.py   (exit 0 = pass)
"""
import os
import sys

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import volume_amplifier as va

# representative candle inputs per volume TYPE (r50 picks the cliff, body/wick the shape)
CONV = dict(ratio_50=2.0, body_ratio=0.7, upper_wick=0.05, lower_wick=0.05, body=1.0,
            candle_direction=1, recent_closes=[10, 11], today_open=10.0, prev_close=10.0)
THIN = dict(ratio_50=0.5, body_ratio=0.7, upper_wick=0.05, lower_wick=0.05, body=1.0,
            candle_direction=1, recent_closes=[10, 11], today_open=10.0, prev_close=10.0)
CLIMAX = dict(ratio_50=5.0, body_ratio=0.7, upper_wick=0.05, lower_wick=0.05, body=1.0,
              candle_direction=1, recent_closes=[10, 11], today_open=10.0, prev_close=10.0)
REJECT = dict(ratio_50=2.0, body_ratio=0.2, upper_wick=2.0, lower_wick=0.1, body=0.3,
              candle_direction=1, recent_closes=[10, 11], today_open=10.0, prev_close=10.0)


def _ok(cond, msg):
    if not cond:
        print("FAIL:", msg)
        raise SystemExit(1)


def main():
    gate = va.INTRADAY_TYPE_CONF_GATE
    _ok(0.0 < gate <= 1.0, f"gate in (0,1]; got {gate}")

    # 1) EOD / historical / default-caller (confidence=1.0): classification UNCHANGED.
    _ok(va._classify_signal(**CONV, confidence=1.0)[0] == "CONVICTION", "EOD CONVICTION unchanged")
    _ok(va._classify_signal(**THIN, confidence=1.0)[0] == "THIN_AIR", "EOD THIN_AIR unchanged")
    _ok(va._classify_signal(**CLIMAX, confidence=1.0)[0] == "CLIMAX", "EOD CLIMAX unchanged")
    _ok(va._classify_signal(**REJECT, confidence=1.0)[0] == "REJECTION", "EOD REJECTION unchanged")
    # default arg (callers that don't pass confidence) must behave as 1.0
    _ok(va._classify_signal(**CONV) == va._classify_signal(**CONV, confidence=1.0), "default==1.0")

    # 2) ABOVE the gate, output is INVARIANT to confidence (this is what EOD relies on).
    for c in (gate, 0.6, 0.9, 1.0, 2.0):
        _ok(va._classify_signal(**CONV, confidence=c)[0] == "CONVICTION",
            f"CONVICTION invariant at conf={c} (>=gate)")
        _ok(va._classify_signal(**THIN, confidence=c)[0] == "THIN_AIR",
            f"THIN_AIR invariant at conf={c} (>=gate)")

    # 3) BELOW the gate (early session), every volume TYPE collapses to NEUTRAL (fakeout suppressed).
    lo = max(0.0, gate - 0.2)
    for nm, kw in (("CONVICTION", CONV), ("THIN_AIR", THIN), ("CLIMAX", CLIMAX), ("REJECTION", REJECT)):
        _ok(va._classify_signal(**kw, confidence=lo) == ("NEUTRAL", 0),
            f"{nm} gated to NEUTRAL at conf={lo} (<gate)")

    # 3b) BANKED-VOLUME LOCK: a genuine early spike (observed already >= elevated cliff)
    #     confirms the TYPE *now* even at low time-confidence — the early-spike miss fix.
    elev = va.ELEVATED_RATIO_THRESHOLD
    avg = 1_000_000.0
    spike_vol = elev * avg          # observed alone already at the elevated cliff (banked)
    quiet_vol = 0.2 * avg           # normal early pace -> projection-driven, must stay gated
    _ok(va._effective_type_confidence(spike_vol, avg, 0.1) == 1.0,
        "banked spike (observed>=elevated) -> conf 1.0 regardless of clock")
    _ok(va._effective_type_confidence(quiet_vol, avg, 0.1) == 0.1,
        "low banked volume -> falls back to time confidence (fakeout stays gated)")
    _ok(va._effective_type_confidence(1.6 * avg, avg, 0.0) == 1.0, "above cliff at conf 0 -> 1.0")
    _ok(va._effective_type_confidence(spike_vol, 0.0, 0.3) == 0.3, "avg_50=0 guard -> time conf")
    # end-to-end: the banked spike's eff_conf un-gates CONVICTION that the raw time gate would hide
    eff = va._effective_type_confidence(spike_vol, avg, 0.1)   # 1.0
    _ok(va._classify_signal(**CONV, confidence=eff)[0] == "CONVICTION",
        "banked spike -> CONVICTION fires (not NEUTRAL) despite time_conf=0.1")
    _ok(va._classify_signal(**CONV, confidence=0.1)[0] == "NEUTRAL",
        "same candle WITHOUT the banked lock would have been gated to NEUTRAL")

    # 4) env disable -> reverts to ungated TYPE even at low confidence (ops escape hatch).
    import importlib
    os.environ["INTRADAY_TYPE_CONF_GATE"] = "0"
    importlib.reload(va)
    _ok(va._classify_signal(**CONV, confidence=0.1)[0] == "CONVICTION",
        "INTRADAY_TYPE_CONF_GATE=0 disables the gate")
    os.environ.pop("INTRADAY_TYPE_CONF_GATE", None)
    importlib.reload(va)

    print("PASS: intraday TYPE gate is bit-identical at confidence>=gate (EOD/backtest invariant) "
          "and suppresses the CONVICTION/THIN_AIR fakeout below the gate.")


if __name__ == "__main__":
    main()
