"""Immutable specifications for the two V14 forward-paper accounts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from math import isfinite


SCHEMA_VERSION = 1
INITIAL_EQUITY_GBP = 100_000.0
STRATEGY_ID = "v14_regime_switch_5day"
SOURCE_MANIFEST_SHA256 = (
    "12e75d4b7126c74b7d56f551141fb582d03d1268afd4e61bbce8764acb319b61"
)
SOURCE_AMENDMENT_SHA256 = (
    "c64527bff8e15a246f963273bf65de4e9342dbe50c98f7082a487cedd9a14da7"
)

REGIME_SYMBOL = "SPY"
SECTOR_SYMBOLS = ("XLK", "XLE", "XLV", "XLI", "XLF", "XLP", "XLU")
SYMBOLS = (REGIME_SYMBOL, *SECTOR_SYMBOLS)


@dataclass(frozen=True, slots=True)
class BookSpec:
    book_id: str
    label: str
    profile: str
    runtime_id: str
    daily_loss_fraction: float
    maximum_loss_fraction: float
    per_trade_risk_fraction: float
    aggregate_risk_fraction: float
    gross_fraction: float
    single_name_fraction: float
    initial_equity_gbp: float = INITIAL_EQUITY_GBP
    maximum_loss_mode: str = "static"
    internal_buffer_fraction: float = 0.25
    entry_utilization: float = 0.80
    holding_sessions: int = 5
    stop_atr_multiple: float = 1.5
    fee_bps_each_side: float = 5.0
    stop_slippage_bps: float = 5.0
    annual_holding_rate: float = 0.0
    annual_short_borrow_rate: float = 0.02
    maximum_positions: int = 4
    vix_stress_threshold: float = 30.0
    maximum_vix_age_days: int = 6
    maximum_fx_age_days: int = 6
    rsi_period: int = 2
    rsi_entry_threshold: float = 10.0
    sma_window: int = 200
    atr_window: int = 20

    def __post_init__(self) -> None:
        if self.book_id not in {"v6", "v10"}:
            raise ValueError("book_id must be v6 or v10")
        if self.maximum_loss_mode != "static":
            raise ValueError("V14 forward books use the frozen static loss mode")
        numeric = asdict(self)
        for key, value in numeric.items():
            if isinstance(value, float) and not isfinite(value):
                raise ValueError(f"non-finite BookSpec field: {key}")

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def spec_sha256(self) -> str:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "strategy_id": STRATEGY_ID,
            "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
            "source_amendment_sha256": SOURCE_AMENDMENT_SHA256,
            "spec": self.to_dict(),
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return sha256(raw).hexdigest()


BOOKS: dict[str, BookSpec] = {
    "v6": BookSpec(
        book_id="v6",
        label="Book V6 · Static 6%",
        profile="strict_3_6_static",
        runtime_id="__apex_book_v6_forward_paper_runtime__",
        daily_loss_fraction=0.03,
        maximum_loss_fraction=0.06,
        per_trade_risk_fraction=0.0075,
        aggregate_risk_fraction=0.015,
        gross_fraction=1.50,
        single_name_fraction=0.50,
    ),
    "v10": BookSpec(
        book_id="v10",
        label="Book V10 · Static 10%",
        profile="standard_5_10_static",
        runtime_id="__apex_book_v10_forward_paper_runtime__",
        daily_loss_fraction=0.05,
        maximum_loss_fraction=0.10,
        per_trade_risk_fraction=0.0085,
        aggregate_risk_fraction=0.0255,
        gross_fraction=2.00,
        single_name_fraction=0.75,
    ),
}


def book_spec(book_id: str) -> BookSpec:
    try:
        return BOOKS[str(book_id).lower()]
    except KeyError as exc:
        raise ValueError("book must be v6 or v10") from exc
