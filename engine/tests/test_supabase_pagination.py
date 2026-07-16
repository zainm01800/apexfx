import pytest
import httpx
from unittest.mock import MagicMock
from apex_quant.storage.supabase_util import fetch_all_rows

def test_fetch_all_rows_paginates(monkeypatch):
    calls = []
    
    def mock_get(url, headers, timeout):
        calls.append(url)
        # Check offset in query
        if "offset=0" in url:
            return MagicMock(status_code=200, json=lambda: [{"id": i} for i in range(10)])
        elif "offset=10" in url:
            return MagicMock(status_code=200, json=lambda: [{"id": i} for i in range(10, 20)])
        elif "offset=20" in url:
            return MagicMock(status_code=200, json=lambda: [{"id": i} for i in range(20, 25)])
        return MagicMock(status_code=404, text="Not found")

    monkeypatch.setattr(httpx, "get", mock_get)
    
    url = "https://mock.supabase.co/rest/v1/mock_table?select=*"
    headers = {"apikey": "test"}
    
    rows = fetch_all_rows(url, headers, page_size=10)
    
    assert len(rows) == 25
    assert len(calls) == 3
    assert "offset=0" in calls[0]
    assert "offset=10" in calls[1]
    assert "offset=20" in calls[2]
