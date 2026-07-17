"""Tests for the MT4 + Mock executors.

Verifies:
- Payload structure matches the MQ4 bridge parser's expectations.
- Unique per-signal files (signal_<id>.json) with embedded client order ids.
- Atomic write pattern (MT4Executor writes to .tmp then renames).
- Fail-closed behavior when the common dir is missing (no silent mkdir).
- The fills-handshake ack polling (wait_for_ack).
- MockExecutor returns the same payload shape.
- Path resolution fallback chain.
- Edge cases: volume defaults, concurrency safety.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path

import pytest

from apex_quant.execution.mt4_executor import (
    MT4Executor,
    SIGNAL_FILENAME,
    SIGNAL_GLOB,
    _resolve_common_dir,
    resolve_mt4_common_dir,
)
from apex_quant.execution.mock_executor import MockExecutor


def _signal_files(dir_path: Path) -> list[Path]:
    """All written v1.10 signal files in *dir_path*."""
    return sorted(dir_path.glob("signal_*.json"))


def _read_single_signal(dir_path: Path) -> dict:
    files = _signal_files(dir_path)
    assert len(files) == 1, f"expected exactly 1 signal file, got {files}"
    return json.loads(files[0].read_text(encoding="utf-8"))


# ===================================================================
#  Mock executor tests
# ===================================================================


class TestMockExecutor:
    def test_submit_order_returns_payload(self):
        """MockExecutor.submit_order() returns a dict with expected fields."""
        ex = MockExecutor(default_volume=0.10)
        payload = ex.submit_order(symbol="EURUSD", cmd="buy", volume=0.5)

        assert isinstance(payload, dict)
        assert payload["symbol"] == "EURUSD"
        assert payload["cmd"] == "buy"
        assert payload["volume"] == 0.5
        assert payload["sl"] == 0.0
        assert payload["tp"] == 0.0

    def test_submit_order_default_volume(self):
        """When volume is None, falls back to default_volume."""
        ex = MockExecutor(default_volume=0.25)
        payload = ex.submit_order(symbol="GBPUSD", cmd="sell")
        assert payload["volume"] == 0.25

    def test_submit_order_zero_volume_falls_back(self):
        """When volume is 0, falls back to default_volume."""
        ex = MockExecutor(default_volume=0.10)
        payload = ex.submit_order(symbol="USDJPY", cmd="buy", volume=0.0)
        assert payload["volume"] == 0.10

    def test_submit_order_sl_tp_round_trip(self):
        """SL and TP are included as floats."""
        ex = MockExecutor()
        payload = ex.submit_order(
            symbol="BTCUSD", cmd="buy", volume=0.01, sl=45000.0, tp=48000.0
        )
        assert payload["sl"] == 45000.0
        assert payload["tp"] == 48000.0

    def test_wait_for_ack_synthetic(self):
        """Mock orders 'fill' instantly — a synthetic ok ack is returned."""
        ex = MockExecutor()
        ack = ex.wait_for_ack()
        assert ack["ok"] is True

    def test_repr(self):
        ex = MockExecutor(default_volume=0.5)
        r = repr(ex)
        assert "MockExecutor" in r
        assert "0.50" in r


# ===================================================================
#  MT4 executor tests
# ===================================================================


class TestMT4Executor:
    def test_submit_order_writes_file(self, tmp_path: Path):
        """A valid order creates a unique signal file in the target directory."""
        ex = MT4Executor(common_dir=tmp_path, default_volume=0.10)
        result = ex.submit_order(symbol="EURUSD", cmd="buy", volume=0.5)

        assert result.parent == tmp_path
        assert result.name.startswith("signal_") and result.suffix == ".json"
        assert result.exists()

        # Validate payload structure.
        data = _read_single_signal(tmp_path)
        assert data["symbol"] == "EURUSD"
        assert data["cmd"] == "buy"
        assert data["volume"] == 0.5
        assert data["sl"] == 0.0
        assert data["tp"] == 0.0
        # Fills handshake: the client order id is embedded and matches the filename.
        assert data["id"]
        assert result.name == f"signal_{data['id']}.json"

    def test_unique_file_per_signal(self, tmp_path: Path):
        """Two writes inside one EA poll window must BOTH survive (audit L2)."""
        ex = MT4Executor(common_dir=tmp_path, default_volume=0.10)
        ex.submit_order(symbol="EURUSD", cmd="buy", volume=0.1)
        ex.submit_order(symbol="GBPUSD", cmd="sell", volume=0.2)

        files = _signal_files(tmp_path)
        assert len(files) == 2
        ids = {json.loads(f.read_text())["id"] for f in files}
        assert len(ids) == 2  # distinct client order ids

    def test_atomic_write_no_partial_file(self, tmp_path: Path):
        """No .tmp file remains after a successful rename."""
        ex = MT4Executor(common_dir=tmp_path, default_volume=0.10)
        ex.submit_order(symbol="EURUSD", cmd="buy")
        assert not list(tmp_path.glob("*.tmp"))

    def test_atomic_write_rename_failure_cleansup(self, tmp_path: Path, monkeypatch):
        """If rename fails, the .tmp file is cleaned up."""
        ex = MT4Executor(common_dir=tmp_path, default_volume=0.10)

        def _broken_rename(src, dst):
            raise OSError("Permission denied")

        monkeypatch.setattr(Path, "rename", _broken_rename)

        with pytest.raises(OSError):
            ex.submit_order(symbol="EURUSD", cmd="buy")

        assert not list(tmp_path.glob("*.tmp")), ".tmp file should be cleaned up on failure"

    def test_volume_default_fallback(self, tmp_path: Path):
        """When submit_order receives volume=None, default_volume is used."""
        ex = MT4Executor(common_dir=tmp_path, default_volume=0.25)
        ex.submit_order(symbol="GBPUSD", cmd="sell", volume=None)

        data = _read_single_signal(tmp_path)
        assert data["volume"] == 0.25

    def test_volume_zero_falls_back(self, tmp_path: Path):
        """volume=0 should fall back to default."""
        ex = MT4Executor(common_dir=tmp_path, default_volume=0.10)
        ex.submit_order(symbol="USDJPY", cmd="buy", volume=0.0)

        data = _read_single_signal(tmp_path)
        assert data["volume"] == 0.10

    def test_sl_tp_included(self, tmp_path: Path):
        """SL and TP are preserved in the written payload."""
        ex = MT4Executor(common_dir=tmp_path, default_volume=0.10)
        ex.submit_order(symbol="BTCUSD", cmd="buy", volume=0.01, sl=45000.0, tp=48000.0)

        data = _read_single_signal(tmp_path)
        assert data["sl"] == 45000.0
        assert data["tp"] == 48000.0

    def test_missing_directory_fails_closed(self, tmp_path: Path):
        """A missing common dir is a loud error, never a silent mkdir (L10)."""
        missing = tmp_path / "does" / "not" / "exist"
        with pytest.raises(FileNotFoundError):
            MT4Executor(common_dir=missing, default_volume=0.10)
        assert not missing.exists()

    def test_close_with_ticket_is_ticket_scoped(self, tmp_path: Path):
        """close_position(ticket=...) embeds the ticket for the EA (audit L3)."""
        ex = MT4Executor(common_dir=tmp_path, default_volume=0.10)
        ex.close_position(symbol="EURUSD", ticket=424242)

        data = _read_single_signal(tmp_path)
        assert data["cmd"] == "close"
        assert data["ticket"] == 424242

    def test_close_without_ticket_legacy(self, tmp_path: Path):
        """close_position() without a ticket keeps legacy symbol-scoped semantics."""
        ex = MT4Executor(common_dir=tmp_path, default_volume=0.10)
        ex.close_position(symbol="EURUSD")

        data = _read_single_signal(tmp_path)
        assert data["cmd"] == "close"
        assert "ticket" not in data

    def test_partial_close_and_modify_sl_payloads(self, tmp_path: Path):
        """TMS commands carry the ticket field."""
        ex = MT4Executor(common_dir=tmp_path, default_volume=0.10)
        ex.partial_close(symbol="EURUSD", ticket=111, volume=0.05)
        ex.modify_sl(symbol="EURUSD", ticket=111, new_sl=1.0825)

        files = _signal_files(tmp_path)
        assert len(files) == 2
        payloads = [json.loads(f.read_text()) for f in files]
        pc = next(p for p in payloads if p["cmd"] == "partial_close")
        ms = next(p for p in payloads if p["cmd"] == "modify_sl")
        assert pc["ticket"] == 111 and pc["volume"] == 0.05
        assert ms["ticket"] == 111 and ms["new_sl"] == 1.0825

    def test_wait_for_ack_success(self, tmp_path: Path):
        """wait_for_ack returns the EA's fill receipt once it appears."""
        ex = MT4Executor(common_dir=tmp_path, default_volume=0.10)
        ex.submit_order(symbol="EURUSD", cmd="buy", volume=0.1)
        sid = ex._last_signal_id
        assert sid

        ack_payload = {"id": sid, "cmd": "buy", "symbol": "EURUSD",
                       "ticket": 555, "fill_price": 1.10001, "ok": True}

        def _write_ack():
            time.sleep(0.3)
            (tmp_path / f"ack_{sid}.json").write_text(json.dumps(ack_payload))

        t = threading.Thread(target=_write_ack)
        t.start()
        ack = ex.wait_for_ack(timeout_s=3.0, poll_interval_s=0.1)
        t.join()

        assert ack is not None
        assert ack["ticket"] == 555
        assert ack["ok"] is True

    def test_wait_for_ack_timeout(self, tmp_path: Path):
        """No ack within the budget returns None (caller must not mark filled)."""
        ex = MT4Executor(common_dir=tmp_path, default_volume=0.10)
        ex.submit_order(symbol="EURUSD", cmd="buy", volume=0.1)
        ack = ex.wait_for_ack(timeout_s=0.4, poll_interval_s=0.1)
        assert ack is None

    def test_concurrent_writes(self, tmp_path: Path):
        """Multiple threads write unique files without corruption or loss."""
        ex = MT4Executor(common_dir=tmp_path, default_volume=0.10)
        n_threads = 20
        errors: list[Exception] = []

        def _write(cmd: str):
            try:
                ex.submit_order(symbol="EURUSD", cmd=cmd, volume=0.1)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=_write, args=("buy" if i % 2 == 0 else "sell",))
            for i in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent writes produced errors: {errors}"
        # Every signal survives in its own file — no last-write-wins.
        files = _signal_files(tmp_path)
        assert len(files) == n_threads
        for f in files:
            data = json.loads(f.read_text(encoding="utf-8"))
            assert data["symbol"] == "EURUSD"
            assert data["cmd"] in ("buy", "sell")
            assert data["volume"] == 0.1

    def test_repr(self, tmp_path: Path):
        ex = MT4Executor(common_dir=tmp_path, default_volume=0.5)
        r = repr(ex)
        assert "MT4Executor" in r
        assert "signal_dir=" in r
        assert "0.50" in r


