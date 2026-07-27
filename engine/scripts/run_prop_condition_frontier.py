"""Pre-registered PROP-CONDITION RATE FRONTIER study on the trend-ensemble book.

Pre-registration: engine/data_store/prop_condition_prereg.md (2026-07-27, written BEFORE
any sweep run; the 12 trials are recorded before execution, dedup-safe). This is a
MEASUREMENT study, not an adoption gate: it reports the efficient frontier of monthly
rate vs funded-rule survival so the future funded runner (config.prop.yaml) can be placed
at an informed operating point. Nothing here changes a certified default.

Base: ADOPTED multi-horizon trend ensemble (momentum_lookbacks [63,126,252]) on Book H
gold, certified panel insertion order (EQUITY_CORE first), certified machinery and costs.
ONLY two risk knobs vary (the full pre-registered selection set, 12 configs):

    max_risk_per_trade in {0.0075, 0.01, 0.0125, 0.015}
    max_portfolio_risk in {0.025, 0.035, 0.045}

Per config: full-window metrics + monthly tail + daily-loss distribution, then a Monte
Carlo per firm profile (prereg section 4): trade outcomes resampled from the config's OWN
backtest trade-return pool with the config's OWN empirical closures-per-day distribution,
20,000 paths, seeded from SeedSequence([42, config_index, firm_index]):

    ftmo_1step        target +10%, daily -3% of day-start equity, 10% EOD-trailing floor
    the5ers_pro_growth target +6%, daily -5% of day-start equity, 5% static floor

Attempt cap 252 trading days (12 months; timeouts count as not-passed). Funded 12-month
survival simulated per cell with a fresh balance and reset peak. EOD-only: a daily-bar
backtest has no intraday path (prereg section 9). Iteration window strictly < 2025-01-01.
Determinism: run twice via --out <twin>; payload byte-identical modulo generated_at and
the ledger bookkeeping line.

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_prop_condition_frontier.py                    # full study
    .venv-mac/bin/python scripts/run_prop_condition_frontier.py --instruments AAPL,MSFT,NVDA --no-ledger
    .venv-mac/bin/python scripts/run_prop_condition_frontier.py --out <twin.json>  # determinism rerun

Exit code 0 on successful completion (measurement study: no verdict).
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")
ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from apex_quant.backtest.portfolio import PortfolioBacktester  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor, ParquetStore, clean  # noqa: E402
from apex_quant.validation.trials import TrialLedger  # noqa: E402

from run_portfolio_gate import (  # noqa: E402
    COMMON_PARAMS,
    DEFAULT_HOLDOUT_START,
    LEDGER_PATH,
    MIN_BARS,
    WARMUP,
    TrendBook,
    _max_gross_leverage,
    _utc,
)
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC  # noqa: E402
from run_portfolio_gate_cf_cvar import _monthly_tail_stats  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402

DEFAULT_RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "prop_condition_frontier_2026-07-27.json"

UNIVERSE_GOLD = EQUITY_CORE + [GOLD_ETC]
ENSEMBLE_LOOKBACKS = [63, 126, 252]
GOLD_PARAMS = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252,
               "momentum_lookbacks": ENSEMBLE_LOOKBACKS}

# ── The pre-registered selection set: exactly 12 configs (prereg section 2) ────
# Declaration order fixes the MC seed stream per config (prereg section 4): risk-major.
RISKS = [0.0075, 0.01, 0.0125, 0.015]
CAPS = [0.025, 0.035, 0.045]
CONFIGS: dict[str, dict] = {}
for _r in RISKS:
    for _c in CAPS:
        _name = f"prop_r{int(round(_r * 10000)):03d}_cap{int(round(_c * 1000)):03d}"
        CONFIGS[_name] = {"max_risk_per_trade": _r, "max_portfolio_risk": _c}

# ── Firm profiles, fixed by the prereg (section 4) ─────────────────────────────
FIRMS = {
    "ftmo_1step": {"target": 0.10, "daily_limit": 0.03, "floor": 0.10, "floor_kind": "eod_trailing"},
    "the5ers_pro_growth": {"target": 0.06, "daily_limit": 0.05, "floor": 0.05, "floor_kind": "static"},
}
N_PATHS = 20000
MAX_DAYS = 252          # 12-month attempt cap; timeouts count as not-passed
FUNDED_DAYS = 252       # funded 12-month survival window
DAYS_PER_MONTH = 21
SEED = 42

# Reference points (no new trials): certified 252-only anchor and the adopted ensemble at
# the certified caps, both already in the ledger (prereg section 2).
REFERENCE = {
    "certified_252_mrpt0.01_cap0.065": {
        "source": "engine/data_store/validation/book_h_gapaware_2026-07-22.json",
        "sharpe": 0.86284, "max_drawdown": 0.16315, "worst_daily_return": -0.0509,
        "avg_monthly_pnl": 1783.0,
    },
    "ensemble_63_126_252_mrpt0.01_cap0.065": {
        "source": "engine/data_store/validation/trend_ensemble_gate_2026-07-27.json",
        "sharpe": 0.92377, "max_drawdown": 0.15921, "worst_daily_return": -0.034487,
        "avg_monthly_pnl": 2001.68,
    },
}


def _cfg(mrpt: float, cap: float):
    """Certified cfg with ONLY the two swept risk knobs overridden (prereg section 2)."""
    cfg = copy.deepcopy(get_config())
    cfg.risk.max_risk_per_trade = mrpt
    cfg.risk.max_portfolio_risk = cap
    return cfg


def _trade_return_pool(res) -> tuple[np.ndarray, np.ndarray, dict]:
    """Per-trade returns (pnl / equity as-of entry date) and the empirical closures-per-day
    distribution over the equity curve's trading days (prereg section 3)."""
    eq = res.equity
    pool = np.empty(len(res.trades), dtype=float)
    exit_dates: dict[pd.Timestamp, int] = {}
    for i, tr in enumerate(res.trades):
        t_in = pd.Timestamp(tr.entry_time)
        t_in = t_in.tz_localize("UTC") if t_in.tzinfo is None else t_in
        e0 = eq.asof(t_in)
        pool[i] = tr.pnl / (float(e0) if np.isfinite(e0) and e0 > 0 else float(eq.iloc[0]))
        t_out = pd.Timestamp(tr.exit_time)
        t_out = t_out.tz_localize("UTC") if t_out.tzinfo is None else t_out
        exit_dates[t_out.normalize()] = exit_dates.get(t_out.normalize(), 0) + 1
    days = eq.index.normalize()
    counts = np.zeros(len(days), dtype=int)
    pos = {d: j for j, d in enumerate(days)}
    for d, n in exit_dates.items():
        if d in pos:
            counts[pos[d]] += n
    summary = {
        "n_trades": int(len(pool)),
        "n_days": int(len(days)),
        "closures_per_day_mean": round(float(counts.mean()), 6),
        "closures_per_day_max": int(counts.max()),
        "trade_return_mean": round(float(pool.mean()), 8),
        "trade_return_std": round(float(pool.std(ddof=1)), 8) if len(pool) > 1 else 0.0,
        "trade_return_min": round(float(pool.min()), 8),
        "trade_return_max": round(float(pool.max()), 8),
    }
    return pool, counts, summary


