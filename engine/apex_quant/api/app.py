"""FastAPI service exposing the quant engine to the frontend.

Run locally:
    cd engine
    .venv\\Scripts\\python.exe -m uvicorn apex_quant.api.app:app --port 8000 --reload

Endpoints (all point-in-time as of the latest bar):
    GET /health
    GET /instruments
    GET /regime/{instrument}?method=rule_based|hmm
    GET /signal/{instrument}
    GET /risk/{instrument}?equity=100000
    GET /features/{instrument}
    GET /validation/{strategy}?instrument=EUR/USD   (served from precomputed cache)
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from apex_quant.api.schemas import (
    HealthResponse,
    RegimeResponse,
    RiskResponse,
    SignalResponse,
)
from apex_quant.api.service import EngineService
from apex_quant.config import get_config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("apex_quant.api")

cfg = get_config()
service = EngineService(cfg)
app = FastAPI(title="APEX Quant Engine", version=f"0.1.{cfg.version}")

# CORS: the JS frontend (dev server / Vercel) calls this local service.
_default_origins = ["http://localhost:3001", "http://127.0.0.1:3001", "http://localhost:3000"]
_env_origins = [o.strip() for o in os.environ.get("APEX_CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_env_origins or _default_origins,
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


def _handle(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("engine error")
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


@app.get("/health", response_model=HealthResponse)
@app.get("/", response_model=HealthResponse)
def health():
    return HealthResponse(
        status="ok", service="apex-quant-engine", version=cfg.version,
        instruments=cfg.data.instruments,
    )


@app.get("/instruments")
def instruments():
    return {"instruments": cfg.data.instruments}


@app.get("/regime/{instrument:path}", response_model=RegimeResponse)
def regime(instrument: str, method: str = Query("rule_based", pattern="^(rule_based|hmm)$")):
    return _handle(service.regime, instrument, method)


_STRAT_RE = "^(baseline|ml_gbm|ml_linear)$"


@app.get("/signal/{instrument:path}", response_model=SignalResponse)
def signal(instrument: str, strategy: str = Query("baseline", pattern=_STRAT_RE)):
    return _handle(service.signal, instrument, strategy)


@app.get("/risk/{instrument:path}", response_model=RiskResponse)
def risk(instrument: str, equity: float = Query(None, gt=0), peak_equity: float = Query(None, gt=0),
         strategy: str = Query("baseline", pattern=_STRAT_RE)):
    return _handle(service.risk, instrument, equity, peak_equity, strategy)


@app.get("/features/{instrument:path}")
def features(instrument: str):
    return _handle(service.features, instrument)


@app.get("/validation/{strategy}")
def validation(strategy: str, instrument: str = Query(...)):
    report = service.validation(strategy, instrument)
    if report is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No cached validation for {strategy} on {instrument}. "
                "Run: .venv\\Scripts\\python.exe scripts/run_validation.py"
            ),
        )
    return report


@app.post("/refresh")
def refresh(instrument: str = Query(None)):
    service.refresh(instrument)
    return {"refreshed": instrument or "all"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
