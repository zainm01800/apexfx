"""Pre-registered gate: SPY-regime short veto on the Book H gold equity sleeve.

Prereg: engine/data_store/spy_regime_short_veto_prereg.md (2026-08-08, written BEFORE this run).

Challenger: on the 12 single-name stocks (STOCKS_12), SHORT signals become FLAT whenever
SPY's close at the decision bar is above SPY's 200d SMA. ETFs/SGLD/longs untouched.
Control: certified equity-sleeve book (must reproduce Sharpe ~0.9955, 1546 trades from the
2026-07-24 blackout gate control).

Clone of run_portfolio_gate_earnings_blackout.py — same machinery, different wrapper.
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
from apex_quant.risk.types import Direction, Signal  # noqa: E402
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
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC, STOCKS_12  # noqa: E402

RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "spy_regime_short_veto_gate_2026-08-08.json"

EQUITY_SLEEVE = EQUITY_CORE + [GOLD_ETC]   # 21 instruments (12 stocks, 8 ETFs, SGLD.L)
GOLD_PARAMS = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}
CERTIFIED_MRPT = 0.01

# Control reproduction targets (2026-07-24 blackout gate control, same sleeve/params).
CONTROL_TARGET = {"sharpe": 0.9955, "n_trades": 1546}

BOOKS = {
    "book_h_gold_equity": {"params": GOLD_PARAMS, "spy_short_veto": False},
    "book_h_gold_equity_spyveto": {"params": GOLD_PARAMS, "spy_short_veto": True},
}


def _cfg():
    cfg = copy.deepcopy(get_config())
    cfg.risk.max_risk_per_trade = CERTIFIED_MRPT
    return cfg


def _spy_above_set(store: ParquetStore, holdout_start) -> set:
    """Bar timestamps (SPY's own calendar) where SPY close > SPY 200d SMA — decision-bar
    state, point-in-time safe (state at bar t uses bar t's close only)."""
    df = store.load("SPY", "1d")
    if df.empty:
        raise RuntimeError("SPY_1d.parquet missing — cannot compute regime set")
    df = clean(df)
    df = df[df.index < holdout_start]
    above = df["close"] > df["close"].rolling(200).mean()
    return set(df.index[above.fillna(False)])


class SpyShortVeto:
    """Strategy wrapper: SHORT -> FLAT inside the SPY>200dma set; everything else proxied."""

    def __init__(self, base, above: set, instrument: str) -> None:
        self._base = base
        self._above = above
        self._instrument = instrument

    def __getattr__(self, name):
        if name == "_base":
            raise AttributeError(name)
        return getattr(self._base, name)

    def generate(self, pit, t, instrument: str = "") -> Signal:
        sig = self._base.generate(pit, t, instrument)
        d = sig.direction
        d = d.value if hasattr(d, "value") else str(d)
        if d == "short" and pd.Timestamp(t) in self._above:
            return Signal(instrument=instrument or self._instrument, direction=Direction.FLAT,
                          probability=0.50, reward_risk=1.5, timeframe="1d",
                          rationale="spy>200dma short veto")
        return sig


class _Model:
    def __init__(self, panel: dict, *, spy_short_veto: bool = False, above: set | None = None,
                 **params) -> None:
        self._tb = TrendBook(panel, **params)
        self._veto = spy_short_veto
        self._above = above or set()

    def strategies(self) -> dict:
        strats = self._tb.strategies()
        if self._veto:
            for inst in list(strats):
                if inst in STOCKS_12:
                    strats[inst] = SpyShortVeto(strats[inst], self._above, inst)
        return strats


def _tail_stats(res) -> dict:
    rets = res.returns
    if rets.empty:
        return {}
    return {"worst_daily_return": round(float(rets.min()), 6),
            "max_drawdown": round(float(res.metrics["max_drawdown"]), 6)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered gate: SPY>200dma short veto, "
                                             "Book H gold equity sleeve (iteration window only).")
    ap.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START)
    ap.add_argument("--no-ledger", action="store_true")
    args = ap.parse_args(argv)

    base_cfg = get_config()
    store = ParquetStore(base_cfg.store_path)
    holdout_start = _utc(args.holdout_start)

    master: dict[str, pd.DataFrame] = {}
    for inst in sorted(EQUITY_SLEEVE):
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
    panel = {inst: master[inst] for inst in EQUITY_SLEEVE if inst in master}
    if len(panel) < 2:
        print("need >= 2 instruments for a portfolio gate")
        return 1
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    timeframes = {k: "1d" for k in panel}

    above = _spy_above_set(store, holdout_start)
    n_stocks = len([i for i in panel if i in STOCKS_12])
    print(f"SPY>200dma bars: {len(above)} | veto applies to {n_stocks} single-name stocks",
          flush=True)

    # Record the 2 pre-registered trials BEFORE running (dedup-safe on re-runs).
    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not args.no_ledger:
        for name, spec in BOOKS.items():
            ledger.record({"book": name, "universe": "book_h_gold_equity_21", "timeframe": "1d",
                           "factory": "trend_book_mtf", "kind": "spy_regime_short_veto_gate",
                           "max_risk_per_trade": CERTIFIED_MRPT,
                           "params": {**spec["params"],
                                      "spy_short_veto": spec["spy_short_veto"]}})
        ledger.save(LEDGER_PATH)
    used_trials = ledger.n_trials if not args.no_ledger else n_before + len(BOOKS)

    print("=" * 72, flush=True)
    print(f"SPY-REGIME SHORT-VETO GATE (BOOK H GOLD EQUITY SLEEVE, mrpt={CERTIFIED_MRPT}) "
          f"2026-08-08 | mode=ITERATION (strictly < {args.holdout_start})")
    print(f"universe: {len(panel)} instruments | books: {list(BOOKS)}")
    print(f"ledger n_trials {n_before} -> {ledger.n_trials if not args.no_ledger else n_before}"
          f" | DSR deflation uses n_trials={used_trials}")
    print("=" * 72, flush=True)

    results: dict[str, dict] = {}
    returns_by_book: dict[str, pd.Series] = {}
    for name, spec in BOOKS.items():
        cfg = _cfg()
        t_start = time.time()
        model = _Model(panel, spy_short_veto=spec["spy_short_veto"], above=above,
                       **spec["params"])
        res = PortfolioBacktester(cfg, exit_mode="managed").run(
            pits, model.strategies(), timeframes=timeframes,
            warmup=WARMUP, periods_per_year=252,
        )
        rets = res.returns
        returns_by_book[name] = rets
        m = res.metrics
        y2022 = float(rets[rets.index.year == 2022].sum())
        results[name] = {"params": {**spec["params"], "spy_short_veto": spec["spy_short_veto"]},
                         "metrics": m,
                         "tail": _tail_stats(res),
                         "y2022_log_return_sum": round(y2022, 6),
                         "full_window_sharpe_per_period": sharpe_ratio(rets, periods_per_year=1)}
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] full run {name}: "
              f"{time.time() - t_start:.0f}s | {res.summary()}", flush=True)

    # Control reproduction hard-check (2026-07-24 gate control on the same sleeve).
    cm = results["book_h_gold_equity"]["metrics"]
    if abs(cm["sharpe"] - CONTROL_TARGET["sharpe"]) > 0.01 or \
            abs(cm["n_trades"] - CONTROL_TARGET["n_trades"]) > 5:
        print(f"CONTROL REPRODUCTION FAILED: sharpe {cm['sharpe']} vs "
              f"{CONTROL_TARGET['sharpe']}, trades {cm['n_trades']} vs "
              f"{CONTROL_TARGET['n_trades']}", flush=True)
        return 1
    print(f"control reproduction: OK (sharpe {cm['sharpe']:.4f}, trades {cm['n_trades']})",
          flush=True)

    a = results["book_h_gold_equity"]
    b = results["book_h_gold_equity_spyveto"]
    deltas = {k: round(b["metrics"][k] - a["metrics"][k], 6) for k in
              ("sharpe", "profit_factor", "expectancy_pnl", "win_rate", "max_drawdown",
               "total_return", "n_trades")}
    deltas["worst_daily_return"] = round(b["tail"]["worst_daily_return"]
                                         - a["tail"]["worst_daily_return"], 6)

    # PBO across the 2-config selection set.
    aligned = pd.concat(list(returns_by_book.values()), axis=1).dropna()
    M = aligned.to_numpy()
    pbo = (probability_of_backtest_overfitting(M, n_splits=base_cfg.validation.pbo.n_splits, seed=base_cfg.seed)
           if M.shape[1] >= 2 and M.shape[0] >= 40 else {"pbo": None, "note": "insufficient matrix"})
    print(f"PBO across {M.shape[1]} configs: {pbo}", flush=True)

    # CPCV per config (the same 15 paths as every prior gate).
    trial_sharpes = [results[n]["full_window_sharpe_per_period"] for n in BOOKS]
    verdicts: dict[str, dict] = {}
    for name, spec in BOOKS.items():
        cfg = _cfg()
        t_start = time.time()
        cpcv = run_portfolio_cpcv(
            panel, pits,
            lambda p, _v=spec["spy_short_veto"], **kw: _Model(
                p, spy_short_veto=_v, above=above, **kw),
            spec["params"],
            cfg=cfg, timeframes=timeframes, warmup=WARMUP, horizon=HORIZON,
            periods_per_year=252, exit_mode="managed",
        )
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] CPCV {name}: "
              f"{time.time() - t_start:.0f}s | paths={cpcv['oos_sharpe_paths']}", flush=True)
        verdicts[name] = _gate(name, returns_by_book[name], trial_sharpes, pbo, cpcv, used_trials)
        results[name]["cpcv"] = cpcv
        results[name]["gate"] = {k: v for k, v in verdicts[name].items() if k != "reasons"}

    # Pre-registered verdict legs (prereg §verdict).
    l1 = bool(b["metrics"]["sharpe"] >= a["metrics"]["sharpe"])
    l2 = bool(verdicts["book_h_gold_equity_spyveto"]["dsr"].get("dsr", 0.0) >= 0.95)
    l3 = bool(pbo.get("pbo") is not None and pbo["pbo"] < 0.5)
    l4 = bool(b["y2022_log_return_sum"] > 0)
    gate_pass = l1 and l2 and l3
    confirmed = bool(gate_pass and l4)

    print("\n" + "=" * 72, flush=True)
    for name, v in verdicts.items():
        print(f"  {name}: VERDICT {'PASS' if v['passed'] else 'REJECT'}")
        for r in v["reasons"]:
            print(f"    - {r}")
    print(f"  LEGS: L1 dSharpe {deltas['sharpe']:+.4f} >= 0? {l1} | "
          f"L2 DSR {verdicts['book_h_gold_equity_spyveto']['dsr'].get('dsr', 0):.4f} >= 0.95? {l2} | "
          f"L3 PBO {pbo.get('pbo')} < 0.5? {l3} | "
          f"L4 2022 sum {b['y2022_log_return_sum']:+.4f} > 0? {l4}")
    print(f"  PRE-REGISTERED RULE => {'CONFIRMED (adoptable)' if confirmed else 'REJECTED'}")
    print("=" * 72, flush=True)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "iteration",
        "holdout_start": args.holdout_start,
        "prereg": "engine/data_store/spy_regime_short_veto_prereg.md",
        "certified_anchor_max_risk_per_trade": CERTIFIED_MRPT,
        "universe": list(panel.keys()),
        "spy_above_bars": len(above),
        "n_trials_before": n_before,
        "n_trials_used": used_trials,
        "ledger_recorded": not args.no_ledger,
        "pbo": pbo,
        "legs": {"l1_sharpe_no_worse": l1, "l2_dsr": l2, "l3_pbo": l3,
                 "l4_2022_positive": l4},
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
