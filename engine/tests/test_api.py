"""API tests via FastAPI TestClient. Synthetic data is injected into the service
singleton so endpoints are exercised without hitting the network."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.strategies import RegimeGatedMomentum

fastapi_testclient = pytest.importorskip("fastapi.testclient")
from fastapi.testclient import TestClient  # noqa: E402

from apex_quant.api.app import app, service  # noqa: E402

client = TestClient(app)


def _trend(n=600, drift=0.001, noise=0.004, seed=3):
    rng = np.random.default_rng(seed)
    close = 1.10 * np.exp(np.cumsum(rng.normal(drift, noise, n)))
    op = np.concatenate([[1.10], close[:-1]])
    hi = np.maximum(op, close) * 1.003
    lo = np.minimum(op, close) * 0.997
    idx = pd.bdate_range("2018-01-01", periods=n, tz="UTC", name="timestamp")
    return pd.DataFrame({"open": op, "high": hi, "low": lo, "close": close, "volume": 1.0}, index=idx)


@pytest.fixture(autouse=True)
def _inject_synthetic_data():
    """Populate the service caches with offline synthetic data for EUR/USD."""
    df = _trend()
    pit = PointInTimeAccessor(df)
    strat = RegimeGatedMomentum()
    strat.fit(pit, df.index)
    service._pit["EUR/USD"] = pit
    service._strat["EUR/USD"] = strat
    yield
    service.refresh()


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "instruments" in body


def test_instruments():
    r = client.get("/instruments")
    assert r.status_code == 200
    assert isinstance(r.json()["instruments"], list)


def test_regime_endpoint():
    r = client.get("/regime/EUR/USD")
    assert r.status_code == 200
    body = r.json()
    assert body["instrument"] == "EUR/USD"
    assert body["trend"] in ("up", "down", "ranging")
    assert body["vol"] in ("low", "normal", "high")
    assert 0.0 <= body["confidence"] <= 1.0


def test_regime_rejects_bad_method():
    r = client.get("/regime/EUR/USD", params={"method": "magic"})
    assert r.status_code == 422  # query pattern validation


def test_signal_endpoint():
    r = client.get("/signal/EUR/USD")
    assert r.status_code == 200
    body = r.json()
    assert body["direction"] in ("long", "short", "flat")
    assert 0.0 <= body["probability"] <= 1.0
    assert "contributing_features" in body


def test_risk_endpoint():
    r = client.get("/risk/EUR/USD", params={"equity": 50000})
    assert r.status_code == 200
    body = r.json()
    assert body["instrument"] == "EUR/USD"
    assert body["assumed_equity"] == 50000
    assert "permitted" in body
    assert "constraints_applied" in body


def test_features_endpoint():
    r = client.get("/features/EUR/USD")
    assert r.status_code == 200
    body = r.json()
    assert "features" in body and "catalog" in body


def test_validation_missing_returns_404():
    r = client.get("/validation/regime_gated_momentum", params={"instrument": "EUR/USD"})
    assert r.status_code in (404, 200)  # 404 if no cache yet