def _daily_loss_stats(rets: pd.Series) -> dict:
    """Daily-loss distribution from the run's daily equity returns (prereg section 3)."""
    r = rets.dropna()
    worst5 = r.nsmallest(5)
    return {
        "n_days": int(len(r)),
        "min": round(float(r.min()), 6),
        "p01": round(float(r.quantile(0.01)), 6),
        "p05": round(float(r.quantile(0.05)), 6),
        "worst_5_days": [
            {"date": str(d.date()), "ret": round(float(v), 6)} for d, v in worst5.items()
        ],
        "frac_days_le_-2.5pct": round(float((r <= -0.025).mean()), 6),
        "frac_days_le_-3pct": round(float((r <= -0.03).mean()), 6),
        "frac_days_le_-5pct": round(float((r <= -0.05).mean()), 6),
    }


def _draw_day_returns(rng: np.random.Generator, pool: np.ndarray, counts: np.ndarray,
                      n_paths: int) -> np.ndarray:
    """One simulated day: closure counts from the empirical distribution, then that many
    trade returns i.i.d. from the config's pool; day return = sum (prereg section 4)."""
    k = counts[rng.integers(0, len(counts), size=n_paths)]
    total = int(k.sum())
    if total == 0:
        return np.zeros(n_paths)
    draws = pool[rng.integers(0, len(pool), size=total)]
    path_ids = np.repeat(np.arange(n_paths), k)
    return np.bincount(path_ids, weights=draws, minlength=n_paths)


