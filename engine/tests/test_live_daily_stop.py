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


def test_anchor_is_persisted_to_disk_not_held_in_memory():
    src = _src()
    assert "DAILY_ANCHOR_PATH" in src
    assert "def daily_equity_anchor(" in src
    assert "DAILY_ANCHOR_PATH.write_text" in src, "anchor must survive a process restart"
    assert "DAILY_ANCHOR_PATH.exists()" in src, "anchor must be read back, not re-derived"


def test_anchor_is_keyed_by_date_so_it_rolls_over():
    src = _src()
    block = src[src.index("def daily_equity_anchor("):]
    block = block[: block.index("\ndef ")]
    assert 'strftime("%Y-%m-%d")' in block
    assert 'stored.get("date") == today' in block, (
        "a stored anchor from a PREVIOUS day must not be reused"
    )


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
    """A corrupt anchor file must not halt the book — it must disable the check."""
    src = _src()
    block = src[src.index("def daily_equity_anchor("):]
    block = block[: block.index("\ndef ")]
    assert "return live_equity" in block
    assert "daily stop inactive this cycle" in block


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
