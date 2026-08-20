from __future__ import annotations

from apex_quant.storage import paper_store


def test_c_reads_namespaced_fallback_when_dedicated_tables_are_missing(monkeypatch):
    payload = {
        "daily": [{"date": "2026-08-20", "equity": 100_123.0}],
        "positions": [{"instrument": "MSFT", "direction": "long"}],
    }

    def fake_get(table, params):
        if table in (paper_store.DAILY_TABLE_C, paper_store.POSITIONS_TABLE_C):
            return None
        assert table == paper_store.FALLBACK_TABLE_C
        return [{"feature_vector": payload}]

    monkeypatch.setattr(paper_store, "_get", fake_get)
    assert paper_store.fetch_latest_daily(paper_store.DAILY_TABLE_C)["equity"] == 100_123.0
    assert paper_store.fetch_daily_curve(paper_store.DAILY_TABLE_C) == [
        {"date": "2026-08-20", "equity": 100_123.0}
    ]
    assert paper_store.fetch_open_positions(paper_store.POSITIONS_TABLE_C)[0]["instrument"] == "MSFT"


def test_c_writes_namespaced_fallback_when_dedicated_upsert_fails(monkeypatch):
    state = {"daily": [], "positions": []}

    monkeypatch.setattr(paper_store, "_post_upsert", lambda table, rows: table != paper_store.DAILY_TABLE_C)
    monkeypatch.setattr(paper_store, "_fetch_c_fallback", lambda: state)
    writes = []
    monkeypatch.setattr(paper_store, "_write_c_fallback", lambda payload: writes.append(payload.copy()) or True)

    assert paper_store.upsert_daily(
        [{"date": "2026-08-20", "equity": 100_000.0}], table=paper_store.DAILY_TABLE_C
    )
    assert writes[-1]["daily"] == [{"date": "2026-08-20", "equity": 100_000.0}]


def test_dedicated_empty_table_does_not_resurrect_stale_fallback(monkeypatch):
    monkeypatch.setattr(paper_store, "_get", lambda table, params: [])
    monkeypatch.setattr(
        paper_store, "_fetch_c_fallback",
        lambda: {"positions": [{"instrument": "STALE"}]},
    )
    assert paper_store.fetch_open_positions(paper_store.POSITIONS_TABLE_C) == []
