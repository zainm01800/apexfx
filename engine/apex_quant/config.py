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


# -- Section models --------------------------------------------------------------
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
    instruments: list[str] = Field(default_factory=list)   # forex universe
    equities: list[str] = Field(default_factory=list)       # equity / ETF universe
    crypto: list[str] = Field(default_factory=list)          # crypto universe
    session: SessionConfig = Field(default_factory=SessionConfig)
    quality: QualityConfig = Field(default_factory=QualityConfig)
    live_timeframes: list[str] | None = None


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
    target_portfolio_vol: float = 0.0623
    kelly_fraction: float = 0.0
    max_risk_per_trade: float = 0.0085
    max_total_exposure: float = 3.0
    max_correlated_exposure: float = 1.5
    correlation_threshold: float = 0.60
    atr_window: int = 14
    atr_stop_mult: float = 2.0
    drawdown_breaker: float = 0.20
    drawdown_reducing_limit: float = 0.10
    min_position: float = 0.0
    max_concurrent_trades: int = 10
    max_swing_slots: int = 10
    max_trend_slots: int = 10
    max_tom_slots: int = 5
    max_crypto_xs_slots: int = 4
    max_portfolio_risk: float = 0.035


class BacktestConfig(BaseModel):
    initial_equity: float = 100_000
    # Legacy forex cost defaults — kept for back-compat. The authoritative,
    # per-asset cost model now lives in AssetClassesConfig (see asset_classes).
    spread_pips: float = 1.0
    pip_size_default: float = 0.0001
    commission_per_trade: float = 0.0
    slippage_bps: float = 0.5


# Crypto bases used to classify a "BASE/USD" id as crypto rather than forex when
# it isn't explicitly listed in the configured universe.
CRYPTO_BASES = {
    "BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "BNB", "LTC",
    "DOT", "AVAX", "LINK", "MATIC", "BCH", "ATOM", "ETC", "XLM",
    "ARB", "SUI",
}

# Timeframe string -> bar length in minutes, for bars-per-year annualization
# (AppConfig.bars_per_year). Daily/weekly are handled separately.
_TF_MINUTES = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "8h": 480, "12h": 720,
}
_WEEKS_PER_YEAR = 52.14


class AssetClassConfig(BaseModel):
    """Trading mechanics for one asset class. Forex quotes spreads in *pips*;
    equities and crypto quote them in *basis points of price* — using a forex
    cost model on a $200 stock would manufacture a fake edge. Crypto trades 365
    days/yr; forex and (cash) equities ~252.

    Forex only: ``pair_rt_cost_pips`` / ``pair_tf_rt_cost_pips`` carry measured
    per-pair realized round-trip costs (pips) and ``cross_rt_cost_pips`` the
    fallback for unlisted crosses. An override REPLACES ``spread_pips`` +
    ``slippage_bps`` for that pair — it already is the full round-trip cost,
    applied half per fill (slippage resolves to 0.0)."""
    annualization: int = 252
    cost_model: Literal["pips", "bps"] = "pips"
    spread_pips: float = 1.0          # pips model only
    pip_size: float = 0.0001          # pips model only (JPY pairs override in code)
    spread_bps: float = 2.0           # bps model only
    slippage_bps: float = 0.5
    commission_per_trade: float = 0.0
    cross_rt_cost_pips: float | None = None   # pips model: unlisted-cross RT default
    pair_rt_cost_pips: dict[str, float] = Field(default_factory=dict)
    pair_tf_rt_cost_pips: dict[str, dict[str, float]] = Field(default_factory=dict)


class AssetClassesConfig(BaseModel):
    forex: AssetClassConfig = Field(default_factory=lambda: AssetClassConfig(
        annualization=252, cost_model="pips", spread_pips=1.0, pip_size=0.0001,
        slippage_bps=0.5, commission_per_trade=0.0))
    equity: AssetClassConfig = Field(default_factory=lambda: AssetClassConfig(
        annualization=252, cost_model="bps", spread_bps=2.0,
        slippage_bps=1.0, commission_per_trade=0.0))
    crypto: AssetClassConfig = Field(default_factory=lambda: AssetClassConfig(
        annualization=365, cost_model="bps", spread_bps=5.0,
        slippage_bps=2.0, commission_per_trade=0.0))


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


