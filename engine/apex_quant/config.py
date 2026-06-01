"""Typed, versioned configuration + reproducibility control.

Loads ``config.yaml`` into validated Pydantic models so every module reads
strongly-typed parameters and NEVER hard-codes a magic number. Environment
variables prefixed ``APEX_`` override the YAML (e.g. ``APEX_SEED=7``), which is
handy for CI and for sweeping a parameter inside a CPCV fold.

Reproducibility: ``set_global_seeds()`` pins numpy's global RNG. Modules that
need their own generator should call ``get_rng()`` rather than touching the
global state, so pipelines stay deterministic.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

import numpy as np
import yaml
from pydantic import BaseModel, Field

ENGINE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ENGINE_ROOT / "config.yaml"


# ── Section models ──────────────────────────────────────────────────────────────
class SessionConfig(BaseModel):
    week_open_dow: int = 6
    week_open_hour_utc: int = 22
    week_close_dow: int = 4
    week_close_hour_utc: int = 22


class QualityConfig(BaseModel):
    max_gap_bars: int = 1
    duplicate_policy: Literal["keep_last", "keep_first"] = "keep_last"


class DataConfig(BaseModel):
    provider: str = "yahoo"
    timeframe: str = "1d"
    store_dir: str = "data_store"
    instruments: list[str] = Field(default_factory=list)
    session: SessionConfig = Field(default_factory=SessionConfig)
    quality: QualityConfig = Field(default_factory=QualityConfig)


class FeaturesConfig(BaseModel):
    momentum_lookbacks: list[int] = [21, 63, 126, 252]
    vol_windows: list[int] = [21, 63]
    trend_ma: int = 200
    trend_slope_window: int = 21
    carry_enabled: bool = False
    cot_enabled: bool = False


class GarchConfig(BaseModel):
    p: int = 1
    q: int = 1
    mean: str = "Zero"
    dist: str = "t"
    horizon: int = 5
    min_obs: int = 250
    rescale_factor: float = 100.0


class VolatilityConfig(BaseModel):
    realized_windows: list[int] = [21, 63]
    annualization_factor: int = 252
    garch: GarchConfig = Field(default_factory=GarchConfig)


class HmmConfig(BaseModel):
    n_states: int = 3
    covariance_type: Literal["full", "diag", "tied", "spherical"] = "full"
    n_iter: int = 200
    min_obs: int = 250


class RuleBasedConfig(BaseModel):
    ma_window: int = 200
    slope_window: int = 21
    vol_percentile_window: int = 252
    vol_high_pct: float = 0.70
    vol_low_pct: float = 0.30
    ranging_slope_eps: float = 0.0005


class RegimeConfig(BaseModel):
    hmm: HmmConfig = Field(default_factory=HmmConfig)
    rule_based: RuleBasedConfig = Field(default_factory=RuleBasedConfig)


class RiskConfig(BaseModel):
    target_portfolio_vol: float = 0.10
    kelly_fraction: float = 0.25
    max_risk_per_trade: float = 0.01
    max_total_exposure: float = 3.0
    max_correlated_exposure: float = 1.5
    correlation_threshold: float = 0.60
    atr_window: int = 14
    atr_stop_mult: float = 2.0
    drawdown_breaker: float = 0.20
    min_position: float = 0.0


class BacktestConfig(BaseModel):
    initial_equity: float = 100_000
    spread_pips: float = 1.0
    pip_size_default: float = 0.0001
    commission_per_trade: float = 0.0
    slippage_bps: float = 0.5


class CpcvConfig(BaseModel):
    n_groups: int = 6
    n_test_groups: int = 2
    embargo_pct: float = 0.01


class DsrConfig(BaseModel):
    benchmark_sharpe: float = 0.0


class PboConfig(BaseModel):
    n_splits: int = 16


class ValidationConfig(BaseModel):
    cpcv: CpcvConfig = Field(default_factory=CpcvConfig)
    dsr: DsrConfig = Field(default_factory=DsrConfig)
    pbo: PboConfig = Field(default_factory=PboConfig)


class AppConfig(BaseModel):
    version: int = 1
    seed: int = 42
    data: DataConfig = Field(default_factory=DataConfig)
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)
    volatility: VolatilityConfig = Field(default_factory=VolatilityConfig)
    regime: RegimeConfig = Field(default_factory=RegimeConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)

    @property
    def store_path(self) -> Path:
        """Absolute path to the local historical store."""
        p = Path(self.data.store_dir)
        return p if p.is_absolute() else ENGINE_ROOT / p


# ── Loading + env overrides ───────────────────────────────────────────────────
def _apply_env_overrides(raw: dict) -> dict:
    """Apply APEX_-prefixed env overrides for top-level scalars.

    Nested overrides use double underscore, e.g. ``APEX_RISK__KELLY_FRACTION=0.1``.
    Kept deliberately small — config.yaml is the source of truth.
    """
    for key, val in os.environ.items():
        if not key.startswith("APEX_"):
            continue
        path = key[len("APEX_") :].lower().split("__")
        node = raw
        for part in path[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                break
        else:
            leaf = path[-1]
            # Coerce to the existing type when one is present.
            existing = node.get(leaf) if isinstance(node, dict) else None
            node[leaf] = _coerce_like(val, existing)
    return raw


def _coerce_like(val: str, like) -> object:
    if isinstance(like, bool):
        return val.lower() in ("1", "true", "yes", "on")
    if isinstance(like, int):
        try:
            return int(val)
        except ValueError:
            return val
    if isinstance(like, float):
        try:
            return float(val)
        except ValueError:
            return val
    return val


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load + validate config from YAML, applying APEX_ env overrides."""
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    raw: dict = {}
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    raw = _apply_env_overrides(raw)
    return AppConfig.model_validate(raw)


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Process-wide cached config singleton."""
    return load_config()


# ── Reproducibility ───────────────────────────────────────────────────────────
def set_global_seeds(seed: int | None = None) -> int:
    """Pin global RNG state for deterministic pipelines. Returns the seed used."""
    s = seed if seed is not None else get_config().seed
    np.random.seed(s)
    os.environ["PYTHONHASHSEED"] = str(s)
    return s


def get_rng(seed: int | None = None) -> np.random.Generator:
    """A fresh, isolated numpy Generator — preferred over the global RNG."""
    s = seed if seed is not None else get_config().seed
    return np.random.default_rng(s)
