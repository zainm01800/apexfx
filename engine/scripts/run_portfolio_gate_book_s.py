"""Pre-registered gate: Book S — 2.00% risk on 5 slots vs the live 0.75% on 12 slots.

Prereg: engine/data_store/concentration_risk_prereg.md (2026-07-23), written BEFORE this run.

Claim under test: the book's edge is concentrated in the top-ranked candidates, so cutting to
5 slots and re-deploying the freed capital at 2.00% risk raises Sharpe rather than merely
adding leverage. Independently supported by frontier_breadth_slots.json (Sharpe 0.922 -> 0.460
as slots widen 12 -> 30, i.e. marginal positions carry negative edge).

Binding rule (prereg §4): adopt only if DSR > 0.95, CPCV median OOS Sharpe > 0 with >50% paths
positive, paired block bootstrap p < 0.05, forward p95 drawdown <= 16%, AND CAGR >= 8.4%
(£700/month). PBO reported but NOT binding.

Also reports the falsification test the prereg named in advance: per-trade expectancy must be
materially HIGHER for the 5-slot book. If it is not, this is leverage dressed as a finding.

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_portfolio_gate_book_s.py
    .venv-mac/bin/python scripts/run_portfolio_gate_book_s.py --no-ledger   # dry run
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
    TrendBook, _gate, _max_gross_leverage, _utc,
)
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7, _class_breakdown  # noqa: E402

DEFAULT_RESULTS = ENGINE_DIR / "data_store" / "validation" / "book_s_gate_2026-07-23.json"

BASELINE = "book_s_control_075_12"
CHALLENGER = "book_s_conc_200_5"
#: label -> (risk, concurrent, swing)
CONFIGS = {
    BASELINE: (0.0075, 12, 10),
    CHALLENGER: (0.0200, 5, 5),
}
#: Every 5-slot cell examined in the concentration sweep, charged (prereg §1).
SWEPT_CELLS = [(0.0050, 5), (0.0100, 5), (0.0150, 5), (0.0200, 5),
               (0.0050, 3), (0.0200, 3)]

DD_CEILING = 0.16       # BINDING (prereg §4.5) — set from "around 12%" + headroom
CAGR_FLOOR = 0.084      # BINDING (prereg §4.6) — £700/month on £100k
DSR_THRESHOLD = 0.95


def forward_drawdown(returns: pd.Series, n_sims: int = 20000, seed: int = 42) -> dict:
    r = returns.dropna().to_numpy()
    if len(r) < 100:
        return {"p95_drawdown": None}
    rng = np.random.default_rng(seed)
    eq = np.cumprod(1.0 + rng.choice(r, size=(n_sims, 252), replace=True), axis=1)
    pk = np.maximum.accumulate(eq, axis=1)
    dd = ((pk - eq) / pk).max(axis=1)
    return {
        "median_drawdown": float(np.median(dd)),
        "p95_drawdown": float(np.percentile(dd, 95)),
        "prob_exceed_ceiling": float((dd > DD_CEILING).mean()),
        "prob_exceed_20pct": float((dd > 0.20).mean()),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered gate: concentration (Book S).")
    ap.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START)
    ap.add_argument("--no-ledger", action="store_true")
    ap.add_argument("--out", default=str(DEFAULT_RESULTS))
    args = ap.parse_args(argv)

    cfg = get_config()
    store = ParquetStore(cfg.store_path)
    holdout = _utc(args.holdout_start)
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
        for risk, slots in SWEPT_CELLS:
            ledger.record({"book": f"book_s_cell_r{int(risk*10000):04d}_s{slots}",
                           "universe": "book_h_gold_39", "timeframe": "1d",
                           "factory": "trend_book_concentration",
                           "params": {**params, "max_risk_per_trade": risk,
                                      "max_concurrent_trades": slots}})
        for name, (risk, conc, swing) in CONFIGS.items():
            ledger.record({"book": name, "universe": "book_h_gold_39", "timeframe": "1d",
                           "factory": "trend_book_concentration_gate",
                           "params": {**params, "max_risk_per_trade": risk,
                                      "max_concurrent_trades": conc}})
        ledger.save(LEDGER_PATH)
    used = ledger.n_trials if not args.no_ledger else n_before + len(SWEPT_CELLS) + 2

    print("=" * 96, flush=True)
    print(f"GATE (BOOK S — CONCENTRATION) | {len(panel)} instruments | "
          f"ITERATION < {args.holdout_start}")
    print(f"ledger {n_before} -> {used} | DSR deflates by n={used}")
    print(f"BINDING: forward p95 DD <= {DD_CEILING*100:.0f}%  AND  "
          f"CAGR >= {CAGR_FLOOR*100:.1f}% (£700/mo)")
    print("=" * 96, flush=True)

    results: dict[str, dict] = {}
    rets: dict[str, pd.Series] = {}
    for name, (risk, conc, swing) in CONFIGS.items():
        rc = cfg.risk.model_copy(update={
            "max_risk_per_trade": risk, "max_concurrent_trades": conc,
            "max_swing_slots": swing,
        })
        t0 = time.time()
        res = PortfolioBacktester(cfg, risk_manager=RiskManager(rc), exit_mode="managed",
                                  slot_allocation="expected_value").run(
            pits, TrendBook(panel, **params).strategies(), timeframes=timeframes,
            warmup=WARMUP, periods_per_year=252)
        rets[name] = res.returns
        m, eq = res.metrics, res.equity
        cagr = float(eq.iloc[-1] / eq.iloc[0]) ** (252.0 / len(eq)) - 1
        fd = forward_drawdown(res.returns)
        results[name] = {
            "risk_per_trade": risk, "slots": conc,
            "metrics": m, "cagr": cagr, "gbp_per_month": cagr * 100_000 / 12,
            "forward_drawdown": fd,
            "expectancy_pct": m.get("expectancy_pct"),
            "expectancy_pnl": m.get("expectancy_pnl"),
            "max_gross_leverage": _max_gross_leverage(res),
            "per_asset_class": _class_breakdown(res.per_instrument, cfg),
            "full_window_sharpe_per_period": sharpe_ratio(res.returns, periods_per_year=1),
            "constraint_log": dict(res.constraint_log),
        }
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {name}: "
              f"{time.time()-t0:.0f}s | CAGR {cagr*100:.2f}% (£{cagr*100000/12:.0f}/mo) "
              f"sharpe {m['sharpe']:.3f} btDD {m['max_drawdown']*100:.1f}% "
              f"fwd p95 {fd['p95_drawdown']*100:.1f}% trades {m['n_trades']} "
              f"expectancy {m.get('expectancy_pct', 0)*100:+.3f}%", flush=True)

    aligned = pd.concat(list(rets.values()), axis=1).dropna()
    pbo = (probability_of_backtest_overfitting(aligned.to_numpy(),
                                               n_splits=cfg.validation.pbo.n_splits,
                                               seed=cfg.seed)
           if aligned.shape[0] >= 40 else {"pbo": None})
    print(f"\nPBO (reported, NOT binding): {pbo}", flush=True)

    trial_sharpes = [results[n]["full_window_sharpe_per_period"] for n in results]
    verdicts: dict[str, dict] = {}
    for name in CONFIGS:
        t0 = time.time()
        cpcv = run_portfolio_cpcv(
            panel, pits, lambda p_, **kw: TrendBook(p_, **kw), params, cfg=cfg,
            timeframes=timeframes, warmup=WARMUP, horizon=HORIZON,
            periods_per_year=252, exit_mode="managed")
        v = _gate(name, rets[name], trial_sharpes, pbo, cpcv, used)
        fd = results[name]["forward_drawdown"]
        v["dd_ceiling_ok"] = bool(fd["p95_drawdown"] is not None
                                  and fd["p95_drawdown"] <= DD_CEILING)
        v["cagr_floor_ok"] = bool(results[name]["cagr"] >= CAGR_FLOOR)
        if name == CHALLENGER:
            v["paired_vs_control"] = paired_block_bootstrap(
                rets[BASELINE], rets[CHALLENGER], block_size=21,
                n_bootstraps=10000, seed=42, periods_per_year=252)
        verdicts[name] = v
        results[name]["cpcv"] = cpcv
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] CPCV {name}: "
              f"{time.time()-t0:.0f}s | median {cpcv['oos_sharpe_median']:.4f} "
              f"{cpcv['frac_positive']*100:.0f}% positive", flush=True)

    p = verdicts[CHALLENGER].get("paired_vs_control", {})
    p_val = p.get("p_value_one_sided", 1.0)
    adopt = bool(verdicts[CHALLENGER]["dsr_pass"] and verdicts[CHALLENGER]["cpcv_pass"]
                 and verdicts[CHALLENGER]["dd_ceiling_ok"]
                 and verdicts[CHALLENGER]["cagr_floor_ok"]
                 and p_val is not None and p_val < 0.05)
    verdicts[CHALLENGER]["adopt_eligible"] = adopt

    # Prereg §2 falsification: expectancy must be materially higher, else it is leverage.
    e_base = results[BASELINE].get("expectancy_pct") or 0.0
    e_chal = results[CHALLENGER].get("expectancy_pct") or 0.0
    mech = e_chal > e_base * 1.10
    print("\n" + "=" * 96, flush=True)
    print(f"  PREREG §2 FALSIFICATION — per-trade expectancy:")
    print(f"    control  {e_base*100:+.4f}%   challenger {e_chal*100:+.4f}%   "
          f"-> mechanism {'SUPPORTED' if mech else 'NOT supported (this is leverage)'}")
    for name, v in verdicts.items():
        r_, m_ = results[name], results[name]["metrics"]
        print(f"  {name}: £{r_['gbp_per_month']:.0f}/mo sharpe {m_['sharpe']:.3f} "
              f"| DSR {v['dsr'].get('dsr', 0):.3f} {'ok' if v['dsr_pass'] else 'FAIL'} "
              f"| CPCV {'ok' if v['cpcv_pass'] else 'FAIL'} "
              f"| DD<=16% {'ok' if v['dd_ceiling_ok'] else 'FAIL'} "
              f"| £700 {'ok' if v['cagr_floor_ok'] else 'FAIL'}", flush=True)
    if p:
        print(f"  paired (challenger vs control): Δsharpe {p.get('sharpe_delta', 0):+.3f} "
              f"p={p_val:.4f} CI [{p.get('ci_95_lower', 0):+.3f}, "
              f"{p.get('ci_95_upper', 0):+.3f}]", flush=True)
    print("-" * 96)
    print(f"  DECISION: {'ADOPT ' + CHALLENGER if adopt else 'ADOPT NOTHING'}")
    print("=" * 96, flush=True)

    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "prereg": "engine/data_store/concentration_risk_prereg.md",
           "n_trials_before": n_before, "n_trials_used": used,
           "pbo": pbo, "pbo_binding": False,
           "dd_ceiling": DD_CEILING, "cagr_floor": CAGR_FLOOR,
           "mechanism_supported": mech,
           "expectancy": {"control": e_base, "challenger": e_chal},
           "books": results, "verdicts": verdicts,
           "decision": {"adopt": CHALLENGER if adopt else None}}
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    ok = results_path.exists() and results_path.stat().st_size > 1000
    print(f"results {'WRITTEN' if ok else 'FAILED TO WRITE'}: {results_path}", flush=True)
    return 0 if adopt else 1


if __name__ == "__main__":
    sys.exit(main())
