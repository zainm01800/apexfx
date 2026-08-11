"""Pre-registered gate: momentum spillover (SPY -> crypto/FX entry conditioning).

Prereg: engine/data_store/momentum_spillover_gate_prereg.md (2026-08-08, BEFORE this run).
Auto-researcher proposal momentum-spillover-effect-2026-08-08 operationalised as a gate on
the certified Book H gold book: crypto/FX LONG entries only when SPY's trailing L-day return
is positive, SHORT entries only when negative. Equity/ETF/metals untouched.

Control hard-check: certified anchor Sharpe 0.86284, 1637 trades.
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
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402

RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "momentum_spillover_gate_2026-08-08.json"

UNIVERSE_GOLD = EQUITY_CORE + [GOLD_ETC]
GOLD_PARAMS = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}
CERTIFIED_MRPT = 0.01

CERTIFIED_GOLD = {"sharpe": 0.8628380346245177, "profit_factor": 1.324524180228228,
                  "win_rate": 0.5577275503970678, "max_drawdown": 0.16315348773173277,
                  "n_trades": 1637, "total_return": 1.9255133668640645,
                  "expectancy_pnl": 120.44265729993896, "final_equity": 292551.33668640646}

BOOKS = {
    "book_h_gold_252": {"params": GOLD_PARAMS, "spill_L": 0},
    "book_h_gold_252_spill20": {"params": GOLD_PARAMS, "spill_L": 20},
    "book_h_gold_252_spill50": {"params": GOLD_PARAMS, "spill_L": 50},
}


def _cfg():
    cfg = copy.deepcopy(get_config())
    cfg.risk.max_risk_per_trade = CERTIFIED_MRPT
    return cfg


class SpilloverGate:
    """Wrapper: on crypto/FX, LONG only when SPY trailing L-day return > 0 (risk-on),
    SHORT only when < 0. risk_on is the set of the instrument's own bar timestamps
    mapped through SPY's calendar."""

    def __init__(self, base, risk_on: set, instrument: str) -> None:
        self._base = base
        self._risk_on = risk_on
        self._instrument = instrument

    def __getattr__(self, name):
        if name == "_base":
            raise AttributeError(name)
        return getattr(self._base, name)

    def generate(self, pit, t, instrument: str = "") -> Signal:
        sig = self._base.generate(pit, t, instrument)
        d = sig.direction
        d = d.value if hasattr(d, "value") else str(d)
        on = pd.Timestamp(t) in self._risk_on
        if (d == "long" and not on) or (d == "short" and on):
            return Signal(instrument=instrument or self._instrument, direction=Direction.FLAT,
                          probability=0.50, reward_risk=1.5, timeframe="1d",
                          rationale="spillover regime veto")
        return sig


class _Model:
    def __init__(self, panel: dict, *, spill_L: int = 0, risk_on_map: dict | None = None,
                 gated: tuple = (), **params) -> None:
        self._tb = TrendBook(panel, **params)
        self._spill_L = spill_L
        self._risk_on_map = risk_on_map or {}
        self._gated = gated

    def strategies(self) -> dict:
        strats = self._tb.strategies()
        if self._spill_L > 0:
            for inst in self._gated:
                if inst in strats:
                    strats[inst] = SpilloverGate(strats[inst], self._risk_on_map[inst], inst)
        return strats


