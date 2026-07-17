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

Persistence integrity (2026-07-17 audit, D-H4): writes are atomic (tmp file +
``os.replace`` — a crash mid-write can never tear the JSON), loads treat a
corrupt file as *missing* (logged, start empty) rather than raising
permanently, and :meth:`locked` serialises the whole load->record->save
sequence under an fcntl file lock so concurrent gate runs cannot undercount
trials by losing each other's updates.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from pathlib import Path

from apex_quant.data._filelock import file_lock

logger = logging.getLogger(__name__)


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

    def _write(self, p: Path) -> None:
        """Atomic write (tmp + os.replace); caller holds any lock."""
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f"{p.name}.tmp{os.getpid()}")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._trials, fh, indent=2)
            os.replace(tmp, p)
        finally:
            if tmp.exists():
                tmp.unlink()

    def save(self, path: str | Path) -> Path:
        """Persist the ledger to JSON, atomically, under an exclusive lock."""
        p = Path(path)
        with file_lock(p):
            self._write(p)
        return p

    @classmethod
    def load(cls, path: str | Path) -> "TrialLedger":
        """Load a ledger from JSON. Missing OR CORRUPT file => empty ledger.

        A torn/invalid file is logged and treated as missing — the ledger is
        an append-only counter, so rebuilding it by re-recording is always
        safe, and a crash must never brick every future gate run (D-H4).
        """
        led = cls()
        p = Path(path)
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    led._trials = data
                else:
                    raise ValueError(f"expected JSON object, got {type(data).__name__}")
            except Exception as exc:  # JSONDecodeError, UnicodeDecodeError, OSError, ValueError
                logger.warning(
                    "TrialLedger.load: %s unreadable (%s: %s) — starting empty",
                    p, type(exc).__name__, exc,
                )
        return led

    @classmethod
    @contextmanager
    def locked(cls, path: str | Path):
        """``with TrialLedger.locked(path) as led:`` — load, mutate, save under
        one exclusive file lock.

        This is the race-free way to record trials from scripts that run
        concurrently: the whole load->record->save is serialised, so two gate
        runs can no longer read the same file, each add their configs, and
        have the last writer silently drop the other's trials (D-H4).
        """
        p = Path(path)
        with file_lock(p):
            led = cls.load(p)
            yield led
            led._write(p)

    def __len__(self) -> int:
        return self.n_trials

    def __repr__(self) -> str:
        return f"TrialLedger(n_trials={self.n_trials}, n_with_sharpe={len(self.sharpes)})"
