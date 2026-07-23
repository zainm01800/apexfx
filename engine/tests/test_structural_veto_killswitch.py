"""execution.llm_structural_veto must actually gate the structural veto.

The switch was declared with an explicit contract in config.py:

    "LLM structural veto kill-switch. Default OFF — the research verdict was DROP (lessons
     invent thresholds from n=1 and can flatten any signal). The veto function stays intact
     but only runs when this is explicitly switched on."

Nothing checked it. On 2026-07-23, with the flag set to false, the veto ran anyway and blocked
DOGE/USD, AVAX/USD and XRP/USD on an is_volatility_spike flag.

It matters beyond the flag being ignored: no gate script or backtester applies this veto, so
every trade it blocks is one the certified £587/month result assumes was taken. A live-only
filter silently redefines the strategy.
"""
from __future__ import annotations

from pathlib import Path

from apex_quant.config import get_config

ENGINE_DIR = Path(__file__).resolve().parent.parent
LIVE = ENGINE_DIR / "scripts" / "run_live_paper_trading.py"


def _veto_fn_source() -> str:
    src = LIVE.read_text(encoding="utf-8")
    start = src.index("def apply_deepseek_structural_veto(")
    return src[start: src.index("\ndef ", start + 1)]


def test_default_is_off():
    assert get_config().execution.llm_structural_veto is False


def test_veto_checks_the_killswitch_before_doing_anything():
    body = _veto_fn_source()
    assert 'getattr(cfg.execution, "llm_structural_veto", False)' in body

    gate_at = body.index("llm_structural_veto")
    llm_at = body.index("build_llm(")
    assert gate_at < llm_at, (
        "the switch must be checked BEFORE the LLM is built — otherwise a disabled veto "
        "still costs an API call on every signal"
    )


def test_disabled_path_returns_permit_not_veto():
    """Fail-ALLOW: a disabled filter must never block a trade."""
    body = _veto_fn_source()
    # anchor on the GUARD, not the first mention (which is in the explanatory comment)
    guard = 'if not bool(getattr(cfg.execution, "llm_structural_veto", False)):'
    assert guard in body, "the kill-switch guard must be a real branch, not just a comment"
    following = body[body.index(guard): body.index(guard) + 300]
    assert "return True" in following, "disabled must PERMIT the trade"
    assert "return False" not in following.split("return True")[0]


def test_the_veto_is_absent_from_the_backtest_path():
    """If it is not in the gate, it must not silently run live — that is the whole point."""
    import subprocess
    hits = subprocess.run(
        ["grep", "-rln", "apply_deepseek_structural_veto",
         str(ENGINE_DIR / "apex_quant"), str(ENGINE_DIR / "scripts")],
        capture_output=True, text=True).stdout.split()
    offenders = [h for h in hits if "run_live_paper_trading" not in h]
    assert offenders == [], (
        f"structural veto leaked into non-live code: {offenders} — the certified result "
        f"does not include it"
    )


def test_sentiment_filter_still_gates_correctly():
    """The sibling filter got this right; pin it so it stays right."""
    src = (ENGINE_DIR / "apex_quant" / "ai" / "sentiment_filter.py").read_text()
    assert "if not sent_cfg.enabled:" in src
    assert "return signal" in src
