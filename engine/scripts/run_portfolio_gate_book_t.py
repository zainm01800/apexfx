"""Pre-registered gate: Book T — meta-label gate on Book H.

Prereg: engine/data_store/meta_label_prereg.md (2026-07-23), written BEFORE this run.

The secondary model predicts P(primary trade hits target before stop) and vetoes the weakest
signals. It can only REMOVE trades, so the risk profile (risk-per-trade, slots, stops) is
untouched — which is the point: it is the one mechanism left that can raise Sharpe without
raising drawdown.

LEAKAGE CONTROL IS THE WHOLE GAME: the secondary is fitted on bars strictly BEFORE
`--train-end` and every backtest is run only on bars AFTER it. Baseline and challengers share
the identical test window.

Binding rule (prereg §4): adopt the highest-Sharpe threshold with paired bootstrap p < 0.05 vs
the baseline AND forward p95 drawdown within +1pp of the baseline. DSR and PBO reported.

Also reports the two pre-registered falsification checks: veto rate must be 5-60%, and win rate
AND expectancy must both RISE (otherwise the gate is filtering at random).

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_portfolio_gate_book_t.py
    .venv-mac/bin/python scripts/run_portfolio_gate_book_t.py --no-ledger
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
from apex_quant.strategies.meta_labeling import MetaLabeledStrategy  # noqa: E402
from apex_quant.validation.metrics import (  # noqa: E402
    deflated_sharpe_ratio, probability_of_backtest_overfitting, sharpe_ratio,
)
from apex_quant.validation.paired_tests import paired_block_bootstrap  # noqa: E402
from apex_quant.validation.trials import TrialLedger  # noqa: E402

from run_portfolio_gate import (  # noqa: E402
    COMMON_PARAMS, DEFAULT_HOLDOUT_START, LEDGER_PATH, MIN_BARS, WARMUP, TrendBook, _utc,
)
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402

DEFAULT_RESULTS = ENGINE_DIR / "data_store" / "validation" / "book_t_gate_2026-07-23.json"
TRAIN_END = "2019-01-01"
BASELINE = "book_t_baseline"
THRESHOLDS = {"book_t_meta_050": 0.50, "book_t_meta_055": 0.55, "book_t_meta_060": 0.60}
N_EXTRA_CHARGED = 2          # thresholds examined in dev, charged not gated
DD_TOLERANCE = 0.01          # +1pp on forward p95 (prereg §4.2)


def forward_p95(returns: pd.Series, seed: int = 42) -> float:
    r = returns.dropna().to_numpy()
    if len(r) < 60:
        return float("nan")
    rng = np.random.default_rng(seed)
    eq = np.cumprod(1.0 + rng.choice(r, size=(20000, 252), replace=True), axis=1)
    pk = np.maximum.accumulate(eq, axis=1)
    return float(np.percentile(((pk - eq) / pk).max(axis=1), 95))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered gate: meta-label (Book T).")
    ap.add_argument("--train-end", default=TRAIN_END)
    ap.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START)
    ap.add_argument("--no-ledger", action="store_true")
    ap.add_argument("--out", default=str(DEFAULT_RESULTS))
    args = ap.parse_args(argv)

    cfg = get_config()
    store = ParquetStore(cfg.store_path)
    holdout = _utc(args.holdout_start)
    train_end = _utc(args.train_end)
    results_path = Path(args.out)

    panel: dict[str, pd.DataFrame] = {}
    for inst in EQUITY_CORE + [GOLD_ETC] + list(cfg.data.crypto) + FX_MAJORS_7:
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

    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not args.no_ledger:
        for name in [BASELINE, *THRESHOLDS] + [f"book_t_dev_{i}" for i in range(N_EXTRA_CHARGED)]:
            ledger.record({"book": name, "universe": "book_h_gold_39", "timeframe": "1d",
                           "factory": "trend_book_meta_label",
                           "params": {**params, "train_end": args.train_end}})
        ledger.save(LEDGER_PATH)
    used = ledger.n_trials if not args.no_ledger else n_before + 3 + N_EXTRA_CHARGED + 1

    print("=" * 100, flush=True)
    print(f"GATE (BOOK T — META-LABEL) | {len(panel)} instruments")
    print(f"  train (secondary fit): < {args.train_end}    "
          f"test (all backtests): {args.train_end} -> {args.holdout_start}")
    print(f"  ledger {n_before} -> {used} | DSR deflates by n={used}")
    print(f"  BINDING: paired p<0.05 AND forward p95 DD within +{DD_TOLERANCE*100:.0f}pp")
    print("=" * 100, flush=True)

    train_stamps = {inst: df.index[df.index < train_end] for inst, df in panel.items()}

    results: dict[str, dict] = {}
    rets: dict[str, pd.Series] = {}

    def run(strategies, label):
        t0 = time.time()
        res = PortfolioBacktester(cfg, risk_manager=RiskManager(cfg.risk),
                                  exit_mode="managed",
                                  slot_allocation="expected_value").run(
            pits, strategies, timeframes=timeframes, start=train_end,
            warmup=WARMUP, periods_per_year=252)
        m, eq = res.metrics, res.equity
        cagr = float(eq.iloc[-1] / eq.iloc[0]) ** (252.0 / len(eq)) - 1
        rets[label] = res.returns
        results[label] = {
            "metrics": m, "cagr": cagr, "gbp_per_month": cagr * 100_000 / 12,
            "forward_p95": forward_p95(res.returns),
            "full_window_sharpe_per_period": sharpe_ratio(res.returns, periods_per_year=1),
        }
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {label}: "
              f"{time.time()-t0:.0f}s | CAGR {cagr*100:.2f}% (£{cagr*100000/12:.0f}/mo) "
              f"sharpe {m['sharpe']:.3f} maxDD {m['max_drawdown']*100:.1f}% "
              f"trades {m['n_trades']} win {m['win_rate']*100:.1f}% "
              f"exp {m.get('expectancy_pct', 0)*100:+.3f}%", flush=True)
        return res

    run(TrendBook(panel, **params).strategies(), BASELINE)

    for label, thr in THRESHOLDS.items():
        base_strats = TrendBook(panel, **params).strategies()
        metas = {}
        n_fit = 0
        for inst, base in base_strats.items():
            ml = MetaLabeledStrategy(base, model="gbm", threshold=thr, seed=cfg.seed)
            try:
                ml.fit(pits[inst], train_stamps[inst])
                if ml.is_fitted():
                    n_fit += 1
            except Exception as e:              # noqa: BLE001
                print(f"    fit failed {inst}: {type(e).__name__}: {e}", flush=True)
            metas[inst] = ml
        print(f"  [{label}] secondary fitted on {n_fit}/{len(metas)} instruments", flush=True)
        run(metas, label)

    # ---- verdicts -----------------------------------------------------------------
    base_m = results[BASELINE]["metrics"]
    trial_sharpes = [results[n]["full_window_sharpe_per_period"] for n in results]
    aligned = pd.concat(list(rets.values()), axis=1).dropna()
    pbo = (probability_of_backtest_overfitting(aligned.to_numpy(),
                                               n_splits=cfg.validation.pbo.n_splits,
                                               seed=cfg.seed)
           if aligned.shape[0] >= 40 else {"pbo": None})

    verdicts = {}
    print("\n" + "=" * 100, flush=True)
    print(f"  baseline: £{results[BASELINE]['gbp_per_month']:.0f}/mo "
          f"sharpe {base_m['sharpe']:.3f} trades {base_m['n_trades']} "
          f"win {base_m['win_rate']*100:.1f}% exp {base_m.get('expectancy_pct',0)*100:+.3f}% "
          f"p95 {results[BASELINE]['forward_p95']*100:.1f}%", flush=True)

    for label in THRESHOLDS:
        m = results[label]["metrics"]
        p = paired_block_bootstrap(rets[BASELINE], rets[label], block_size=21,
                                   n_bootstraps=10000, seed=42, periods_per_year=252)
        dsr = deflated_sharpe_ratio(rets[label].to_numpy(), trial_sharpes, 252, n_trials=used)
        veto_rate = 1.0 - (m["n_trades"] / base_m["n_trades"]) if base_m["n_trades"] else 0.0
        dd_ok = results[label]["forward_p95"] <= results[BASELINE]["forward_p95"] + DD_TOLERANCE
        p_val = p.get("p_value_one_sided", 1.0)
        precision_ok = (m["win_rate"] > base_m["win_rate"]
                        and (m.get("expectancy_pct") or 0) > (base_m.get("expectancy_pct") or 0))
        bite_ok = 0.05 <= veto_rate <= 0.60
        adopt = bool(p_val < 0.05 and dd_ok)
        verdicts[label] = {
            "paired": p, "dsr": dsr, "veto_rate": veto_rate, "dd_neutral": dd_ok,
            "precision_improved": precision_ok, "gate_bites": bite_ok,
            "adopt_eligible": adopt,
        }
        print(f"  {label}: £{results[label]['gbp_per_month']:.0f}/mo "
              f"sharpe {m['sharpe']:.3f} trades {m['n_trades']} "
              f"win {m['win_rate']*100:.1f}% exp {m.get('expectancy_pct',0)*100:+.3f}% "
              f"p95 {results[label]['forward_p95']*100:.1f}%", flush=True)
        print(f"      veto {veto_rate*100:.1f}% {'ok' if bite_ok else 'FAIL(inert/destructive)'}"
              f" | precision {'UP' if precision_ok else 'NOT improved'}"
              f" | DD {'neutral' if dd_ok else 'WORSE'}"
              f" | paired Δsharpe {p.get('sharpe_delta',0):+.3f} p={p_val:.4f}"
              f" | DSR {dsr.get('dsr',0):.3f}"
              f"  -> {'ELIGIBLE' if adopt else 'REJECT'}", flush=True)

    elig = [k for k, v in verdicts.items() if v["adopt_eligible"]]
    winner = max(elig, key=lambda k: results[k]["metrics"]["sharpe"]) if elig else None
    print("-" * 100)
    print(f"  DECISION: {'ADOPT ' + winner if winner else 'ADOPT NOTHING'}")
    print("=" * 100, flush=True)

    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "prereg": "engine/data_store/meta_label_prereg.md",
           "train_end": args.train_end, "test_window": [args.train_end, args.holdout_start],
           "n_trials_before": n_before, "n_trials_used": used,
           "pbo": pbo, "books": results, "verdicts": verdicts,
           "decision": {"winner": winner}}
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"results WRITTEN: {results_path}", flush=True)
    return 0 if winner else 1


if __name__ == "__main__":
    sys.exit(main())
