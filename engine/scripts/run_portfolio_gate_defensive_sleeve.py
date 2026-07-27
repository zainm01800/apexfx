"""Pre-registered portfolio-level gate: SUKUK/GOLD DEFENSIVE CASH-SUBSTITUTE SLEEVE
on Book H gold.

Pre-registration: engine/data_store/defensive_sleeve_prereg.md (2026-07-27, written
BEFORE any challenger run; the 3 trials are recorded before execution, dedup-safe).
Hypothesis: the certified book's regime-filter idle capital earns 0% in GBP cash;
routing it into a sukuk (SPSK) + gold (SGLD.L) defensive sleeve beats cash by >= 2%/yr
net on the idle capital, with sleeve standalone net Sharpe >= 0.25 and max DD <= 8%,
lifting the book >= +0.05 Sharpe without degrading its deflated significance. Book H
gold universe, certified params, certified risk anchor (max_risk_per_trade 0.01 — the
2026-07-22 gap-aware certified state; passed explicitly as in every post-2026-07-23
gate).

Exactly 3 pre-registered configs (the full selection set; ONLY the idle-cash treatment
differs):
  defslv_control_cash   control (no sleeve — certified GBP cash; anchor hard-check)
  defslv_static_50_50   challenger A (50/50 SGLD.L/SPSK on idle capital)
  defslv_inverse_vol    challenger B (inverse-63d-vol SGLD.L/SPSK on idle capital)

Same three gates, same thresholds, same machinery as every prior gate. Iteration window
only: strictly < 2025-01-01. Seed 42. Determinism: run twice, byte-identical except
timestamps/ledger counters (verified in the gate report via a twin --out run).

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_portfolio_gate_defensive_sleeve.py                 # full gate
    .venv-mac/bin/python scripts/run_portfolio_gate_defensive_sleeve.py --instruments AAPL,MSFT,NVDA --no-ledger
    .venv-mac/bin/python scripts/run_portfolio_gate_defensive_sleeve.py --out <twin>    # determinism rerun

Exit code 0 iff at least one challenger is ADOPTED under the pre-registered rule.
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

from apex_quant.backtest.defensive_sleeve import DefensiveSleeveSpec  # noqa: E402
from apex_quant.backtest.portfolio import PortfolioBacktester  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor, ParquetStore, clean  # noqa: E402
from apex_quant.validation.metrics import (  # noqa: E402
    probability_of_backtest_overfitting,
    sharpe_ratio,
)
from apex_quant.validation.portfolio_report import run_portfolio_cpcv  # noqa: E402
from apex_quant.validation.trials import TrialLedger  # noqa: E402

from run_portfolio_gate import (  # noqa: E402
    COMMON_PARAMS,
    DEFAULT_HOLDOUT_START,
    HORIZON,
    LEDGER_PATH,
    MIN_BARS,
    WARMUP,
    TrendBook,
    _gate,
    _max_gross_leverage,
    _utc,
)
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC, SUKUK_ETF  # noqa: E402
from run_portfolio_gate_cf_cvar import _monthly_tail_stats  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402

DEFAULT_RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "defensive_sleeve_gate_2026-07-27.json"

UNIVERSE_GOLD = EQUITY_CORE + [GOLD_ETC]
GOLD_PARAMS = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}
CERTIFIED_MRPT = 0.01  # certified 2026-07-22 gap-aware anchor (see prereg header)
SLEEVE_LEGS = [GOLD_ETC, SUKUK_ETF]   # SGLD.L, SPSK
INV_VOL_WINDOW = 63                   # pre-registered (prereg section 2)

# Certified-anchor reproduction (book_h_gapaware_2026-07-22.json): the control MUST
# reproduce these numbers — hard-fail the run if it does not.
CERTIFIED_GOLD = {"sharpe": 0.8628380346245177, "profit_factor": 1.324524180228228,
                  "win_rate": 0.5577275503970678, "max_drawdown": 0.16315348773173277,
                  "n_trades": 1637, "total_return": 1.9255133668640645,
                  "expectancy_pnl": 120.44265729993896, "final_equity": 292551.33668640646}

# The full pre-registered selection set (3 trials): ONLY the idle-cash treatment differs.
BOOKS = {
    "defslv_control_cash": None,
    "defslv_static_50_50": "static",
    "defslv_inverse_vol": "inverse_vol",
}
CONTROL = "defslv_control_cash"
CHALLENGERS = ["defslv_static_50_50", "defslv_inverse_vol"]

# Pre-registered adoption thresholds (prereg section 5).
MIN_IDLE_YIELD = 0.02       # sleeve must beat GBP cash by >= 2%/yr net on idle capital
MIN_SLEEVE_SHARPE = 0.25    # sleeve standalone net Sharpe floor
MAX_SLEEVE_DD = 0.08        # sleeve standalone max drawdown cap
MIN_BOOK_UPLIFT = 0.05      # book Sharpe(challenger) - Sharpe(control) floor


def _cfg():
    cfg = copy.deepcopy(get_config())
    cfg.risk.max_risk_per_trade = CERTIFIED_MRPT
    return cfg


def _oneway(base_cfg, leg: str) -> float:
    """One-way sleeve cost from the config mechanics: (half spread + slippage)."""
    m = base_cfg.mechanics_for(leg)
    return (0.5 * m.spread_bps + m.slippage_bps) / 1e4


def _spec(base_cfg, closes: dict[str, pd.Series], mode: str | None) -> DefensiveSleeveSpec | None:
    if mode is None:
        return None
    return DefensiveSleeveSpec(
        closes=closes,
        mode=mode,
        static_weights={leg: 0.5 for leg in SLEEVE_LEGS},
        vol_window=INV_VOL_WINDOW,
        oneway_cost={leg: _oneway(base_cfg, leg) for leg in SLEEVE_LEGS},
    )


def _sleeve_standalone(spec: DefensiveSleeveSpec, timeline: pd.DatetimeIndex) -> dict:
    """The sleeve as a standalone fully-invested asset on the union timeline (prereg
    §5): r(t) = sum x(t-1)*r_leg(t) - sum |x(t)-x(t-1)| * oneway_leg, compounded."""
    arrays = spec.align(timeline)
    legs = list(spec.closes)
    n = len(timeline)
    r = np.zeros(n)
    prev = {leg: 0.0 for leg in legs}
    for i in range(n):
        ret = sum(prev[leg] * arrays["ret"][leg][i] for leg in legs)
        cost = sum(abs(arrays["mix"][leg][i] - prev[leg]) * spec.oneway_cost.get(leg, 0.0)
                   for leg in legs)
        r[i] = ret - cost
        prev = {leg: arrays["mix"][leg][i] for leg in legs}
    eq = pd.Series(np.cumprod(1.0 + r), index=timeline)
    rets = eq.pct_change().dropna()
    sharpe = float(rets.mean() / rets.std(ddof=1) * np.sqrt(252)) if rets.std(ddof=1) > 0 else 0.0
    dd = float(-(eq / eq.cummax() - 1.0).min())
    ann_return = float(eq.iloc[-1] ** (252.0 / max(1, len(eq))) - 1.0) if len(eq) > 1 else 0.0
    return {"net_sharpe": round(sharpe, 4), "max_drawdown": round(dd, 6),
            "ann_return": round(ann_return, 6),
            "mean_mix": {leg: round(float(arrays["mix"][leg].mean()), 4) for leg in legs}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered gate: sukuk/gold defensive "
                                             "cash-substitute sleeve on Book H gold "
                                             "(iteration window only).")
    ap.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START)
    ap.add_argument("--instruments", default="",
                    help="comma-separated subset of the gold universe (smoke testing)")
    ap.add_argument("--no-ledger", action="store_true",
                    help="smoke-test mode: do NOT record trials; DSR still deflates by the "
                         "count the run WOULD have used (current + 3)")
    ap.add_argument("--skip-cpcv", action="store_true",
                    help="smoke-test mode: skip CPCV (gate verdicts unevaluated)")
    ap.add_argument("--out", default=str(DEFAULT_RESULTS_PATH),
                    help="results JSON path (use a twin path for the determinism rerun)")
    args = ap.parse_args(argv)

    base_cfg = get_config()
    store = ParquetStore(base_cfg.store_path)
    holdout_start = _utc(args.holdout_start)
    subset = {s.strip() for s in args.instruments.split(",") if s.strip()}
    results_path = Path(args.out)

    crypto = list(base_cfg.data.crypto)
    wanted = sorted(set(UNIVERSE_GOLD) | set(crypto) | set(FX_MAJORS_7) | set(SLEEVE_LEGS))
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
    # The certified panel preserves the BOOK'S insertion order (EQUITY_CORE first), NOT load
    # order — the certified numbers are ordering-sensitive (ordering_sensitivity_audit.md).
    panel = {inst: master[inst] for inst in UNIVERSE_GOLD + crypto + FX_MAJORS_7 if inst in master}
    if len(panel) < 2:
        print("need >= 2 instruments for a portfolio gate")
        return 1
    sleeve_missing = [leg for leg in SLEEVE_LEGS if leg not in master]
    if sleeve_missing and not subset:
        print(f"sleeve legs missing in-window data: {sleeve_missing}")
        return 1
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    timeframes = {k: "1d" for k in panel}
    sleeve_closes = {leg: master[leg]["close"] for leg in SLEEVE_LEGS if leg in master}

    # Record the 3 pre-registered trials BEFORE running (exact keys dedup on re-runs).
    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not args.no_ledger:
        for name, mode in BOOKS.items():
            ledger.record({"book": name, "universe": "book_h_gold_39", "timeframe": "1d",
                           "factory": "trend_book_mtf", "kind": "defensive_sleeve_gate",
                           "max_risk_per_trade": CERTIFIED_MRPT,
                           "sleeve": {"mode": mode or "cash", "legs": SLEEVE_LEGS,
                                      "vol_window": INV_VOL_WINDOW},
                           "params": GOLD_PARAMS})
        ledger.save(LEDGER_PATH)
    used_trials = ledger.n_trials if not args.no_ledger else n_before + len(BOOKS)

    print("=" * 72, flush=True)
    print(f"DEFENSIVE CASH-SUBSTITUTE SLEEVE GATE (BOOK H GOLD, mrpt={CERTIFIED_MRPT}) "
          f"2026-07-27 | mode=ITERATION (strictly < {args.holdout_start})")
    print(f"universe: {len(panel)} instruments | sleeve legs: {list(sleeve_closes)} | "
          f"configs: {list(BOOKS)}")
    print(f"ledger n_trials {n_before} -> {ledger.n_trials if not args.no_ledger else n_before}"
          f" | DSR deflation uses n_trials={used_trials}")
    print("=" * 72, flush=True)

    # 1. Full-window run per config (the sleeve is the ONLY difference).
    results: dict[str, dict] = {}
    returns_by_book: dict[str, pd.Series] = {}
    specs: dict[str, DefensiveSleeveSpec | None] = {}
    for name, mode in BOOKS.items():
        spec = _spec(base_cfg, sleeve_closes, mode)
        specs[name] = spec
        cfg = _cfg()
        t_start = time.time()
        model = TrendBook(panel, **GOLD_PARAMS)
        res = PortfolioBacktester(cfg, exit_mode="managed", defensive_sleeve=spec).run(
            pits, model.strategies(), timeframes=timeframes,
            warmup=WARMUP, periods_per_year=252,
        )
        rets = res.returns
        returns_by_book[name] = rets
        m = res.metrics
        results[name] = {"params": {**GOLD_PARAMS, "defensive_sleeve": mode or "cash"},
                         "metrics": m,
                         "max_gross_leverage": _max_gross_leverage(res),
                         "constraint_log": res.constraint_log,
                         "per_instrument": res.per_instrument,
                         "monthly_tail": _monthly_tail_stats(res, cfg.backtest.initial_equity),
                         "full_window_sharpe_per_period": sharpe_ratio(rets, periods_per_year=1)}
        if spec is not None:
            results[name]["sleeve_standalone"] = _sleeve_standalone(spec, res.equity.index)
            n_years = len(res.equity) / 252.0
            mean_idle = m.get("defensive_sleeve_mean_idle_capital", 0.0)
            results[name]["idle_yield_per_year"] = round(
                m.get("defensive_sleeve_net_pnl", 0.0) / mean_idle / n_years
                if mean_idle > 0 and n_years > 0 else 0.0, 6)
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] full run {name}: "
              f"{time.time() - t_start:.0f}s | {res.summary()}", flush=True)
        mt = results[name]["monthly_tail"]
        extra = (f" | sleeve net {m.get('defensive_sleeve_net_pnl', 0):+,.0f} "
                 f"(cost {m.get('defensive_sleeve_cost_total', 0):,.0f}) "
                 f"idle~{m.get('defensive_sleeve_mean_idle_frac', 0)*100:.0f}% "
                 f"yield {results[name].get('idle_yield_per_year', 0)*100:.2f}%/yr "
                 f"standalone {results[name].get('sleeve_standalone')}"
                 if spec is not None else "")
        print(f"    expectancy={m['expectancy_pnl']:.2f} ({m['expectancy_pct']*100:.3f}%/trade) "
              f"PF={m.get('profit_factor')} win={m['win_rate']*100:.2f}% "
              f"maxDD={m['max_drawdown']*100:.2f}% worst_day={mt['worst_daily_return']*100:.2f}% "
              f"avg {mt['avg_monthly_pnl']:+,.0f}/mo{extra}", flush=True)

    # Certified-anchor reproduction: the control must reproduce
    # book_h_gapaware_2026-07-22.json (gold) — hard-fail the run if it does not.
    if not args.instruments:
        m0 = results[CONTROL]["metrics"]
        mismatch = {k: (m0[k], v) for k, v in CERTIFIED_GOLD.items()
                    if abs(m0[k] - v) > (0.5 if k in ("n_trades",)
                                        else 1e-6 * max(1.0, abs(v)))}
        if mismatch:
            print(f"CERTIFIED REPRODUCTION FAILED: {mismatch}", flush=True)
            return 1
        print("certified-anchor reproduction: EXACT "
              f"(sharpe {m0['sharpe']:.5f}, {m0['n_trades']} trades, "
              f"final_equity {m0['final_equity']:.2f})", flush=True)

    # 2. PBO across the 3-config selection set (standing overlap caveat, prereg §4).
    aligned = pd.concat(list(returns_by_book.values()), axis=1).dropna()
    M = aligned.to_numpy()
    pbo = (probability_of_backtest_overfitting(M, n_splits=base_cfg.validation.pbo.n_splits, seed=base_cfg.seed)
           if M.shape[1] >= 2 and M.shape[0] >= 40 else {"pbo": None, "note": "insufficient matrix"})
    print(f"PBO across {M.shape[1]} configs: {pbo}", flush=True)

    # 3. CPCV per config (the same 15 paths as every prior gate; the sleeve flows into
    #    every fold exactly like trade_manager).
    trial_sharpes = [results[n]["full_window_sharpe_per_period"] for n in BOOKS]
    verdicts: dict[str, dict] = {}
    for name, mode in BOOKS.items():
        t_start = time.time()
        if args.skip_cpcv:
            cpcv = {"n_paths": 0, "oos_sharpe_mean": 0.0, "oos_sharpe_std": 0.0,
                    "oos_sharpe_median": 0.0, "frac_positive": 0.0, "oos_sharpe_paths": []}
        else:
            cpcv = run_portfolio_cpcv(
                panel, pits, lambda p, **kw: TrendBook(p, **kw), GOLD_PARAMS,
                cfg=_cfg(), timeframes=timeframes, warmup=WARMUP, horizon=HORIZON,
                periods_per_year=252, exit_mode="managed",
                defensive_sleeve=specs[name],
            )
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] CPCV {name}: "
              f"{time.time() - t_start:.0f}s | paths={cpcv['oos_sharpe_paths']}", flush=True)
        verdicts[name] = _gate(name, returns_by_book[name], trial_sharpes, pbo, cpcv, used_trials)
        results[name]["cpcv"] = cpcv
        results[name]["gate"] = {k: v for k, v in verdicts[name].items() if k != "reasons"}

    # 4. Pre-registered adoption rule (prereg section 5): idle yield >= 2%/yr AND sleeve
    #    standalone net Sharpe >= 0.25 AND sleeve max DD <= 8% AND book uplift >= +0.05
    #    AND book DSR > 0.95 at the full ledger count AND DSR not below control. Kill: any leg.
    adoption: dict[str, dict] = {}
    ctrl_sharpe = results[CONTROL]["metrics"]["sharpe"]
    ctrl_dsr = verdicts[CONTROL]["dsr"].get("dsr", 0.0)
    for name in CHALLENGERS:
        r = results[name]
        sa = r["sleeve_standalone"]
        idle_yield = r["idle_yield_per_year"]
        uplift = r["metrics"]["sharpe"] - ctrl_sharpe
        dsr_val = verdicts[name]["dsr"].get("dsr", 0.0)
        legs = {
            "idle_yield_gte_2pct": bool(idle_yield >= MIN_IDLE_YIELD),
            "sleeve_sharpe_gte_0.25": bool(sa["net_sharpe"] >= MIN_SLEEVE_SHARPE),
            "sleeve_maxdd_lte_8pct": bool(sa["max_drawdown"] <= MAX_SLEEVE_DD),
            "book_uplift_gte_0.05": bool(uplift >= MIN_BOOK_UPLIFT),
            "dsr_gte_0.95_and_not_below_control": bool(dsr_val > 0.95 and dsr_val >= ctrl_dsr),
        }
        adopted = all(legs.values())
        adoption[name] = {
            "adopted": bool(adopted),
            "legs": legs,
            "idle_yield_per_year": idle_yield,
            "sleeve_standalone": sa,
            "book_sharpe_uplift": round(uplift, 6),
            "dsr": dsr_val,
            "control_dsr": ctrl_dsr,
        }
        print(f"ADOPTION {name}: yield {idle_yield*100:.2f}%/yr (>=2%? {legs['idle_yield_gte_2pct']}) | "
              f"sleeve Sharpe {sa['net_sharpe']:.3f} (>=0.25? {legs['sleeve_sharpe_gte_0.25']}) | "
              f"sleeve maxDD {sa['max_drawdown']*100:.2f}% (<=8%? {legs['sleeve_maxdd_lte_8pct']}) | "
              f"uplift {uplift:+.4f} (>=+0.05? {legs['book_uplift_gte_0.05']}) | "
              f"DSR {dsr_val:.3f} vs ctrl {ctrl_dsr:.3f} "
              f"(ok? {legs['dsr_gte_0.95_and_not_below_control']}) => "
              f"{'ADOPTED' if adopted else 'REJECTED'}", flush=True)

    print("\n" + "=" * 72, flush=True)
    for name, v in verdicts.items():
        print(f"  {name}: VERDICT {'PASS' if v['passed'] else 'REJECT'}")
        for r_ in v["reasons"]:
            print(f"    - {r_}")
    any_adopted = any(a["adopted"] for a in adoption.values())
    print(f"  PRE-REGISTERED RULE => {'AT LEAST ONE CHALLENGER ADOPTED' if any_adopted else 'ALL CHALLENGERS REJECTED'}")
    print("=" * 72, flush=True)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "iteration",
        "holdout_start": args.holdout_start,
        "prereg": "engine/data_store/defensive_sleeve_prereg.md",
        "certified_anchor_max_risk_per_trade": CERTIFIED_MRPT,
        "universe": list(panel.keys()),
        "sleeve_legs": list(sleeve_closes),
        "n_trials_before": n_before,
        "n_trials_used": used_trials,
        "ledger_recorded": not args.no_ledger,
        "pbo": pbo,
        "adoption": adoption,
        "verdict_rule": "ADOPTED" if any_adopted else "REJECTED",
        "books": results,
        "verdicts": verdicts,
    }
    results_path.parent.mkdir(parents=True, exist_ok=True)
    with open(results_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"results written to {results_path}", flush=True)
    return 0 if any_adopted else 1


if __name__ == "__main__":
    sys.exit(main())
