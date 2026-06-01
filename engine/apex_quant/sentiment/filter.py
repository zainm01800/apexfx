"""Sentiment filter - FILTER / VETO ONLY.

The single rule, enforced structurally: sentiment may SHRINK or CANCEL a signal
it contradicts; it can NEVER create a position (a FLAT signal stays FLAT) and
NEVER enlarges one (agreement leaves the signal untouched - no boosting). So the
worst sentiment can do is move you toward cash, never further into the market.
"""

from __future__ import annotations

import numpy as np

from apex_quant.config import SentimentConfig, get_config
from apex_quant.risk.types import Direction, Signal
from apex_quant.sentiment.provider import SentimentScore


class SentimentFilter:
    def __init__(self, cfg: SentimentConfig | None = None):
        self.cfg = cfg or get_config().sentiment

    def apply(self, signal: Signal, sentiment: SentimentScore | None) -> tuple[Signal, str]:
        """Return (possibly modified signal, explanation)."""
        if sentiment is None or signal.direction == Direction.FLAT:
            return signal, "no sentiment applied"

        dir_sign = 1.0 if signal.direction == Direction.LONG else -1.0
        # contradiction in [-1,1]: positive => sentiment opposes the trade
        contradiction = (-dir_sign * sentiment.score) * float(np.clip(sentiment.confidence, 0, 1))

        if contradiction >= self.cfg.veto_threshold:
            vetoed = signal.model_copy(update={
                "direction": Direction.FLAT, "probability": 0.5, "confidence": 0.0,
                "rationale": signal.rationale + f" | VETOED by sentiment ({sentiment.score:+.2f})",
            })
            return vetoed, f"vetoed (contradiction {contradiction:.2f} >= {self.cfg.veto_threshold})"

        if contradiction >= self.cfg.damp_threshold:
            frac = (contradiction - self.cfg.damp_threshold) / max(
                1e-9, self.cfg.veto_threshold - self.cfg.damp_threshold
            )
            # shrink probability toward the breakeven (0.5): smaller Kelly bet
            new_p = signal.probability - frac * (signal.probability - 0.5)
            damped = signal.model_copy(update={
                "probability": float(new_p),
                "confidence": float(signal.confidence * (1.0 - frac)),
                "rationale": signal.rationale + f" | sentiment-damped x{1-frac:.2f} ({sentiment.score:+.2f})",
            })
            return damped, f"damped (contradiction {contradiction:.2f})"

        return signal, f"sentiment aligned/neutral ({sentiment.score:+.2f}); no change"
