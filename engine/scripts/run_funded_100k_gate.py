"""Run the frozen 2026-09-03 Funded-100K research gate.

This is deliberately an isolated research runner.  It never writes the shared
``trial_ledger.json`` or any Book A/B/C/R/paper state.  Its only default writes are
the dedicated funded trial ledger and the deterministic JSON/Markdown artifacts
declared below.

The protocol is intentionally allowed to return ``NO_FUNDED_STRATEGY``.  Daily
Yahoo OHLC can provide a conservative co-extreme equity bound, but it cannot prove
minute-level firm-rule compliance, exact broker fills, or account-currency parity.
Consequently no result from this runner can be labelled funded-ready; the highest
possible status is ``PROVISIONAL_PAPER_ONLY``.

Full preregistered run (100,000 paths and 100 order permutations)::

    cd engine
    .venv-mac/bin/python scripts/run_funded_100k_gate.py

Small engineering smoke (the output remains explicitly non-binding)::

    .venv-mac/bin/python scripts/run_funded_100k_gate.py \
        --n-paths 50 --order-permutations 3 --skip-cpcv
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd


ENGINE_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = ENGINE_DIR.parent
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

from apex_quant.backtest.portfolio import PortfolioBacktester, PortfolioResult  # noqa: E402
from apex_quant.backtest.result import compute_metrics  # noqa: E402
from apex_quant.config import AppConfig, get_config  # noqa: E402
from apex_quant.data import ParquetStore, PointInTimeAccessor, clean  # noqa: E402
from apex_quant.risk.trade_manager import TradeManager  # noqa: E402
from apex_quant.validation.funded_simulator import (  # noqa: E402
    DayRecord,
    FundedRules,
    chunked_synchronized_funded_bootstrap,
    firm_session_key,
    replay_funded_rules,
)
from apex_quant.validation.metrics import (  # noqa: E402
    deflated_sharpe_ratio,
    probability_of_backtest_overfitting,
    sharpe_ratio,
)
from apex_quant.validation.cpcv import cpcv_splits  # noqa: E402
from apex_quant.validation.trials import TrialLedger  # noqa: E402

from run_book_c_deep_audit import PARAMS, TrendBook  # noqa: E402
from run_portfolio_gate import HORIZON, MIN_BARS, WARMUP  # noqa: E402
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402


SCHEMA_VERSION = 1
PROTOCOL_DATE = "2026-09-03"
PROTOCOL_PATH = ENGINE_DIR / "data_store" / "funded_100k_prereg_2026-09-03.md"
MAIN_LEDGER_PATH = ENGINE_DIR / "data_store" / "validation" / "trial_ledger.json"
DEFAULT_LEDGER_PATH = (
    ENGINE_DIR / "data_store" / "validation" / "funded_100k_trial_ledger_2026-09-03.json"
)
DEFAULT_JSON_PATH = (
    ENGINE_DIR / "data_store" / "validation" / "funded_100k_gate_2026-09-03.json"
)
DEFAULT_REPORT_PATH = (
    ENGINE_DIR / "data_store" / "validation" / "funded_100k_gate_2026-09-03.md"
)

ACCOUNT = 100_000.0
SEED = 20260903
BUILD_START, BUILD_END = "2016-01-01", "2020-12-31"
SELECTION_START, SELECTION_END = "2021-01-01", "2022-12-31"
VALIDATION_START, VALIDATION_END = "2023-01-01", "2024-12-31"
CONFIRMATION_START = "2025-01-01"
PERIODS_PER_YEAR = 365
MEAN_BLOCK_LENGTHS = (5, 10, 21)
DEFAULT_N_PATHS = 100_000
DEFAULT_ORDER_PERMUTATIONS = 100
DEFAULT_BOOTSTRAP_CHUNK = 250

TRACE_REQUIRED = (
    "day_start_equity",
    "day_start_balance",
    "opening_equity",
    "conservative_intraday_min_equity",
    "end_balance",
    "end_equity",
    "closed_pnl",
    "positions_opened",
    "verified_flat_at_end",
    "risk_sizing_base",
    "gross_exposure",
    "planned_stop_risk",
    "worst_symbol_adverse_loss",
)

# Economic groupings are fixed here before results are computed.  The top-profit
# group is selected only for the preregistered removal stress.
CLUSTERS: dict[str, str] = {
    **{symbol: "single_stock_growth" for symbol in (
        "AAPL", "MSFT", "NVDA", "META", "AMZN", "GOOGL", "TSLA", "AMD",
        "PLTR", "TSM", "NFLX", "UBER",
    )},
    "ISWD.L": "islamic_broad_equity",
    "ISDU.L": "islamic_broad_equity",
    "ISDE.L": "islamic_broad_equity",
    "XLK": "technology_equity",
    "SMH": "technology_equity",
    "SOXX": "technology_equity",
    "XLE": "energy_equity",
    "XBI": "biotech_equity",
    "SGLD.L": "physical_gold",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _utc(value: Any) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    return (
        timestamp.tz_localize("UTC")
        if timestamp.tzinfo is None
        else timestamp.tz_convert("UTC")
    )


def _jsonable(value: Any) -> Any:
    """Convert scientific/python values to strict, deterministic JSON values."""

    if isinstance(value, Mapping):
        return {str(key): _jsonable(val) for key, val in sorted(value.items(), key=lambda x: str(x[0]))}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, np.ndarray):
        return [_jsonable(item) for item in value.tolist()]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (float, np.floating)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, Path):
        return str(value)
    return value


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp{os.getpid()}")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = json.dumps(
        _jsonable(payload), indent=2, sort_keys=True, allow_nan=False,
        separators=(",", ": "),
    ) + "\n"
    _atomic_write(path, encoded)


def _assert_isolated_write(path: Path) -> None:
    resolved = path.expanduser().resolve()
    protected = MAIN_LEDGER_PATH.resolve()
    if resolved == protected:
        raise ValueError("the funded runner may never write the shared trial_ledger.json")
    lowered = {part.lower() for part in resolved.parts}
    if "paper_portfolio" in lowered or "paper_portfolio_b" in lowered or "paper_portfolio_r" in lowered:
        raise ValueError("the funded runner may never write paper-book state")
    if resolved.name in {"config.yaml", "config.prop.yaml", "state.json"}:
        raise ValueError(f"protected state/config output path: {resolved}")


def _candidate_declarations(protocol_sha256: str) -> list[dict[str, Any]]:
    common = {
        "protocol_sha256": protocol_sha256,
        "declared_on": PROTOCOL_DATE,
        "account_seed": ACCOUNT,
        "data_frequency": "daily_OHLC",
    }
    return [
        {
            **common,
            "candidate": "C_FUNDED",
            "role": "eligible_primary",
            "family": "frozen_book_c_63_126_252",
            "selection_rule": "point_in_time_expected_value_then_instrument",
            "scale_rule": "build_2016_2020_frozen_formula_floor_0.05",
        },
        {
            **common,
            "candidate": "C_FUNDED_075_SCALE",
            "role": "robustness_only_cannot_win",
            "family": "frozen_book_c_63_126_252",
            "scale_rule": "exactly_0.75_times_C_FUNDED_scale",
        },
        {
            **common,
            "candidate": "R_FUNDED",
            "role": "conditional_diagnostic",
            "family": "frozen_book_r_252",
            "eligibility_condition": "exact selected-firm instrument map and family integration",
        },
        {
            **common,
            "candidate": "PLATFORM_NATIVE_DIVERSIFIED_TREND",
            "role": "data_blocked_candidate",
            "eligibility_condition": "selected-firm symbols plus raw bid_ask history",
        },
    ]


def initialize_funded_ledger(path: Path, protocol_sha256: str) -> dict[str, Any]:
    """Persist immutable candidate declarations before any data/backtest work."""

    _assert_isolated_write(path)
    declarations = _candidate_declarations(protocol_sha256)
    expected = {
        "schema_version": SCHEMA_VERSION,
        "kind": "funded_100k_dedicated_trial_ledger",
        "protocol_path": str(PROTOCOL_PATH.relative_to(REPO_DIR)),
        "protocol_sha256": protocol_sha256,
        "declarations_frozen_before_computation": True,
        "declarations": declarations,
        "results": {},
    }
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        immutable_keys = (
            "schema_version", "kind", "protocol_path", "protocol_sha256",
            "declarations_frozen_before_computation", "declarations",
        )
        drift = [key for key in immutable_keys if existing.get(key) != expected.get(key)]
        if drift:
            raise RuntimeError(
                "dedicated funded ledger conflicts with this frozen protocol: "
                + ", ".join(drift)
            )
        return existing
    _write_json(path, expected)
    return expected


def update_funded_ledger_results(
    path: Path, ledger: Mapping[str, Any], results: Mapping[str, Any]
) -> dict[str, Any]:
    updated = dict(ledger)
    updated["results"] = _jsonable(results)
    _write_json(path, updated)
    return updated


def floor_to_005(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("scale input must be finite")
    # Epsilon only protects exact binary representations such as 0.35/0.05.
    return math.floor((value + 1e-12) / 0.05) * 0.05


def _rolling_one_year_drawdowns(trace: pd.DataFrame) -> np.ndarray:
    """Rolling calendar-year max DD using EOD peaks and the OHLC lower bound."""

    if len(trace) < PERIODS_PER_YEAR:
        raise ValueError("build trace has fewer than 365 firm-day observations")
    end = trace["end_equity"].to_numpy(dtype=float)
    low = trace["conservative_intraday_min_equity"].to_numpy(dtype=float)
    values: list[float] = []
    for finish in range(PERIODS_PER_YEAR - 1, len(trace)):
        begin = finish - PERIODS_PER_YEAR + 1
        peak = float(trace["day_start_equity"].iloc[begin])
        worst = 0.0
        for position in range(begin, finish + 1):
            worst = max(worst, max(0.0, 1.0 - low[position] / peak))
            peak = max(peak, end[position])
        values.append(worst)
    return np.asarray(values, dtype=float)


def calibrate_scale(trace: pd.DataFrame) -> dict[str, Any]:
    """Apply the preregistered build-only formula without validation feedback.

    ``L_gap`` uses twice the complete worst-symbol adverse amount supplied by the
    trace.  That is deliberately more conservative than pretending daily OHLC can
    isolate an opening-gap and ordinary-cost component.  It is labelled a proxy and
    keeps the final evidence ceiling provisional.
    """

    required = {
        "day_start_equity", "conservative_intraday_min_equity", "end_equity",
        "worst_symbol_adverse_loss",
    }
    missing = sorted(required.difference(trace.columns))
    if missing:
        raise ValueError("scale trace is missing columns: " + ", ".join(missing))
    starts = trace["day_start_equity"].to_numpy(dtype=float)
    if len(starts) == 0 or np.any(~np.isfinite(starts)) or np.any(starts <= 0.0):
        raise ValueError("build trace has invalid day-start equity")
    losses = np.maximum(
        0.0,
        (starts - trace["conservative_intraday_min_equity"].to_numpy(dtype=float)) / starts,
    )
    rolling_dd = _rolling_one_year_drawdowns(trace)
    symbol_adverse = (
        trace["worst_symbol_adverse_loss"].to_numpy(dtype=float) / starts
    )
    # NumPy's "higher" method avoids interpolation that could understate a tail.
    l_day = float(np.quantile(losses, 0.999, method="higher"))
    d_1y = float(np.quantile(rolling_dd, 0.99, method="higher"))
    l_gap = float(2.0 * np.max(symbol_adverse))
    denominators = {"L_day": l_day, "D_1y": d_1y, "L_gap": l_gap}
    invalid = [name for name, value in denominators.items() if not math.isfinite(value) or value <= 0.0]
    if invalid:
        raise ValueError("non-finite/non-positive calibration denominator: " + ", ".join(invalid))
    terms = {
        "one": 1.0,
        "daily": 0.50 * 0.03 / l_day,
        "drawdown": 0.50 * 0.10 / d_1y,
        "gap_proxy": 0.35 * 0.03 / l_gap,
    }
    raw = min(terms.values())
    scale = floor_to_005(raw)
    if not math.isfinite(scale) or scale <= 0.0:
        raise ValueError(f"frozen formula produced unusable scale {scale!r}")
    return {
        "window": {"start": BUILD_START, "end": BUILD_END},
        "L_day": l_day,
        "L_day_quantile": "99.9% higher",
        "D_1y": d_1y,
        "D_1y_quantile": "99% higher over rolling 365 firm days",
        "L_gap": l_gap,
        "L_gap_semantics": (
            "2x entire observed worst-symbol OHLC adverse loss (costs embedded); "
            "conservative proxy because daily data cannot isolate the gap component"
        ),
        "formula_terms": terms,
        "raw_minimum": raw,
        "scale": scale,
        "validation_recalibration": False,
    }


def _multiply_costs(cfg: AppConfig, multiplier: float) -> None:
    if multiplier <= 0.0 or not math.isfinite(multiplier):
        raise ValueError("cost multiplier must be finite and positive")
    if multiplier == 1.0:
        return
    for class_name in ("forex", "equity", "crypto"):
        mechanics = getattr(cfg.asset_classes, class_name)
        mechanics.spread_pips *= multiplier
        mechanics.spread_bps *= multiplier
        mechanics.slippage_bps *= multiplier
        mechanics.commission_per_trade *= multiplier
        mechanics.short_borrow_bps_annual *= multiplier
        if class_name == "forex":
            if mechanics.cross_rt_cost_pips is not None:
                mechanics.cross_rt_cost_pips *= multiplier
            mechanics.pair_rt_cost_pips = {
                key: value * multiplier for key, value in mechanics.pair_rt_cost_pips.items()
            }
            mechanics.pair_tf_rt_cost_pips = {
                key: {tf: value * multiplier for tf, value in values.items()}
                for key, values in mechanics.pair_tf_rt_cost_pips.items()
            }


def funded_config(*, scale: float, cost_multiplier: float = 1.0) -> AppConfig:
    """Research config for the bounded C_FUNDED risk geometry.

    The backtester can enforce caps and close-bar loss controls, but cannot replay
    persistent intra-session guard state from daily OHLC.  That implementation gap
    is reported as a failing gate rather than concealed here.
    """

    if not 0.0 < scale <= 1.0:
        raise ValueError("funded scale must be in (0, 1]")
    cfg = copy.deepcopy(get_config())
    cfg.backtest.initial_equity = ACCOUNT
    # The frozen Book C risk anchor is 0.85%; the build-only formula scales that
    # anchor uniformly.  It is not reset to the legacy 1.00% research baseline.
    cfg.risk.max_risk_per_trade = 0.0085 * scale
    cfg.risk.max_portfolio_risk = 0.0105
    cfg.risk.max_total_exposure = 0.75
    cfg.risk.max_correlated_exposure = 0.30
    cfg.risk.max_position_notional_pct = 0.10
    cfg.risk.max_concurrent_trades = 5
    cfg.risk.max_swing_slots = 5
    cfg.risk.slot_allocation = "expected_value"
    cfg.risk.portfolio_risk_cap_mode = "simultaneous"
    # Do not manufacture "guard-adjusted" returns from a close-only mechanism.
    # The exact stateful 1.2/1.8/6 guard cannot be counterfactually executed from
    # daily OHLC; rule replay instead ends a path at the internal 1.8%/6% levels.
    cfg.risk.daily_loss_limit = 0.0
    cfg.risk.daily_loss_flatten = False
    cfg.risk.drawdown_breaker = 0.99
    cfg.risk.drawdown_reducing_limit = 0.99
    _multiply_costs(cfg, cost_multiplier)
    return cfg


def _unscaled_calibration_config() -> AppConfig:
    # Scale=1 is the registered unscaled C_FUNDED signal/risk geometry.  No later
    # partition is consulted before ``calibrate_scale`` fixes the final scale.
    return funded_config(scale=1.0)


def _load_panel() -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    cfg = get_config()
    store = ParquetStore(cfg.store_path)
    requested = list(EQUITY_CORE) + [GOLD_ETC] + list(cfg.data.crypto) + list(FX_MAJORS_7)
    panel: dict[str, pd.DataFrame] = {}
    manifest: dict[str, Any] = {}
    for instrument in requested:
        path = store.path_for(instrument, "1d")
        frame = store.load(instrument, "1d")
        if frame.empty:
            manifest[instrument] = {"status": "missing", "path": path.name}
            continue
        frame = clean(frame)
        if len(frame) < MIN_BARS:
            manifest[instrument] = {
                "status": "insufficient", "path": path.name, "rows": len(frame),
            }
            continue
        panel[instrument] = frame
        manifest[instrument] = {
            "status": "loaded",
            "path": path.name,
            "sha256": _sha256(path),
            "rows": len(frame),
            "first_bar": frame.index.min().isoformat(),
            "last_bar": frame.index.max().isoformat(),
        }
    missing_loaded = [instrument for instrument in requested if instrument not in panel]
    if missing_loaded and missing_loaded != ["MATIC/USD"]:
        raise RuntimeError(
            "frozen Book C panel is incomplete (only documented MATIC/USD absence is allowed): "
            + ", ".join(missing_loaded)
        )
    if len(panel) != 39:
        raise RuntimeError(f"frozen Book C expected 39 loaded instruments, got {len(panel)}")
    return panel, manifest


def _run_c(
    panel: Mapping[str, pd.DataFrame],
    *,
    scale: float,
    cost_multiplier: float = 1.0,
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp | None = VALIDATION_END,
    warmup: int = WARMUP,
    capture_trace: bool = True,
) -> PortfolioResult:
    ordered = dict(panel)
    cfg = funded_config(scale=scale, cost_multiplier=cost_multiplier)
    pits = {name: PointInTimeAccessor(frame) for name, frame in ordered.items()}
    model = TrendBook(ordered, **PARAMS)
    return PortfolioBacktester(
        cfg,
        exit_mode="managed",
        trade_manager=TradeManager(),
        slot_allocation="expected_value",
        capture_funded_trace=capture_trace,
        enforce_entry_bar_exits=True,
        funded_sizing_limits=(0.03, 0.10),
        retain_pre_start_history=start is not None,
    ).run(
        pits,
        model.strategies(),
        timeframes={name: "1d" for name in ordered},
        warmup=warmup,
        start=None if start is None else _utc(start),
        end=None if end is None else _utc(end),
        periods_per_year=PERIODS_PER_YEAR,
    )


def _run_unscaled_build(panel: Mapping[str, pd.DataFrame]) -> PortfolioResult:
    ordered = dict(panel)
    cfg = _unscaled_calibration_config()
    pits = {name: PointInTimeAccessor(frame) for name, frame in ordered.items()}
    model = TrendBook(ordered, **PARAMS)
    return PortfolioBacktester(
        cfg,
        exit_mode="managed",
        trade_manager=TradeManager(),
        slot_allocation="expected_value",
        capture_funded_trace=True,
        enforce_entry_bar_exits=True,
        funded_sizing_limits=(0.03, 0.10),
        retain_pre_start_history=True,
    ).run(
        pits,
        model.strategies(),
        timeframes={name: "1d" for name in ordered},
        warmup=WARMUP,
        start=_utc(BUILD_START),
        end=_utc(BUILD_END),
        periods_per_year=PERIODS_PER_YEAR,
    )


def _require_balance_trace(result: PortfolioResult) -> pd.DataFrame:
    if result.funded_trace is None:
        raise RuntimeError("DATA_BLOCKED_BALANCE_TRACE: funded trace capture returned None")
    missing = sorted(set(TRACE_REQUIRED).difference(result.funded_trace.columns))
    if missing:
        raise RuntimeError(
            "DATA_BLOCKED_BALANCE_TRACE: missing closed-balance fields: " + ", ".join(missing)
        )
    trace = result.funded_trace.copy()
    if trace.empty:
        raise RuntimeError("DATA_BLOCKED_BALANCE_TRACE: funded trace is empty")
    if trace.index.tz is None:
        raise RuntimeError("DATA_BLOCKED_BALANCE_TRACE: trace timestamps are timezone-naive")
    numeric = trace.loc[:, TRACE_REQUIRED].to_numpy(dtype=float)
    if np.any(~np.isfinite(numeric)):
        raise RuntimeError("DATA_BLOCKED_BALANCE_TRACE: trace contains non-finite values")
    return trace


def _slice_trace(trace: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    begin = _utc(start)
    finish = _utc(end) + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
    return trace[(trace.index >= begin) & (trace.index <= finish)].copy()


def _trades_in_window(result: PortfolioResult, start: str, end: str) -> list[Any]:
    begin, finish = _utc(start), _utc(end) + pd.Timedelta(days=1)
    selected = []
    for trade in result.trades:
        timestamp = _utc(trade.exit_time)
        if begin <= timestamp < finish:
            selected.append(trade)
    return selected


def _metrics_in_window(result: PortfolioResult, start: str, end: str) -> dict[str, Any]:
    equity = result.equity.loc[(_utc(start) <= result.equity.index) & (result.equity.index < _utc(end) + pd.Timedelta(days=1))]
    return compute_metrics(
        equity,
        _trades_in_window(result, start, end),
        periods_per_year=PERIODS_PER_YEAR,
    )


def trace_to_day_records(trace: pd.DataFrame) -> tuple[DayRecord, ...]:
    """Convert an extended trace without substituting marked equity for balance."""

    missing = sorted(set(TRACE_REQUIRED).difference(trace.columns))
    if missing:
        raise ValueError("cannot build funded replay records; missing: " + ", ".join(missing))
    records: list[DayRecord] = []
    for timestamp, row in trace.sort_index().iterrows():
        aware = _utc(timestamp)
        records.append(
            DayRecord(
                session=firm_session_key(
                    aware, timezone="Europe/Prague", rollover=pd.Timestamp("00:00").time()
                ),
                timestamp=aware,
                day_start_balance=float(row["day_start_balance"]),
                intraday_min_equity=float(row["conservative_intraday_min_equity"]),
                end_balance=float(row["end_balance"]),
                end_equity=float(row["end_equity"]),
                closed_pnl=float(row["closed_pnl"]),
                day_start_equity=float(row["day_start_equity"]),
                # The backtester records the exact funded capital available to
                # NEW decisions on this union day.  A single daily base remains
                # an approximation for positions carried from older decisions;
                # that limitation is surfaced as a separate data gate.
                source_risk_base=float(row["risk_sizing_base"]),
                intraday_min_timestamp=aware,
                positions_opened=row["positions_opened"],
                verified_flat_at_end=row["verified_flat_at_end"],
            )
        )
    return tuple(records)


def _validate_independent_records(
    records: Sequence[DayRecord], initial_balance: float = ACCOUNT
) -> tuple[DayRecord, ...]:
    """Require an independently seeded, internally continuous account path.

    Historical binding partitions are run from a fresh account; they must never
    be post-hoc rebased from a continuous full-history book.  Re-scaling a path
    by marked equity would also double-de-lever funded-sized P&L now that each row
    records its true (normally much smaller) risk-sizing base.
    """

    if not records:
        raise ValueError("independent partition produced no funded records")
    first = records[0]
    tolerance = max(1e-8, abs(initial_balance) * 1e-10)
    if (
        abs(first.day_start_balance - initial_balance) > tolerance
        or abs(float(first.day_start_equity) - initial_balance) > tolerance
    ):
        raise ValueError(
            "partition inherited capital/state instead of starting at the £100k seed"
        )
    for previous, current in zip(records, records[1:]):
        if abs(current.day_start_balance - previous.end_balance) > tolerance:
            raise ValueError("partition balance path is discontinuous")
        if abs(float(current.day_start_equity) - previous.end_equity) > tolerance:
            raise ValueError("partition marked-equity path is discontinuous")
    return tuple(records)


def _rules(
    *, max_loss_mode: str, target: float | None, internal_terminal: bool = False
) -> FundedRules:
    # Without a minute-level counterfactual liquidation simulator, a path that
    # touches the registered 1.8% daily-flatten or 6% cycle-halt level is ended at
    # that observation.  Recovery afterwards is never credited.  This does not
    # claim guard-adjusted returns; it is a conservative terminal-path proxy.
    daily_loss = 0.018 if internal_terminal else 0.03
    max_loss = 0.06 if internal_terminal else 0.10
    return FundedRules(
        initial_balance=ACCOUNT,
        profit_target_pct=target,
        daily_loss_pct=daily_loss,
        max_loss_pct=max_loss,
        max_loss_mode=max_loss_mode,
        daily_loss_basis="initial_balance",
        session_timezone="Europe/Prague",
    )


def _replay_payload(records: Sequence[DayRecord]) -> dict[str, Any]:
    rebased = _validate_independent_records(records)
    official_variants = {}
    internal_variants = {}
    for mode in ("static", "eod_trailing"):
        official = replay_funded_rules(
            rebased, _rules(max_loss_mode=mode, target=None, internal_terminal=False)
        )
        internal = replay_funded_rules(
            rebased, _rules(max_loss_mode=mode, target=None, internal_terminal=True)
        )
        official_variants[mode] = asdict(official)
        internal_variants[mode] = asdict(internal)
    peak = ACCOUNT
    worst_daily = 0.0
    worst_drawdown = 0.0
    for day in rebased:
        worst_daily = max(
            worst_daily,
            max(0.0, (day.day_start_balance - day.intraday_min_equity) / ACCOUNT),
        )
        worst_drawdown = max(
            worst_drawdown,
            max(0.0, (peak - day.intraday_min_equity) / peak),
        )
        peak = max(peak, day.end_balance)
    return {
        "official_3pct_10pct_rule_modes": official_variants,
        "internal_terminal_1_8pct_6pct_rule_modes": internal_variants,
        "official_breach": any(
            value["status"] == "breached" for value in official_variants.values()
        ),
        "internal_terminal_failure": any(
            value["status"] == "breached" for value in internal_variants.values()
        ),
        "internal_terminal_semantics": (
            "Path ends at first 1.8% daily or 6% maximum-loss threshold touch; "
            "no later recovery is credited and no guard-adjusted return is claimed."
        ),
        "worst_intraday_daily_loss_pct_initial": worst_daily,
        "worst_peak_to_intraday_bound_drawdown": worst_drawdown,
        "records": len(rebased),
    }


def _winner_cut_records(records: Sequence[DayRecord]) -> tuple[DayRecord, ...]:
    """Non-binding day-level proxy; this is *not* the registered trade haircut.

    A net-positive day can contain losing trades, and a daily equity change mixes
    realised and floating P&L.  The result may be shown only as a sensitivity
    diagnostic; the binding winner-cut gate must remain DATA_BLOCKED_TRADE_PATH.
    """

    running_balance = ACCOUNT
    running_equity = ACCOUNT
    transformed: list[DayRecord] = []
    for source in records:
        balance_start = source.day_start_balance
        equity_start = float(source.day_start_equity)
        if balance_start <= 0.0 or equity_start <= 0.0:
            raise ValueError("invalid source balance/equity")
        balance_return = source.closed_pnl / balance_start
        equity_return = source.end_equity / equity_start - 1.0
        low_return = source.intraday_min_equity / equity_start - 1.0
        if balance_return > 0.0:
            balance_return *= 0.5
        if equity_return > 0.0:
            equity_return *= 0.5
        if low_return > 0.0:
            low_return *= 0.5
        end_balance = running_balance * (1.0 + balance_return)
        end_equity = running_equity * (1.0 + equity_return)
        # A day-level minimum always includes its opening observation.  Explicitly
        # retaining zero avoids manufacturing a minimum above start equity when a
        # source day remained profitable throughout.
        intraday = running_equity * (1.0 + min(0.0, low_return, equity_return))
        transformed.append(
            DayRecord(
                session=source.session,
                timestamp=source.timestamp,
                day_start_balance=running_balance,
                intraday_min_equity=intraday,
                end_balance=end_balance,
                end_equity=end_equity,
                closed_pnl=end_balance - running_balance,
                day_start_equity=running_equity,
                source_risk_base=running_equity,
                intraday_min_timestamp=source.intraday_min_timestamp,
                positions_opened=source.positions_opened,
                # A day-level haircut separately transforms balance and marked
                # equity, so source flatness cannot be carried through safely.
                verified_flat_at_end=False,
            )
        )
        running_balance = end_balance
        running_equity = end_equity
    return tuple(transformed)


def _records_return(records: Sequence[DayRecord]) -> float:
    if not records:
        return 0.0
    return float(records[-1].end_equity / records[0].day_start_balance - 1.0)


def _trace_cap_diagnostics(trace: pd.DataFrame) -> dict[str, Any]:
    equity = trace["end_equity"].replace(0.0, np.nan)
    start = trace["day_start_equity"].replace(0.0, np.nan)
    gross = float((trace["gross_exposure"] / equity).max())
    stop_risk = float((trace["planned_stop_risk"] / equity).max())
    symbol = float((trace["worst_symbol_adverse_loss"] / start).max())
    return {
        "max_gross_exposure_pct_equity": gross,
        "max_planned_stop_risk_pct_equity": stop_risk,
        "max_observed_symbol_adverse_loss_pct_day_start": symbol,
        "gross_cap": 0.75,
        "planned_stop_risk_cap": 0.0105,
        "stressed_symbol_loss_cap": 0.0045,
        "gross_cap_pass": bool(gross <= 0.75 + 1e-12),
        "planned_stop_risk_cap_pass": bool(stop_risk <= 0.0105 + 1e-12),
        # This observed OHLC number is not the exact pre-order stressed-loss model.
        "observed_symbol_adverse_cap_pass": bool(symbol <= 0.0045 + 1e-12),
        "stressed_symbol_pretrade_gate_available": False,
    }


def _serialize_bootstrap_block(block: Any) -> dict[str, Any]:
    return {
        "mean_block_length": block.mean_block_length,
        "pass_probability": asdict(block.pass_probability),
        "breach_probability": asdict(block.breach_probability),
        "survival_probability": asdict(block.survival_probability),
        "median_sessions_to_pass": block.median_sessions_to_pass,
        "breach_reasons": dict(block.breach_reasons),
    }


def _bootstrap_common_chunked(
    records_by_candidate: Mapping[str, Sequence[DayRecord]],
    *,
    target: float | None,
    sample_length: int,
    n_paths: int,
    seed: int,
    chunk_size: int,
) -> dict[str, Any]:
    """Use the vectorized, bounded-memory simulator with common random numbers."""

    if not records_by_candidate:
        return {}
    lengths = {len(records) for records in records_by_candidate.values()}
    if len(lengths) != 1:
        raise ValueError("common-random candidates must have aligned observation counts")
    n_observations = next(iter(lengths))
    sessions = {
        tuple(record.session for record in records)
        for records in records_by_candidate.values()
    }
    if len(sessions) != 1:
        raise ValueError("common-random candidates must have identical sessions")
    cases: dict[str, Sequence[DayRecord]] = {}
    rules_by_case: dict[str, FundedRules] = {}
    for candidate, candidate_records in records_by_candidate.items():
        for mode in ("static", "eod_trailing"):
            key = f"{candidate}::{mode}"
            cases[key] = candidate_records
            rules_by_case[key] = _rules(
                max_loss_mode=mode, target=target, internal_terminal=True
            )
    synchronized = chunked_synchronized_funded_bootstrap(
        cases,
        rules_by_case,
        sizing_mode="conservative_buffer",
        n_paths=n_paths,
        sample_length=sample_length,
        mean_block_lengths=MEAN_BLOCK_LENGTHS,
        seed=seed,
        chunk_size=chunk_size,
    )
    output: dict[str, dict[str, Any]] = {
        candidate: {} for candidate in records_by_candidate
    }
    for strategy_report in synchronized.strategies:
        candidate, mode = strategy_report.name.split("::", 1)
        output[candidate][mode] = [
            _serialize_bootstrap_block(block)
            for block in strategy_report.report.blocks
        ]
    return {
        "n_paths": n_paths,
        "sample_length": sample_length,
        "mean_block_lengths": list(MEAN_BLOCK_LENGTHS),
        "common_random_numbers": True,
        "seed": seed,
        "chunk_size": chunk_size,
        "sizing_mode": "conservative_buffer",
        "terminal_thresholds": {
            "daily_loss": 0.018,
            "maximum_loss": 0.06,
            "recovery_after_touch_credited": False,
        },
        "candidates": output,
    }


def _bootstrap_extreme(
    report: Mapping[str, Any], candidate: str, field: str, bound: str, operation: str
) -> float | None:
    values = [
        float(block[field][bound])
        for mode in report.get("candidates", {}).get(candidate, {}).values()
        for block in mode
    ]
    if not values:
        return None
    return min(values) if operation == "min" else max(values)


def _decision_fingerprint(result: PortfolioResult) -> str:
    digest = hashlib.sha256()
    digest.update(np.asarray(result.equity.index.asi8, dtype="int64").tobytes())
    digest.update(np.asarray(result.equity.to_numpy(dtype=float), dtype="float64").tobytes())
    for trade in result.trades:
        digest.update(
            json.dumps(trade.model_dump(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
    digest.update(json.dumps(result.constraint_log, sort_keys=True).encode("utf-8"))
    return digest.hexdigest()


def _order_permutation_test(
    panel: Mapping[str, pd.DataFrame],
    *,
    scale: float,
    reference: PortfolioResult,
    n_permutations: int,
) -> dict[str, Any]:
    if n_permutations <= 0:
        return {"status": "SKIPPED_NON_BINDING", "passed": False, "requested": n_permutations}
    names = list(panel)
    reference_hash = _decision_fingerprint(reference)
    mismatches: list[dict[str, Any]] = []
    rng = np.random.default_rng(SEED + 17)
    for iteration in range(n_permutations):
        order = list(rng.permutation(names))
        permuted = {name: panel[name] for name in order}
        result = _run_c(
            permuted,
            scale=scale,
            start=VALIDATION_START,
            end=VALIDATION_END,
            capture_trace=False,
        )
        observed = _decision_fingerprint(result)
        if observed != reference_hash:
            mismatches.append({
                "permutation": iteration + 1,
                "fingerprint": observed,
                "first_five": order[:5],
            })
            if len(mismatches) >= 10:
                break
    return {
        "status": "EVALUATED",
        "requested": n_permutations,
        "completed": n_permutations if not mismatches else min(n_permutations, iteration + 1),
        "reference_fingerprint": reference_hash,
        "mismatches": mismatches,
        "passed": not mismatches and n_permutations == 100,
        "engineering_smoke_equal": not mismatches,
    }


def _future_poison_test(
    panel: Mapping[str, pd.DataFrame], *, scale: float, reference: PortfolioResult
) -> dict[str, Any]:
    poison_after = _utc(VALIDATION_END)
    poisoned: dict[str, pd.DataFrame] = {}
    rows_poisoned = 0
    for name, frame in panel.items():
        changed = frame.copy()
        mask = changed.index > poison_after
        rows_poisoned += int(mask.sum())
        if mask.any():
            multipliers = np.linspace(10.0, 1000.0, int(mask.sum()))
            for column in ("open", "high", "low", "close"):
                changed.loc[mask, column] = changed.loc[mask, column].to_numpy(dtype=float) * multipliers
            if "volume" in changed.columns:
                changed.loc[mask, "volume"] = 9.99e18
        poisoned[name] = changed
    poisoned_result = _run_c(
        poisoned,
        scale=scale,
        start=VALIDATION_START,
        end=VALIDATION_END,
        capture_trace=False,
    )
    reference_hash = _decision_fingerprint(reference)
    poison_hash = _decision_fingerprint(poisoned_result)
    return {
        "status": "EVALUATED",
        "poison_after": VALIDATION_END,
        "rows_poisoned": rows_poisoned,
        "reference_fingerprint": reference_hash,
        "poisoned_fingerprint": poison_hash,
        "passed": bool(rows_poisoned > 0 and reference_hash == poison_hash),
    }


def _cluster_for(instrument: str, cfg: AppConfig) -> str:
    if instrument in CLUSTERS:
        return CLUSTERS[instrument]
    asset_class = cfg.asset_class_of(instrument)
    if asset_class == "crypto":
        return "crypto"
    if asset_class == "forex":
        return "major_fx"
    return "other_equity"


def _concentration(
    result: PortfolioResult,
    panel: Mapping[str, pd.DataFrame],
    *,
    scale: float,
) -> dict[str, Any]:
    cfg = get_config()
    by_instrument: dict[str, float] = defaultdict(float)
    by_cluster: dict[str, float] = defaultdict(float)
    for trade in _trades_in_window(result, VALIDATION_START, VALIDATION_END):
        pnl = float(trade.pnl)
        by_instrument[trade.instrument] += pnl
        by_cluster[_cluster_for(trade.instrument, cfg)] += pnl
    total_net = sum(by_instrument.values())
    positive_instruments = {key: value for key, value in by_instrument.items() if value > 0.0}
    positive_clusters = {key: value for key, value in by_cluster.items() if value > 0.0}
    top_cluster = max(positive_clusters, key=positive_clusters.get) if positive_clusters else None
    instrument_share = (
        max(positive_instruments.values()) / total_net
        if total_net > 0.0 and positive_instruments else None
    )
    cluster_share = (
        max(positive_clusters.values()) / total_net
        if total_net > 0.0 and positive_clusters else None
    )
    removal = None
    if top_cluster is not None:
        reduced_panel = {
            name: frame for name, frame in panel.items()
            if _cluster_for(name, cfg) != top_cluster
        }
        reduced = _run_c(
            reduced_panel,
            scale=scale,
            start=VALIDATION_START,
            end=VALIDATION_END,
            capture_trace=False,
        )
        reduced_metrics = _metrics_in_window(reduced, VALIDATION_START, VALIDATION_END)
        removal = {
            "removed_cluster": top_cluster,
            "remaining_instruments": len(reduced_panel),
            "validation_metrics": reduced_metrics,
            "positive": bool(reduced_metrics.get("total_return", 0.0) > 0.0),
        }
    return {
        "by_instrument_net_pnl": dict(sorted(by_instrument.items())),
        "by_cluster_net_pnl": dict(sorted(by_cluster.items())),
        "validation_net_trade_pnl": total_net,
        "max_positive_instrument_share_of_net_profit": instrument_share,
        "max_positive_cluster_share_of_net_profit": cluster_share,
        "instrument_share_cap_pass": bool(instrument_share is not None and instrument_share <= 0.35),
        "cluster_share_cap_pass": bool(cluster_share is not None and cluster_share <= 0.35),
        "top_cluster_removal": removal,
        "passed": bool(
            instrument_share is not None
            and cluster_share is not None
            and instrument_share <= 0.35
            and cluster_share <= 0.35
            and removal is not None
            and removal["positive"]
        ),
    }


def _cpcv(
    panel: Mapping[str, pd.DataFrame], *, scale: float, skip: bool
) -> dict[str, Any]:
    if skip:
        return {"status": "SKIPPED_NON_BINDING", "n_paths": 0, "passed_12_of_15": False}
    cutoff = _utc(VALIDATION_END)
    evidence_panel = {
        name: frame.loc[frame.index <= cutoff].copy()
        for name, frame in panel.items()
    }
    timeline = pd.DatetimeIndex(
        sorted(set().union(*(set(frame.index) for frame in evidence_panel.values())))
    )
    cfg = funded_config(scale=scale)
    validation_cfg = cfg.validation.cpcv
    splits = cpcv_splits(
        len(timeline),
        validation_cfg.n_groups,
        validation_cfg.n_test_groups,
        validation_cfg.embargo_pct,
        purge=HORIZON,
    )
    oos: list[float] = []
    independent_blocks = 0
    for train_indices, test_indices in splits:
        if len(train_indices) < 60 or len(test_indices) < 30:
            continue
        # A CPCV combination can contain separated test groups.  Running from its
        # first to last date would let capital and positions traverse intervening
        # non-test groups.  Instead each contiguous block starts from a fresh £100k
        # account, while the opt-in backtester retains only earlier price history
        # for indicators.  Book C is frozen/stateless, so no fitted parameter is
        # learned from ``train_indices``; those indices define purge/embargo only.
        breaks = np.flatnonzero(np.diff(test_indices) > 1) + 1
        block_indices = np.split(test_indices, breaks)
        path_returns: list[np.ndarray] = []
        for block in block_indices:
            if len(block) < 2:
                continue
            result = _run_c(
                evidence_panel,
                scale=scale,
                start=timeline[int(block[0])],
                end=timeline[int(block[-1])],
                warmup=WARMUP,
                capture_trace=False,
            )
            test_dates = timeline[block]
            selected = result.returns[result.returns.index.isin(test_dates)]
            if not selected.empty:
                path_returns.append(selected.to_numpy(dtype=float))
            independent_blocks += 1
        combined = (
            np.concatenate(path_returns)
            if path_returns
            else np.asarray([], dtype=float)
        )
        oos.append(sharpe_ratio(combined, periods_per_year=1))
    values = np.asarray(oos if oos else [0.0], dtype=float)
    report = {
        "n_paths": len(oos),
        "oos_sharpe_mean": float(values.mean()),
        "oos_sharpe_std": float(values.std(ddof=1)) if len(values) > 1 else 0.0,
        "oos_sharpe_median": float(np.median(values)),
        "frac_positive": float(np.mean(values > 0.0)),
        "oos_sharpe_paths": [round(float(value), 4) for value in values],
        "entry_bar_exits": "enabled_stop_first_for_ambiguous_daily_OHLC",
        "independent_test_blocks": independent_blocks,
        "partition_account_seed": ACCOUNT,
        "pre_start_history": "retained_for_indicators_only",
    }
    positive = sum(value > 0.0 for value in report["oos_sharpe_paths"])
    return {
        **report,
        "positive_paths": positive,
        "required_positive_paths": 12,
        "purge_sessions": HORIZON,
        "embargo_pct": cfg.validation.cpcv.embargo_pct,
        "passed_12_of_15": bool(report["n_paths"] == 15 and positive >= 12),
        "status": "EVALUATED",
    }


def _dsr_reports(
    validation_returns: Mapping[str, pd.Series],
    main_ledger: Mapping[str, Any],
    *,
    declaration_count: int,
) -> dict[str, dict[str, Any]]:
    """Build DSR reports without manufacturing cross-trial dispersion.

    Repository TrialLedger values are treated as annualised Sharpe observations
    and normalised to the per-period units required by ``deflated_sharpe_ratio``.
    The spent trial count remains binding even when historical Sharpe values are
    missing.  Two closely related scale variants are not enough to estimate the
    dispersion of 300+ prior trials, so that case is explicitly data-blocked.
    """

    candidate_sharpes = {
        name: sharpe_ratio(series.to_numpy(dtype=float), periods_per_year=1)
        for name, series in validation_returns.items()
    }
    raw_main = [
        float(value)
        for value in main_ledger.get("sharpes", [])
        if value is not None and math.isfinite(float(value))
    ]
    main_per_period = [value / math.sqrt(PERIODS_PER_YEAR) for value in raw_main]
    dispersion = main_per_period + list(candidate_sharpes.values())
    main_trial_count = int(main_ledger.get("n_trials", 0) or 0)
    n_trials = main_trial_count + int(declaration_count)
    complete_history = (
        main_trial_count > 0
        and len(main_per_period) == main_trial_count
        and len(main_per_period) >= 2
    )

    output: dict[str, dict[str, Any]] = {}
    for name, series in validation_returns.items():
        naive = deflated_sharpe_ratio(
            series.to_numpy(dtype=float),
            list(candidate_sharpes.values()),
            periods_per_year=PERIODS_PER_YEAR,
            n_trials=n_trials,
        )
        common = {
            "n_trials": n_trials,
            "repository_spent_trials": main_trial_count,
            "repository_sharpes_available": len(main_per_period),
            "dedicated_candidate_sharpes_available": len(candidate_sharpes),
            "observed_dispersion_count": len(dispersion),
            "dispersion_units": "per_period",
            "repository_sharpe_normalization": (
                f"assumed annualized; divided by sqrt({PERIODS_PER_YEAR})"
            ),
            "candidate_validation_per_period_sharpes": candidate_sharpes,
            "naive_two_related_configs_non_binding": naive,
        }
        if not complete_history:
            output[name] = {
                **common,
                "status": "DATA_BLOCKED_TRIAL_SHARPE_HISTORY",
                "dsr": None,
                "passed": False,
                "reason": (
                    "The spent repository trial count has no complete matching "
                    "Sharpe history; two related funded scales cannot estimate its "
                    "cross-trial dispersion."
                ),
            }
            continue
        evaluated = deflated_sharpe_ratio(
            series.to_numpy(dtype=float),
            dispersion,
            periods_per_year=PERIODS_PER_YEAR,
            n_trials=n_trials,
        )
        output[name] = {
            **common,
            **evaluated,
            "status": "EVALUATED",
            "passed": bool(evaluated.get("dsr", 0.0) >= 0.95),
        }
    return output


def _main_ledger_observation() -> dict[str, Any]:
    if not MAIN_LEDGER_PATH.exists():
        return {"exists": False, "n_trials": 0, "sha256": None, "sharpes": []}
    ledger = TrialLedger.load(MAIN_LEDGER_PATH)
    return {
        "exists": True,
        "n_trials": ledger.n_trials,
        "sha256": _sha256(MAIN_LEDGER_PATH),
        "sharpes": ledger.sharpes,
    }


def _gate(name: str, threshold: str, value: Any, passed: bool, *, status: str = "EVALUATED") -> dict[str, Any]:
    return {
        "gate": name,
        "threshold": threshold,
        "value": value,
        "status": status,
        "passed": bool(passed),
    }


def _render_report(payload: Mapping[str, Any]) -> str:
    lines = [
        "# Funded-100K pre-registered gate",
        "",
        f"**Verdict: {payload['verdict']}**",
        "",
        "This is a daily-OHLC research result. Its evidence ceiling is explicitly "
        "**PROVISIONAL_PAPER_ONLY**; it is not proof that a paid challenge or funded "
        "account would survive executable intraday rules.",
        "",
        f"Protocol: `{payload['protocol']['path']}` (`{payload['protocol']['sha256'][:12]}…`).",
        f"Dedicated declarations were written before computation: `{payload['isolation']['dedicated_ledger']}`.",
        "The shared A/B/C/R trial ledger and all paper state were read-only / untouched.",
        "",
        "## Candidate results",
        "",
        "| Candidate | Status | Scale | Validation return | Sharpe | PF | Binding gates |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("C_FUNDED", "C_FUNDED_075_SCALE", "R_FUNDED", "PLATFORM_NATIVE_DIVERSIFIED_TREND"):
        candidate = payload["candidates"][name]
        scale = candidate.get("scale")
        metrics = candidate.get("validation_metrics") or {}
        gates = candidate.get("gates") or []
        passed = sum(bool(gate.get("passed")) for gate in gates)
        lines.append(
            f"| {name} | {candidate.get('status')} | "
            f"{scale if scale is not None else '—'} | "
            f"{_pct(metrics.get('total_return'))} | "
            f"{_num(metrics.get('sharpe'))} | {_num(metrics.get('profit_factor'))} | "
            f"{passed}/{len(gates)} |"
        )
    lines += [
        "",
        "## Why this is not funded-ready",
        "",
    ]
    for reason in payload["blocking_reasons"]:
        lines.append(f"- {reason}")
    lines += [
        "",
        "No threshold was weakened and no failing runner-up was promoted. A passing "
        "retrospective would authorize only a separate `funded_100k_shadow` paper account; "
        "paid/live use still requires the exact contract, minute bid/ask history, and the "
        "forward-evidence clock in the preregistration.",
        "",
    ]
    return "\n".join(lines)


def _pct(value: Any) -> str:
    return "—" if value is None else f"{float(value) * 100:.2f}%"


def _num(value: Any) -> str:
    return "—" if value is None else f"{float(value):.3f}"


def _blocked_candidate(name: str, status: str, reason: str) -> dict[str, Any]:
    return {
        "candidate": name,
        "status": status,
        "eligible_to_win": False,
        "reason": reason,
        "scale": None,
        "validation_metrics": None,
        "gates": [
            _gate("candidate_availability", "implemented and exact instruments available", reason, False, status=status)
        ],
        "passed_all_binding_gates": False,
    }


def _failed_trace_payload(
    *,
    protocol_sha: str,
    ledger_path: Path,
    out_json: Path,
    out_report: Path,
    ledger: Mapping[str, Any],
    error: str,
    n_paths: int,
    permutations: int,
) -> dict[str, Any]:
    candidates = {
        "C_FUNDED": _blocked_candidate("C_FUNDED", "DATA_BLOCKED_BALANCE_TRACE", error),
        "C_FUNDED_075_SCALE": _blocked_candidate("C_FUNDED_075_SCALE", "DATA_BLOCKED_BALANCE_TRACE", error),
        "R_FUNDED": _blocked_candidate(
            "R_FUNDED", "INELIGIBLE_EXACT_SYMBOL_MAP_UNAVAILABLE",
            "The selected firm/product and exact executable instrument map are not frozen.",
        ),
        "PLATFORM_NATIVE_DIVERSIFIED_TREND": _blocked_candidate(
            "PLATFORM_NATIVE_DIVERSIFIED_TREND", "DATA_BLOCKED",
            "Exact provider symbols and raw bid/ask history have not been supplied.",
        ),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "protocol": {
            "path": str(PROTOCOL_PATH.relative_to(REPO_DIR)),
            "sha256": protocol_sha,
            "registered_on": PROTOCOL_DATE,
            "evidence_ceiling": "PROVISIONAL_PAPER_ONLY",
        },
        "run_config": {"n_paths": n_paths, "order_permutations": permutations, "seed": SEED},
        "isolation": {
            "dedicated_ledger": str(ledger_path),
            "shared_trial_ledger_written": False,
            "book_or_paper_state_written": False,
            "output_json": str(out_json),
            "output_report": str(out_report),
        },
        "candidates": candidates,
        "blocking_reasons": [error, "Minute bid/ask and exact firm/product data are absent."],
        "winner": None,
        "verdict": "NO_FUNDED_STRATEGY",
    }


def _partition_seed_manifest(
    trace: pd.DataFrame, *, start: str, end: str | pd.Timestamp
) -> dict[str, Any]:
    """Evidence that one partition began flat from the registered £100k seed."""

    if trace.empty:
        raise ValueError("partition trace is empty")
    first = trace.iloc[0]
    begin = _utc(start)
    finish = _utc(end) + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
    tolerance = ACCOUNT * 1e-10
    seeded = bool(
        abs(float(first["day_start_balance"]) - ACCOUNT) <= tolerance
        and abs(float(first["day_start_equity"]) - ACCOUNT) <= tolerance
        and abs(float(first["opening_equity"]) - ACCOUNT) <= tolerance
        and float(first["actual_open_gross_exposure"]) == 0.0
        and trace.index.min() >= begin
        and trace.index.max() <= finish
    )
    if not seeded:
        raise RuntimeError(
            "partition did not start flat at £100k or emitted events outside its window"
        )
    return {
        "start": start,
        "end": str(end),
        "first_event": trace.index.min().isoformat(),
        "last_event": trace.index.max().isoformat(),
        "first_day_start_balance": float(first["day_start_balance"]),
        "first_day_start_equity": float(first["day_start_equity"]),
        "first_opening_equity": float(first["opening_equity"]),
        "first_actual_open_gross_exposure": float(
            first["actual_open_gross_exposure"]
        ),
        "independent_seed_verified": True,
        "pre_start_history": "retained_for_indicators_only_not_account_state",
    }


def run_gate(args: argparse.Namespace) -> dict[str, Any]:
    if not PROTOCOL_PATH.is_file():
        raise FileNotFoundError(PROTOCOL_PATH)
    protocol_sha = _sha256(PROTOCOL_PATH)
    ledger_path = Path(args.ledger).expanduser()
    out_json = Path(args.out_json).expanduser()
    out_report = Path(args.out_report).expanduser()
    for path in (ledger_path, out_json, out_report):
        _assert_isolated_write(path)

    # This call intentionally precedes panel loading, backtests, and calibration.
    ledger = initialize_funded_ledger(ledger_path, protocol_sha)
    main_ledger = _main_ledger_observation()

    panel, data_manifest = _load_panel()
    unscaled = _run_unscaled_build(panel)
    try:
        unscaled_trace = _require_balance_trace(unscaled)
    except RuntimeError as exc:
        payload = _failed_trace_payload(
            protocol_sha=protocol_sha,
            ledger_path=ledger_path,
            out_json=out_json,
            out_report=out_report,
            ledger=ledger,
            error=str(exc),
            n_paths=args.n_paths,
            permutations=args.order_permutations,
        )
        payload["data_manifest"] = data_manifest
        payload["main_ledger_read_only_observation"] = {
            key: value for key, value in main_ledger.items() if key != "sharpes"
        }
        _write_json(out_json, payload)
        _atomic_write(out_report, _render_report(payload))
        return payload

    build_unscaled_trace = _slice_trace(unscaled_trace, BUILD_START, BUILD_END)
    calibration = calibrate_scale(build_unscaled_trace)
    scales = {
        "C_FUNDED": float(calibration["scale"]),
        "C_FUNDED_075_SCALE": float(calibration["scale"]) * 0.75,
    }

    partitions = {
        "build": (BUILD_START, BUILD_END),
        "selection": (SELECTION_START, SELECTION_END),
        "validation": (VALIDATION_START, VALIDATION_END),
    }
    latest_frozen_bar = max(frame.index.max() for frame in panel.values())
    all_partitions: dict[str, tuple[str, str | pd.Timestamp]] = {
        **partitions,
        "confirmation_previously_inspected": (
            CONFIRMATION_START,
            latest_frozen_bar,
        ),
    }
    partition_results: dict[str, dict[str, PortfolioResult]] = defaultdict(dict)
    partition_traces: dict[str, dict[str, pd.DataFrame]] = defaultdict(dict)
    partition_manifests: dict[str, dict[str, Any]] = defaultdict(dict)
    records: dict[str, dict[str, tuple[DayRecord, ...]]] = defaultdict(dict)
    historical: dict[str, dict[str, Any]] = defaultdict(dict)

    # Every evidence window is a fresh £100k run.  Price bars before ``start``
    # remain available only for causal indicators; no positions, capital, peaks,
    # or pending orders cross a partition boundary.
    for name, scale in scales.items():
        for partition, (start, end) in all_partitions.items():
            result = _run_c(panel, scale=scale, start=start, end=end)
            trace = _require_balance_trace(result)
            day_records = trace_to_day_records(trace)
            _validate_independent_records(day_records)
            partition_results[name][partition] = result
            partition_traces[name][partition] = trace
            partition_manifests[name][partition] = _partition_seed_manifest(
                trace, start=start, end=end
            )
            records[name][partition] = day_records
            historical[name][partition] = _replay_payload(day_records)

    # These aliases intentionally mean the independently seeded validation run,
    # not a slice from a longer account path.
    results = {
        name: partition_results[name]["validation"] for name in scales
    }
    traces = {
        name: partition_traces[name]["validation"] for name in scales
    }

    validation_records = {name: records[name]["validation"] for name in scales}
    winner_cut = {
        name: _winner_cut_records(validation_records[name]) for name in scales
    }
    doubled_cost_validation: dict[str, dict[str, Any]] = {}
    doubled_cost_records: dict[str, tuple[DayRecord, ...]] = {}
    doubled_cost_results: dict[str, PortfolioResult] = {}
    for name in scales:
        doubled_cost_results[name] = _run_c(
            panel,
            scale=scales[name],
            cost_multiplier=2.0,
            start=VALIDATION_START,
            end=VALIDATION_END,
        )
        doubled_trace = _require_balance_trace(doubled_cost_results[name])
        _partition_seed_manifest(
            doubled_trace, start=VALIDATION_START, end=VALIDATION_END
        )
        doubled_cost_records[name] = trace_to_day_records(doubled_trace)
        _validate_independent_records(doubled_cost_records[name])
        doubled_cost_validation[name] = {
            "metrics": doubled_cost_results[name].metrics,
            "replay": _replay_payload(doubled_cost_records[name]),
            "independent_seed_verified": True,
        }

    if args.skip_bootstrap:
        evaluation_bootstrap = {
            "status": "SKIPPED_NON_BINDING", "n_paths": 0, "candidates": {}
        }
        survival_12m = survival_24m = evaluation_bootstrap
        doubled_cost_survival_12m = evaluation_bootstrap
    else:
        evaluation_bootstrap = _bootstrap_common_chunked(
            validation_records,
            target=0.10,
            sample_length=252,
            n_paths=args.n_paths,
            seed=SEED + 100,
            chunk_size=args.bootstrap_chunk,
        )
        survival_12m = _bootstrap_common_chunked(
            validation_records,
            target=None,
            sample_length=365,
            n_paths=args.n_paths,
            seed=SEED + 200,
            chunk_size=args.bootstrap_chunk,
        )
        survival_24m = _bootstrap_common_chunked(
            validation_records,
            target=None,
            sample_length=730,
            n_paths=args.n_paths,
            seed=SEED + 300,
            chunk_size=args.bootstrap_chunk,
        )
        # Doubled costs are backed by a full engine rerun.  The available aggregate
        # day trace cannot identify positive individual trades, so the winner cut
        # remains a non-binding day-level proxy and receives no binding bootstrap.
        doubled_cost_survival_12m = _bootstrap_common_chunked(
            doubled_cost_records,
            target=None,
            sample_length=365,
            n_paths=args.n_paths,
            seed=SEED + 200,
            chunk_size=args.bootstrap_chunk,
        )
    winners_cut_survival_12m = {
        "status": "DATA_BLOCKED_TRADE_PATH",
        "reason": (
            "Aggregate day records cannot apply the registered 50% haircut to each "
            "positive trade while preserving the reconciled balance/equity path."
        ),
    }

    cpcv = {
        name: _cpcv(panel, scale=scale, skip=args.skip_cpcv)
        for name, scale in scales.items()
    }
    validation_returns = {
        name: results[name].returns.loc[
            (_utc(VALIDATION_START) <= results[name].returns.index)
            & (results[name].returns.index < _utc(VALIDATION_END) + pd.Timedelta(days=1))
        ]
        for name in scales
    }
    aligned = pd.concat(validation_returns, axis=1).dropna()
    # The main ledger is read only.  Its entire spent count still deflates this
    # dedicated campaign, whose four declarations were persisted pre-computation.
    dsr = _dsr_reports(
        validation_returns,
        main_ledger,
        declaration_count=len(ledger["declarations"]),
    )
    pbo = probability_of_backtest_overfitting(
        aligned.to_numpy(dtype=float), n_splits=10, seed=SEED
    )

    future_poison = _future_poison_test(
        panel, scale=scales["C_FUNDED"], reference=results["C_FUNDED"]
    )
    order_test = _order_permutation_test(
        panel,
        scale=scales["C_FUNDED"],
        reference=results["C_FUNDED"],
        n_permutations=args.order_permutations,
    )
    concentration = {
        name: _concentration(results[name], panel, scale=scale)
        for name, scale in scales.items()
    }

    candidate_payloads: dict[str, Any] = {}
    ledger_results: dict[str, Any] = {}
    for name, scale in scales.items():
        validation_metrics = results[name].metrics
        cap_diagnostics = _trace_cap_diagnostics(pd.concat(
            [partition_traces[name][partition] for partition in partitions]
        ))
        historical_zero = all(
            not historical[name][partition]["official_breach"] for partition in partitions
        )
        worst_daily = max(
            historical[name][partition]["worst_intraday_daily_loss_pct_initial"]
            for partition in partitions
        )
        worst_dd = max(
            historical[name][partition]["worst_peak_to_intraday_bound_drawdown"]
            for partition in partitions
        )
        evaluation_pass_lower = _bootstrap_extreme(
            evaluation_bootstrap, name, "pass_probability", "lower", "min"
        )
        evaluation_breach_upper = _bootstrap_extreme(
            evaluation_bootstrap, name, "breach_probability", "upper", "max"
        )
        survival_12_lower = _bootstrap_extreme(
            survival_12m, name, "survival_probability", "lower", "min"
        )
        survival_24_lower = _bootstrap_extreme(
            survival_24m, name, "survival_probability", "lower", "min"
        )
        winners_return_proxy = _records_return(winner_cut[name])
        doubled_return = doubled_cost_validation[name]["metrics"].get("total_return", 0.0)
        scenario_matrix = {
            "base": {
                "status": "EVALUATED_DAILY_OHLC_PROVISIONAL",
                "historical_replay": historical[name],
                "bootstrap_12m": survival_12m,
            },
            "doubled_costs": {
                "status": "EVALUATED_ENGINE_RERUN",
                "validation": doubled_cost_validation[name],
                "bootstrap_12m": doubled_cost_survival_12m,
            },
            "volatility_1_5x_gap_2x": {
                "status": "DATA_BLOCKED",
                "reason": "trace has no separable gap/continuous-volatility components",
            },
            "one_30_min_liquidation_outage_per_year": {
                "status": "DATA_BLOCKED",
                "reason": "daily bars have no liquidation event clock or queued fill path",
            },
            "fills_50pct_worst_liquidity_sessions": {
                "status": "DATA_BLOCKED",
                "reason": "no order-level fills, rejects, depth, or executable liquidity series",
            },
            "one_missed_stop_per_year": {
                "status": "DATA_BLOCKED",
                "reason": "no stop-order event stream or next executable quote path",
            },
            "winners_cut_50pct_losses_unchanged": {
                "status": "DATA_BLOCKED_TRADE_PATH",
                "proxy_status": "NON_BINDING_DAY_LEVEL_SENSITIVITY_ONLY",
                "validation_return_proxy": winners_return_proxy,
                "bootstrap_12m": winners_cut_survival_12m,
                "reason": (
                    "The trace is aggregated by day and cannot cut each positive "
                    "trade by 50% while reconciling realised and floating equity."
                ),
            },
            "combined_severe": {
                "status": "DATA_BLOCKED",
                "reason": (
                    "cannot combine unsupported gap, outage, partial-fill, and missed-stop "
                    "events without inventing an intraday path"
                ),
            },
        }
        primary_only = name == "C_FUNDED"
        gates = [
            _gate(
                "exact_build_scale_calibration", "exact doubled gap component plus doubled ordinary costs",
                calibration["L_gap_semantics"], False,
                status="PROVISIONAL_CONSERVATIVE_PROXY",
            ),
            _gate("zero_base_official_breaches_all_partitions", "0", historical_zero, historical_zero),
            _gate("worst_intraday_daily_loss", "<= 1.80%", worst_daily, worst_daily <= 0.018),
            _gate("worst_drawdown", "<= 6.00%", worst_dd, worst_dd <= 0.06),
            _gate(
                "combined_historical_stress_zero_breaches", "0",
                "daily data lacks outage/partial-fill/missed-stop event stream", False,
                status="DATA_BLOCKED_EXECUTION_PATH",
            ),
            _gate(
                "evaluation_target_lower_wilson", ">= 70%", evaluation_pass_lower,
                evaluation_pass_lower is not None and evaluation_pass_lower >= 0.70,
                status="EVALUATED" if evaluation_pass_lower is not None else "SKIPPED_NON_BINDING",
            ),
            _gate(
                "evaluation_breach_upper_wilson", "<= 5%", evaluation_breach_upper,
                evaluation_breach_upper is not None and evaluation_breach_upper <= 0.05,
                status="EVALUATED" if evaluation_breach_upper is not None else "SKIPPED_NON_BINDING",
            ),
            _gate(
                "funded_survival_12m_lower_wilson", ">= 99%", survival_12_lower,
                survival_12_lower is not None and survival_12_lower >= 0.99,
                status="EVALUATED" if survival_12_lower is not None else "SKIPPED_NON_BINDING",
            ),
            _gate(
                "funded_survival_24m_lower_wilson", ">= 97.5%", survival_24_lower,
                survival_24_lower is not None and survival_24_lower >= 0.975,
                status="EVALUATED" if survival_24_lower is not None else "SKIPPED_NON_BINDING",
            ),
            _gate(
                "combined_stress_survival_12m_lower_wilson", ">= 95%",
                "daily data lacks required severe execution events", False,
                status="DATA_BLOCKED_EXECUTION_PATH",
            ),
            _gate("validation_sharpe", ">= 0.75", validation_metrics.get("sharpe"), validation_metrics.get("sharpe", 0.0) >= 0.75),
            _gate("validation_profit_factor", ">= 1.15", validation_metrics.get("profit_factor"), (validation_metrics.get("profit_factor") or 0.0) >= 1.15),
            _gate("validation_doubled_cost_return", "> 0", doubled_return, doubled_return > 0.0),
            _gate(
                "validation_winners_cut_50_return", "> 0",
                "day-level proxy available; exact trade/equity path unavailable",
                False, status="DATA_BLOCKED_TRADE_PATH",
            ),
            _gate("cpcv_positive_paths", ">= 12/15", cpcv[name].get("positive_paths"), cpcv[name].get("passed_12_of_15", False), status=cpcv[name].get("status", "EVALUATED")),
            _gate(
                "deflated_sharpe_ratio", ">= 0.95", dsr[name].get("dsr"),
                bool(dsr[name].get("passed")),
                status=dsr[name].get("status", "EVALUATED"),
            ),
            _gate("probability_backtest_overfitting", "<= 0.25", pbo.get("pbo"), pbo.get("pbo") is not None and pbo["pbo"] <= 0.25),
            _gate("profit_concentration", "top instrument/cluster <=35%; top-cluster removal >0", concentration[name], concentration[name]["passed"]),
            _gate("future_poison_exact", "identical", future_poison["passed"], future_poison["passed"]),
            _gate(
                "input_order_permutations", "100/100 identical", order_test.get("completed"),
                bool(order_test.get("passed")), status=order_test.get("status", "EVALUATED"),
            ),
            _gate(
                "exact_intraday_account_guard_replay", "all guard transitions executable",
                "daily OHLC cannot replay 1.2% block / 1.8% flatten / 6% persistent latch",
                False, status="DATA_BLOCKED_INTRADAY_GUARD",
            ),
            _gate(
                "exact_exposure_level_resampling", "originating risk base per open lot",
                "trace records one decision base per day, not each carried lot's original base",
                False, status="DATA_BLOCKED_CARRIED_POSITION_RISK_BASE",
            ),
            _gate(
                "entry_bar_stop_execution", "entry-bar stop/target order resolved and filled",
                "enabled; ambiguous daily OHLC is resolved stop-first",
                True, status="EVALUATED_DAILY_OHLC_PROVISIONAL",
            ),
            _gate(
                "exact_data_and_contract_adequacy", "minute bid/ask + symbol map + costs/swaps/FX/rules",
                "Yahoo underlying daily bars and unspecified firm/product",
                False, status="DATA_BLOCKED",
            ),
        ]
        passed = all(gate["passed"] for gate in gates)
        candidate_payloads[name] = {
            "candidate": name,
            "status": "PROVISIONAL_PAPER_ONLY" if passed else "FAILED_BINDING_GATES",
            "eligible_to_win": bool(primary_only),
            "robustness_only": not primary_only,
            "scale": scale,
            "scale_calibration": calibration,
            "risk_geometry": {
                "max_risk_per_trade": 0.0085 * scale,
                "risk_fraction_basis": (
                    "min(current equity, initial balance, remaining 3% daily-floor "
                    "buffer, remaining strict 10% EOD-trailing max-floor buffer)"
                ),
                "fresh_account_decision_sizing_base": 3_000.0,
                "planned_portfolio_stop_risk_cap": 0.0105,
                "single_symbol_stressed_loss_cap": 0.0045,
                "gross_exposure_cap": 0.75,
                "correlated_exposure_cap": 0.30,
                "position_notional_cap": 0.10,
                "max_positions": 5,
                "entry_order": "point_in_time_expected_value_then_instrument",
                "portfolio_risk_allocation": "simultaneous",
            },
            "validation_metrics": validation_metrics,
            "historical_replay": historical[name],
            "independent_partition_manifest": partition_manifests[name],
            "cap_diagnostics": cap_diagnostics,
            "doubled_cost_validation": doubled_cost_validation[name],
            "winners_cut_50_validation_return_proxy_non_binding": winners_return_proxy,
            "stress_scenarios": scenario_matrix,
            "bootstrap": {
                "evaluation_252_sessions": evaluation_bootstrap,
                "survival_12_months_365_firm_days": survival_12m,
                "survival_24_months_730_firm_days": survival_24m,
                "combined_severe": {
                    "status": "DATA_BLOCKED_EXECUTION_PATH",
                    "reason": "no minute liquidation/fill/missed-stop event stream",
                },
            },
            "cpcv": cpcv[name],
            "dsr": dsr[name],
            "pbo": pbo,
            "concentration": concentration[name],
            "future_poison": future_poison,
            "order_permutations": order_test,
            "net_payout_per_month_lower_95": None,
            "net_payout_blocker": "firm payout split, fees, and payout policy are unspecified",
            "gates": gates,
            "passed_all_binding_gates": passed,
        }
        ledger_results[name] = {
            "scale": scale,
            "validation_per_period_sharpe": sharpe_ratio(
                validation_returns[name].to_numpy(dtype=float), periods_per_year=1
            ),
            "passed_all_binding_gates": passed,
        }

    candidate_payloads["R_FUNDED"] = _blocked_candidate(
        "R_FUNDED",
        "INELIGIBLE_EXACT_SYMBOL_MAP_UNAVAILABLE",
        (
            "The exact selected-firm instrument list is not supplied. Although a research "
            "Book R implementation exists, its USD ETF family cannot be assumed executable "
            "on an unspecified funded platform. No proxy substitution is allowed."
        ),
    )
    candidate_payloads["PLATFORM_NATIVE_DIVERSIFIED_TREND"] = _blocked_candidate(
        "PLATFORM_NATIVE_DIVERSIFIED_TREND",
        "DATA_BLOCKED",
        (
            "The selected provider symbol list and raw one-minute-or-better bid/ask history "
            "have not been supplied; preregistration prohibits inventing ETF proxies."
        ),
    )
    ledger_results["R_FUNDED"] = {"status": candidate_payloads["R_FUNDED"]["status"]}
    ledger_results["PLATFORM_NATIVE_DIVERSIFIED_TREND"] = {
        "status": candidate_payloads["PLATFORM_NATIVE_DIVERSIFIED_TREND"]["status"]
    }
    ledger = update_funded_ledger_results(ledger_path, ledger, ledger_results)

    eligible_passers = [
        name for name in ("C_FUNDED",)
        if candidate_payloads[name]["passed_all_binding_gates"]
    ]
    # Payout ranking is impossible without a selected contract.  Even a statistical
    # pass therefore cannot silently become funded-ready.
    winner = eligible_passers[0] if len(eligible_passers) == 1 else None
    verdict = "PROVISIONAL_PAPER_ONLY" if winner else "NO_FUNDED_STRATEGY"
    blocking_reasons = [
        "Daily OHLC supplies a conservative co-extreme bound, not an observed intraday path.",
        "Exact one-minute bid/ask, spread/commission/swap, partial/rejected-fill, and liquidation-outage data are absent.",
        "The funded firm, product, account currency, executable symbols, news/weekend rules, and automation permission are not frozen.",
        "Book C's mixed quote currencies are not converted from executable FX snapshots in this historical engine.",
        "The daily backtester cannot counterfactually replay and persist the 1.2% session block, 1.8% flatten verification, and 6% cycle latch.",
        "The repository spent-trial ledger lacks the complete Sharpe history required to estimate DSR dispersion.",
        "Aggregate daily records cannot reproduce the registered positive-trade winner haircut.",
        "A day-level sizing base does not identify each carried position's originating funded risk base.",
    ]
    if not eligible_passers:
        blocking_reasons.insert(0, "No eligible candidate passed every binding preregistered gate.")

    payload = {
        "schema_version": SCHEMA_VERSION,
        "protocol": {
            "path": str(PROTOCOL_PATH.relative_to(REPO_DIR)),
            "sha256": protocol_sha,
            "registered_on": PROTOCOL_DATE,
            "historical_blind_claim": False,
            "evidence_ceiling": "PROVISIONAL_PAPER_ONLY",
        },
        "run_config": {
            "n_paths": args.n_paths,
            "default_n_paths": DEFAULT_N_PATHS,
            "order_permutations": args.order_permutations,
            "default_order_permutations": DEFAULT_ORDER_PERMUTATIONS,
            "bootstrap_chunk": args.bootstrap_chunk,
            "seed": SEED,
            "smoke_or_non_binding": bool(
                args.n_paths != DEFAULT_N_PATHS
                or args.order_permutations != DEFAULT_ORDER_PERMUTATIONS
                or args.skip_cpcv
                or args.skip_bootstrap
            ),
        },
        "isolation": {
            "dedicated_ledger": str(ledger_path),
            "dedicated_trial_count": len(ledger["declarations"]),
            "shared_trial_ledger": str(MAIN_LEDGER_PATH),
            "shared_trial_ledger_written": False,
            "book_or_paper_state_written": False,
            "output_json": str(out_json),
            "output_report": str(out_report),
        },
        "main_ledger_read_only_observation": {
            key: value for key, value in main_ledger.items() if key != "sharpes"
        },
        "data_manifest": data_manifest,
        "data_limitations": {
            "frequency": "daily OHLC",
            "conservative_intraday_bound": True,
            "bound_semantics": traces["C_FUNDED"].attrs.get("semantics"),
            "account_currency_conversion": "not executable/verified; monetary values are engine bookkeeping only",
            "entry_bar_stop_execution": (
                "enabled for every candidate/fold/stress; ambiguous daily OHLC resolves stop-first, "
                "which is conservative but not an observed intraday sequence"
            ),
            "monte_carlo_internal_threshold_semantics": (
                "1.8% daily and 6% maximum threshold touches terminate the path; "
                "later recovery is not credited and returns are not called guard-adjusted"
            ),
            "funded_sizing_base": (
                "implemented in the historical engine for each decision; min(current "
                "equity, £100k initial, remaining daily-floor buffer, remaining strict "
                "EOD-trailing max-floor buffer)"
            ),
            "carried_position_resampling": (
                "one decision base is recorded per day; originating bases for carried "
                "lots are unavailable, so exact exposure-level bootstrap is blocked"
            ),
            "official_status_ceiling": "PROVISIONAL_PAPER_ONLY",
        },
        "calibration": calibration,
        "candidates": candidate_payloads,
        "blocking_reasons": blocking_reasons,
        "winner": winner,
        "verdict": verdict,
    }
    _write_json(out_json, payload)
    _atomic_write(out_report, _render_report(payload))
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Frozen 2026-09-03 Funded-100K research/survival gate"
    )
    parser.add_argument("--out-json", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--out-report", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER_PATH))
    parser.add_argument(
        "--n-paths", type=int, default=DEFAULT_N_PATHS,
        help="common-random stationary-bootstrap paths (binding default: 100000)",
    )
    parser.add_argument(
        "--order-permutations", type=int, default=DEFAULT_ORDER_PERMUTATIONS,
        help="input-order permutations (binding default: 100)",
    )
    parser.add_argument(
        "--bootstrap-chunk", type=int, default=DEFAULT_BOOTSTRAP_CHUNK,
        help="bounded-memory path batch size; does not change requested path count",
    )
    parser.add_argument(
        "--skip-cpcv", action="store_true",
        help="engineering smoke only; skipped binding gate is recorded as failed",
    )
    parser.add_argument(
        "--skip-bootstrap", action="store_true",
        help="engineering smoke only; skipped binding gates are recorded as failed",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.n_paths <= 0:
        parser.error("--n-paths must be positive")
    if args.order_permutations < 0:
        parser.error("--order-permutations cannot be negative")
    if args.bootstrap_chunk <= 0:
        parser.error("--bootstrap-chunk must be positive")
    payload = run_gate(args)
    print(json.dumps({
        "verdict": payload["verdict"],
        "winner": payload["winner"],
        "evidence_ceiling": payload["protocol"]["evidence_ceiling"],
        "json": args.out_json,
        "report": args.out_report,
        "ledger": args.ledger,
    }, indent=2, sort_keys=True))
    return 0 if payload["verdict"] == "PROVISIONAL_PAPER_ONLY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
