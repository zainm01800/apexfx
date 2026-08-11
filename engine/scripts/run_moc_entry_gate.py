"""Pre-registered gate: MOC (close-fill) entries on the certified Book H gold book.

Prereg: engine/data_store/moc_entry_gate_prereg.md (2026-08-08, written BEFORE this run).

Control: certified convention (signals on bar t close -> fill bar t+1 open) — must reproduce
the certified anchor EXACTLY (Sharpe 0.86284, 1637 trades, final equity 292,551.34).
Challenger: entry_fill="close" (fill at the decision bar's close).

Same machinery as every prior portfolio gate. Ledger-charged before running.
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

import pandas as pd  # noqa: E402

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
    _utc,
)
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402

RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "moc_entry_gate_2026-08-08.json"

UNIVERSE_GOLD = EQUITY_CORE + [GOLD_ETC]
GOLD_PARAMS = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}
CERTIFIED_MRPT = 0.01

CERTIFIED_GOLD = {"sharpe": 0.8628380346245177, "profit_factor": 1.324524180228228,
                  "win_rate": 0.5577275503970678, "max_drawdown": 0.16315348773173277,
                  "n_trades": 1637, "total_return": 1.9255133668640645,
                  "expectancy_pnl": 120.44265729993896, "final_equity": 292551.33668640646}

BOOKS = {
    "book_h_gold_252_open": {"params": GOLD_PARAMS, "entry_fill": "open"},
    "book_h_gold_252_moc": {"params": GOLD_PARAMS, "entry_fill": "close"},
}


def _cfg():
    cfg = copy.deepcopy(get_config())
    cfg.risk.max_risk_per_trade = CERTIFIED_MRPT
    return cfg


def _tail_stats(res) -> dict:
    rets = res.returns
    if rets.empty:
        return {}
    return {"worst_daily_return": round(float(rets.min()), 6),
            "max_drawdown": round(float(res.metrics["max_drawdown"]), 6)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered gate: MOC close-fill entries on the "
                                             "certified Book H gold book (iteration window only).")
    ap.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START)
    ap.add_argument("--no-ledger", action="store_true")
    args = ap.parse_args(argv)

    base_cfg = get_config()
    store = ParquetStore(base_cfg.store_path)
    holdout_start = _utc(args.holdout_start)

    crypto = list(base_cfg.data.crypto)
    wanted = sorted(set(UNIVERSE_GOLD) | set(crypto) | set(FX_MAJORS_7))
    master: dict[str, pd.DataFrame] = {}
    for inst in wanted:
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
    panel = {inst: master[inst] for inst in UNIVERSE_GOLD + crypto + FX_MAJORS_7 if inst in master}
    if len(panel) < 2:
        print("need >= 2 instruments")
        return 1
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    timeframes = {k: "1d" for k in panel}

    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not args.no_ledger:
        for name, spec in BOOKS.items():
            ledger.record({"book": name, "universe": "book_h_gold_39", "timeframe": "1d",
                           "factory": "trend_book_mtf", "kind": "moc_entry_gate",
                           "max_risk_per_trade": CERTIFIED_MRPT,
                           "params": {**spec["params"], "entry_fill": spec["entry_fill"]}})
        ledger.save(LEDGER_PATH)
    used_trials = ledger.n_trials if not args.no_ledger else n_before + len(BOOKS)

    print("=" * 72, flush=True)
    print(f"MOC ENTRY GATE (BOOK H GOLD, mrpt={CERTIFIED_MRPT}) 2026-08-08 | "
          f"mode=ITERATION (strictly < {args.holdout_start})")
    print(f"universe: {len(panel)} instruments | books: {list(BOOKS)}")
    print(f"ledger n_trials {n_before} -> {ledger.n_trials if not args.no_ledger else n_before}"
          f" | DSR deflation uses n_trials={used_trials}")
    print("=" * 72, flush=True)

    results: dict[str, dict] = {}
    returns_by_book: dict[str, pd.Series] = {}
    for name, spec in BOOKS.items():
        cfg = _cfg()
        t_start = time.time()
        model = TrendBook(panel, **spec["params"])
        res = PortfolioBacktester(cfg, exit_mode="managed",
                                  entry_fill=spec["entry_fill"]).run(
            pits, model.strategies(), timeframes=timeframes,
            warmup=WARMUP, periods_per_year=252,
        )
        rets = res.returns
        returns_by_book[name] = rets
        m = res.metrics
        results[name] = {"params": {**spec["params"], "entry_fill": spec["entry_fill"]},
                         "metrics": m,
                         "tail": _tail_stats(res),
                         "full_window_sharpe_per_period": sharpe_ratio(rets, periods_per_year=1)}
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] full run {name}: "
              f"{time.time() - t_start:.0f}s | {res.summary()}", flush=True)

    # Control reproduction hard-check against the certified anchor.
    cm = results["book_h_gold_252_open"]["metrics"]
    mismatch = {k: (cm.get(k), v) for k, v in CERTIFIED_GOLD.items()
                if abs(cm.get(k, float("nan")) - v) > (0.5 if k == "n_trades" else 1e-6 * max(1.0, abs(v)))}
    if mismatch:
        print(f"CERTIFIED REPRODUCTION FAILED: {mismatch}", flush=True)
        return 1
    print(f"certified-anchor reproduction: EXACT (sharpe {cm['sharpe']:.5f}, "
          f"{cm['n_trades']} trades)", flush=True)

    a = results["book_h_gold_252_open"]
    b = results["book_h_gold_252_moc"]
    deltas = {k: round(b["metrics"][k] - a["metrics"][k], 6) for k in
              ("sharpe", "profit_factor", "expectancy_pnl", "win_rate", "max_drawdown",
               "total_return", "n_trades")}
    deltas["worst_daily_return"] = round(b["tail"]["worst_daily_return"]
                                         - a["tail"]["worst_daily_return"], 6)
    net_a = float(a["metrics"]["final_equity"]) - 100000.0
    net_b = float(b["metrics"]["final_equity"]) - 100000.0
    deltas["net_pnl"] = round(net_b - net_a, 2)

    aligned = pd.concat(list(returns_by_book.values()), axis=1).dropna()
    M = aligned.to_numpy()
    pbo = (probability_of_backtest_overfitting(M, n_splits=base_cfg.validation.pbo.n_splits, seed=base_cfg.seed)
           if M.shape[1] >= 2 and M.shape[0] >= 40 else {"pbo": None, "note": "insufficient matrix"})
    print(f"PBO across {M.shape[1]} configs: {pbo}", flush=True)

    trial_sharpes = [results[n]["full_window_sharpe_per_period"] for n in BOOKS]
    verdicts: dict[str, dict] = {}
    for name, spec in BOOKS.items():
        cfg = _cfg()
        t_start = time.time()
        cpcv = run_portfolio_cpcv(
            panel, pits,
            lambda p, _ef=spec["entry_fill"], **kw: TrendBook(p, **kw),
            spec["params"],
            cfg=cfg, timeframes=timeframes, warmup=WARMUP, horizon=HORIZON,
            periods_per_year=252, exit_mode="managed",
            entry_fill=spec["entry_fill"],
        )
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] CPCV {name}: "
              f"{time.time() - t_start:.0f}s | paths={cpcv['oos_sharpe_paths']}", flush=True)
        verdicts[name] = _gate(name, returns_by_book[name], trial_sharpes, pbo, cpcv, used_trials)
        results[name]["cpcv"] = cpcv
        results[name]["gate"] = {k: v for k, v in verdicts[name].items() if k != "reasons"}

    # Pre-registered verdict legs (prereg §verdict).
    l1 = bool(b["metrics"]["sharpe"] >= a["metrics"]["sharpe"])
    l2 = bool(verdicts["book_h_gold_252_moc"]["dsr"].get("dsr", 0.0) >= 0.95)
    l3 = bool(pbo.get("pbo") is not None and pbo["pbo"] < 0.5)
    paths_b = results["book_h_gold_252_moc"]["cpcv"]["oos_sharpe_paths"]
    paths_a = results["book_h_gold_252_open"]["cpcv"]["oos_sharpe_paths"]
    med_b = float(pd.Series(paths_b).median())
    med_a = float(pd.Series(paths_a).median())
    l4 = bool(sum(1 for p in paths_b if p > 0) >= 12 and med_b >= med_a)
    l5 = bool(net_b >= net_a + 10000.0)
    confirmed = bool(l1 and l2 and l3 and l4 and l5)

    print("\n" + "=" * 72, flush=True)
    for name, v in verdicts.items():
        print(f"  {name}: VERDICT {'PASS' if v['passed'] else 'REJECT'}")
        for r in v["reasons"]:
            print(f"    - {r}")
    print(f"  LEGS: L1 dSharpe {deltas['sharpe']:+.4f} >= 0? {l1} | "
          f"L2 DSR {verdicts['book_h_gold_252_moc']['dsr'].get('dsr', 0):.4f} >= 0.95? {l2} | "
          f"L3 PBO {pbo.get('pbo')} < 0.5? {l3} | "
          f"L4 CPCV {sum(1 for p in paths_b if p > 0)}/15 pos & med {med_b:.4f} >= {med_a:.4f}? {l4} | "
          f"L5 net +£{deltas['net_pnl']:,.0f} >= +£10k? {l5}")
    print(f"  PRE-REGISTERED RULE => {'CONFIRMED (adoptable)' if confirmed else 'REJECTED'}")
    print("=" * 72, flush=True)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "iteration",
        "holdout_start": args.holdout_start,
        "prereg": "engine/data_store/moc_entry_gate_prereg.md",
        "certified_anchor_max_risk_per_trade": CERTIFIED_MRPT,
        "universe": list(panel.keys()),
        "n_trials_before": n_before,
        "n_trials_used": used_trials,
        "ledger_recorded": not args.no_ledger,
        "pbo": pbo,
        "legs": {"l1_sharpe_no_worse": l1, "l2_dsr": l2, "l3_pbo": l3,
                 "l4_cpcv_paths": l4, "l5_net_improvement_10k": l5},
        "verdict_rule": "CONFIRMED" if confirmed else "REJECTED",
        "deltas_challenger_minus_control": deltas,
        "books": results,
        "verdicts": verdicts,
    }
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_PATH, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"results written to {RESULTS_PATH}", flush=True)
    return 0 if confirmed else 1


if __name__ == "__main__":
    sys.exit(main())
