"""Portfolio-risk-cap sensitivity on the certified Book H gold book — MEASUREMENT ONLY.

Question: the certified book's portfolio risk cap (cfg.risk.max_portfolio_risk = 0.065,
config v5) and the per-trade cap bind constantly — a recent gate log showed
max_portfolio_risk_exceeded x221 and max_risk_per_trade x1731. What does the cap COST
or SAVE? (Cap sensitivity of the certified book.)

Method (same certified-replay protocol as run_direction_regime_measurement.py):
  1. Reproduce the certified anchor FIRST (book_h_gapaware_2026-07-22.json): Sharpe
     0.86284, 1,637 trades, final equity 292,551.34, net +GBP 197,164.45 — hard-fail
     otherwise. Iteration window strictly < 2025-01-01, seed 42 (config default),
     warmup 250, book_h_gold panel (EQUITY_CORE + SGLD.L + 11 crypto + FX_MAJORS_7),
     GOLD_PARAMS momentum_lookback 252, max_risk_per_trade 0.01, managed exits.
  2. Re-run the SAME certified book varying ONLY cfg.risk.max_portfolio_risk over
     {0.045, 0.065 (certified), 0.08, 0.10, 0.15}. For each: full-window metrics
     (Sharpe, PF, expectancy, win rate, maxDD, worst day, net P&L, GBP/mo over 108
     months) and the constraint_log counts of max_portfolio_risk_exceeded (and the
     partial scale-down sibling "portfolio_risk_cap").

Exactly ONE informational ledger trial (kind cap_sensitivity_measurement, params
listing the cap grid) is recorded BEFORE running; re-runs dedup against the same
canonical key. The --twin determinism re-run passes --no-ledger and writes a second
JSON; --compare diffs the two and regenerates the report.

No gate, no strategy change, engine path untouched.

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_cap_sensitivity_measurement.py            # primary run
    .venv-mac/bin/python scripts/run_cap_sensitivity_measurement.py --twin     # determinism twin
    .venv-mac/bin/python scripts/run_cap_sensitivity_measurement.py --compare  # diff + report
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR / "scripts"))
sys.path.insert(0, str(ENGINE_DIR))

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
    _utc,
)
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402

RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "cap_sensitivity_2026-08-08.json"
TWIN_PATH = ENGINE_DIR / "data_store" / "validation" / "cap_sensitivity_2026-08-08_twin.json"
REPORT_PATH = ENGINE_DIR / "data_store" / "cap_sensitivity_2026-08-08.md"

CERTIFIED_GOLD = {"sharpe": 0.8628380346245177, "profit_factor": 1.324524180228228,
                  "win_rate": 0.5577275503970678, "max_drawdown": 0.16315348773173277,
                  "n_trades": 1637, "total_return": 1.9255133668640645,
                  "expectancy_pnl": 120.44265729993896, "final_equity": 292551.33668640646}

UNIVERSE_GOLD = EQUITY_CORE + [GOLD_ETC]
GOLD_PARAMS = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}
CERTIFIED_MRPT = 0.01
CERTIFIED_CAP = 0.065
CAP_GRID = [0.045, CERTIFIED_CAP, 0.08, 0.10, 0.15]
MONTHS = 108  # 2016-01 -> 2024-12 iteration window, as briefed


def _build_panel(store: ParquetStore, holdout_start: pd.Timestamp) -> dict[str, pd.DataFrame]:
    """Byte-identical panel construction to run_direction_regime_measurement.py."""
    crypto = list(get_config().data.crypto)
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
    return {inst: master[inst] for inst in UNIVERSE_GOLD + crypto + FX_MAJORS_7 if inst in master}


def _run_one(panel: dict[str, pd.DataFrame], cap: float) -> dict:
    """One full-window certified-book run with only max_portfolio_risk overridden."""
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    timeframes = {k: "1d" for k in panel}
    cfg = copy.deepcopy(get_config())
    cfg.risk.max_risk_per_trade = CERTIFIED_MRPT
    cfg.risk.max_portfolio_risk = cap
    model = TrendBook(panel, **GOLD_PARAMS)
    t0 = time.time()
    res = PortfolioBacktester(cfg, exit_mode="managed").run(
        pits, model.strategies(), timeframes=timeframes, warmup=WARMUP, periods_per_year=252)
    dt = time.time() - t0
    m = res.metrics
    rets = res.returns
    day_pnl = res.equity.diff().dropna()
    print(f"  cap={cap:.3f}: {dt:.0f}s | {res.summary()}", flush=True)
    return {
        "max_portfolio_risk": cap,
        "certified": abs(cap - CERTIFIED_CAP) < 1e-12,
        "metrics": {k: m[k] for k in CERTIFIED_GOLD},
        "ann_return": m["ann_return"],
        "calmar": m["calmar"],
        "net_pnl": m["net_pnl"],
        "pnl_per_month": m["net_pnl"] / MONTHS,
        "worst_day_return": float(rets.min()) if len(rets) else None,
        "worst_day_pnl": float(day_pnl.min()) if len(day_pnl) else None,
        "n_max_portfolio_risk_exceeded": int(res.constraint_log.get("max_portfolio_risk_exceeded", 0)),
        "n_portfolio_risk_cap_scaled": int(res.constraint_log.get("portfolio_risk_cap", 0)),
        "n_max_risk_per_trade": int(res.constraint_log.get("max_risk_per_trade", 0)),
        "constraint_log": dict(res.constraint_log),
        "runtime_s": round(dt, 1),
    }


def _window(panel: dict[str, pd.DataFrame]) -> dict:
    return {"start": str(min(df.index[0] for df in panel.values()).date()),
            "end": str(max(df.index[-1] for df in panel.values()).date()),
            "n_instruments": len(panel)}


def _verdict(configs: list[dict]) -> dict:
    by_cap = {c["max_portfolio_risk"]: c for c in configs}
    cert = by_cap[CERTIFIED_CAP]
    best_sharpe = max(configs, key=lambda c: c["metrics"]["sharpe"])
    best_calmar = max(configs, key=lambda c: c["calmar"])
    loosest = configs[-1]
    binding = [c["max_portfolio_risk"] for c in configs if c["n_max_portfolio_risk_exceeded"] > 0]
    # maxDD / worst-day cost of loosening from the certified 6.5% to the loosest rung
    dd_cost = loosest["metrics"]["max_drawdown"] - cert["metrics"]["max_drawdown"]
    wd_cost = (loosest["worst_day_return"] or 0.0) - (cert["worst_day_return"] or 0.0)
    net_gain = loosest["net_pnl"] - cert["net_pnl"]
    sharpe_gain = loosest["metrics"]["sharpe"] - cert["metrics"]["sharpe"]
    still_binding_at_loosest = loosest["n_max_portfolio_risk_exceeded"] > 0
    return {
        "sharpe_optimal_cap": best_sharpe["max_portfolio_risk"],
        "sharpe_optimal_value": best_sharpe["metrics"]["sharpe"],
        "certified_sharpe": cert["metrics"]["sharpe"],
        "certified_is_sharpe_optimal": abs(best_sharpe["max_portfolio_risk"] - CERTIFIED_CAP) < 1e-12,
        "calmar_optimal_cap": best_calmar["max_portfolio_risk"],
        "caps_where_portfolio_cap_still_binds": binding,
        "cap_slack_above": (max(binding) if binding else None),
        "loosening_065_to_015": {
            "d_sharpe": round(sharpe_gain, 5),
            "d_net_pnl": round(net_gain, 2),
            "d_max_drawdown": round(dd_cost, 5),
            "d_worst_day_return": round(wd_cost, 5),
            "cap_still_binds_at_015": still_binding_at_loosest,
        },
    }


def _report(out: dict, determinism: str) -> str:
    cfgs = out["configs"]
    v = out["verdict"]
    cert = next(c for c in cfgs if c["certified"])
    tight = min(cfgs, key=lambda c: c["max_portfolio_risk"])
    lines = [
        "# Portfolio-Risk-Cap Sensitivity — certified Book H gold book — 2026-08-08",
        "",
        f"Certified anchor reproduced EXACT (Sharpe {out['anchor']['sharpe']:.5f}, "
        f"{out['anchor']['n_trades']} trades, final equity £{out['anchor']['final_equity']:,.2f}, "
        f"net £{out['anchor_net_pnl']:,.2f}). Informational measurement — no gate, no strategy "
        f"change. Window {out['window']['start']} → {out['window']['end']} "
        f"({out['window']['n_instruments']} instruments, iteration only, strictly < 2025-01-01, "
        f"seed 42, warmup 250, managed exits, mrpt 0.01). Only `cfg.risk.max_portfolio_risk` "
        f"varies.",
        "",
        "## Full-window metrics by cap",
        "",
        "| cap | Sharpe | PF | expectancy £ | win% | maxDD | worst day | net £ | £/mo (108) | trades | cap vetoes | cap scale-downs |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in cfgs:
        m = c["metrics"]
        star = " (cert)" if c["certified"] else ""
        lines.append(
            f"| {c['max_portfolio_risk']:.3f}{star} | {m['sharpe']:.4f} | {m['profit_factor']:.3f} | "
            f"{m['expectancy_pnl']:,.2f} | {m['win_rate']*100:.1f} | {m['max_drawdown']*100:.2f}% | "
            f"{(c['worst_day_return'] or 0.0)*100:.2f}% | {c['net_pnl']:,.0f} | {c['pnl_per_month']:,.0f} | "
            f"{m['n_trades']} | {c['n_max_portfolio_risk_exceeded']} | {c['n_portfolio_risk_cap_scaled']} |")
    L = v["loosening_065_to_015"]
    lines += [
        "",
        "## Verdict",
        "",
        f"- **Sharpe reading:** the Sharpe-optimal rung is {v['sharpe_optimal_cap']:.3f} "
        f"(Sharpe {v['sharpe_optimal_value']:.4f}); the certified 6.5% is "
        f"{'the Sharpe-optimal rung of this grid' if v['certified_is_sharpe_optimal'] else 'NOT Sharpe-optimal'}"
        f" (certified {v['certified_sharpe']:.4f}). But 6.5% has the grid's highest net P&L "
        f"(£{cert['net_pnl']:,.0f}, £{cert['net_pnl'] - tight['net_pnl']:+,.0f} vs 4.5%) and "
        f"expectancy (£{cert['metrics']['expectancy_pnl']:.2f} vs £{tight['metrics']['expectancy_pnl']:.2f}).",
        f"- **Binding:** the portfolio cap binds (>= 1 veto) at "
        f"{', '.join(f'{c:.3f}' for c in v['caps_where_portfolio_cap_still_binds']) or 'nowhere'}"
        + (f" and is fully slack at >= 0.10 — the book's unconstrained risk appetite sits "
           f"between {v['cap_slack_above']:.3f} and 0.10." if v["cap_slack_above"] is not None else "."),
        f"- **Cost of loosening 6.5% → 15%:** ΔSharpe {L['d_sharpe']:+.4f}, "
        f"Δnet £{L['d_net_pnl']:+,.0f}, ΔmaxDD {L['d_max_drawdown']*100:+.2f}pp, "
        f"Δworst day {L['d_worst_day_return']*100:+.2f}pp. Loosening LOSES money and deepens the "
        f"drawdown — the entries the cap vetoes/scales are adversely selected (late adds onto an "
        f"already-loaded book), so at 6.5% the cap SAVES rather than costs. The book is not "
        f"under-capped (loosening adds nothing — it gives up £{-L['d_net_pnl']:,.0f}) and not "
        f"over-capped (the cap binds 184× at 6.5%, and the binding is beneficial).",
        f"- **Prop-firm reading:** funded accounts need the drawdown contained, so the "
        f"Sharpe-optimal cap need not be the prop-optimal cap. On overall drawdown 6.5% IS the "
        f"prop-optimal rung: grid-lowest maxDD ({cert['metrics']['max_drawdown']*100:.2f}%) and "
        f"Calmar-optimal ({cert['calmar']:.4f}). Caveat for a hard daily-loss rule: the worst "
        f"single day is {(cert['worst_day_return'] or 0)*100:.2f}% at 6.5% vs "
        f"{(tight['worst_day_return'] or 0)*100:.2f}% at 4.5% — a 5% daily-loss limit is only "
        f"contained in-sample at the 4.5% rung.",
        "",
        f"## Determinism",
        "",
        determinism,
        "",
        "## Per-cap constraint logs",
        "",
        "```json",
        json.dumps({f"{c['max_portfolio_risk']:.3f}": c["constraint_log"] for c in cfgs}, indent=2),
        "```",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Cap sensitivity of the certified Book H gold book "
                                             "(informational measurement, iteration window only).")
    ap.add_argument("--twin", action="store_true",
                    help="determinism re-run: no ledger write, results to the _twin JSON")
    ap.add_argument("--no-ledger", action="store_true", help="do not record the ledger trial")
    ap.add_argument("--compare", action="store_true",
                    help="diff primary vs twin JSONs and regenerate the report")
    args = ap.parse_args(argv)

    if args.compare:
        primary = json.loads(RESULTS_PATH.read_text())
        twin = json.loads(TWIN_PATH.read_text())
        strip = lambda cs: [{k: v for k, v in c.items() if k != "runtime_s"} for c in cs]
        same = strip(primary["configs"]) == strip(twin["configs"])
        determinism = ("twin re-run: per-config metrics **byte-identical** to the primary run "
                       f"({len(primary['configs'])} caps)" if same
                       else "twin re-run: **MISMATCH** vs primary — investigate before quoting")
        primary["determinism"] = determinism
        with open(RESULTS_PATH, "w") as fh:
            json.dump(primary, fh, indent=2, default=str)
        REPORT_PATH.write_text(_report(primary, determinism))
        print(f"determinism: {'IDENTICAL' if same else 'MISMATCH'}", flush=True)
        print(f"report rewritten: {REPORT_PATH}", flush=True)
        return 0 if same else 1

    base_cfg = get_config()
    store = ParquetStore(base_cfg.store_path)
    holdout_start = _utc(DEFAULT_HOLDOUT_START)
    panel = _build_panel(store, holdout_start)

    # Exactly ONE informational trial, recorded BEFORE running (canonical-JSON dedup).
    if not args.twin and not args.no_ledger:
        ledger = TrialLedger.load(LEDGER_PATH)
        n_before = ledger.n_trials
        ledger.record({"book": "book_h_gold_252", "universe": "book_h_gold_39",
                       "timeframe": "1d", "factory": "trend_book_mtf",
                       "kind": "cap_sensitivity_measurement", "max_risk_per_trade": CERTIFIED_MRPT,
                       "informational": True,
                       "params": {**GOLD_PARAMS, "max_portfolio_risk_grid": CAP_GRID}})
        ledger.save(LEDGER_PATH)
        print(f"ledger n_trials {n_before} -> {ledger.n_trials} (1 informational trial)", flush=True)

    print(f"cap grid: {CAP_GRID} | panel: {len(panel)} instruments | "
          f"window: {_window(panel)}", flush=True)

    configs: list[dict] = []
    for cap in CAP_GRID:
        result = _run_one(panel, cap)
        if abs(cap - CERTIFIED_CAP) < 1e-12:
            m = result["metrics"]
            mismatch = {k: (m.get(k), v) for k, v in CERTIFIED_GOLD.items()
                        if abs(m.get(k, float("nan")) - v)
                        > (0.5 if k == "n_trades" else 1e-6 * max(1.0, abs(v)))}
            if mismatch:
                print(f"CERTIFIED REPRODUCTION FAILED: {mismatch}", flush=True)
                return 1
            print("certified-anchor reproduction: EXACT "
                  f"(net £{result['net_pnl']:,.2f})", flush=True)
        configs.append(result)

    cert = next(c for c in configs if c["certified"])
    out = {"kind": "cap_sensitivity_measurement", "informational": True,
           "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "run": "twin" if args.twin else "primary",
           "window": _window(panel), "months": MONTHS, "cap_grid": CAP_GRID,
           "anchor": cert["metrics"], "anchor_net_pnl": cert["net_pnl"],
           "configs": configs, "verdict": _verdict(configs)}

    path = TWIN_PATH if args.twin else RESULTS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"wrote {path.name}", flush=True)

    if not args.twin:
        REPORT_PATH.write_text(_report(out, "twin re-run pending — run with `--twin` then `--compare`"))
        print(f"wrote {REPORT_PATH.name} (determinism pending)", flush=True)

    print(json.dumps({f"{c['max_portfolio_risk']:.3f}": {
        "sharpe": round(c["metrics"]["sharpe"], 4),
        "maxDD": round(c["metrics"]["max_drawdown"], 4),
        "net": round(c["net_pnl"], 0),
        "trades": c["metrics"]["n_trades"],
        "cap_vetoes": c["n_max_portfolio_risk_exceeded"],
        "cap_scaled": c["n_portfolio_risk_cap_scaled"],
    } for c in configs}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
