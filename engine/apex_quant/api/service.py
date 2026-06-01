"""Engine service layer: bridges HTTP requests to the quant pipeline.

Holds per-instrument caches (data + fitted strategy) so endpoints stay fast.
Everything it returns is point-in-time as of the latest bar; the heavy CPCV/DSR/
PBO validation is precomputed offline (scripts/run_validation.py) and served from
a JSON cache - never run inside a request.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from apex_quant.config import AppConfig, get_config
from apex_quant.data import PointInTimeAccessor, clean, get_adapter
from apex_quant.features import compute_feature_matrix, default_features, feature_catalog
from apex_quant.regime import classify_regime
from apex_quant.risk import AccountState, MarketState, RiskManager
from apex_quant.risk.stops import atr
from apex_quant.sentiment import GroqNewsSentiment, SentimentFilter
from apex_quant.strategies import MLStrategy, RegimeGatedMomentum
from apex_quant.volatility import forecast_volatility

STRATEGY_KINDS = ("baseline", "ml_gbm", "ml_linear")


class EngineService:
    def __init__(self, cfg: AppConfig | None = None, history_years: int = 5):
        self.cfg = cfg or get_config()
        self.history_years = history_years
        self.adapter = get_adapter(self.cfg.data.provider)
        self._risk_mgr = RiskManager(self.cfg.risk)
        self._pit: dict[str, PointInTimeAccessor] = {}
        self._strat: dict[tuple, object] = {}
        self._val_dir = self.cfg.store_path / "validation"
        self._sentiment = GroqNewsSentiment(self.cfg.sentiment)
        self._sfilter = SentimentFilter(self.cfg.sentiment)

    # -- data + strategy caches ------------------------------------------------
    def pit(self, instrument: str) -> PointInTimeAccessor:
        if instrument not in self._pit:
            end = pd.Timestamp.utcnow().tz_localize(None)
            start = end - pd.Timedelta(days=365 * self.history_years + 30)
            df = clean(self.adapter.get_history(instrument, start, end))
            if len(df) < 260:
                raise ValueError(f"insufficient history for {instrument} ({len(df)} bars)")
            self._pit[instrument] = PointInTimeAccessor(df)
        return self._pit[instrument]

    @staticmethod
    def _build_strategy(kind: str):
        if kind == "ml_gbm":
            return MLStrategy(model="gbm")
        if kind == "ml_linear":
            return MLStrategy(model="linear")
        return RegimeGatedMomentum()

    def strategy(self, instrument: str, kind: str = "baseline"):
        if kind not in STRATEGY_KINDS:
            raise ValueError(f"unknown strategy '{kind}'; choose {STRATEGY_KINDS}")
        key = (instrument, kind)
        if key not in self._strat:
            pit = self.pit(instrument)
            strat = self._build_strategy(kind)
            strat.fit(pit, pit.as_of(pit.end).index)
            self._strat[key] = strat
        return self._strat[key]

    def _apply_sentiment(self, sig, instrument, t):
        """Return (possibly filtered signal, sentiment_block|None). No-op unless
        sentiment is enabled AND a provider is reachable."""
        if not self.cfg.sentiment.enabled:
            return sig, None
        sent = self._sentiment.score(instrument, t)
        if sent is None:
            return sig, None
        filtered, msg = self._sfilter.apply(sig, sent)
        return filtered, {"score": round(sent.score, 3), "confidence": round(sent.confidence, 2),
                          "n_articles": sent.n_articles, "effect": msg}

    def refresh(self, instrument: str | None = None) -> None:
        if instrument:
            self._pit.pop(instrument, None)
            self._strat.pop(instrument, None)
        else:
            self._pit.clear()
            self._strat.clear()

    # -- endpoints' business logic ---------------------------------------------
    def regime(self, instrument: str, method: str = "rule_based") -> dict:
        pit = self.pit(instrument)
        label = classify_regime(pit, pit.end, method=method)
        return {
            "instrument": instrument,
            "as_of": str(pit.end.date()),
            **label.model_dump(),
            "name": label.name,
            "aggression_scalar": round(label.aggression_scalar(), 3),
        }

    def signal(self, instrument: str, kind: str = "baseline") -> dict:
        pit = self.pit(instrument)
        strat = self.strategy(instrument, kind)
        info = strat.explain(pit, pit.end, instrument)
        sig = strat.generate(pit, pit.end, instrument)
        sig, sentiment_block = self._apply_sentiment(sig, instrument, pit.end)
        if sentiment_block is not None:
            info["direction"] = sig.direction.value
            info["probability"] = sig.probability
            info["confidence"] = sig.confidence
            if sig.direction.value == "flat":
                info["uncertainty"] = None
            info["sentiment"] = sentiment_block
        info["strategy"] = strat.name
        info["as_of"] = str(pit.end.date())
        return info

    def risk(self, instrument: str, equity: float | None = None, peak_equity: float | None = None,
             kind: str = "baseline") -> dict:
        pit = self.pit(instrument)
        t = pit.end
        eq = equity or self.cfg.backtest.initial_equity
        peak = peak_equity or eq

        strat = self.strategy(instrument, kind)
        sig = strat.generate(pit, t, instrument)
        sig, sentiment_block = self._apply_sentiment(sig, instrument, t)
        hist = pit.as_of(t)
        price = float(hist["close"].iloc[-1])
        vf = forecast_volatility(pit, t, method="ewma")
        atr_val = atr(hist, self.cfg.risk.atr_window)
        regime = classify_regime(pit, t, method="rule_based")

        if not (vf.annualized > 0 and atr_val > 0):
            return {"instrument": instrument, "as_of": str(t.date()), "permitted": False,
                    "rationale": "volatility/ATR unavailable", "assumed_equity": eq}

        market = MarketState(instrument=instrument, price=price, ann_vol=vf.annualized, atr=atr_val)
        account = AccountState(equity=eq, peak_equity=peak)
        pos = self._risk_mgr.permit(sig, account, market, regime=regime)
        out = pos.model_dump()
        out.update({
            "instrument": instrument,
            "as_of": str(t.date()),
            "price": round(price, 6),
            "ann_vol": round(vf.annualized, 4),
            "atr": round(atr_val, 6),
            "regime": regime.name,
            "assumed_equity": eq,
            "signal_probability": round(sig.probability, 3),
            "strategy": strat.name,
        })
        if sentiment_block is not None:
            out["sentiment"] = sentiment_block
        return out

    def features(self, instrument: str) -> dict:
        pit = self.pit(instrument)
        feats = default_features()
        row = compute_feature_matrix(pit, [pit.end], feats).iloc[-1]
        return {
            "instrument": instrument,
            "as_of": str(pit.end.date()),
            "features": {k: (None if pd.isna(v) else round(float(v), 5)) for k, v in row.items()},
            "catalog": feature_catalog(feats),
        }

    def validation(self, strategy: str, instrument: str) -> dict | None:
        path = self._val_dir / f"{strategy}__{instrument.replace('/', '_')}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save_validation(self, report: dict, strategy: str, instrument: str) -> Path:
        self._val_dir.mkdir(parents=True, exist_ok=True)
        path = self._val_dir / f"{strategy}__{instrument.replace('/', '_')}.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return path

    # -- AI research (Phase 3) — served from precomputed cache -----------------
    @property
    def _research_dir(self) -> Path:
        return self.cfg.store_path / "research"

    def research(self, instrument: str) -> dict | None:
        path = self._research_dir / f"{instrument.replace('/', '_')}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def save_research(self, report: dict, instrument: str) -> Path:
        self._research_dir.mkdir(parents=True, exist_ok=True)
        path = self._research_dir / f"{instrument.replace('/', '_')}.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return path