def _simulate(rng: np.random.Generator, pool: np.ndarray, counts: np.ndarray, firm: dict,
              n_days: int) -> dict:
    """One firm-profile sim: n_paths paths x n_days days. Status per path:
    0 active at end (timeout/survived), 1 passed (target), 2 daily-rule fail, 3 floor fail.
    Check order on EOD equity: daily breach -> floor breach -> target (prereg section 4).
    Only ACTIVE paths accrue returns; a passed/failed path's equity is frozen."""
    eq = np.ones(N_PATHS)
    peak = np.ones(N_PATHS)
    active = np.ones(N_PATHS, dtype=bool)
    status = np.zeros(N_PATHS, dtype=int)
    pass_day = np.full(N_PATHS, n_days + 1, dtype=int)
    breach_events = 0
    active_days = 0
    trailing = firm["floor_kind"] == "eod_trailing"
    target = firm["target"]  # inf on the funded phase (no profit target once funded)
    for day in range(n_days):
        if not active.any():
            break
        day_start = eq.copy()
        r = _draw_day_returns(rng, pool, counts, N_PATHS)
        eq = np.where(active, eq * (1.0 + r), eq)
        active_days += int(active.sum())
        # 1. daily-loss rule
        daily_bust = active & (eq <= day_start * (1.0 - firm["daily_limit"]))
        breach_events += int(daily_bust.sum())
        status[daily_bust] = 2
        active &= ~daily_bust
        # 2. floor
        if trailing:
            peak = np.maximum(peak, eq)
            floor_bust = active & (eq <= peak * (1.0 - firm["floor"]))
        else:
            floor_bust = active & (eq <= 1.0 - firm["floor"])
        status[floor_bust] = 3
        active &= ~floor_bust
        # 3. target (skipped when target is inf: funded phase has no target)
        hit = active & (eq >= 1.0 + target)
        pass_day[hit] = day + 1
        status[hit] = 1
        active &= ~hit
    return {"eq": eq, "status": status, "pass_day": pass_day,
            "breach_events": breach_events, "active_days": active_days}