class SentimentConfig(BaseModel):
    enabled: bool = False          # off by default; sentiment is filter-only, never a trigger
    veto_threshold: float = 0.60   # contradiction strength above this -> veto to flat
    damp_threshold: float = 0.30   # contradiction strength above this -> shrink the bet
    app_url: str = ""              # base URL of the APEX app exposing /api/news + /api/ai
    news_lookback_days: int = 5
    max_age_days: int = 7          # don't apply "today's" news to decisions older than this


class AiConfig(BaseModel):
    """Phase 3 narrow-AI layer. The LLM proposes hypotheses to VALIDATE - it never
    sets signals, sizing, or confidence, and nothing it outputs is ever an order."""
    enabled: bool = False
    app_url: str = ""              # APEX app base URL exposing /api/ai (+ /api/news)
    n_hypotheses: int = 4
    max_tokens: int = 1200
    temperature: float = 0.5
    use_news: bool = True          # ground hypotheses in recent headlines when available
    use_local_llm: bool = False
    local_llm_url: str = ""
    local_llm_model: str = ""
    local_llm_key: str = ""
    # DeepSeek direct API (preferred over app proxy when key is set)
    deepseek_api_key: str = ""     # set via APEX_AI__DEEPSEEK_API_KEY env var or config.yaml
    deepseek_model: str = "deepseek-chat"   # or deepseek-reasoner for R1
    deepseek_base_url: str = "https://api.deepseek.com"
    # Gemini direct API
    gemini_api_key: str = ""       # set via APEX_AI__GEMINI_API_KEY env var or config.yaml


class Mt4Config(BaseModel):
    """MT4 bridge connection settings."""
    common_dir: str = ""
    default_volume: float = 0.10
    suffix: str = ""                # Ticker suffix (e.g. "-g" or "-o") required by broker
    # Broker SERVER clock vs UTC, in hours. MT4's OrderOpenTime()/OrderCloseTime()
    # return the broker's server time but store it as though it were a unix epoch, so
    # every comparison against a real UTC timestamp is skewed by this much. Most FX
    # brokers run UTC+2 in winter and UTC+3 under DST — so THIS VALUE CHANGES TWICE A
    # YEAR. It is configured rather than auto-detected because it cannot be inferred
    # reliably: the only observable (newest event minus now) under-reads by however
    # old the newest trade is. 0.0 = take MT4 timestamps at face value.
    server_utc_offset_hours: float = 0.0


class ZmqConfig(BaseModel):
    """ZeroMQ TCP push-pull bridge settings."""
    enabled: bool = False          # when True, ZMQBridge is used instead of file polling
    host: str = "127.0.0.1"
    port: int = 9091               # PUSH channel: engine -> EA (orders)
    ack_port: int = 9092           # PULL channel: EA -> engine (acks / fills / heartbeats)
    linger_ms: int = 0             # socket linger on close (0 = drop unsent messages)
    send_timeout_ms: int = 1000    # max ms to block on send before giving up
    recv_timeout_ms: int = 50      # max ms to block draining one ack message in poll()
    heartbeat_timeout_s: float = 30.0  # EA considered dead if no heartbeat within this