def _tail_stats(res) -> dict:
    rets = res.returns
    if rets.empty:
        return {}
    return {"worst_daily_return": round(float(rets.min()), 6),
            "max_drawdown": round(float(res.metrics["max_drawdown"]), 6)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered gate: momentum spillover SPY->crypto/FX.")
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
            continue
        df = clean(df)
        df = df[df.index < holdout_start]
        if len(df) < MIN_BARS:
            continue
        master[inst] = df
    panel = {inst: master[inst] for inst in UNIVERSE_GOLD + crypto + FX_MAJORS_7 if inst in master}
    if len(panel) < 2:
        print("need >= 2 instruments")
        return 1
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    timeframes = {k: "1d" for k in panel}

    gated = tuple(inst for inst in panel if inst in set(crypto) | set(FX_MAJORS_7))
    print(f"gated instruments (crypto+FX): {len(gated)}", flush=True)

    # SPY trailing-return series (state at bar t uses bar t's close only — point-in-time safe).
    spy_df = clean(store.load("SPY", "1d"))
    spy_df = spy_df[spy_df.index < holdout_start]
    spy_close = spy_df["close"]

    def risk_on_map(L: int) -> dict:
        ret = spy_close.pct_change(L)
        on = (ret > 0)
        idx = spy_close.index
        out = {}
        for inst in gated:
            inst_idx = panel[inst].index
            pos = idx.searchsorted(inst_idx, side="right") - 1
            pos = pos.clip(min=0)
            out[inst] = set(inst_idx[on.iloc[pos].to_numpy()])
        return out

    maps = {L: risk_on_map(L) for L in (20, 50)}

    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not args.no_ledger:
        for name, spec in BOOKS.items():
            ledger.record({"book": name, "universe": "book_h_gold_39", "timeframe": "1d",
                           "factory": "trend_book_mtf", "kind": "momentum_spillover_gate",
                           "max_risk_per_trade": CERTIFIED_MRPT,
                           "params": {**spec["params"], "spill_L": spec["spill_L"]}})
        ledger.save(LEDGER_PATH)
    used_trials = ledger.n_trials if not args.no_ledger else n_before + len(BOOKS)

    print("=" * 72, flush=True)
    print(f"MOMENTUM-SPILLOVER GATE (BOOK H GOLD, mrpt={CERTIFIED_MRPT}) 2026-08-08 | "
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
        L = spec["spill_L"]
        model = _Model(panel, spill_L=L, risk_on_map=maps.get(L), gated=gated, **spec["params"])
        res = PortfolioBacktester(cfg, exit_mode="managed").run(
            pits, model.strategies(), timeframes=timeframes,
            warmup=WARMUP, periods_per_year=252,
        )
        rets = res.returns
        returns_by_book[name] = rets
        m = res.metrics
        results[name] = {"params": {**spec["params"], "spill_L": L},
                         "metrics": m,
                         "tail": _tail_stats(res),
                         "full_window_sharpe_per_period": sharpe_ratio(rets, periods_per_year=1)}
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] full run {name}: "
              f"{time.time() - t_start:.0f}s | {res.summary()}", flush=True)

    cm = results["book_h_gold_252"]["metrics"]
    mismatch = {k: (cm.get(k), v) for k, v in CERTIFIED_GOLD.items()
                if abs(cm.get(k, float("nan")) - v) > (0.5 if k == "n_trades" else 1e-6 * max(1.0, abs(v)))}
    if mismatch:
        print(f"CERTIFIED REPRODUCTION FAILED: {mismatch}", flush=True)
        return 1
    print(f"certified-anchor reproduction: EXACT (sharpe {cm['sharpe']:.5f}, "
          f"{cm['n_trades']} trades)", flush=True)

    aligned = pd.concat(list(returns_by_book.values()), axis=1).dropna()
    M = aligned.to_numpy()
    pbo = (probability_of_backtest_overfitting(M, n_splits=base_cfg.validation.pbo.n_splits, seed=base_cfg.seed)
           if M.shape[1] >= 2 and M.shape[0] >= 40 else {"pbo": None, "note": "insufficient matrix"})
    print(f"PBO across {M.shape[1]} configs: {pbo}", flush=True)

    trial_sharpes = [results[n]["full_window_sharpe_per_period"] for n in BOOKS]
    verdicts: dict[str, dict] = {}
    for name, spec in BOOKS.items():
        cfg = _cfg()
        L = spec["spill_L"]
        t_start = time.time()
        cpcv = run_portfolio_cpcv(
            panel, pits,
            lambda p, _L=L, **kw: _Model(p, spill_L=_L, risk_on_map=maps.get(_L),
                                         gated=gated, **kw),
            spec["params"],
            cfg=cfg, timeframes=timeframes, warmup=WARMUP, horizon=HORIZON,
            periods_per_year=252, exit_mode="managed",
        )
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] CPCV {name}: "
              f"{time.time() - t_start:.0f}s | paths={cpcv['oos_sharpe_paths']}", flush=True)
        verdicts[name] = _gate(name, returns_by_book[name], trial_sharpes, pbo, cpcv, used_trials)
        results[name]["cpcv"] = cpcv
        results[name]["gate"] = {k: v for k, v in verdicts[name].items() if k != "reasons"}

    a = results["book_h_gold_252"]
    chal_names = ["book_h_gold_252_spill20", "book_h_gold_252_spill50"]
    best = max(chal_names, key=lambda n: results[n]["metrics"]["sharpe"])
    b = results[best]
    d_sharpe = b["metrics"]["sharpe"] - a["metrics"]["sharpe"]
    l1 = bool(d_sharpe >= 0.0)
    l2 = bool(verdicts[best]["dsr"].get("dsr", 0.0) >= 0.95)
    l3 = bool(pbo.get("pbo") is not None and pbo["pbo"] < 0.5)
    paths_b = b["cpcv"]["oos_sharpe_paths"]
    med_b = float(pd.Series(paths_b).median())
    med_a = float(pd.Series(a["cpcv"]["oos_sharpe_paths"]).median())
    l4 = bool(sum(1 for p in paths_b if p > 0) >= 12 and med_b >= med_a)
    confirmed = bool(l1 and l2 and l3 and l4)

    print("\n" + "=" * 72, flush=True)
    for name, v in verdicts.items():
        print(f"  {name}: VERDICT {'PASS' if v['passed'] else 'REJECT'}")
        for r in v["reasons"]:
            print(f"    - {r}")
    print(f"  best challenger: {best}")
    print(f"  LEGS: L1 dSharpe {d_sharpe:+.4f} >= 0? {l1} | "
          f"L2 DSR {verdicts[best]['dsr'].get('dsr', 0):.4f} >= 0.95? {l2} | "
          f"L3 PBO {pbo.get('pbo')} < 0.5? {l3} | "
          f"L4 CPCV {sum(1 for p in paths_b if p > 0)}/15 & med {med_b:.4f} >= {med_a:.4f}? {l4}")
    print(f"  PRE-REGISTERED RULE => {'CONFIRMED (adoptable)' if confirmed else 'REJECTED'}")
    print("=" * 72, flush=True)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "iteration",
        "holdout_start": args.holdout_start,
        "prereg": "engine/data_store/momentum_spillover_gate_prereg.md",
        "proposal": "momentum-spillover-effect-2026-08-08",
        "universe": list(panel.keys()),
        "gated": list(gated),
        "n_trials_before": n_before,
        "n_trials_used": used_trials,
        "ledger_recorded": not args.no_ledger,
        "pbo": pbo,
        "best_challenger": best,
        "legs": {"l1_sharpe": l1, "l2_dsr": l2, "l3_pbo": l3, "l4_cpcv": l4},
        "verdict_rule": "CONFIRMED" if confirmed else "REJECTED",
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
