"""Specifications for Book V24 (Noise-Band Momentum) and Book V30 (ATR Breakout)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from math import isfinite


SCHEMA_VERSION = 1
INITIAL_EQUITY_GBP = 100_000.0


@dataclass(frozen=True, slots=True)
class Profile:
    daily: float
    maximum: float
    original_maximum: float
    risk: float
    vol_target: float
    gross: float
    utilization: float = 0.90
    buffer: float = 0.25
    min_notional_gbp: float = 1000.0


PROFILES: dict[str, Profile] = {
    "higher_5_12": Profile(
        daily=0.05,
        maximum=0.12,
        original_maximum=0.10,
        risk=0.01,
        vol_target=0.02,
        gross=4.0,
        utilization=0.90,
        buffer=0.25,
        min_notional_gbp=1000.0,
    ),
    "lower_3_7": Profile(
        daily=0.03,
        maximum=0.07,
        original_maximum=0.06,
        risk=0.0075,
        vol_target=0.015,
        gross=3.0,
        utilization=0.90,
        buffer=0.25,
        min_notional_gbp=1000.0,
    ),
}


@dataclass(frozen=True, slots=True)
class BookSpec:
    book_id: str
    label: str
    strategy_id: str
    strategy_variant: str
    profile: str
    runtime_id: str
    daily_loss_fraction: float
    maximum_loss_fraction: float
    original_maximum_loss_fraction: float
    per_trade_risk_fraction: float
    volatility_target_fraction: float
    max_gross_exposure_x: float
    sizing_utilization: float = 0.90
    internal_buffer_fraction: float = 0.25
    min_notional_gbp: float = 1000.0
    initial_equity_gbp: float = INITIAL_EQUITY_GBP
    fee_bps_each_side: float = 1.0
    stop_slippage_bps: float = 1.0
    evaluation_interval_minutes: int = 30
    evaluation_start_offset: int = 30  # 10:00 NY
    evaluation_end_offset: int = 360    # 15:30 NY for V24, 375 for V30
    mandatory_flat_offset: int = 389    # 15:59 NY
    symbol: str = "SPY"

    def __post_init__(self) -> None:
        if self.book_id not in {"v24", "v30"}:
            raise ValueError("book_id must be v24 or v30")
        for key, value in asdict(self).items():
            if isinstance(value, float) and not isfinite(value):
                raise ValueError(f"non-finite BookSpec field: {key}")

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def spec_sha256(self) -> str:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "book_id": self.book_id,
            "strategy_id": self.strategy_id,
            "strategy_variant": self.strategy_variant,
            "spec": self.to_dict(),
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return sha256(raw).hexdigest()


BOOKS: dict[str, BookSpec] = {
    "v24": BookSpec(
        book_id="v24",
        label="Book V24 · SPY Noise-Band Momentum",
        strategy_id="spy_noise_band_momentum_v24",
        strategy_variant="noise_band",
        profile="higher_5_12_static",
        runtime_id="__apex_book_v24_forward_paper_runtime__",
        daily_loss_fraction=0.05,
        maximum_loss_fraction=0.12,
        original_maximum_loss_fraction=0.10,
        per_trade_risk_fraction=0.01,
        volatility_target_fraction=0.02,
        max_gross_exposure_x=4.0,
        evaluation_interval_minutes=30,
        evaluation_start_offset=30,
        evaluation_end_offset=360,
    ),
    "v30": BookSpec(
        book_id="v30",
        label="Book V30 · SPY ATR Breakout",
        strategy_id="spy_atr_intraday_v30",
        strategy_variant="atr_open_stop",
        profile="higher_5_12_static",
        runtime_id="__apex_book_v30_forward_paper_runtime__",
        daily_loss_fraction=0.05,
        maximum_loss_fraction=0.12,
        original_maximum_loss_fraction=0.10,
        per_trade_risk_fraction=0.01,
        volatility_target_fraction=0.02,
        max_gross_exposure_x=4.0,
        evaluation_interval_minutes=15,
        evaluation_start_offset=30,
        evaluation_end_offset=375,
    ),
}
