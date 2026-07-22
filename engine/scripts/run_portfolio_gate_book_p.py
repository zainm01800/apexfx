"""Pre-registered gate: Book P — risk-per-trade 0.50% / 0.75% vs the current 1.00%.

Prereg: engine/data_store/risk_per_trade_prereg.md (2026-07-22).

Risk-per-trade is NOT a simple volume knob here. `max_portfolio_risk = 0.065` truncates
positions arbitrarily once risk-per-trade is large: cap-hit counts run 0 / 163 / 1,204 across
0.50% / 1.00% / 2.00% and trade count collapses 1,694 -> 458. The gain tracks the cap-hit
count, which is the falsifiable mechanism recorded in the prereg.

All configs use slot_allocation="expected_value" so the ordering artifact
(ordering_sensitivity_audit.md: Sharpe 0.217->0.863 on iteration order alone) is removed and
risk-per-trade is the only variable. Gap-aware stop fills active throughout.

FIVE trials are charged, not three: the two challengers were selected after seeing a 5-value
sweep, so the honest deflation count includes every value examined.

Binding decision rule (prereg §4) — adopt the highest-Sharpe config that satisfies ALL of:
  1. DSR > 0.95 at the full ledger count
  2. CPCV median OOS Sharpe > 0 and >50% paths positive
  3. PAIRED bootstrap vs baseline, p < 0.05 (PBO is reported but NOT binding — it cannot
     discriminate near-twin books; see the prereg)
  4. 95th-percentile forward 1-year drawdown <= 10%  (the funded-account wall)

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_portfolio_gate_book_p.py
    .venv-mac/bin/python scripts/run_portfolio_gate_book_p.py --out data_store/validation/book_p_gate_run2.json
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

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from apex_quant.backtest.portfolio import PortfolioBacktester  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor, ParquetStore, clean  # noqa: E402
from apex_quant.risk.manager import RiskManager  # noqa: E402
from apex_quant.validation.metrics import (  # noqa: E402
    probability_of_backtest_overfitting, sharpe_ratio,
)
from apex_quant.validation.paired_tests import paired_block_bootstrap  # noqa: E402
from apex_quant.validation.portfolio_report import run_portfolio_cpcv  # noqa: E402
from apex_quant.validation.trials import TrialLedger  # noqa: E402

from run_portfolio_gate import (  # noqa: E402
    COMMON_PARAMS, DEFAULT_HOLDOUT_START, HORIZON, LEDGER_PATH, MIN_BARS, WARMUP,
    TrendBook, _cap_families, _gate, _max_gross_leverage, _utc,
)
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7, _class_breakdown  # noqa: E402

DEFAULT_RESULTS = ENGINE_DIR / "data_store" / "validation" / "book_p_gate_2026-07-22.json"

BASELINE = "book_p_rpt100"
#: Gated configs. The 1.50%/2.00% values were examined in the sweep and are charged to the
#: ledger below, but are not gated — they were clearly worse and gating them would only
#: destabilise PBO further.
RISK_LEVELS = {"book_p_rpt100": 0.0100, "book_p_rpt075": 0.0075, "book_p_rpt050": 0.0050}
#: Every value examined during the diagnostic sweep — ALL are charged (prereg §1).
SWEPT_VALUES = [0.0050, 0.0075, 0.0100, 0.0150, 0.0200]
DD_WALL = 0.10          # funded-account 95th-percentile drawdown limit


def forward_drawdown(returns: pd.Series, n_sims: int = 20000, seed: int = 42) -> dict:
    """95th-percentile 1-year max drawdown, simulated from the realised return process.

    The backtest's max drawdown is the worst of ONE path; a funded account faces the
    distribution. The wall must be judged against the tail, not the single realisation.
    """
    r = returns.dropna().to_numpy()
    if len(r) < 100:
        return {"p95_drawdown": None, "note": "series too short"}
    rng = np.random.default_rng(seed)
    ppy = 252
    draws = rng.choice(r, size=(n_sims, ppy), replace=True)
    eq = np.cumprod(1.0 + draws, axis=1)
    peak = np.maximum.accumulate(eq, axis=1)
    dd = ((peak - eq) / peak).max(axis=1)
    return {
        "median_drawdown": float(np.median(dd)),
        "p95_drawdown": float(np.percentile(dd, 95)),
        "prob_breach_10pct": float((dd > DD_WALL).mean()),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered gate: risk-per-trade (Book P).")
    ap.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START)
    ap.add_argument("--no-ledger", action="store_true")
    ap.add_argument("--out", default=str(DEFAULT_RESULTS))
    args = ap.parse_args(argv)

    cfg = get_config()
    store = ParquetStore(cfg.store_path)
    holdout = _utc(args.holdout_start)
    results_path = Path(args.out)

    universe = EQUITY_CORE + [GOLD_ETC] + list(cfg.data.crypto) + FX_MAJORS_7
    panel: dict[str, pd.DataFrame] = {}
    for inst in universe:
        df = store.load(inst, "1d")
        if df.empty:
            continue
        df = clean(df)
        df = df[df.index < holdout]
        if len(df) >= MIN_BARS:
            panel[inst] = df
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    timeframes = {k: "1d" for k in panel}
    params = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}

    # Charge EVERY swept value (prereg §1) — selection came from seeing all five.
    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not args.no_ledger:
        for rpt in SWEPT_VALUES:
            ledger.record({"book": f"book_p_rpt{int(rpt*10000):04d}", "universe": "book_h_gold_39",
                           "timeframe": "1d", "factory": "trend_book_ev_rpt",
                           "params": {**params, "max_risk_per_trade": rpt,
                                      "slot_allocation": "expected_value"}})
        ledger.save(LEDGER_PATH)
    used = ledger.n_trials if not args.no_ledger else n_before + len(SWEPT_VALUES)

    print("=" * 78, flush=True)
    print(f"GATE (BOOK P — RISK PER TRADE) | {len(panel)} instruments | ITERATION < {args.holdout_start}")
    print(f"ledger {n_before} -> {ledger.n_trials if not args.no_ledger else n_before} "
          f"| DSR deflates by n={used} (ALL {len(SWEPT_VALUES)} swept values charged)")
    print("=" * 78, flush=True)

    results: dict[str, dict] = {}
    rets: dict[str, pd.Series] = {}
    for name, rpt in RISK_LEVELS.items():
        rc = cfg.risk.model_copy(update={"max_risk_per_trade": rpt})
        t0 = time.time()
        res = PortfolioBacktester(cfg, risk_manager=RiskManager(rc), exit_mode="managed",
                                  slot_allocation="expected_value").run(
            pits, TrendBook(panel, **params).strategies(), timeframes=timeframes,
            warmup=WARMUP, periods_per_year=252)
        rets[name] = res.returns
        m = res.metrics
        caphits = sum(v for k, v in res.constraint_log.items() if "portfolio_risk" in k)
        results[name] = {
            "risk_per_trade": rpt, "metrics": m,
            "portfolio_cap_hits": caphits,
            "max_gross_leverage": _max_gross_leverage(res),
            "per_asset_class": _class_breakdown(res.per_instrument, cfg),
            "full_window_sharpe_per_period": sharpe_ratio(res.returns, periods_per_year=1),
            "forward_drawdown": forward_drawdown(res.returns),
        }
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {name} "
              f"({rpt*100:.2f}%): {time.time()-t0:.0f}s | {res.summary()}", flush=True)
        fd = results[name]["forward_drawdown"]
        print(f"    cap hits {caphits} | fwd p95 DD {fd['p95_drawdown']*100:.1f}% "
              f"| P(breach 10%) {fd['prob_breach_10pct']*100:.1f}%", flush=True)

    aligned = pd.concat(list(rets.values()), axis=1).dropna()
    pbo = (probability_of_backtest_overfitting(aligned.to_numpy(),
                                               n_splits=cfg.validation.pbo.n_splits, seed=cfg.seed)
           if aligned.shape[0] >= 40 else {"pbo": None})
    print(f"PBO (reported, NOT binding — cannot discriminate near-twins): {pbo}", flush=True)

    trial_sharpes = [results[n]["full_window_sharpe_per_period"] for n in results]
    verdicts: dict[str, dict] = {}
    for name, rpt in RISK_LEVELS.items():
        rc = cfg.risk.model_copy(update={"max_risk_per_trade": rpt})
        t0 = time.time()
        cpcv = run_portfolio_cpcv(
            panel, pits, lambda p_, **kw: TrendBook(p_, **kw), params, cfg=cfg,
            timeframes=timeframes, warmup=WARMUP, horizon=HORIZON,
            periods_per_year=252, exit_mode="managed")
        v = _gate(name, rets[name], trial_sharpes, pbo, cpcv, used)
        paired = (paired_block_bootstrap(rets[BASELINE], rets[name], periods_per_year=252)
                  if name != BASELINE else None)
        fd = results[name]["forward_drawdown"]
        v["paired_vs_baseline"] = paired
        v["dd_wall_ok"] = bool(fd["p95_drawdown"] is not None and fd["p95_drawdown"] <= DD_WALL)
        # Binding rule: DSR + CPCV + paired p<0.05 + drawdown wall. PBO deliberately excluded.
        v["adopt_eligible"] = bool(
            v["dsr_pass"] and v["cpcv_pass"] and v["dd_wall_ok"]
            and (name == BASELINE or (paired and paired["p_value_one_sided"] < 0.05))
        )
        verdicts[name] = v
        results[name]["cpcv"] = cpcv
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] CPCV {name}: "
              f"{time.time()-t0:.0f}s | median {cpcv['oos_sharpe_median']:.4f} "
              f"{cpcv['frac_positive']*100:.0f}% positive", flush=True)

    print("\n" + "=" * 78, flush=True)
    for name, v in verdicts.items():
        p = v.get("paired_vs_baseline")
        fd = results[name]["forward_drawdown"]
        m = results[name]["metrics"]
        print(f"  {name} ({RISK_LEVELS[name]*100:.2f}%): sharpe {m['sharpe']:.3f} "
              f"ret {m['total_return']*100:.1f}% | DSR {'ok' if v['dsr_pass'] else 'FAIL'} "
              f"| CPCV {'ok' if v['cpcv_pass'] else 'FAIL'} "
              f"| wall {'ok' if v['dd_wall_ok'] else f'FAIL ({fd['p95_drawdown']*100:.1f}%)'}"
              + (f" | paired p={p['p_value_one_sided']:.4f} Δsharpe {p['sharpe_delta']:+.3f}" if p else "")
              + f"  -> {'ELIGIBLE' if v['adopt_eligible'] else 'REJECT'}")

    elig = [n for n, v in verdicts.items() if v["adopt_eligible"] and n != BASELINE]
    winner = max(elig, key=lambda n: results[n]["metrics"]["sharpe"]) if elig else None
    print("-" * 78)
    print(f"  DECISION: {'ADOPT ' + winner if winner else 'ADOPT NOTHING — current 1.00% stands'}")
    print("=" * 78, flush=True)

    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "prereg": "engine/data_store/risk_per_trade_prereg.md",
           "n_trials_before": n_before, "n_trials_used": used,
           "swept_values_charged": SWEPT_VALUES,
           "pbo": pbo, "pbo_binding": False, "dd_wall": DD_WALL,
           "books": results, "verdicts": verdicts,
           "decision": {"winner": winner, "eligible": elig}}
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    # Verify the artefact actually landed — a prior session reported "saved" four times
    # while writing nothing, because json.dump raised on non-serialisable objects.
    ok = results_path.exists() and results_path.stat().st_size > 1000
    print(f"results {'WRITTEN' if ok else 'FAILED TO WRITE'}: {results_path} "
          f"({results_path.stat().st_size if results_path.exists() else 0} bytes)", flush=True)
    return 0 if winner else 1


if __name__ == "__main__":
    sys.exit(main())
