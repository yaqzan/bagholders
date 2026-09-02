from database.utils import scoring


def test_call_explain_confirmation_ratio_matches_raw_context_shape():
    confirmed = scoring._call_explain_confirmation_ratio(
        raw_rsi=72,
        raw_stoch=81,
        macdh_raw=0.5,
        bb_pct=1.05,
        pct_from_ema50=8.0,
    )
    weak = scoring._call_explain_confirmation_ratio(
        raw_rsi=55,
        raw_stoch=40,
        macdh_raw=-0.5,
        bb_pct=0.2,
        pct_from_ema50=-1.0,
    )

    assert confirmed == 1.0
    assert weak == 0.0


def test_call_overextension_index_blends_multi_axis_extension():
    quiet = scoring._call_overextension_index(
        raw_rsi=55,
        raw_stoch=60,
        bb_pct=0.55,
        pct_from_ema50=1.0,
        pct_from_ema200=8.0,
    )
    extended = scoring._call_overextension_index(
        raw_rsi=78,
        raw_stoch=96,
        bb_pct=1.20,
        pct_from_ema50=26.0,
        pct_from_ema200=70.0,
    )

    assert quiet == 0.0
    assert 0.0 < extended <= 2.0
    assert extended > quiet


def test_scw_relief_is_smoothly_smaller_for_confirmed_boundary_calls():
    base_args = (75, 75, 75, 75, 20, 75)
    weekly_kwargs = {
        "ws_trend": 50,
        "ws_rsi": 50,
        "ws_macd": 50,
        "prev_ws_trend": 50,
        "prev_ws_rsi": 50,
        "prev_ws_macd": 50,
    }

    _, confirmed_info, _ = scoring.compute_overall_score(
        *base_args,
        raw_rsi=72,
        raw_stoch=81,
        macdh_raw=0.5,
        bb_pct=1.05,
        pct_from_ema50=8.0,
        **weekly_kwargs,
    )
    _, weak_info, _ = scoring.compute_overall_score(
        *base_args,
        raw_rsi=55,
        raw_stoch=40,
        macdh_raw=-0.5,
        bb_pct=0.2,
        pct_from_ema50=-1.0,
        **weekly_kwargs,
    )

    assert confirmed_info["scw_dampen"] < weak_info["scw_dampen"]
    assert confirmed_info["scw_scalar"] < weak_info["scw_scalar"]
    assert confirmed_info["scw_base_dampen"] == weak_info["scw_base_dampen"]


def test_scw_overextension_taper_reduces_relief_when_enabled(monkeypatch):
    monkeypatch.setattr(scoring, "SCW_EXT_TAPER_STRENGTH", 1.0)
    monkeypatch.setattr(scoring, "SCW_EXT_TAPER_MID", 0.70)
    monkeypatch.setattr(scoring, "SCW_EXT_TAPER_WIDTH", 0.18)
    base_args = (75, 75, 75, 75, 20, 75)
    weekly_kwargs = {
        "ws_trend": 50,
        "ws_rsi": 50,
        "ws_macd": 50,
        "prev_ws_trend": 50,
        "prev_ws_rsi": 50,
        "prev_ws_macd": 50,
    }

    _, normal_info, _ = scoring.compute_overall_score(
        *base_args,
        raw_rsi=72,
        raw_stoch=81,
        macdh_raw=0.5,
        bb_pct=1.05,
        pct_from_ema50=8.0,
        pct_from_ema200=10.0,
        **weekly_kwargs,
    )
    _, extended_info, _ = scoring.compute_overall_score(
        *base_args,
        raw_rsi=72,
        raw_stoch=81,
        macdh_raw=0.5,
        bb_pct=1.05,
        pct_from_ema50=28.0,
        pct_from_ema200=70.0,
        **weekly_kwargs,
    )

    assert extended_info["scw_dampen"] > normal_info["scw_dampen"]
    assert extended_info["scw_scalar"] > normal_info["scw_scalar"]
    assert extended_info["scw_ext_idx"] > normal_info["scw_ext_idx"]
