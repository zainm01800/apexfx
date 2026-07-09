"""Tests for the MT4 + Mock executors.

Verifies:
- Payload structure matches the MQ4 bridge parser's expectations.
- Atomic write pattern (MT4Executor writes to .tmp then renames).
- MockExecutor returns the same payload shape.
- Path resolution fallback chain.
- Edge cases: missing directory creation, volume defaults, concurrency safety.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

import pytest

from apex_quant.execution.mt4_executor import (
    MT4Executor,
    SIGNAL_FILENAME,
    _resolve_common_dir,
)
from apex_quant.execution.mock_executor import MockExecutor


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
        """A valid order creates the signal file in the target directory."""
        ex = MT4Executor(common_dir=tmp_path, default_volume=0.10)
        result = ex.submit_order(symbol="EURUSD", cmd="buy", volume=0.5)

        assert result == tmp_path / SIGNAL_FILENAME
        assert result.exists()

        # Validate payload structure.
        with open(result, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["symbol"] == "EURUSD"
        assert data["cmd"] == "buy"
        assert data["volume"] == 0.5
        assert data["sl"] == 0.0
        assert data["tp"] == 0.0

    def test_atomic_write_no_partial_file(self, tmp_path: Path):
        """The .tmp file is removed after successful rename."""
        ex = MT4Executor(common_dir=tmp_path, default_volume=0.10)
        ex.submit_order(symbol="EURUSD", cmd="buy")
        tmp_file = tmp_path / (SIGNAL_FILENAME + ".tmp")
        assert not tmp_file.exists()

    def test_atomic_write_rename_failure_cleansup(self, tmp_path: Path, monkeypatch):
        """If rename fails, the .tmp file is cleaned up."""
        ex = MT4Executor(common_dir=tmp_path, default_volume=0.10)

        def _broken_rename(src, dst):
            raise OSError("Permission denied")

        monkeypatch.setattr(Path, "rename", _broken_rename)

        with pytest.raises(OSError):
            ex.submit_order(symbol="EURUSD", cmd="buy")

        tmp_file = tmp_path / (SIGNAL_FILENAME + ".tmp")
        assert not tmp_file.exists(), ".tmp file should be cleaned up on failure"

    def test_volume_default_fallback(self, tmp_path: Path):
        """When submit_order receives volume=None, default_volume is used."""
        ex = MT4Executor(common_dir=tmp_path, default_volume=0.25)
        ex.submit_order(symbol="GBPUSD", cmd="sell", volume=None)

        signal_file = tmp_path / SIGNAL_FILENAME
        with open(signal_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["volume"] == 0.25

    def test_volume_zero_falls_back(self, tmp_path: Path):
        """volume=0 should fall back to default."""
        ex = MT4Executor(common_dir=tmp_path, default_volume=0.10)
        ex.submit_order(symbol="USDJPY", cmd="buy", volume=0.0)

        signal_file = tmp_path / SIGNAL_FILENAME
        with open(signal_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["volume"] == 0.10

    def test_sl_tp_included(self, tmp_path: Path):
        """SL and TP are preserved in the written payload."""
        ex = MT4Executor(common_dir=tmp_path, default_volume=0.10)
        ex.submit_order(symbol="BTCUSD", cmd="buy", volume=0.01, sl=45000.0, tp=48000.0)

        signal_file = tmp_path / SIGNAL_FILENAME
        with open(signal_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["sl"] == 45000.0
        assert data["tp"] == 48000.0

    def test_directory_created_automatically(self, tmp_path: Path):
        """A nested target directory is created if it doesn't exist."""
        nested = tmp_path / "sub" / "dir"
        ex = MT4Executor(common_dir=nested, default_volume=0.10)
        ex.submit_order(symbol="EURUSD", cmd="buy")

        signal_file = nested / SIGNAL_FILENAME
        assert signal_file.exists()

    def test_concurrent_writes(self, tmp_path: Path):
        """Multiple threads can write without corrupting the signal file."""
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
        # The final file should be valid JSON.
        signal_file = tmp_path / SIGNAL_FILENAME
        with open(signal_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "symbol" in data
        assert "cmd" in data
        assert data["volume"] == 0.1

    def test_repr(self, tmp_path: Path):
        ex = MT4Executor(common_dir=tmp_path, default_volume=0.5)
        r = repr(ex)
        assert "MT4Executor" in r
        assert "signal_path=" in r
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
        assert ex.signal_path == tmp_path.resolve() / SIGNAL_FILENAME