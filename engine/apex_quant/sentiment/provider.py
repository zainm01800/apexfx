"""Sentiment providers.

A provider returns a point-in-time net sentiment score in [-1, 1] for an
instrument. ``GroqNewsSentiment`` wires to the EXISTING APEX news pipeline: it
calls the app's ``/api/news`` (Finnhub) for recent headlines and ``/api/ai``
(Groq) to score them - reusing the app's keys and logic rather than duplicating
them. It degrades gracefully to ``None`` (no signal effect) whenever the app is
unreachable, no key is configured, or the decision time is too old for live news.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod

import pandas as pd
from pydantic import BaseModel

from apex_quant.config import SentimentConfig, get_config


class SentimentScore(BaseModel):
    score: float          # net sentiment in [-1, 1] (negative = bearish)
    confidence: float = 0.5
    n_articles: int = 0
    as_of: str = ""
    detail: str = ""


class SentimentProvider(ABC):
    @abstractmethod
    def score(self, instrument: str, t: pd.Timestamp | str) -> SentimentScore | None:
        """Net sentiment known at ``t``, or None if unavailable."""


class StaticSentiment(SentimentProvider):
    """Deterministic provider for tests / manual overrides."""

    def __init__(self, value: float, confidence: float = 1.0, n_articles: int = 3):
        self._v = float(value)
        self._c = float(confidence)
        self._n = n_articles

    def score(self, instrument, t) -> SentimentScore:
        return SentimentScore(score=self._v, confidence=self._c, n_articles=self._n,
                              as_of=str(pd.Timestamp(t).date()), detail="static")


class GroqNewsSentiment(SentimentProvider):
    """Live sentiment via the APEX app's /api/news + /api/ai endpoints."""

    def __init__(self, cfg: SentimentConfig | None = None, timeout: float = 12.0):
        self.cfg = cfg or get_config().sentiment
        self.timeout = timeout

    def score(self, instrument, t) -> SentimentScore | None:
        if not self.cfg.app_url:
            return None
        t = pd.Timestamp(t)
        t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
        # live-only: don't apply current headlines to a stale historical decision
        now = pd.Timestamp.utcnow()
        if (now - t).days > self.cfg.max_age_days:
            return None

        try:
            import httpx

            base = self.cfg.app_url.rstrip("/")
            with httpx.Client(timeout=self.timeout) as client:
                nres = client.get(f"{base}/api/news", params={"sym": instrument, "type": "Forex"})
                if nres.status_code != 200:
                    return None
                items = nres.json()
                heads = [i.get("title", "") for i in (items if isinstance(items, list) else [])][:12]
                heads = [h for h in heads if h]
                if not heads:
                    return None

                prompt = (
                    f"Headlines about {instrument}:\n- " + "\n- ".join(heads) +
                    "\n\nReturn ONLY JSON: {\"score\": <net sentiment for the FIRST currency "
                    "vs the second, -1 bearish .. 1 bullish>, \"confidence\": <0..1>}"
                )
                ares = client.post(f"{base}/api/ai", json={"prompt": prompt})
                if ares.status_code != 200:
                    return None
                raw = ares.json()
                text = raw.get("text") or raw.get("content") or json.dumps(raw)
                parsed = _extract_json(text)
                if parsed is None or "score" not in parsed:
                    return None
                return SentimentScore(
                    score=max(-1.0, min(1.0, float(parsed["score"]))),
                    confidence=float(parsed.get("confidence", 0.5)),
                    n_articles=len(heads),
                    as_of=str(t.date()),
                    detail="groq+finnhub",
                )
        except Exception:
            return None


def _extract_json(text: str) -> dict | None:
    try:
        s, e = text.index("{"), text.rindex("}") + 1
        return json.loads(text[s:e])
    except Exception:
        return None
