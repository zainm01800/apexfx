"""Trial ledger — the honest denominator for multiple-testing correction.

Selection bias is the number-one way a backtest lies to you: the more
configurations you try, the better the luckiest one looks by chance alone. The
Deflated Sharpe Ratio only corrects for this if it knows the *true* number of
trials, N. In practice a single ``run_validation`` call sees a 3-config grid,
while a research campaign quietly evaluates dozens of timeframe / lookback /
instrument combinations. Deflating by 3 when you really tried 60 manufactures
significance.

``TrialLedger`` accumulates every distinct configuration evaluated (deduped by a
canonical key) and its in-sample Sharpe, and persists across runs. Feed
``ledger.n_trials`` into :func:`deflated_sharpe_ratio` (or ``run_validation``'s
``n_trials`` argument) so the deflation reflects reality.
"""

from __future__ import annotations

import json
from pathlib import Path


class TrialLedger:
    """Deduplicating record of every configuration tried during research.

    Configs are keyed by their canonical JSON (sorted keys), so ``{"a":1,"b":2}``
    and ``{"b":2,"a":1}`` count once. Re-recording a config updates its stored
    Sharpe but never inflates the count.
    """

    def __init__(self) -> None:
        self._trials: dict[str, float | None] = {}

    @staticmethod
    def _key(config: dict) -> str:
        return json.dumps(config, sort_keys=True, default=str)

    def record(self, config: dict, in_sample_sharpe: float | None = None) -> None:
        """Record one evaluated configuration (idempotent per distinct config)."""
        key = self._key(config)
        if in_sample_sharpe is None and key in self._trials:
            return  # keep any Sharpe we already have
        self._trials[key] = None if in_sample_sharpe is None else float(in_sample_sharpe)

    def record_many(self, configs, sharpes=None) -> None:
        """Record a batch of configs, with optional parallel Sharpes."""
        configs = list(configs)
        sharpes = list(sharpes) if sharpes is not None else [None] * len(configs)
        for cfg, sr in zip(configs, sharpes):
            self.record(cfg, sr)

    @property
    def n_trials(self) -> int:
        """Number of distinct configurations evaluated — the honest N for DSR."""
        return len(self._trials)

    @property
    def sharpes(self) -> list[float]:
        """In-sample Sharpes recorded so far (configs with a known Sharpe only)."""
        return [v for v in self._trials.values() if v is not None]

    def save(self, path: str | Path) -> Path:
        """Persist the ledger to JSON so the count survives across sessions."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(self._trials, fh, indent=2)
        return p

    @classmethod
    def load(cls, path: str | Path) -> "TrialLedger":
        """Load a ledger from JSON. Missing file => empty ledger."""
        led = cls()
        p = Path(path)
        if p.exists():
            with open(p, "r", encoding="utf-8") as fh:
                led._trials = json.load(fh) or {}
        return led

    def __len__(self) -> int:
        return self.n_trials

    def __repr__(self) -> str:
        return f"TrialLedger(n_trials={self.n_trials}, n_with_sharpe={len(self.sharpes)})"
