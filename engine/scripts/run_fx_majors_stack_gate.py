#!/usr/bin/env python3
"""FX Majors Stack: book-level gates for the carry-tilt sleeve and the combined
A+B+C stack on the 7 majors (pre-registered:
data_store/fx_majors_stack_prereg_2026-07-17.md).

Sleeve B (carry tilt): CrossSectionalCarry on the 7 majors, headline 30/30
fractions with a quarterly-rotation cost-sensitivity variant.

Combined stack: per-instrument majority vote of the three pre-registered sleeves
- Sleeve A CarryTrendFilter (126/63/21/1.5/rule_based), Sleeve B CrossSectionalCarry
(headline), Sleeve C CurrencyCrossSectionalMomentum (63/k2/21). PortfolioBacktester
keys one strategy per instrument, so "combined" is a per-instrument vote adapter
(StackedStrategy): direction needs >= min_votes agreeing sleeves else FLAT. One
shared RiskManager / config risk caps bind via the normal PortfolioBacktester path.
Approximation (documented in the pre-reg): sleeves are not separately sized - the
combined signal is one position per instrument per direction.

Same three gates as everything else (DSR > 0.95 deflated by the shared ledger's
FULL count, PBO < 0.5 across the sleeve's config grid, CPCV median OOS Sharpe > 0
with > 50% of 15 paths positive), same honesty rules as run_candidate_check.py:
iteration window strictly < 2025-01-01, trials recorded BEFORE the runs.

Composition mirrors scripts/run_portfolio_gate.py (full-window runs double as
DSR/PBO inputs AND the trade-metrics source; run_portfolio_cpcv for the CPCV leg).
No new math lives here.

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_fx_majors_stack_gate.py              # both sleeves
    .venv-mac/bin/python scripts/run_fx_majors_stack_gate.py --sleeve carry
    .venv-mac/bin/python scripts/run_fx_majors_stack_gate.py --determinism-check

Exit code 0 if every gated sleeve passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")
ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from apex_quant.backtest.portfolio import PortfolioBacktester  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor, ParquetStore, clean  # noqa: E402
from apex_quant.data.rates import CSVRateProvider  # noqa: E402
from apex_quant.risk.types import Direction, Signal  # noqa: E402
from apex_quant.strategies.base import Strategy  # noqa: E402
from apex_quant.strategies.carry import CrossSectionalCarry  # noqa: E402
from apex_quant.strategies.carry_trend_filter import CarryTrendFilter  # noqa: E402
from apex_quant.strategies.currency_momentum import CurrencyCrossSectionalMomentum  # noqa: E402
from apex_quant.validation.metrics import (  # noqa: E402
    deflated_sharpe_ratio,
    probability_of_backtest_overfitting,
    sharpe_ratio,
)
from apex_quant.validation.portfolio_report import (  # noqa: E402
    DSR_THRESHOLD,
    PBO_THRESHOLD,
    run_portfolio_cpcv,
)
from apex_quant.validation.trials import TrialLedger  # noqa: E402

LEDGER_PATH = ENGINE_DIR / "data_store" / "validation" / "trial_ledger.json"
RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "fx_majors_stack_gate_2026-07-17.json"
DEFAULT_HOLDOUT_START = "2025-01-01"
MIN_BARS = 300
WARMUP = 250
HORIZON = 21

MAJORS = ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD", "NZD/USD"]

# ── Pre-registered configurations (see the pre-reg doc; do not extend ad hoc) ──
CARRY_GRID = [
    {"long_frac": 0.30, "short_frac": 0.30, "holding_horizon": 21, "reward_risk": 1.5},  # headline
    {"long_frac": 0.30, "short_frac": 0.30, "holding_horizon": 63, "reward_risk": 1.5},  # quarterly-rotation variant
]
CTF_PARAMS = {"momentum_lookback": 126, "vol_window": 63, "holding_horizon": 21,
              "reward_risk": 1.5, "regime_method": "rule_based", "timeframe": "1d"}
MOM_PARAMS = {"lookback": 63, "k": 2, "holding_horizon": 21}
STACK_GRID = [
    {"min_votes": 2},   # headline: 2-of-3 agreement
    {"min_votes": 3},   # unanimity variant
]


class StackedStrategy(Strategy):
    """Per-instrument majority vote over the three stack sleeves."""

    name = "fx_majors_stack"

    def __init__(self, instrument: str, sleeves: list, *, min_votes: int,
                 holding_horizon: int = 21, reward_risk: float = 1.5) -> None:
        self.instrument = instrument
        self.sleeves = sleeves
        self.min_votes = min_votes
        self.holding_horizon = holding_horizon
        self.reward_risk = reward_risk
        self.timeframe = "1d"

    def generate(self, pit: PointInTimeAccessor, t, instrument: str = "") -> Signal:
        inst = instrument or self.instrument
        sigs = [s.generate(pit, t, inst) for s in self.sleeves]
        longs = [s for s in sigs if s.direction == Direction.LONG]
        shorts = [s for s in sigs if s.direction == Direction.SHORT]
        if len(longs) >= self.min_votes and len(longs) > len(shorts):
            direction, agreeing = Direction.LONG, longs
        elif len(shorts) >= self.min_votes and len(shorts) > len(longs):
            direction, agreeing = Direction.SHORT, shorts
        else:
            return Signal(
                instrument=inst, direction=Direction.FLAT, probability=0.5,
                reward_risk=self.reward_risk, timeframe=self.timeframe,
                rationale=f"stack: no majority (L{len(longs)}/S{len(shorts)} of {len(sigs)})",
            )
        p = float(np.mean([s.probability for s in agreeing]))
        conf = float(np.mean([s.confidence for s in agreeing]))
        return Signal(
            instrument=inst, direction=direction, probability=p, reward_risk=self.reward_risk,
            confidence=conf, timeframe=self.timeframe,
            rationale=f"stack {direction.value} {len(agreeing)}/{len(sigs)} votes | p={p:.2f}",
        )


class FXMajorsStack:
    """The combined A+B+C book as one portfolio-level model (EnsembleVote-style:
    exposes only ``.strategies()``; rule-based sleeves have nothing to fit, so
    CPCV's purged train split is intentionally unused)."""

    def __init__(self, panel: dict, *, min_votes: int = 2,
                 ctf_params: dict | None = None, carry_params: dict | None = None,
                 mom_params: dict | None = None) -> None:
        ctf_params = dict(CTF_PARAMS if ctf_params is None else ctf_params)
        carry_params = dict(CARRY_GRID[0] if carry_params is None else carry_params)
        mom_params = dict(MOM_PARAMS if mom_params is None else mom_params)
        self.params = {"min_votes": min_votes, "ctf": ctf_params,
                       "carry": carry_params, "mom": mom_params}
        provider = CSVRateProvider()
        self._ctf = {inst: CarryTrendFilter(instrument=inst, rate_provider=provider, **ctf_params)
                     for inst in panel}
        carry = CrossSectionalCarry(panel, provider, **carry_params).strategies()
        mom = CurrencyCrossSectionalMomentum(panel, **mom_params).strategies()
        self._strategies = {
            inst: StackedStrategy(inst, [self._ctf[inst], carry[inst], mom[inst]],
                                  min_votes=min_votes)
            for inst in panel
        }

    def strategies(self) -> dict:
        return dict(self._strategies)


def _utc(ts) -> pd.Timestamp:
    ts = pd.Timestamp(ts)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _max_gross_leverage(res) -> float:
    """Approx peak gross leverage (same helper as scripts/run_portfolio_gate.py)."""
    eq = res.equity
    if eq.empty or not res.trades:
        return 0.0
    gross = pd.Series(0.0, index=eq.index)
    for tr in res.trades:
        t0, t1 = _utc(tr.entry_time), _utc(tr.exit_time)
        gross[(gross.index >= t0) & (gross.index < t1)] += abs(tr.entry_price * tr.units)
    lev = (gross / eq.replace(0.0, np.nan)).max()
    return float(lev) if np.isfinite(lev) else 0.0


def _cap_families(constraint_log: dict) -> str:
    fam: dict[str, int] = {}
    for k, v in constraint_log.items():
        fam[k.split("=")[0]] = fam.get(k.split("=")[0], 0) + v
    return ", ".join(f"{k}x{v}" for k, v in sorted(fam.items())) or "none"


def _turnover_per_year(res, n_years: float) -> float:
    """Realized round trips per year across the book (trades = closed round trips)."""
    return len(res.trades) / n_years if n_years > 0 else 0.0


def _gate(name: str, rets: pd.Series, trial_sharpes: list[float], pbo: dict,
          cpcv: dict, n_trials: int) -> dict:
    """The three gates, identical to portfolio_report.run_portfolio_validation's."""
    dsr = deflated_sharpe_ratio(rets.to_numpy(), trial_sharpes, 252, n_trials=n_trials)
    dsr_pass = dsr.get("dsr", 0.0) > DSR_THRESHOLD
    pbo_val = pbo.get("pbo")
    pbo_pass = pbo_val is not None and pbo_val < PBO_THRESHOLD
    cpcv_pass = cpcv.get("oos_sharpe_median", 0.0) > 0 and cpcv.get("frac_positive", 0.0) > 0.5
    passed = bool(dsr_pass and pbo_pass and cpcv_pass)
    return {
        "book": name, "passed": passed,
        "dsr_pass": dsr_pass, "pbo_pass": pbo_pass, "cpcv_pass": cpcv_pass,
        "dsr": dsr, "pbo": pbo, "cpcv": cpcv,
        "reasons": [
            f"DSR {dsr.get('dsr', 0):.3f} {'>' if dsr_pass else '<='} {DSR_THRESHOLD} "
            f"(deflated by {dsr.get('n_trials')} trials)",
            f"PBO {pbo_val if pbo_val is not None else 'n/a'} "
            f"{'<' if pbo_pass else '>='} {PBO_THRESHOLD} (config-selection overfit probability)",
            f"CPCV median OOS Sharpe {cpcv.get('oos_sharpe_median', 0):.3f}, "
            f"{cpcv.get('frac_positive', 0)*100:.0f}% of {cpcv.get('n_paths', 0)} paths positive",
        ],
    }


def _run_book_gate(sleeve: str, grid: list[dict], model_factory, ledger_meta: dict,
                   panel: dict, pits: dict, timeframes: dict, cfg,
                   no_ledger: bool) -> dict:
    """One sleeve through the full gate. Full-window runs per config -> DSR/PBO +
    trade metrics; CPCV on the headline. Trials recorded BEFORE the runs."""
    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not no_ledger:
        for params in grid:
            ledger.record({**ledger_meta, "params": params})
        ledger.save(LEDGER_PATH)
    used_trials = ledger.n_trials if not no_ledger else n_before + len(grid)

    print("=" * 72, flush=True)
    print(f"STACK GATE sleeve={sleeve} | grid={len(grid)} config(s) "
          f"| ledger n_trials {n_before} -> {ledger.n_trials if not no_ledger else n_before} "
          f"| DSR deflation uses n_trials={used_trials}", flush=True)

    # 1. Full-window run per config -> returns (DSR/PBO) + trade metrics.
    results: dict[str, dict] = {}
    returns_by_cfg: dict[str, pd.Series] = {}
    n_years = (max(df.index[-1] for df in panel.values())
               - min(df.index[0] for df in panel.values())).days / 365.25
    for i, params in enumerate(grid):
        name = f"{sleeve}_cfg{i}"
        t_start = time.time()
        model = model_factory(panel, **params)
        res = PortfolioBacktester(cfg, exit_mode="managed").run(
            pits, model.strategies(), timeframes=timeframes,
            warmup=WARMUP, periods_per_year=252,
        )
        rets = res.returns
        returns_by_cfg[name] = rets
        m = res.metrics
        results[name] = {"params": params, "metrics": m,
                         "max_gross_leverage": _max_gross_leverage(res),
                         "constraint_log": res.constraint_log,
                         "per_instrument": res.per_instrument,
                         "round_trips_per_year": _turnover_per_year(res, n_years),
                         "full_window_sharpe_per_period": sharpe_ratio(rets, periods_per_year=1)}
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] full run {name}: "
              f"{time.time() - t_start:.0f}s | {res.summary()}", flush=True)
        if not m.get("insufficient_data"):
            print(f"    expectancy={m['expectancy_pnl']:.2f} pnl/trade "
                  f"({m['expectancy_pct']*100:.3f}%/trade) profit_factor={m.get('profit_factor')} "
                  f"maxDD={m['max_drawdown']*100:.1f}% lev~{results[name]['max_gross_leverage']:.2f}x "
                  f"| turnover={results[name]['round_trips_per_year']:.1f} RT/yr "
                  f"| caps bound: {_cap_families(res.constraint_log)}", flush=True)

    # 2. PBO across the sleeve's config grid.
    aligned = pd.concat(list(returns_by_cfg.values()), axis=1).dropna()
    M = aligned.to_numpy()
    pbo = (probability_of_backtest_overfitting(M, n_splits=cfg.validation.pbo.n_splits, seed=cfg.seed)
           if M.shape[1] >= 2 and M.shape[0] >= 40 else {"pbo": None, "note": "insufficient matrix"})
    print(f"PBO across {M.shape[1]} configs: {pbo}", flush=True)

    # 3. CPCV OOS distribution for the headline config.
    trial_sharpes = [results[f"{sleeve}_cfg{i}"]["full_window_sharpe_per_period"]
                     for i in range(len(grid))]
    t_start = time.time()
    cpcv = run_portfolio_cpcv(
        panel, pits, model_factory, grid[0],
        cfg=cfg, timeframes=timeframes, warmup=WARMUP, horizon=HORIZON,
        periods_per_year=252, exit_mode="managed",
    )
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] CPCV {sleeve}: "
          f"{time.time() - t_start:.0f}s | paths={cpcv['oos_sharpe_paths']}", flush=True)

    headline = f"{sleeve}_cfg0"
    verdict = _gate(headline, returns_by_cfg[headline], trial_sharpes, pbo, cpcv, used_trials)
    results[headline]["cpcv"] = cpcv
    for name in results:
        results[name]["gate_input"] = name == headline
    print(f"  {headline}: VERDICT {'PASS' if verdict['passed'] else 'REJECT'}", flush=True)
    for r in verdict["reasons"]:
        print(f"    - {r}", flush=True)

    return {"sleeve": sleeve, "n_trials_before": n_before, "n_trials_used": used_trials,
            "ledger_recorded": not no_ledger, "pbo": pbo, "configs": results,
            "verdict": verdict}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FX majors stack book-level gates "
                                             "(iteration window only).")
    ap.add_argument("--sleeve", choices=["carry", "stack", "both"], default="both")
    ap.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START,
                    help=f"iteration data is strictly before this date (default {DEFAULT_HOLDOUT_START})")
    ap.add_argument("--no-ledger", action="store_true",
                    help="smoke-test mode: do NOT record trials; DSR deflates by the "
                         "count the run WOULD have used")
    ap.add_argument("--determinism-check", action="store_true",
                    help="after the gates, re-run the carry headline backtest and assert "
                         "an identical equity series (seed 42)")
    args = ap.parse_args(argv)

    cfg = get_config()
    store = ParquetStore(cfg.store_path)
    holdout_start = _utc(args.holdout_start)

    panel: dict[str, pd.DataFrame] = {}
    for inst in MAJORS:
        df = store.load(inst, "1d")
        if df.empty:
            print(f"skip {inst}: no cached 1d data")
            continue
        df = clean(df)
        df = df[df.index < holdout_start]
        if len(df) < MIN_BARS:
            print(f"skip {inst}: {len(df)} bars in iteration window")
            continue
        panel[inst] = df
    if len(panel) < 2:
        print("need >= 2 instruments for a portfolio gate")
        return 1
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    timeframes = {k: "1d" for k in panel}

    print("=" * 72, flush=True)
    print(f"FX MAJORS STACK GATE 2026-07-17 | mode=ITERATION (strictly < {args.holdout_start})")
    print(f"universe: {len(panel)} majors | window: {min(df.index[0] for df in panel.values()).date()} "
          f"-> {max(df.index[-1] for df in panel.values()).date()}")
    print("=" * 72, flush=True)

    provider = CSVRateProvider()

    def carry_factory(p, **params):
        return CrossSectionalCarry(p, provider, **params)

    def stack_factory(p, **params):
        return FXMajorsStack(p, **params)

    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "mode": "iteration", "holdout_start": args.holdout_start,
           "universe": list(panel.keys()), "sleeves": {}}
    verdicts: dict[str, dict] = {}

    if args.sleeve in ("carry", "both"):
        r = _run_book_gate(
            "carry", CARRY_GRID, carry_factory,
            {"instrument": "FX7_PORTFOLIO", "timeframe": "1d", "factory": "cross_sectional_carry"},
            panel, pits, timeframes, cfg, args.no_ledger)
        out["sleeves"]["carry"] = r
        verdicts["carry"] = r["verdict"]

    if args.sleeve in ("stack", "both"):
        r = _run_book_gate(
            "stack", STACK_GRID, stack_factory,
            {"instrument": "FX7_STACK", "timeframe": "1d", "factory": "fx_majors_stack"},
            panel, pits, timeframes, cfg, args.no_ledger)
        out["sleeves"]["stack"] = r
        verdicts["stack"] = r["verdict"]

    if args.determinism_check:
        model = carry_factory(panel, **CARRY_GRID[0])
        res1 = PortfolioBacktester(cfg, exit_mode="managed").run(
            pits, model.strategies(), timeframes=timeframes, warmup=WARMUP, periods_per_year=252)
        model2 = carry_factory(panel, **CARRY_GRID[0])
        res2 = PortfolioBacktester(cfg, exit_mode="managed").run(
            pits, model2.strategies(), timeframes=timeframes, warmup=WARMUP, periods_per_year=252)
        same = res1.equity.equals(res2.equity) and len(res1.trades) == len(res2.trades)
        out["determinism_check"] = {"equity_identical": bool(res1.equity.equals(res2.equity)),
                                    "n_trades_identical": len(res1.trades) == len(res2.trades),
                                    "passed": bool(same), "seed": cfg.seed}
        print(f"determinism-check (seed {cfg.seed}): equity identical={res1.equity.equals(res2.equity)}, "
              f"trades {len(res1.trades)} vs {len(res2.trades)} -> {'OK' if same else 'MISMATCH'}", flush=True)

    print("\n" + "=" * 72, flush=True)
    for name, v in verdicts.items():
        print(f"  {name}: VERDICT {'PASS' if v['passed'] else 'REJECT'}")
        for rsn in v["reasons"]:
            print(f"    - {rsn}")
    print("=" * 72, flush=True)

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_PATH, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"results written to {RESULTS_PATH}", flush=True)
    return 0 if verdicts and all(v["passed"] for v in verdicts.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
