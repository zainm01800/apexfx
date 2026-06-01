"""Sentiment filter: proves veto/damp-only semantics (never initiates or boosts)."""

from __future__ import annotations

import pandas as pd

from apex_quant.config import SentimentConfig
from apex_quant.risk.types import Direction, Signal
from apex_quant.sentiment import GroqNewsSentiment, SentimentFilter, StaticSentiment
from apex_quant.sentiment.provider import SentimentScore

CFG = SentimentConfig(veto_threshold=0.60, damp_threshold=0.30)
FILT = SentimentFilter(CFG)


def _long(p=0.70):
    return Signal(instrument="EUR/USD", direction=Direction.LONG, probability=p, reward_risk=1.5, confidence=0.6)


def _short(p=0.70):
    return Signal(instrument="EUR/USD", direction=Direction.SHORT, probability=p, reward_risk=1.5, confidence=0.6)


def _s(v, c=1.0):
    return SentimentScore(score=v, confidence=c)


# -- never initiates -----------------------------------------------------------
def test_flat_signal_never_becomes_a_trade():
    flat = Signal(instrument="EUR/USD", direction=Direction.FLAT, probability=0.5, reward_risk=1.5)
    out, _ = FILT.apply(flat, _s(0.95))         # very bullish news
    assert out.direction == Direction.FLAT       # sentiment cannot create a position


# -- never boosts --------------------------------------------------------------
def test_aligned_sentiment_does_not_boost():
    sig = _long(0.70)
    out, msg = FILT.apply(sig, _s(0.95))         # bullish news agrees with long
    assert out.probability == 0.70               # unchanged, NOT increased
    assert out.direction == Direction.LONG
    assert "no change" in msg


# -- veto ----------------------------------------------------------------------
def test_strong_contradiction_vetoes_long():
    out, msg = FILT.apply(_long(0.72), _s(-0.95))   # very bearish vs long
    assert out.direction == Direction.FLAT
    assert "vetoed" in msg


def test_strong_contradiction_vetoes_short():
    out, _ = FILT.apply(_short(0.72), _s(0.95))     # very bullish vs short
    assert out.direction == Direction.FLAT


# -- damp ----------------------------------------------------------------------
def test_mild_contradiction_damps_toward_breakeven():
    sig = _long(0.70)
    out, msg = FILT.apply(sig, _s(-0.5))            # mild bearish vs long
    assert out.direction == Direction.LONG          # direction kept
    assert 0.5 <= out.probability < 0.70            # shrunk toward breakeven, never up
    assert out.confidence < sig.confidence
    assert "damped" in msg


def test_damping_scales_with_confidence():
    strong = FILT.apply(_long(0.70), _s(-0.5, c=1.0))[0].probability
    weak = FILT.apply(_long(0.70), _s(-0.5, c=0.4))[0].probability
    assert strong <= weak <= 0.70                   # lower confidence => less damping


# -- absence is a no-op --------------------------------------------------------
def test_none_sentiment_is_noop():
    sig = _long(0.70)
    out, _ = FILT.apply(sig, None)
    assert out.probability == 0.70 and out.direction == Direction.LONG


# -- provider graceful degradation ---------------------------------------------
def test_groq_provider_returns_none_without_app_url():
    prov = GroqNewsSentiment(SentimentConfig(app_url=""))
    assert prov.score("EUR/USD", pd.Timestamp.utcnow()) is None


def test_static_provider():
    s = StaticSentiment(0.4).score("EUR/USD", "2024-06-01")
    assert s.score == 0.4 and s.n_articles == 3
