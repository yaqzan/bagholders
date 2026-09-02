"""CI guard for the portfolio param manifest (the schema-driven backtest surface).

Forces every field on strategy_config.DteStrategyConfig + OptionStrategyConfig to
be CLASSIFIED: either a surfaced PARAM (editable in the Backtest UI + overridable
via /api/backtest/run) or an explicit EXCLUDED entry with a reason. A new
portfolio knob that is neither will FAIL this test — which is the whole point:
portfolio-stage changes propagate site-wide instead of silently drifting.

Run: python tests/test_portfolio_param_manifest.py   (also collected by pytest)
"""
import dataclasses
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import strategy_config as sc  # noqa: E402
import portfolio_param_manifest as m  # noqa: E402


def _config_fields() -> set[str]:
    dte = {f.name for f in dataclasses.fields(sc.DteStrategyConfig) if f.name != "option"}
    opt = {f.name for f in dataclasses.fields(sc.OptionStrategyConfig)}
    return dte | opt


def test_every_field_classified():
    fields = _config_fields()
    surfaced = m.surfaced_config_fields()
    excluded = set(m.EXCLUDED)
    unclassified = sorted(fields - surfaced - excluded)
    assert not unclassified, (
        "Portfolio config fields neither surfaced in PARAMS nor in EXCLUDED:\n  "
        + "\n  ".join(unclassified)
        + "\n\nFix: add a Param to portfolio_param_manifest.PARAMS to expose it in the "
        "Backtest UI, OR add it to EXCLUDED with a one-line reason if it is structural."
    )


def test_no_stale_exclusions():
    fields = _config_fields()
    stale = sorted(set(m.EXCLUDED) - fields)
    assert not stale, f"EXCLUDED references fields that no longer exist: {stale}"


def test_exclusions_have_reasons():
    blank = sorted(k for k, v in m.EXCLUDED.items() if not (v or "").strip())
    assert not blank, f"EXCLUDED entries with empty reason: {blank}"


def test_param_sources_exist():
    """Every surfaced Param must draw its default from a real config field."""
    fields = _config_fields()
    bad = []
    for p in m.PARAMS:
        f = m._src_field(p)
        if f is not None and f not in fields:
            bad.append(f"{p.key} -> {p.src} (field {f!r} missing)")
    assert not bad, "Param.src points at non-existent config fields:\n  " + "\n  ".join(bad)


def test_param_keys_unique():
    keys = [p.key for p in m.PARAMS]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    assert not dupes, f"Duplicate Param.key values: {dupes}"


def test_tier_members_covered():
    """Tier params must cover every TIER_ALLOC / PUT_TIER_ALLOC member (by src),
    and their cfg display-keys must match the engine's tier_alloc dict exactly."""
    call_src = {p.src.split(".", 1)[1] for p in m.PARAMS if p.src.startswith("tier.")}
    put_src = {p.src.split(".", 1)[1] for p in m.PARAMS if p.src.startswith("puttier.")}
    assert set(sc.STRATEGY_30DTE.TIER_ALLOC) == call_src, (
        f"call-tier src coverage: config={set(sc.STRATEGY_30DTE.TIER_ALLOC)} manifest={call_src}")
    assert set(sc.STRATEGY_30DTE.PUT_TIER_ALLOC) == put_src, (
        f"put-tier src coverage: config={set(sc.STRATEGY_30DTE.PUT_TIER_ALLOC)} manifest={put_src}")
    import backtest_cascade as bc  # the engine's tier_alloc display keys
    disp = {p.transform.split(":", 1)[1] for p in m.PARAMS if p.transform.startswith("tier:")}
    assert set(bc.TIER_ALLOC) == disp, (
        f"tier display-key mismatch: engine={set(bc.TIER_ALLOC)} manifest={disp}")


def test_engine_cfg_keys_in_sync():
    """ENGINE_CFG_KEYS must equal the cfg.get keys run_backtest actually reads
    (minus internal/derived). Keeps the 'editable' flag honest: wiring a new
    param into run_backtest (adding its cfg.get) fails this until ENGINE_CFG_KEYS
    is updated — which flips that param to editable in the UI + applier."""
    import re
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest_cascade.py")
    read = set(re.findall(r"cfg\.get\('([a-z_]+)'", open(path, encoding="utf-8").read()))
    knobs = read - m._NON_KNOB_CFG_KEYS
    extra = sorted(knobs - set(m.ENGINE_CFG_KEYS))
    missing = sorted(set(m.ENGINE_CFG_KEYS) - knobs)
    assert not extra and not missing, (
        "ENGINE_CFG_KEYS drifted from backtest_cascade.run_backtest:\n"
        f"  read-by-engine but not in ENGINE_CFG_KEYS (wire to editable): {extra}\n"
        f"  in ENGINE_CFG_KEYS but engine no longer reads: {missing}")


def test_engine_global_keys_in_sync():
    """ENGINE_GLOBAL_KEYS must equal backtest_cascade._CFG_KNOB_GLOBALS keys, so the
    manifest's 'editable' flag matches what the per-request module-global override
    actually applies. Adding a knob to the engine map without updating the manifest
    (or vice-versa) fails here."""
    import backtest_cascade as bc
    eng = set(bc._CFG_KNOB_GLOBALS)
    extra = sorted(eng - set(m.ENGINE_GLOBAL_KEYS))
    missing = sorted(set(m.ENGINE_GLOBAL_KEYS) - eng)
    assert not extra and not missing, (
        "ENGINE_GLOBAL_KEYS drifted from backtest_cascade._CFG_KNOB_GLOBALS:\n"
        f"  engine map only: {extra}\n  manifest only: {missing}")


def test_editable_targets_are_applied():
    """Every editable Param's cfg target is applied somewhere: read by run_backtest
    (ENGINE_CFG_KEYS), set via the module-global override (ENGINE_GLOBAL_KEYS), or
    a slippage knob (ENDPOINT_SLIP_KEYS). Catches a typo'd editable knob."""
    applied = set(m.ENGINE_CFG_KEYS) | set(m.ENGINE_GLOBAL_KEYS) | set(m.ENDPOINT_SLIP_KEYS)
    for p in m.PARAMS:
        if m.is_editable(p):
            for k in m.cfg_targets(p):
                assert k in applied, f"{p.key}: editable but cfg target {k!r} is not applied anywhere"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {fn.__name__}\n        {e}")
    n_fields = len(_config_fields())
    print(f"\n{len(m.PARAMS)} surfaced params, {len(m.EXCLUDED)} excluded, "
          f"{n_fields} config fields total.")
    sys.exit(1 if failed else 0)
