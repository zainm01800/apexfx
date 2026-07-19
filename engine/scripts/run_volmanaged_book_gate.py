"""Pre-registered gate: the VOL-MANAGED multi-asset trend book (Sleeve A of the
max-Sharpe stack) vs the plain Book D baseline.

Hypothesis (pre-registered in engine/data_store/volmanaged_book_prereg.md, basis
docs/research/2026-07-18_beating_sharpe_1_2.md): wrapping Book D's per-instrument
signals in a conditional vol-target overlay (Barroso & Santa-Clara 2015: damp by
min(1, target_vol / own signal-vol proxy), target 0.10, 21d proxy; Daniel &
Moskowitz 2016: force FLAT when the instrument's 21d vol > 1.5x its 126d median
AND its 21d return < 0) lifts Sharpe by +0.1-0.3 vs the plain book and cuts
maxDD. Book D's sizing is already vol-scaled via RiskManager, so redundancy is
the stated alternative hypothesis.

Books (all on the SAME 42-instrument multi-asset universe, same window strictly
< 2025-01-01, same v5 costs, managed exits, caps binding, seed 42):
  * book_a_plain_252    — exact re-run of book_d_multiasset_252 (the clean-data
                          baseline of engine/data_store/portfolio_gate_multiasset_2026-07-17.md;
                          NOT a new ledger trial — identical canonical config
                          dedupes; re-run needed for aligned returns for PBO).
  * book_a_vm_252       — the same book wrapped per instrument in
                          VolTargetOverlay(target_vol=0.10, proxy_window=21,
                          median_window=126, stand_mult=1.5, panic_ret_window=21).
                          NEW ledger trial.
  * book_a_vm_252_standdown_only — ablation diagnostic (vol_scale=False,
                          stand_down=True): is the drawdown help from the
                          stand-down alone? NEW ledger trial. Full-window only
                          (no CPCV — diagnostic, stated in the pre-registration).

Gates (unchanged): DSR > 0.95 deflated by the ledger's FULL updated count
(n=184 after recording the 2 new trials BEFORE the runs; trial_sharpes = the 3
evaluated full-window Sharpes), PBO < 0.5 across the 3 evaluated configs
(2-way plain-vs-vm PBO also reported), CPCV median OOS Sharpe > 0 with > 50%
of 15 paths positive (plain + vm only).

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_volmanaged_book_gate.py
    .venv-mac/bin/python scripts/run_volmanaged_book_gate.py --instruments SPY,BTC/USD,USD/JPY --no-ledger

Exit code 0 if the vol-managed book passes all three gates, 1 otherwise.
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
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

import pandas as pd  # noqa: E402

from apex_quant.backtest.portfolio import PortfolioBacktester  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor, ParquetStore, clean  # noqa: E402
from apex_quant.strategies.vol_target_overlay import VolTargetOverlay  # noqa: E402
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
    _cap_families,
    _gate,
    _max_gross_leverage,
    _utc,
)
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402

RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "volmanaged_book_gate_2026-07-19.json"

# Book D's exact config (the frozen forward-paper trend book).
PLAIN_PARAMS = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}

# Pre-registered overlay parameters.
VM_PARAMS = {
    "target_vol": 0.10,
    "proxy_window": 21,
    "median_window": 126,
    "stand_mult": 1.5,
    "panic_ret_window": 21,
    "vol_scale": True,
    "stand_down": True,
}

# The evaluated selection set: (name, factory_kind, params, record_in_ledger, run_cpcv)
BOOKS = {
    "book_a_plain_252": ("plain", PLAIN_PARAMS, False, True),
    "book_a_vm_252": ("vm", {**PLAIN_PARAMS, "vm_params": VM_PARAMS}, True, True),
    "book_a_vm_252_standdown_only": (
        "vm", {**PLAIN_PARAMS, "vm_params": {**VM_PARAMS, "vol_scale": False}}, True, False),
}


class VolManagedBook:
    """The Book D trend book with each per-instrument strategy wrapped in
    ``VolTargetOverlay`` (same ``.strategies()``-only interface as TrendBook —
    rule-based, nothing to fit, CPCV's purged train split intentionally unused).
    """

    def __init__(self, panel: dict, *, vm_params: dict | None = None, **params) -> None:
        inner = TrendBook(panel, **params)
        vm = dict(vm_params or {})
        self._strategies = {
            inst: VolTargetOverlay(base, holding_horizon=params["holding_horizon"], **vm)
            for inst, base in inner.strategies().items()
        }

    def strategies(self) -> dict:
        return dict(self._strategies)

    def overlay_stats(self) -> dict:
        """Firing counters from the full-window run (fresh instances per CPCV fold
        are not aggregated)."""
        return {inst: {"signals": s.n_signals, "scaled": s.n_scaled,
                       "standdowns": s.n_standdowns}
                for inst, s in self._strategies.items()}


def _make_model(kind: str, panel: dict, params: dict):
    if kind == "plain":
        return TrendBook(panel, **params)
    return VolManagedBook(panel, **params)


def _annualized_turnover(res) -> float:
    """One-way entry notional traded per year per unit of mean equity. Quote-
    currency conversion ignored (same approximation as _max_gross_leverage)."""
    if not res.trades or res.equity.empty:
        return 0.0
    notional = sum(abs(tr.entry_price * tr.units) for tr in res.trades)
    mean_eq = float(res.equity.mean())
    years = len(res.equity) / 252.0
    return float(notional / mean_eq / years) if mean_eq > 0 and years > 0 else 0.0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered gate: vol-managed multi-asset trend "
                                             "book vs plain Book D (iteration window only).")
    ap.add_argument("--instruments", default="",
                    help="comma-separated subset (default: 24 equities + 12 crypto + 7 FX majors)")
    ap.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START,
                    help=f"iteration data is strictly before this date (default {DEFAULT_HOLDOUT_START})")
    ap.add_argument("--no-ledger", action="store_true",
                    help="smoke-test mode: do NOT record trials; DSR still deflates by the "
                         "ledger count the run WOULD have used (current + 2)")
    args = ap.parse_args(argv)

    cfg = get_config()
    store = ParquetStore(cfg.store_path)
    holdout_start = _utc(args.holdout_start)
    instruments = ([s.strip() for s in args.instruments.split(",") if s.strip()]
                   or list(cfg.data.equities) + list(cfg.data.crypto) + FX_MAJORS_7)

    panel: dict[str, pd.DataFrame] = {}
    for inst in instruments:
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

    # Record the 2 pre-registered NEW trials (vm, standdown-only ablation) BEFORE
    # running; the plain book is already ledgered as book_d_multiasset_252.
    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    n_new = sum(1 for _, _, rec, _ in BOOKS.values() if rec)
    if not args.no_ledger:
        for name, (_, params, rec, _) in BOOKS.items():
            if rec:
                ledger.record({"book": name, "universe": "multiasset_43", "timeframe": "1d",
                               "factory": "vol_target_overlay_trend_book", "params": params})
        ledger.save(LEDGER_PATH)
    used_trials = ledger.n_trials if not args.no_ledger else n_before + n_new

    print("=" * 72, flush=True)
    print(f"VOL-MANAGED BOOK GATE 2026-07-19 | mode=ITERATION (strictly < {args.holdout_start})")
    print(f"universe: {len(panel)} instruments | window: "
          f"{min(df.index[0] for df in panel.values()).date()} "
          f"-> {max(df.index[-1] for df in panel.values()).date()}")
    print(f"books: {list(BOOKS)} | ledger n_trials {n_before} -> "
          f"{ledger.n_trials if not args.no_ledger else n_before} | DSR deflation uses n_trials={used_trials}")
    print("=" * 72, flush=True)

    # 1. Full-window runs (aligned returns for PBO/DSR + trade metrics).
    results: dict[str, dict] = {}
    returns_by_book: dict[str, pd.Series] = {}
    models: dict[str, object] = {}
    for name, (kind, params, _rec, _cpcv) in BOOKS.items():
        t_start = time.time()
        model = _make_model(kind, panel, params)
        models[name] = model
        res = PortfolioBacktester(cfg, exit_mode="managed").run(
            pits, model.strategies(), timeframes=timeframes,
            warmup=WARMUP, periods_per_year=252,
        )
        rets = res.returns
        returns_by_book[name] = rets
        m = res.metrics
        results[name] = {"kind": kind, "params": params, "metrics": m,
                         "max_gross_leverage": _max_gross_leverage(res),
                         "annualized_turnover": _annualized_turnover(res),
                         "constraint_log": res.constraint_log,
                         "per_instrument": res.per_instrument,
                         "full_window_sharpe_per_period": sharpe_ratio(rets, periods_per_year=1)}
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] full run {name}: "
              f"{time.time() - t_start:.0f}s | {res.summary()}", flush=True)
        if not m.get("insufficient_data"):
            print(f"    expectancy={m['expectancy_pnl']:.2f} pnl/trade "
                  f"({m['expectancy_pct']*100:.3f}%/trade) profit_factor={m.get('profit_factor')} "
                  f"win_rate={m['win_rate']*100:.1f}% maxDD={m['max_drawdown']*100:.1f}% "
                  f"lev~{results[name]['max_gross_leverage']:.2f}x "
                  f"turnover~{results[name]['annualized_turnover']:.1f}x/yr "
                  f"| caps bound: {_cap_families(res.constraint_log)}", flush=True)

    # 1b. Overlay firing stats (full-window instances only).
    for name, model in models.items():
        stats = getattr(model, "overlay_stats", None)
        if not callable(stats):
            continue
        st = stats()
        tot_sig = sum(v["signals"] for v in st.values())
        tot_scl = sum(v["scaled"] for v in st.values())
        tot_sd = sum(v["standdowns"] for v in st.values())
        results[name]["overlay_stats"] = {
            "per_instrument": st, "signals": tot_sig, "scaled": tot_scl, "standdowns": tot_sd,
            "standdown_rate": (tot_sd / tot_sig) if tot_sig else 0.0,
            "scale_rate": (tot_scl / tot_sig) if tot_sig else 0.0,
        }
        print(f"    overlay {name}: {tot_sd}/{tot_sig} signals stood down "
              f"({(tot_sd / tot_sig * 100) if tot_sig else 0:.1f}%), "
              f"{tot_scl} damped ({(tot_scl / tot_sig * 100) if tot_sig else 0:.1f}%)", flush=True)

    # 2. PBO across the evaluated selection set (3 configs); also the 2-way
    #    plain-vs-vm comparison for the headline.
    aligned3 = pd.concat(list(returns_by_book.values()), axis=1).dropna()
    M3 = aligned3.to_numpy()
    pbo3 = (probability_of_backtest_overfitting(M3, n_splits=cfg.validation.pbo.n_splits, seed=cfg.seed)
            if M3.shape[1] >= 2 and M3.shape[0] >= 40 else {"pbo": None, "note": "insufficient matrix"})
    aligned2 = pd.concat([returns_by_book["book_a_plain_252"],
                          returns_by_book["book_a_vm_252"]], axis=1).dropna()
    M2 = aligned2.to_numpy()
    pbo2 = (probability_of_backtest_overfitting(M2, n_splits=cfg.validation.pbo.n_splits, seed=cfg.seed)
            if M2.shape[0] >= 40 else {"pbo": None, "note": "insufficient matrix"})
    print(f"PBO across 3 evaluated configs: {pbo3}", flush=True)
    print(f"PBO plain-vs-vm (2-way headline): {pbo2}", flush=True)

    # 3. CPCV OOS distribution (plain + vm; the ablation is a full-window
    #    diagnostic only, per the pre-registration).
    trial_sharpes = [results[n]["full_window_sharpe_per_period"] for n in BOOKS]
    verdicts: dict[str, dict] = {}
    for name, (kind, params, _rec, run_cpcv) in BOOKS.items():
        if not run_cpcv:
            continue
        t_start = time.time()
        cpcv = run_portfolio_cpcv(
            panel, pits, lambda p, _k=kind, _pa=params, **kw: _make_model(_k, p, _pa),
            {}, cfg=cfg, timeframes=timeframes, warmup=WARMUP, horizon=HORIZON,
            periods_per_year=252, exit_mode="managed",
        )
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] CPCV {name}: "
              f"{time.time() - t_start:.0f}s | paths={cpcv['oos_sharpe_paths']}", flush=True)
        results[name]["cpcv"] = cpcv
        verdicts[name] = _gate(name, returns_by_book[name], trial_sharpes, pbo3, cpcv, used_trials)
        results[name]["gate"] = {k: v for k, v in verdicts[name].items() if k != "reasons"}

    print("\n" + "=" * 72, flush=True)
    for name, v in verdicts.items():
        print(f"  {name}: VERDICT {'PASS' if v['passed'] else 'REJECT'}")
        for r in v["reasons"]:
            print(f"    - {r}")
    print("=" * 72, flush=True)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "iteration",
        "holdout_start": args.holdout_start,
        "universe": list(panel.keys()),
        "n_trials_before": n_before,
        "n_trials_used": used_trials,
        "ledger_recorded": not args.no_ledger,
        "pbo_3way": pbo3,
        "pbo_plain_vs_vm": pbo2,
        "books": results,
        "verdicts": verdicts,
    }
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_PATH, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"results written to {RESULTS_PATH}", flush=True)
    vm_verdict = verdicts.get("book_a_vm_252")
    return 0 if (vm_verdict and vm_verdict["passed"]) else 1


if __name__ == "__main__":
    sys.exit(main())