class ExecutionConfig(BaseModel):
    """Live execution settings. Off by default — paper/live only when enabled."""
    enabled: bool = False
    # 2026-07-17: "ibkr" routes the live FX book to the IBKR paper account
    # (IBKRLiveBridge over IBKRExecutor); rollback = provider "mt4".
    provider: Literal["mt4", "mock", "zmq", "ibkr"] = "mt4"
    mt4: Mt4Config = Field(default_factory=Mt4Config)
    zmq: ZmqConfig = Field(default_factory=ZmqConfig)
    live_min_position: float = 15000.0
    # 2026-07-17: new trades are exit-managed by TradeManager (backtest parity);
    # false → legacy inline TMS + 15-min invalidation scans for everything.
    managed_exits: bool = True
    # 2026-07-17: 15m/1h only time pullback entries into the 1d direction;
    # false → legacy standalone intraday directional signals.
    htf_direction_only: bool = True
    # 2026-07-17 (audit A-C1): LLM structural veto kill-switch. Default OFF —
    # the research verdict was DROP (lessons invent thresholds from n=1 and can
    # flatten any signal). The veto function stays intact but only runs when
    # this is explicitly switched on.
    llm_structural_veto: bool = False
    # 2026-07-17 (audit L9/L10): freshness tolerance for the EA-written bridge
    # files (mt4_positions.json / mt4_account.json), rewritten every ~500 ms.
    # Older files mean a dead EA or a wrong common_dir — dispatch and TMS are
    # skipped (fail closed).
    mt4_max_file_age_s: float = 5.0
    # 2026-07-17 (fills handshake): poll budget for the EA's ack_<id>.json
    # after dispatching an order. No ack → the trade is NOT stamped filled_at.
    mt4_ack_timeout_s: float = 10.0


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
    sentiment: SentimentConfig = Field(default_factory=SentimentConfig)
    ai: AiConfig = Field(default_factory=AiConfig)
    asset_classes: AssetClassesConfig = Field(default_factory=AssetClassesConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)

    @property
    def store_path(self) -> Path:
        """Absolute path to the local historical store."""
        p = Path(self.data.store_dir)
        return p if p.is_absolute() else ENGINE_ROOT / p

    @property
    def universe(self) -> list[str]:
        """Every configured instrument across all asset classes (forex first)."""
        return list(self.data.instruments) + list(self.data.equities) + list(self.data.crypto)

    def asset_class_of(self, instrument: str) -> str:
        """Classify an instrument id as 'forex' | 'equity' | 'crypto'. Explicit
        membership in a configured universe wins; otherwise fall back to a
        symbol-shape heuristic (BASE/USD with a known crypto base -> crypto, any
        other slash -> forex, no slash -> equity)."""
        if instrument in self.data.crypto:
            return "crypto"
        if instrument in self.data.equities:
            return "equity"
        if instrument in self.data.instruments:
            return "forex"
        if "/" in instrument:
            base = instrument.split("/", 1)[0].upper()
            return "crypto" if base in CRYPTO_BASES else "forex"
        return "equity"

    def mechanics_for(self, instrument: str) -> AssetClassConfig:
        """Resolve the trading-mechanics (cost model + annualization) for an
        instrument from its asset class."""
        return getattr(self.asset_classes, self.asset_class_of(instrument))

    def forex_cost_components(self, instrument: str, timeframe: str | None = None) -> tuple[float, float]:
        """Effective ``(spread_pips, slippage_bps)`` for a forex instrument.

        Per-pair overrides (measured realized round-trip costs, config v5) take
        precedence — pair×timeframe, then pair, then the unlisted-cross default
        (a forex pair with no USD leg). An override IS the full round-trip cost
        in pips, so it is returned with slippage 0.0 and the backtest applies
        half per fill. Anything else falls back to the class spread/slippage.
        """
        m = self.mechanics_for(instrument)
        if self.asset_class_of(instrument) == "forex":
            fx = self.asset_classes.forex
            tf = (timeframe or "").lower()
            rt = fx.pair_tf_rt_cost_pips.get(instrument, {}).get(tf)
            if rt is None:
                rt = fx.pair_rt_cost_pips.get(instrument)
            if rt is None and "USD" not in instrument.upper():
                rt = fx.cross_rt_cost_pips
            if rt is not None:
                return rt, 0.0
        return m.spread_pips, m.slippage_bps

    def bars_per_year(self, instrument: str, timeframe: str | None = None) -> float:
        """Annualization factor (bars per year) for per-bar performance metrics
        (Sharpe / ann_return / Calmar / Sortino).

        ``AssetClassConfig.annualization`` is the DAILY convention (252 forex /
        equity, 365 crypto) and stays the fallback when the timeframe is unknown
        — hardcoding it for every bar size understated a 1h Sharpe by ~sqrt(24)
        (audit E5). Intraday factors derive from the session conventions:

          * forex:  ~5x24h week (SessionConfig: Sun 22:00 -> Fri 22:00 UTC)
                    => 1h = 24 x 5 x 52.14 ~ 6257, 15m = 4x that
          * equity: ~6.5h cash session x 252 days
          * crypto: 24/7 => 1h = 24 x 365

        Daily and weekly keep the conventional counts (252 / 52; 365, 365/7 for
        crypto) rather than the session-derived 260.7 so daily numbers stay
        comparable with the existing record.
        """
        tf = str(timeframe or "1d").lower().strip()
        ac = self.asset_class_of(instrument)
        daily = float(self.mechanics_for(instrument).annualization)
        if tf in ("1d", "d", "1day"):
            return daily
        if tf in ("1w", "w", "1week"):
            return 52.0 if ac != "crypto" else daily / 7.0
        minutes = _TF_MINUTES.get(tf)
        if minutes is None:
            return daily  # unknown timeframe -> daily convention (old behaviour)
        if ac == "crypto":
            bars_per_day, days_per_year = 24.0 * 60.0 / minutes, daily
        elif ac == "forex":
            bars_per_day, days_per_year = 24.0 * 60.0 / minutes, 5.0 * _WEEKS_PER_YEAR
        else:  # equity cash session ~6.5h
            bars_per_day, days_per_year = 6.5 * 60.0 / minutes, daily
        return bars_per_day * days_per_year