# ===================================================================
#  Path resolution tests
# ===================================================================


class TestPathResolution:
    def test_resolve_from_config(self, monkeypatch):
        """When MT4_COMMON_DIR env var is unset, falls back to config."""
        monkeypatch.delenv("MT4_COMMON_DIR", raising=False)

        # The config has execution.mt4.common_dir set (in config.yaml), so
        # _resolve_common_dir should return a resolved version of that path.
        result = _resolve_common_dir()
        from apex_quant.config import get_config

        cfg = get_config()
        expected = Path(cfg.execution.mt4.common_dir).resolve()
        assert result == expected

    def test_resolve_from_env_var(self, monkeypatch):
        """Environment variable MT4_COMMON_DIR takes priority."""
        monkeypatch.setenv("MT4_COMMON_DIR", "/custom/mt4/path")
        result = _resolve_common_dir()
        assert result == Path("/custom/mt4/path").resolve()

    def test_public_resolver_matches_private(self, monkeypatch):
        """The public resolver (single path for reads+writes, L11) agrees."""
        monkeypatch.setenv("MT4_COMMON_DIR", "/custom/mt4/path")
        assert resolve_mt4_common_dir() == _resolve_common_dir()

    def test_resolve_fallback_default(self, monkeypatch):
        """When both env var and config.common_dir are empty, use hard-coded default."""
        monkeypatch.delenv("MT4_COMMON_DIR", raising=False)

        # Build a minimal fake config tree where common_dir is empty.
        class FakeMt4:
            common_dir = ""

        class FakeExec:
            mt4 = FakeMt4()

        class FakeConfig:
            execution = FakeExec()

        monkeypatch.setattr(
            "apex_quant.execution.mt4_executor.get_config",
            lambda: FakeConfig(),
        )
        result = _resolve_common_dir()
        expected = Path(
            "/Applications/MetaTrader 4.app/Contents/SharedSupport/"
            "wine/drive_c/Program Files (x86)/MetaTrader 4/MQL4/Files"
        ).resolve()
        assert result == expected

    def test_executor_uses_env_var(self, monkeypatch, tmp_path: Path):
        """MT4Executor reads the env var when constructor common_dir is None."""
        monkeypatch.setenv("MT4_COMMON_DIR", str(tmp_path))
        ex = MT4Executor(default_volume=0.10)
        assert ex.signal_dir == tmp_path.resolve()
