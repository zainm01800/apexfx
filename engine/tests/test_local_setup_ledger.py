"""Local setup ledger + the record-then-dispatch invariant.

`open_new_trade()` used to gate order dispatch on a successful Supabase INSERT, so a database
outage silently dropped every signal (twelve in one scan during the 402 quota block) while
IBKR sat connected and idle. The invariant that actually matters is **never place an order you
cannot account for** — which a durable local record satisfies just as well as a cloud one.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from apex_quant.data import local_setup_ledger as L

ENGINE_DIR = Path(__file__).resolve().parent.parent
LIVE = ENGINE_DIR / "scripts" / "run_live_paper_trading.py"


def _payload(sid="AAPL_1700000000"):
    return {"id": sid, "symbol": "AAPL", "outcome": "pending", "stop_loss": 1.0}


def test_record_and_read_roundtrip(tmp_path):
    p = tmp_path / "s.jsonl"
    assert L.record_setup(_payload(), p, "quota block") is True
    rows = L.read_setups(p)
    assert len(rows) == 1
    assert rows[0]["setup"]["id"] == "AAPL_1700000000"
    assert rows[0]["reason"] == "quota block"
    assert rows[0]["source"] == "local_fallback"


def test_appends_rather_than_overwrites(tmp_path):
    p = tmp_path / "s.jsonl"
    L.record_setup(_payload("A_1"), p)
    L.record_setup(_payload("B_2"), p)
    L.record_setup(_payload("C_3"), p)
    assert L.pending_ids(p) == ["A_1", "B_2", "C_3"]


def test_corrupt_tail_does_not_lose_earlier_records(tmp_path):
    """A crash mid-write must cost at most the last line — the reason for JSONL."""
    p = tmp_path / "s.jsonl"
    L.record_setup(_payload("A_1"), p)
    L.record_setup(_payload("B_2"), p)
    with open(p, "a") as fh:
        fh.write('{"recorded_at": "2026-07-23T00:00:00+00:00", "setu')
    assert L.pending_ids(p) == ["A_1", "B_2"]


def test_missing_file_reads_empty_not_error(tmp_path):
    assert L.read_setups(tmp_path / "nope.jsonl") == []
    assert L.pending_ids(tmp_path / "nope.jsonl") == []


def test_unwritable_path_returns_false_so_dispatch_is_refused(tmp_path):
    """The return value gates order dispatch — it must never be optimistic."""
    blocked = tmp_path / "afile"
    blocked.write_text("not a directory")
    assert L.record_setup(_payload(), blocked / "sub" / "s.jsonl") is False


def test_payload_is_stored_verbatim(tmp_path):
    """The local row must carry everything the cloud row would, for later reconciliation."""
    p = tmp_path / "s.jsonl"
    full = {"id": "X_1", "symbol": "X", "stop_loss": 1.5, "target_price": 2.5,
            "setup_features": {"auto": True, "style": "swing"}, "outcome": "pending"}
    L.record_setup(full, p)
    assert L.read_setups(p)[0]["setup"] == full


# ---- live-loop wiring: record-then-dispatch --------------------------------------

def _src() -> str:
    return LIVE.read_text(encoding="utf-8")


def test_dispatch_is_gated_on_recorded_not_on_the_http_response():
    src = _src()
    assert "if recorded:" in src, "dispatch must be gated on the record, not the HTTP status"
    assert "if r.status_code in (200, 201, 204):\n            print(f\"  [triggered]" not in src, (
        "the old cloud-only gate must be gone"
    )


def test_local_ledger_is_used_when_supabase_is_blocked_or_failing():
    src = _src()
    block = src[src.index("def open_new_trade("):]
    block = block[: block.index("\ndef ")]
    assert "supabase_guard.is_blocked()" in block, "must skip the request while blocked"
    assert block.count("local_setup_ledger.record_setup") >= 3, (
        "need the fallback on the blocked, non-2xx and exception paths"
    )


def test_a_failed_cloud_write_still_trips_the_breaker():
    src = _src()
    block = src[src.index("def open_new_trade("):]
    block = block[: block.index("\ndef ")]
    assert "supabase_guard.note_response" in block


def test_abort_path_does_not_reference_the_unbound_response():
    """On the breaker path no request is made, so `r` is unbound — touching it would raise."""
    src = _src()
    block = src[src.index("def open_new_trade("):]
    block = block[: block.index("\ndef ")]
    tail = block[block.index("[ABORT] No durable record"):]
    assert "r.status_code" not in tail and "r.text" not in tail
