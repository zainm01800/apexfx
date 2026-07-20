"""config.prop.yaml — the prop-firm profile must stay loadable and keep its rules.

The profile is inert (nothing loads it in production; the paper test reads
config.yaml via get_config()) but it is the pre-agreed risk contract for the
future funded account — so it must always validate and its four firm rules
must not drift silently when config.yaml evolves.
"""

from pathlib import Path

from apex_quant.config import load_config

ENGINE_DIR = Path(__file__).resolve().parent.parent


def test_prop_profile_validates_and_keeps_firm_rules():
    prop = load_config(ENGINE_DIR / "config.prop.yaml")
    assert prop.risk.max_risk_per_trade == 0.01       # 1% per trade
    assert prop.risk.max_portfolio_risk == 0.040      # aggregated open risk under the 5% firm day-cap
    assert prop.risk.drawdown_reducing_limit == 0.03  # de-risk ramp from 3% off peak
    assert prop.risk.drawdown_breaker == 0.06         # halt new positions at 6% off peak (-8% firm floor buffer)


def test_prop_profile_diverges_from_base_only_in_risk():
    base = load_config(ENGINE_DIR / "config.yaml").model_dump()
    prop = load_config(ENGINE_DIR / "config.prop.yaml").model_dump()
    base["risk"] = prop["risk"] = None
    assert base == prop, "prop profile must differ from config.yaml ONLY in the risk section"