def _mc_cell(pool: np.ndarray, counts: np.ndarray, firm: dict, cfg_idx: int, firm_idx: int) -> dict:
    ss = np.random.SeedSequence([SEED, cfg_idx, firm_idx])
    rng_eval, rng_funded = (np.random.default_rng(s) for s in ss.spawn(2))

    ev = _simulate(rng_eval, pool, counts, firm, MAX_DAYS)
    passed = ev["status"] == 1
    n_pass = int(passed.sum())
    med_months = (float(np.median(ev["pass_day"][passed])) / DAYS_PER_MONTH) if n_pass else None
    eval_out = {
        "n_paths": N_PATHS,
        "max_days": MAX_DAYS,
        "pass_prob": round(n_pass / N_PATHS, 4),
        "median_months_to_pass": round(med_months, 2) if med_months is not None else None,
        "timeout_frac": round(float((ev["status"] == 0).mean()), 4),
        "fail_daily_frac": round(float((ev["status"] == 2).mean()), 4),
        "fail_floor_frac": round(float((ev["status"] == 3).mean()), 4),
        "daily_breach_day_rate": (round(ev["breach_events"] / ev["active_days"], 8)
                                  if ev["active_days"] else None),
    }

    fu = _simulate(rng_funded, pool, counts, {**firm, "target": float("inf")}, FUNDED_DAYS)
    survived = fu["status"] == 0  # funded has no target: survive = no daily/floor breach
    surv_eq = fu["eq"][survived]
    monthly = (surv_eq ** (1.0 / (FUNDED_DAYS / DAYS_PER_MONTH)) - 1.0) if surv_eq.size else np.array([])
    funded_out = {
        "n_paths": N_PATHS,
        "months": FUNDED_DAYS / DAYS_PER_MONTH,
        "survival_12mo": round(float(survived.mean()), 4),
        "fail_daily_frac": round(float((fu["status"] == 2).mean()), 4),
        "fail_floor_frac": round(float((fu["status"] == 3).mean()), 4),
        "median_monthly_pct_survivors": (round(float(np.median(monthly)) * 100.0, 3)
                                         if monthly.size else None),
    }
    return {"eval": eval_out, "funded": funded_out}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered prop-condition rate frontier "
                                             "(measurement study, iteration window only).")
    ap.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START)
    ap.add_argument("--instruments", default="",
                    help="comma-separated subset of the gold universe (smoke testing)")
    ap.add_argument("--no-ledger", action="store_true",
                    help="smoke-test mode: do NOT record trials")
    ap.add_argument("--out", default=str(DEFAULT_RESULTS_PATH),
                    help="results JSON path (use a twin path for the determinism rerun)")
    args = ap.parse_args(argv)

    base_cfg = get_config()
    store = ParquetStore(base_cfg.store_path)
    holdout_start = _utc(args.holdout_start)
    subset = {s.strip() for s in args.instruments.split(",") if s.strip()}
    results_path = Path(args.out)

    crypto = list(base_cfg.data.crypto)
    wanted = sorted(set(UNIVERSE_GOLD) | set(crypto) | set(FX_MAJORS_7))
    master: dict[str, pd.DataFrame] = {}
    for inst in wanted:
        if subset and inst not in subset:
            continue
        df = store.load(inst, "1d")
        if df.empty:
            print(f"skip {inst}: no cached 1d data")
            continue
        df = clean(df)
        df = df[df.index < holdout_start]
        if len(df) < MIN_BARS:
            print(f"skip {inst}: {len(df)} bars in iteration window")
            continue
        master[inst] = df
    # Certified panel insertion order (EQUITY_CORE first) — ordering-sensitive.
    panel = {inst: master[inst] for inst in UNIVERSE_GOLD + crypto + FX_MAJORS_7 if inst in master}
    if len(panel) < 2:
        print("need >= 2 instruments for the frontier study")
        return 1
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    timeframes = {k: "1d" for k in panel}

    # Record the 12 pre-registered trials BEFORE running (exact keys dedup on re-runs).
    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not args.no_ledger:
        for name, knobs in CONFIGS.items():
            ledger.record({"book": name, "universe": "book_h_gold_39", "timeframe": "1d",
                           "factory": "trend_book_mtf", "kind": "prop_condition_frontier",
                           "momentum_lookbacks": ENSEMBLE_LOOKBACKS,
                           "max_risk_per_trade": knobs["max_risk_per_trade"],
                           "max_portfolio_risk": knobs["max_portfolio_risk"],
                           "params": GOLD_PARAMS})
        ledger.save(LEDGER_PATH)

    print("=" * 72, flush=True)
    print(f"PROP-CONDITION RATE FRONTIER (measurement study) 2026-07-27 | "
          f"mode=ITERATION (strictly < {args.holdout_start})")
    print(f"universe: {len(panel)} instruments | configs: {len(CONFIGS)} | "
          f"MC: {N_PATHS} paths x {MAX_DAYS}d eval + {FUNDED_DAYS}d funded, seed {SEED}")
    print(f"ledger n_trials {n_before} -> "
          f"{ledger.n_trials if not args.no_ledger else n_before} "
          f"({'recorded' if not args.no_ledger else 'no-ledger smoke'})")
    print("=" * 72, flush=True)

    results: dict[str, dict] = {}
    for cfg_idx, (name, knobs) in enumerate(CONFIGS.items()):
        t_start = time.time()
        cfg = _cfg(knobs["max_risk_per_trade"], knobs["max_portfolio_risk"])
        model = TrendBook(panel, **GOLD_PARAMS)
        res = PortfolioBacktester(cfg, exit_mode="managed").run(
            pits, model.strategies(), timeframes=timeframes,
            warmup=WARMUP, periods_per_year=252)
        m = res.metrics
        pool, counts, dist_summary = _trade_return_pool(res)
        mc = {firm: _mc_cell(pool, counts, FIRMS[firm], cfg_idx, firm_idx)
              for firm_idx, firm in enumerate(FIRMS)}
        results[name] = {
            "knobs": knobs,
            "params": GOLD_PARAMS,
            "metrics": m,
            "max_gross_leverage": _max_gross_leverage(res),
            "constraint_log": res.constraint_log,
            "monthly_tail": _monthly_tail_stats(res, base_cfg.backtest.initial_equity),
            "daily_loss": _daily_loss_stats(res.returns),
            "trade_distribution": dist_summary,
            "mc": mc,
        }
        mt = results[name]["monthly_tail"]
        dl = results[name]["daily_loss"]
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {name}: "
              f"{time.time() - t_start:.0f}s | sharpe={m['sharpe']:.5f} "
              f"trades={m['n_trades']} avg {mt['avg_monthly_pnl']:+,.0f}/mo "
              f"maxDD={m['max_drawdown']*100:.2f}% worst_day={dl['min']*100:.2f}% | "
              f"FTMO pass={mc['ftmo_1step']['eval']['pass_prob']*100:.1f}% "
              f"surv={mc['ftmo_1step']['funded']['survival_12mo']*100:.1f}% | "
              f"5ers pass={mc['the5ers_pro_growth']['eval']['pass_prob']*100:.1f}% "
              f"surv={mc['the5ers_pro_growth']['funded']['survival_12mo']*100:.1f}%",
              flush=True)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "iteration",
        "study": "measurement (efficient frontier; NOT an adoption gate)",
        "holdout_start": args.holdout_start,
        "prereg": "engine/data_store/prop_condition_prereg.md",
        "base": {"momentum_lookbacks": ENSEMBLE_LOOKBACKS, "universe": "book_h_gold_39",
                 "everything_else": "certified (config.yaml)"},
        "universe": list(panel.keys()),
        "firms": FIRMS,
        "mc_design": {"n_paths": N_PATHS, "max_days_eval": MAX_DAYS, "funded_days": FUNDED_DAYS,
                      "days_per_month": DAYS_PER_MONTH,
                      "seed_stream": "SeedSequence([42, config_index, firm_index]).spawn(2) "
                                     "-> [eval, funded]",
                      "resampling": "empirical closures-per-day + i.i.d. trade returns from "
                                    "the config's own backtest pool, EOD-only"},
        "reference": REFERENCE,
        "n_trials_before": n_before,
        "n_trials_after": ledger.n_trials,
        "ledger_recorded": not args.no_ledger,
        "configs": results,
    }
    results_path.parent.mkdir(parents=True, exist_ok=True)
    with open(results_path, "w", encoding="utf-8") as fh:
        # compact separators: the full per-config trade pools make this ~385KB compact
        # vs ~930KB with indent=2 — the repo's network corrupts big pushes/blobs
        # (see scripts/push_via_rest.sh header). Values are identical either way.
        json.dump(out, fh, separators=(",", ":"), default=str)
    print(f"results written to {results_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