# -- Loading + env overrides ---------------------------------------------------
def _apply_env_overrides(raw: dict) -> dict:
    """Apply APEX_-prefixed env overrides for top-level scalars.

    Nested overrides use double underscore, e.g. ``APEX_RISK__KELLY_FRACTION=0.1``.
    Kept deliberately small - config.yaml is the source of truth.
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


def _deep_merge(base: dict, over: dict) -> dict:
    """Recursively merge ``over`` onto ``base`` (``over`` wins on scalars/lists)."""
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load + validate config from YAML, applying APEX_ env overrides.

    A profile may declare ``_extends: <file>`` to inherit everything from another
    config and override only what it changes. Profiles were previously full copies,
    which silently rotted the moment the base config changed (e.g. growing the scan
    universe made config.prop.yaml differ in `data` as well as `risk`). Inheriting
    means a profile states ONLY its deltas and can never drift.
    """
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    raw: dict = {}
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        parent = raw.pop("_extends", None)
        if parent:
            parent_path = Path(parent)
            if not parent_path.is_absolute():
                parent_path = cfg_path.parent / parent_path
            with open(parent_path, "r", encoding="utf-8") as fh:
                raw = _deep_merge(yaml.safe_load(fh) or {}, raw)
    raw = _apply_env_overrides(raw)
    return AppConfig.model_validate(raw)


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Process-wide cached config singleton."""
    return load_config()


# -- Reproducibility -----------------------------------------------------------
def set_global_seeds(seed: int | None = None) -> int:
    """Pin global RNG state for deterministic pipelines. Returns the seed used."""
    s = seed if seed is not None else get_config().seed
    np.random.seed(s)
    os.environ["PYTHONHASHSEED"] = str(s)
    return s


def get_rng(seed: int | None = None) -> np.random.Generator:
    """A fresh, isolated numpy Generator - preferred over the global RNG."""
    s = seed if seed is not None else get_config().seed
    return np.random.default_rng(s)
