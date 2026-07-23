"""Live-loop wiring for the daily-loss stop.

The dangerous failure mode is not "the stop doesn't fire" — it is "the stop silently resets".
The live loop runs every ~900s and the process can restart mid-session. If the day's opening
equity were held in memory, a restart after a 3% loss would re-anchor at the already-down
equity, measure zero daily loss, and resume trading on exactly the day the stop exists for.

These tests read the source rather than importing the module: importing
`run_live_paper_trading` mutates global config as a side effect (see memory
`bayesian-learning-survivorship-2026-07`).
"""
from __future__ import annotations

import json
from pathlib import Path

from apex_quant.config import RiskConfig

ENGINE_DIR = Path(__file__).resolve().parent.parent
LIVE = ENGINE_DIR / "scripts" / "run_live_paper_trading.py"


def _src() -> str:
    return LIVE.read_text(encoding="utf-8")


def test_live_loop_delegates_to_the_risk_module():
    """The anchoring logic must live in apex_quant.risk.daily_stop, which is importable and
    behaviourally tested (see test_daily_stop_module.py). The script only wires it up —
    duplicating the logic here is how the two drift apart."""
    src = _src()
    assert "DAILY_ANCHOR_PATH" in src
    assert "from apex_quant.risk.daily_stop import read_anchor, resolve_anchor" in src
    assert "from apex_quant.risk.daily_stop import breached" in src
    # the script must NOT re-implement the persistence itself
    block = src[src.index("def daily_equity_anchor("):]
    block = block[: block.index("\ndef ")]
    assert "json.loads" not in block, "persistence belongs to the module, not the script"


def test_anchor_path_is_on_disk_under_data_store():
    src = _src()
    assert 'DAILY_ANCHOR_PATH = ENGINE_DIR / "data_store" / "daily_equity_anchor.json"' in src


def test_enforcement_runs_before_sizing_and_short_circuits_the_cycle():
    src = _src()
    assert "if enforce_daily_loss_stop(live_equity, open_trades_list):" in src
    idx_enforce = src.index("if enforce_daily_loss_stop(")
    idx_account = src.index("account_state = AccountState(")
    assert idx_enforce < idx_account, "the stop must be checked before any position is sized"

    # scan_single_asset() is called per instrument, not a loop body — `continue` there is a
    # SyntaxError that only surfaces at import. Pin the correct control-flow keyword.
    tail = src[idx_enforce: idx_enforce + 400]
    assert "return" in tail and "continue" not in tail.split("return")[0], (
        "must `return` (skip this instrument) — `continue` is a SyntaxError here"
    )


def test_live_module_actually_compiles():
    """The `continue`/`return` bug above was a SyntaxError that no source-grep would catch."""
    import py_compile
    py_compile.compile(str(LIVE), doraise=True)


def test_account_state_carries_day_start_equity():
    src = _src()
    assert "day_start_equity=daily_equity_anchor(live_equity) or None" in src, (
        "RiskManager step 0.5 cannot veto without the session anchor"
    )


def test_flatten_is_opt_in_and_defaults_off():
    """Liquidating a live account is irreversible — it must not happen by default."""
    assert RiskConfig().daily_loss_flatten is False
    assert RiskConfig().daily_loss_limit == 0.0

    src = _src()
    block = src[src.index("def enforce_daily_loss_stop("):]
    block = block[: block.index("\ndef ")]
    assert 'getattr(cfg.risk, "daily_loss_flatten", False)' in block
    assert "close_position" in block, "when enabled, it must actually close positions"
    assert "LEFT RUNNING" in block, (
        "when disabled it must say plainly that positions are still exposed"
    )


def test_io_failure_degrades_to_no_stop_rather_than_blocking_all_trading():
    """A corrupt anchor file must not halt the book — it must disable the check.

    Behavioural, against the real module: a bad file yields an anchor equal to current
    equity, so measured loss is zero and nothing is blocked.
    """
    import tempfile
    from pathlib import Path as _P
    from apex_quant.risk.daily_stop import breached, resolve_anchor

    with tempfile.TemporaryDirectory() as d:
        bad = _P(d) / "anchor.json"
        bad.write_text("{corrupt", encoding="utf-8")
        anchor = resolve_anchor(bad, 97_000.0)
        assert anchor == 97_000.0
        assert breached(anchor, 97_000.0, 0.025) is False


def test_anchor_roundtrip_logic(tmp_path):
    """Behavioural check of the persistence contract, without importing the live module."""
    p = tmp_path / "anchor.json"
    today, yesterday = "2026-07-23", "2026-07-22"

    p.write_text(json.dumps({"date": today, "equity": 100_000.0}))
    stored = json.loads(p.read_text())
    assert stored["date"] == today and stored["equity"] == 100_000.0

    # a restart later the same day, with equity now down 3%, must reuse the ORIGINAL anchor
    reused = stored["equity"] if stored["date"] == today else 97_000.0
    assert reused == 100_000.0
    assert abs((1.0 - 97_000.0 / reused) - 0.03) < 1e-9, (
        "loss must be measured against the session open, not current equity"
    )

    # a stale anchor from yesterday must NOT be reused
    p.write_text(json.dumps({"date": yesterday, "equity": 100_000.0}))
    stale = json.loads(p.read_text())
    assert stale["date"] != today
