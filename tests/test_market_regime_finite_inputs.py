import math

import pandas as pd
import pytest

import market_regime


def test_extract_spy_metrics_skips_trailing_nan_close():
    closes = [float(i) for i in range(1, 206)]
    closes.append(float("nan"))
    spy_df = pd.DataFrame({"Close": closes})

    spy_close, spy_ema50, spy_ema200 = market_regime._extract_spy_metrics(spy_df)

    assert spy_close == 205.0
    assert math.isfinite(spy_ema50)
    assert math.isfinite(spy_ema200)


def test_regime_numeric_assignment_converts_non_finite_values_to_none():
    class Regime:
        pass

    regime = Regime()

    market_regime._set_regime_numeric_fields(
        regime,
        {
            "spy_close": float("nan"),
            "spy_ema50": float("inf"),
            "regime_multiplier": 1.0283,
        },
    )

    assert regime.spy_close is None
    assert regime.spy_ema50 is None
    assert regime.regime_multiplier == pytest.approx(1.0283)
