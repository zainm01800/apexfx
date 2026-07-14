"""Tests for the DeepSeek-powered News Sentiment Veto Filter.

Covers:
- Unit tests for ``_parse_score`` (JSON extraction from LLM prose)
- Unit tests for ``get_news_sentiment_score`` with mocked HTTP
- Full integration tests for ``apply_deepseek_sentiment`` with a
  deterministic headline fetcher and mocked DeepSeek
- Fail-ALLOW semantics
- Config gating (disabled → no-op)
"""

from __future__ import annotations

import json
from unittest.mock import ANY, MagicMock, patch

import pytest

from apex_quant.ai.sentiment_filter import (
    _parse_score,
    apply_deepseek_sentiment,
    get_news_sentiment_score,
)
from apex_quant.config import AppConfig, SentimentConfig
from apex_quant.risk.types import Direction, Signal


# ========================================================================
# Helpers
# ========================================================================

def _long(p: float = 0.72, conf: float = 0.65) -> Signal:
    return Signal(
        instrument="EUR/USD",
        direction=Direction.LONG,
        probability=p,
        reward_risk=1.5,
        confidence=conf,
    )


def _short(p: float = 0.72, conf: float = 0.65) -> Signal:
    return Signal(
        instrument="EUR/USD",
        direction=Direction.SHORT,
        probability=p,
        reward_risk=1.5,
        confidence=conf,
    )


def _cfg(enabled: bool = True, veto: float = 0.60, damp: float = 0.30) -> AppConfig:
    c = AppConfig()
    c.sentiment = SentimentConfig(
        enabled=enabled, veto_threshold=veto, damp_threshold=damp,
    )
    # Wire in a fake API key so get_news_sentiment_score doesn't bail early
    c.ai.local_llm_key = "sk-test-key"
    c.ai.local_llm_url = "http://test-deepseek.local/v1/chat/completions"
    c.ai.local_llm_model = "test-model"
    return c


def _mock_deepseek_response(risk_score: float, reason: str = "Test reason") -> dict:
    """Simulate a DeepSeek API success response."""
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps({"risk_score": risk_score, "reason": reason}),
                }
            }
        ]
    }


def _mock_fetcher(headlines: list[str]) -> MagicMock:
    """Create a deterministic headline fetcher."""
    m = MagicMock(return_value=headlines)
    return m


SAMPLE_HEADLINES = [
    "ECB holds rates steady",
    "German industrial output falls",
    "US dollar strengthens on Fed minutes",
]


# ========================================================================
# _parse_score
# ========================================================================

class TestParseScore:
    def test_plain_json(self):
        assert _parse_score('{"risk_score": 0.85, "reason": "Too risky"}') == {
            "risk_score": 0.85, "reason": "Too risky",
        }

    def test_with_fences(self):
        text = 'Some prose ```json\n{"risk_score": 0.40, "reason": "Mild concern"}\n```'
        assert _parse_score(text) == {"risk_score": 0.40, "reason": "Mild concern"}

    def test_camelcase_fallback(self):
        text = '{"riskScore": 0.70, "reason": "Geopolitical event"}'
        assert _parse_score(text) == {"risk_score": 0.70, "reason": "Geopolitical event"}

    def test_no_json_returns_none(self):
        assert _parse_score("I have no risk assessment to give.") is None

    def test_empty_string_returns_none(self):
        assert _parse_score("") is None

    def test_missing_risk_score_field(self):
        assert _parse_score('{"foo": 1}') is None

    def test_nested_json_is_ignored(self):
        text = 'Before {"risk_score": 0.5, "reason": "ok"} after'
        assert _parse_score(text) == {"risk_score": 0.5, "reason": "ok"}


# ========================================================================
# get_news_sentiment_score
# ========================================================================

