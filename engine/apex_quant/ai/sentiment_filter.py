"""DeepSeek-powered News Sentiment Veto Filter (Phase 2).

Calls the DeepSeek API directly with a "cynical institutional risk manager"
prompt.  Returns a risk score in [0, 1] and a short reason string.

Integration
-----------
Use :func:`apply_deepseek_sentiment` in the signal-generation flow::

    from apex_quant.ai.sentiment_filter import apply_deepseek_sentiment

    sig = strat.generate(pit, latest_time, instrument=sym)
    sig = apply_deepseek_sentiment(sig, sym, fetch_headlines, cfg)

If the API is unreachable or times out the original signal is returned
**unchanged** (fail-ALLOW) — the filter must never lock the trading engine.

If ``sentiment.enabled`` is False the filter is a no-op, so turn it on in
config.yaml::

    sentiment:
      enabled: true
      veto_threshold: 0.60
      damp_threshold: 0.30
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

from apex_quant.config import AppConfig, get_config
from apex_quant.risk.types import Direction, Signal

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_DEFAULT_TIMEOUT: float = 15.0
_CYNICAL_SYSTEM_PROMPT = (
    "You are a cynical institutional risk manager at a major hedge fund. "
    "Your ONLY job is to flag macro / geopolitical / catalyst risks that "
    "could crush a position.  You have ZERO faith in any trade.  "
    "Return ONLY valid JSON with exactly two keys:\n"
    '  - "risk_score": a float from 0.0 (no macro risk) to 1.0 (extreme '
    "immediate risk of capital loss)\n"
    '  - "reason": a short 1-sentence explanation of the veto or warning.\n'
    "Do NOT include any text outside the JSON object."
)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_news_sentiment_score(
    instrument: str,
    headlines_list: list[str],
    *,
    cfg: AppConfig | None = None,
    api_key: str | None = None,
    api_url: str | None = None,
    model: str | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict[str, Any] | None:
    """Call DeepSeek and return ``{"risk_score": float, "reason": str}``.

    Parameters
    ----------
    instrument:
        Trading instrument identifier (e.g. ``"EUR/USD"``, ``"NVDA"``).
    headlines_list:
        Recent news headlines for this instrument.
    cfg, api_key, api_url, model:
        Config source.  Resolution order: explicit kwarg > ``cfg.ai.*`` >
        environment variable ``DEEPSEEK_API_KEY``.
    timeout:
        HTTP request timeout in seconds.

    Returns
    -------
    dict or None
        ``None`` if the API call fails or the response can't be parsed
        (fail-ALLOW behaviour).
    """
    cfg = cfg or get_config()

    # Resolve credentials ---------------------------------------------------
    env_key = ""
    try:
        from pathlib import Path
        env_path = Path(__file__).resolve().parent.parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    if k.strip() == "APEX_LOCAL_LLM_KEY":
                        env_key = v.strip()
                        break
    except Exception:
        pass

    key = api_key or env_key or cfg.ai.local_llm_key or os.environ.get("DEEPSEEK_API_KEY", "")
    url = api_url or cfg.ai.local_llm_url or "https://api.deepseek.com/v1/chat/completions"
    model_name = model or cfg.ai.local_llm_model or "deepseek-chat"

    if not key:
        print("  [DeepSeekSentiment] No API key configured — skipping.")
        return None

    # Build prompt -----------------------------------------------------------
    headlines_text = "\n".join(f"- {h}" for h in headlines_list[:25])
    user_prompt = (
        f"We are considering trading {instrument}.  "
        f"Here are the latest headlines:\n{headlines_text}\n\n"
        "What is your risk assessment?"
    )

    # Call DeepSeek ----------------------------------------------------------
    try:
        import httpx

        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": _CYNICAL_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.3,          # low temp → consistent, decisive
            "max_tokens": 300,
        }

        with httpx.Client(timeout=timeout) as client:
            res = client.post(url, json=payload, headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            })

        if res.status_code != 200:
            print(f"  [DeepSeekSentiment] API status {res.status_code}: {res.text[:200]}")
            return None

        body = res.json()
        content = (
            body.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        if not content:
            return None

        return _parse_score(content)

    except Exception as exc:
        print(f"  [DeepSeekSentiment] Exception {type(exc).__name__}: {exc}")
        return None


def apply_deepseek_sentiment(
    signal: Signal,
    instrument: str,
    headline_fetcher: Callable[[str], list[str]],
    *,
    cfg: AppConfig | None = None,
) -> Signal:
    """Apply the DeepSeek sentiment filter to a *signal* in-place.

    Parameters
    ----------
    signal:
        The original trading signal from the strategy.
    instrument:
        Instrument identifier (passed to the fetcher and DeepSeek).
    headline_fetcher:
        Callable(instrument) -> list of headline strings.  Must never raise;
        return an empty list on any failure.
    cfg:
        Config (defaults to the process-wide singleton).

    Returns
    -------
    Signal
        The original signal unchanged if the filter is disabled, the API
        fails, or risk is low.  A modified (vetoed or damped) signal
        otherwise.
    """
    cfg = cfg or get_config()
    sent_cfg = cfg.sentiment

    # 1. Gate: disabled by config -------------------------------------------
    if not sent_cfg.enabled:
        return signal

    # 2. FLAT signals stay FLAT (filter never initiates) ---------------------
    if signal.direction == Direction.FLAT:
        return signal

    # 3. Fetch headlines ----------------------------------------------------
    try:
        headlines = headline_fetcher(instrument) or []
    except Exception:
        headlines = []

    if not headlines:
        # No news → no risk signal → allow the trade
        return signal

    # 4. Get DeepSeek risk score --------------------------------------------
    result = get_news_sentiment_score(instrument, headlines, cfg=cfg)
    if result is None:
        return signal   # fail-ALLOW

    risk_score = max(0.0, min(1.0, float(result.get("risk_score", 0.0))))
    reason = str(result.get("reason", ""))

    # 5. Veto ---------------------------------------------------------------
    if risk_score >= sent_cfg.veto_threshold:
        vetoed = signal.model_copy(update={
            "direction": Direction.FLAT,
            "probability": 0.5,
            "confidence": 0.0,
            "rationale": (
                signal.rationale
                + f" | DEEPSEEK-VETO (risk {risk_score:.2f}): {reason}"
            ),
        })
        print(
            f"  [DeepSeekSentiment] VETO {instrument} "
            f"(risk {risk_score:.2f} >= {sent_cfg.veto_threshold})"
        )
        return vetoed

    # 6. Damp ---------------------------------------------------------------
    if risk_score >= sent_cfg.damp_threshold:
        # scale factor: 1.0 at damp_threshold → 0.0 at veto_threshold
        damp_range = sent_cfg.veto_threshold - sent_cfg.damp_threshold
        frac = (risk_score - sent_cfg.damp_threshold) / max(damp_range, 1e-9)
        scale = 1.0 - frac  # interpolate 1.0 → 0.0

        new_p = 0.5 + scale * (signal.probability - 0.5)
        damped = signal.model_copy(update={
            "probability": float(new_p),
            "confidence": float(signal.confidence * scale),
            "rationale": (
                signal.rationale
                + f" | DEEPSEEK-DAMPED x{scale:.2f} "
                f"(risk {risk_score:.2f}): {reason}"
            ),
        })
        print(
            f"  [DeepSeekSentiment] DAMPED {instrument} "
            f"(risk {risk_score:.2f}) x{scale:.2f}"
        )
        return damped

    # 7. Low risk — no change -----------------------------------------------
    return signal


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_score(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object from *text*."""
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        data = json.loads(text[start:end])
        # Normalise keys (allow both snake_case and camelCase from LLM)
        if "risk_score" in data or "riskScore" in data:
            return {
                "risk_score": data.get("risk_score", data.get("riskScore", 0.0)),
                "reason": data.get("reason", ""),
            }
        return None
    except (ValueError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Mock runner (``python -m apex_quant.ai.sentiment_filter --mock``)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="DeepSeek Sentiment Filter — mock runner")
    parser.add_argument("--mock", action="store_true", help="Run with sample data")
    args = parser.parse_args()

    if not args.mock:
        parser.print_help()
        raise SystemExit(1)

    SAMPLE_HEADLINES: dict[str, list[str]] = {
        "EUR/USD": [
            "ECB holds rates steady, euro rallies",
            "German industrial production falls 1.2% month-on-month",
            "US dollar strengthens on hawkish Fed minutes",
        ],
        "NVDA": [
            "NVIDIA reports record quarterly revenue, up 265% YoY",
            "AI chip demand continues to outstrip supply",
            "Regulators launch antitrust investigation into AI chip market",
        ],
        "BTC/USD": [
            "Bitcoin plunges 15% after Binance enforcement action",
            "Crypto market cap loses $200 billion in 24 hours",
            "Senator calls for emergency crypto regulations",
        ],
    }

    def mock_fetcher(instrument: str) -> list[str]:
        return SAMPLE_HEADLINES.get(instrument, [])

    from apex_quant.config import AppConfig
    from apex_quant.risk.types import Direction, Signal

    cfg = AppConfig()
    cfg.sentiment.enabled = True

    print("=" * 70)
    print("DeepSeek Sentiment Filter — Mock Runner")
    print("=" * 70)
    print()

    for instr, headlines in SAMPLE_HEADLINES.items():
        sig = Signal(
            instrument=instr,
            direction=Direction.LONG,
            probability=0.72,
            reward_risk=1.8,
            confidence=0.65,
        )
        result = apply_deepseek_sentiment(sig, instr, mock_fetcher, cfg=cfg)
        print(f"  Instrument : {instr}")
        print(f"  Direction  : {result.direction.value.upper()}")
        print(f"  Probability: {result.probability:.2f}")
        print(f"  Confidence : {result.confidence:.2f}")
        print(f"  Rationale  : {result.rationale[:120]}...")
        print()