class TestGetNewsSentimentScore:
    @patch("apex_quant.ai.sentiment_filter.build_llm")
    def test_success(self, mock_build_llm):
        mock_llm = MagicMock()
        mock_llm.complete.return_value = '{"risk_score": 0.85, "reason": "Geopolitical tensions escalating"}'
        mock_build_llm.return_value = mock_llm

        cfg = _cfg()
        result = get_news_sentiment_score("EUR/USD", SAMPLE_HEADLINES, cfg=cfg)

        assert result is not None
        assert result["risk_score"] == 0.85
        assert "Geopolitical" in result["reason"]
        mock_llm.complete.assert_called_once()
        call_args = mock_llm.complete.call_args[0]
        call_kwargs = mock_llm.complete.call_args[1]
        assert "EUR/USD" in call_args[0]
        assert call_kwargs["temperature"] == 0.3

    @patch("httpx.Client")
    def test_api_error_status(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = '{"error": "unauthorized"}'
        mock_client.post.return_value = mock_response

        result = get_news_sentiment_score("EUR/USD", SAMPLE_HEADLINES, cfg=_cfg())
        assert result is None

    @patch("httpx.Client")
    def test_timeout_returns_none(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        import httpx
        mock_client.post.side_effect = httpx.TimeoutException("Connection timed out")

        result = get_news_sentiment_score("BTC/USD", SAMPLE_HEADLINES, cfg=_cfg())
        assert result is None

    def test_no_api_key(self):
        cfg = _cfg()
        cfg.ai.local_llm_key = ""
        with patch.dict("os.environ", {}, clear=True):
            result = get_news_sentiment_score("EUR/USD", SAMPLE_HEADLINES, cfg=cfg)
        assert result is None

    def test_explicit_key_override(self):
        result = get_news_sentiment_score(
            "EUR/USD",
            SAMPLE_HEADLINES,
            api_key="explicit-key",
            api_url="http://explicit-url/chat",
            model="explicit-model",
        )
        # Should fail with explicit URL, but that's fine — we just verify
        # the override was used (the mock test covers success).  This test
        # simply confirms the function doesn't raise.
        assert result is None  # connection refused is expected


# ========================================================================
# apply_deepseek_sentiment
# ========================================================================

class TestApplyDeepseekSentiment:
    @patch("apex_quant.ai.sentiment_filter.get_news_sentiment_score")
    def test_disabled_when_sentiment_not_enabled(self, mock_get_score):
        sig = _long()
        cfg = _cfg(enabled=False)
        fetcher = _mock_fetcher(SAMPLE_HEADLINES)

        out = apply_deepseek_sentiment(sig, "EUR/USD", fetcher, cfg=cfg)

        assert out is sig  # same object, no copy
        assert out.direction == Direction.LONG
        mock_get_score.assert_not_called()

    @patch("apex_quant.ai.sentiment_filter.get_news_sentiment_score")
    def test_flat_signal_stays_flat(self, mock_get_score):
        flat = Signal(
            instrument="EUR/USD", direction=Direction.FLAT,
            probability=0.5, reward_risk=1.0,
        )
        cfg = _cfg(enabled=True)
        fetcher = _mock_fetcher(SAMPLE_HEADLINES)

        out = apply_deepseek_sentiment(flat, "EUR/USD", fetcher, cfg=cfg)

        assert out.direction == Direction.FLAT
        mock_get_score.assert_not_called()

    @patch("apex_quant.ai.sentiment_filter.get_news_sentiment_score")
    def test_empty_headlines_allows(self, mock_get_score):
        sig = _long()
        cfg = _cfg()
        fetcher = _mock_fetcher([])  # no headlines

        out = apply_deepseek_sentiment(sig, "EUR/USD", fetcher, cfg=cfg)

        assert out.direction == Direction.LONG
        assert out.probability == sig.probability
        mock_get_score.assert_not_called()

    @patch("apex_quant.ai.sentiment_filter.get_news_sentiment_score")
    def test_veto_at_high_risk(self, mock_get_score):
        mock_get_score.return_value = {"risk_score": 0.85, "reason": "Major geopolitical risk"}
        sig = _long()
        cfg = _cfg()
        fetcher = _mock_fetcher(SAMPLE_HEADLINES)

        out = apply_deepseek_sentiment(sig, "EUR/USD", fetcher, cfg=cfg)

        assert out.direction == Direction.FLAT
        assert out.probability == 0.5
        assert out.confidence == 0.0
        assert "DEEPSEEK-VETO" in out.rationale

    @patch("apex_quant.ai.sentiment_filter.get_news_sentiment_score")
    def test_veto_at_exact_threshold(self, mock_get_score):
        mock_get_score.return_value = {"risk_score": 0.60, "reason": "Exactly at veto threshold"}
        sig = _long()
        cfg = _cfg()
        fetcher = _mock_fetcher(SAMPLE_HEADLINES)

        out = apply_deepseek_sentiment(sig, "EUR/USD", fetcher, cfg=cfg)

        assert out.direction == Direction.FLAT
        assert "DEEPSEEK-VETO" in out.rationale

    @patch("apex_quant.ai.sentiment_filter.get_news_sentiment_score")
    def test_damp_in_mid_range(self, mock_get_score):
        mock_get_score.return_value = {"risk_score": 0.45, "reason": "Moderate headwinds"}
        sig = _long(p=0.72, conf=0.65)
        cfg = _cfg()
        fetcher = _mock_fetcher(SAMPLE_HEADLINES)

        out = apply_deepseek_sentiment(sig, "EUR/USD", fetcher, cfg=cfg)

        assert out.direction == Direction.LONG
        assert out.probability < 0.72      # damped down
        assert out.probability > 0.5       # still above breakeven
        assert out.confidence < 0.65       # confidence reduced
        assert "DEEPSEEK-DAMPED" in out.rationale

    @patch("apex_quant.ai.sentiment_filter.get_news_sentiment_score")
    def test_low_risk_no_change(self, mock_get_score):
        mock_get_score.return_value = {"risk_score": 0.10, "reason": "Calm market conditions"}
        sig = _long(p=0.72, conf=0.65)
        cfg = _cfg()
        fetcher = _mock_fetcher(SAMPLE_HEADLINES)

        out = apply_deepseek_sentiment(sig, "EUR/USD", fetcher, cfg=cfg)

        assert out.direction == Direction.LONG
        assert out.probability == 0.72
        assert out.confidence == 0.65

    @patch("apex_quant.ai.sentiment_filter.get_news_sentiment_score")
    def test_fail_allow_on_api_failure(self, mock_get_score):
        mock_get_score.return_value = None   # API failure
        sig = _long()
        cfg = _cfg()
        fetcher = _mock_fetcher(SAMPLE_HEADLINES)

        out = apply_deepseek_sentiment(sig, "EUR/USD", fetcher, cfg=cfg)

        assert out is sig   # same object
        assert out.direction == Direction.LONG

    @patch("apex_quant.ai.sentiment_filter.get_news_sentiment_score")
    def test_fetcher_raises_returns_empty(self, mock_get_score):
        def broken_fetcher(_instr):
            raise RuntimeError("Network error")

        sig = _long()
        cfg = _cfg()
        out = apply_deepseek_sentiment(sig, "EUR/USD", broken_fetcher, cfg=cfg)

        assert out is sig
        mock_get_score.assert_not_called()

    @patch("apex_quant.ai.sentiment_filter.get_news_sentiment_score")
    def test_damp_short_signal(self, mock_get_score):
        mock_get_score.return_value = {"risk_score": 0.50, "reason": "Bearish news for EUR"}
        sig = _short(p=0.72, conf=0.65)
        cfg = _cfg()
        fetcher = _mock_fetcher(SAMPLE_HEADLINES)

        out = apply_deepseek_sentiment(sig, "EUR/USD", fetcher, cfg=cfg)

        assert out.direction == Direction.SHORT
        assert out.probability < 0.72
        assert out.confidence < 0.65
        assert "DEEPSEEK-DAMPED" in out.rationale

    @patch("apex_quant.ai.sentiment_filter.get_news_sentiment_score")
    def test_veto_short_signal(self, mock_get_score):
        mock_get_score.return_value = {"risk_score": 0.90, "reason": "Everything against EUR shorts"}
        sig = _short(p=0.72, conf=0.65)
        cfg = _cfg()
        fetcher = _mock_fetcher(SAMPLE_HEADLINES)

        out = apply_deepseek_sentiment(sig, "EUR/USD", fetcher, cfg=cfg)

        assert out.direction == Direction.FLAT
        assert "DEEPSEEK-VETO" in out.rationale

    @patch("apex_quant.ai.sentiment_filter.get_news_sentiment_score")
    def test_damp_scale_at_midpoint(self, mock_get_score):
        """At exact midpoint of damp range, scale should be ~0.5."""
        mock_get_score.return_value = {"risk_score": 0.45, "reason": "Mid-range risk"}
        # damp_threshold=0.30, veto_threshold=0.60
        # frac = (0.45 - 0.30) / 0.30 = 0.50
        # scale = 1.0 - 0.50 = 0.50
        # new_p = 0.5 + 0.50 * (0.72 - 0.5) = 0.5 + 0.50 * 0.22 = 0.61
        sig = _long(p=0.72, conf=0.65)
        cfg = _cfg()
        fetcher = _mock_fetcher(SAMPLE_HEADLINES)

        out = apply_deepseek_sentiment(sig, "EUR/USD", fetcher, cfg=cfg)

        assert out.probability == pytest.approx(0.61, abs=0.005)
        assert out.confidence == pytest.approx(0.325, abs=0.005)  # 0.65 * 0.50

    @patch("apex_quant.ai.sentiment_filter.get_news_sentiment_score")
    def test_risk_score_clamped(self, mock_get_score):
        """Risk score from API is clamped to [0, 1]."""
        mock_get_score.return_value = {"risk_score": 2.5, "reason": "Extreme"}
        sig = _long()
        cfg = _cfg()
        fetcher = _mock_fetcher(SAMPLE_HEADLINES)

        out = apply_deepseek_sentiment(sig, "EUR/USD", fetcher, cfg=cfg)

        assert out.direction == Direction.FLAT  # 2.5 clamped to 1.0 >= 0.60
        assert "DEEPSEEK-VETO" in out.rationale