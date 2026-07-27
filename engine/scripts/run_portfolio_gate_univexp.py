"""Pre-registered portfolio-level gate: UNIVERSE EXPANSION on Book H gold (2026-07-27).

Pre-registration: engine/data_store/universe_expansion_2_prereg.md (written BEFORE any
run; 2 NEW trials recorded before execution, dedup-safe; the control re-records the
certified book_h_gold_252 canonical key and dedups). Owner-commissioned re-test under
the Book K closure conditions — the prereg §1 states the boundary; single shot.

The expansion is what the mechanical Book-K-rule screen (R0 data / R1 halal / R2
independence |corr|<0.50 vs every certified holding / R3 internal dedup) left standing
from a 25-candidate pool of UCITS sector/thematic ETFs (USD lines), untested
halal-screened healthcare/industrials/semis-equipment large caps, and other asset
classes: **SPSK (sukuk) + AMGN**. Everything else failed the independence rule against
the ISDU.L/XLK/SOXX/XLE/XBI block (full table in the prereg §4).

Exactly 3 pre-registered configs (the full selection set):
  univexp_control_252     certified 39, [252] (anchor hard-check vs CERTIFIED_GOLD)
  univexp_expanded_252    certified 39 + SPSK + AMGN, [252]   <- comparison of record
  univexp_expanded_ens    certified 39 + SPSK + AMGN, [63,126,252] (secondary read)

Expanded-panel insertion order (pre-registered): certified 39 in certified order, new
names appended last (SPSK, AMGN) — certified allocations keep first claim on caps.

Adoption rule (prereg §6, comparison of record): ADOPT iff ALL of
  Sharpe(expanded_252) - Sharpe(control) >= +0.05,
  CPCV median(expanded_252) >= CPCV median(control),
  DSR(expanded_252) > 0.95 at the full updated ledger count,
  PBO < 0.5 across the 3-config set.
Kill: any leg fails. The ensemble read is informational against the recorded
trend_ens_blend_63_126_252 control (validation/trend_ensemble_gate_2026-07-27.json).

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_portfolio_gate_univexp.py                 # full gate
    .venv-mac/bin/python scripts/run_portfolio_gate_univexp.py --instruments AAPL,MSFT,SPSK,AMGN --no-ledger --skip-cpcv
    .venv-mac/bin/python scripts/run_portfolio_gate_univexp.py --out <twin>    # determinism rerun

Exit code 0 iff the expansion is ADOPTED under the pre-registered rule.
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

from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor, ParquetStore, clean  # noqa: E402
from apex_quant.validation.metrics import (  # noqa: E402
    probability_of_backtest_overfitting,
    sharpe_ratio,
)
from apex_quant.validation.portfolio_report import run_portfolio_cpcv  # noqa: E402
from apex_quant.validation.trials import TrialLedger  # noqa: E402

from run_portfolio_gate import (  # noqa: E402
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
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC  # noqa: E402
from run_portfolio_gate_cf_cvar import _monthly_tail_stats  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7, _class_breakdown  # noqa: E402
from run_portfolio_gate_trend_ensemble import (  # noqa: E402
    CERTIFIED_GOLD,
    CERTIFIED_MRPT,
    GOLD_PARAMS,
    _cfg,
    _run,
)

DEFAULT_RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "univexp_gate_2026-07-27.json"
ENSEMBLE_CONTROL_PATH = (ENGINE_DIR / "data_store" / "validation"
                         / "trend_ensemble_gate_2026-07-27.json")

NEW_INSTRUMENTS = ["SPSK", "AMGN"]          # survivor-rank order (prereg §4/§5)
CERTIFIED_ORDER = EQUITY_CORE + [GOLD_ETC]

# The full pre-registered selection set (3 configs). Control differs from expanded_252
# ONLY in universe; expanded_ens differs from expanded_252 ONLY in the lookback set.
CONFIGS = {
    "univexp_control_252": {"extra": [], "lookbacks": [252]},
    "univexp_expanded_252": {"extra": NEW_INSTRUMENTS, "lookbacks": [252]},
    "univexp_expanded_ens": {"extra": NEW_INSTRUMENTS, "lookbacks": [63, 126, 252]},
}
CONTROL = "univexp_control_252"
RECORD = "univexp_expanded_252"             # comparison of record (prereg §5)

# Pre-registered adoption thresholds (prereg §6).
SHARPE_IMPROVEMENT_REQUIRED = 0.05
DSR_REQUIRED = 0.95
PBO_REQUIRED = 0.5

# Certified control ledger key — byte-identical to the 2026-07-19 Book H record, so it
# dedups and costs nothing (the same config re-evaluated, per the ledger's semantics).
CERTIFIED_LEDGER_RECORD = {
    "book": "book_h_gold_252", "universe": "book_h_gold_39", "timeframe": "1d",
    "factory": "trend_book_mtf",
    "params": {"carry_filter": False, "holding_horizon": 21, "htf_ma_window": 50,
               "htf_rule": "1w", "momentum_lookback": 252, "regime_method": "rule_based",
               "reward_risk": 1.5, "timeframe": "1d", "vol_window": 63},
}
EXPANDED_UNIVERSE_LABEL = "book_h_gold_39_plus_spsk_amgn_41"


def _params(lookbacks: list[int]) -> dict:
    return {**GOLD_PARAMS, "momentum_lookbacks": list(lookbacks)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered gate: universe expansion "
                                             "(SPSK + AMGN) on Book H gold (iteration window only).")
    ap.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START)
    ap.add_argument("--instruments", default="",
                    help="comma-separated subset intersected with every panel (smoke testing)")
    ap.add_argument("--no-ledger", action="store_true",
                    help="smoke-test mode: do NOT record trials; DSR still deflates by the "
                         "count the run WOULD have used (current + 2 new)")
    ap.add_argument("--skip-cpcv", action="store_true",
                    help="smoke-test mode: skip CPCV (adoption legs unevaluated)")
    ap.add_argument("--out", default=str(DEFAULT_RESULTS_PATH),
                    help="results JSON path (use a twin path for the determinism rerun)")
    args = ap.parse_args(argv)

    base_cfg = get_config()
    store = ParquetStore(base_cfg.store_path)
    holdout_start = _utc(args.holdout_start)
    subset = {s.strip() for s in args.instruments.split(",") if s.strip()}
    results_path = Path(args.out)

    crypto = list(base_cfg.data.crypto)
    wanted = sorted(set(CERTIFIED_ORDER) | set(NEW_INSTRUMENTS) | set(crypto) | set(FX_MAJORS_7))
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

    # Pre-registered insertion order: certified block first (certified order), new names
    # appended LAST so certified allocations keep first claim on slots/risk caps.
    panels: dict[str, dict[str, pd.DataFrame]] = {}
    for name, spec in CONFIGS.items():
        order = CERTIFIED_ORDER + spec["extra"] + crypto + FX_MAJORS_7
        panel = {inst: master[inst] for inst in order if inst in master}
        if len(panel) >= 2:
            panels[name] = panel
    if CONTROL not in panels:
        print("need >= 2 instruments in the control panel")
        return 1

    # Record the trials BEFORE running (prereg §5): the control re-records the certified
    # canonical key (dedups); the two expanded configs are the 2 NEW charges.
    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    n_new = 0
    if not args.no_ledger:
        ledger.record(CERTIFIED_LEDGER_RECORD)
        for name in ("univexp_expanded_252", "univexp_expanded_ens"):
            before = ledger.n_trials
            ledger.record({"book": name, "universe": EXPANDED_UNIVERSE_LABEL, "timeframe": "1d",
                           "factory": "trend_book_mtf", "kind": "universe_expansion_gate",
                           "max_risk_per_trade": CERTIFIED_MRPT,
                           "params": _params(CONFIGS[name]["lookbacks"])})
            n_new += int(ledger.n_trials > before)
        ledger.save(LEDGER_PATH)
    used_trials = ledger.n_trials if not args.no_ledger else n_before + 2

    print("=" * 72, flush=True)
    print(f"UNIVERSE EXPANSION GATE (BOOK H GOLD + SPSK + AMGN, mrpt={CERTIFIED_MRPT}) "
          f"2026-07-27 | mode=ITERATION (strictly < {args.holdout_start})")
    for name, panel in panels.items():
        print(f"  {name}: {len(panel)} instruments, lookbacks={CONFIGS[name]['lookbacks']}")
    print(f"window: {min(df.index[0] for df in master.values()).date()} "
          f"-> {max(df.index[-1] for df in master.values()).date()}")
    print(f"ledger n_trials {n_before} -> {ledger.n_trials if not args.no_ledger else n_before} "
          f"(+{n_new} new) | DSR deflation uses n_trials={used_trials}")
    print("=" * 72, flush=True)

    # 1. Full-window run per config on its own panel.
    results: dict[str, dict] = {}
    returns_by_cfg: dict[str, pd.Series] = {}
    for name, panel in panels.items():
        params = _params(CONFIGS[name]["lookbacks"])
        pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
        timeframes = {k: "1d" for k in panel}
        t_start = time.time()
        res = _run(panel, pits, timeframes, params, _cfg())
        rets = res.returns
        returns_by_cfg[name] = rets
        m = res.metrics
        results[name] = {"params": params, "universe": list(panel.keys()), "metrics": m,
                         "max_gross_leverage": _max_gross_leverage(res),
                         "constraint_log": res.constraint_log,
                         "per_instrument": res.per_instrument,
                         "per_asset_class": _class_breakdown(res.per_instrument, base_cfg),
                         "monthly_tail": _monthly_tail_stats(res, base_cfg.backtest.initial_equity),
                         "full_window_sharpe_per_period": sharpe_ratio(rets, periods_per_year=1)}
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] full run {name}: "
              f"{time.time() - t_start:.0f}s | {res.summary()}", flush=True)
        if not m.get("insufficient_data"):
            mt = results[name]["monthly_tail"]
            print(f"    expectancy={m['expectancy_pnl']:.2f} ({m['expectancy_pct']*100:.3f}%/trade) "
                  f"PF={m.get('profit_factor')} win={m['win_rate']*100:.2f}% "
                  f"maxDD={m['max_drawdown']*100:.2f}% worst_day={mt['worst_daily_return']*100:.2f}% "
                  f"avg {mt['avg_monthly_pnl']:+,.0f}/mo lev~{results[name]['max_gross_leverage']:.2f}x "
                  f"| caps bound: {_cap_families(res.constraint_log)}", flush=True)

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

    # 2. PBO across the 3-config selection set (collinearity caveat, prereg §6).
    aligned = pd.concat(list(returns_by_cfg.values()), axis=1).dropna()
    M = aligned.to_numpy()
    pbo = (probability_of_backtest_overfitting(M, n_splits=base_cfg.validation.pbo.n_splits, seed=base_cfg.seed)
           if M.shape[1] >= 2 and M.shape[0] >= 40 else {"pbo": None, "note": "insufficient matrix"})
    print(f"PBO across {M.shape[1]} configs: {pbo}", flush=True)

    # 3. CPCV per config (the same 15 paths, purge 21, as every prior gate).
    trial_sharpes = [results[n]["full_window_sharpe_per_period"] for n in results]
    verdicts: dict[str, dict] = {}
    for name, panel in panels.items():
        params = _params(CONFIGS[name]["lookbacks"])
        pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
        timeframes = {k: "1d" for k in panel}
        t_start = time.time()
        if args.skip_cpcv:
            cpcv = {"n_paths": 0, "oos_sharpe_mean": 0.0, "oos_sharpe_std": 0.0,
                    "oos_sharpe_median": 0.0, "frac_positive": 0.0, "oos_sharpe_paths": []}
        else:
            cpcv = run_portfolio_cpcv(
                panel, pits, lambda p, **kw: TrendBook(p, **kw), params,
                cfg=_cfg(), timeframes=timeframes, warmup=WARMUP, horizon=HORIZON,
                periods_per_year=252, exit_mode="managed",
            )
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] CPCV {name}: "
              f"{time.time() - t_start:.0f}s | paths={cpcv['oos_sharpe_paths']}", flush=True)
        verdicts[name] = _gate(name, returns_by_cfg[name], trial_sharpes, pbo, cpcv, used_trials)
        results[name]["cpcv"] = cpcv
        results[name]["gate"] = {k: v for k, v in verdicts[name].items() if k != "reasons"}

    # 4. Pre-registered adoption rule (prereg §6) on the comparison of record.
    m_ctrl = results[CONTROL]["metrics"]
    m_rec = results[RECORD]["metrics"]
    cpcv_ctrl = results[CONTROL]["cpcv"]
    cpcv_rec = results[RECORD]["cpcv"]
    dsr_rec = verdicts[RECORD]["dsr"].get("dsr", 0.0)
    pbo_val = pbo.get("pbo")
    sharpe_leg = bool(m_rec["sharpe"] - m_ctrl["sharpe"] >= SHARPE_IMPROVEMENT_REQUIRED)
    cpcv_leg = bool(cpcv_rec.get("oos_sharpe_median", -1) >= cpcv_ctrl.get("oos_sharpe_median", 1)
                    and cpcv_rec.get("n_paths", 0) == 15)
    dsr_leg = bool(dsr_rec > DSR_REQUIRED)
    pbo_leg = bool(pbo_val is not None and pbo_val < PBO_REQUIRED)
    adopted = bool(sharpe_leg and cpcv_leg and dsr_leg and pbo_leg)
    adoption = {
        "comparison_of_record": f"{RECORD} vs {CONTROL} (certified [252] config)",
        "adopted": adopted,
        "sharpe_leg": {"required": f"delta >= +{SHARPE_IMPROVEMENT_REQUIRED}",
                       "control": round(m_ctrl["sharpe"], 5), "expanded": round(m_rec["sharpe"], 5),
                       "delta": round(m_rec["sharpe"] - m_ctrl["sharpe"], 5), "pass": sharpe_leg},
        "cpcv_leg": {"required": "expanded median >= control median",
                     "control_median": cpcv_ctrl.get("oos_sharpe_median"),
                     "expanded_median": cpcv_rec.get("oos_sharpe_median"), "pass": cpcv_leg},
        "dsr_leg": {"required": f"DSR > {DSR_REQUIRED} @ n={used_trials}",
                    "dsr": round(dsr_rec, 4), "pass": dsr_leg},
        "pbo_leg": {"required": f"PBO < {PBO_REQUIRED}", "pbo": pbo_val, "pass": pbo_leg},
        "kill_rule": "any leg fails => REJECT (prereg §6)",
    }
    print(f"\nADOPTION (comparison of record): "
          f"Sharpe {m_ctrl['sharpe']:.5f} -> {m_rec['sharpe']:.5f} "
          f"(delta {m_rec['sharpe'] - m_ctrl['sharpe']:+.5f}, need +0.05: {sharpe_leg}) | "
          f"CPCV median {cpcv_ctrl.get('oos_sharpe_median')} -> {cpcv_rec.get('oos_sharpe_median')} "
          f"(>= control: {cpcv_leg}) | DSR {dsr_rec:.4f} (>0.95: {dsr_leg}) | "
          f"PBO {pbo_val} (<0.5: {pbo_leg}) => {'ADOPTED' if adopted else 'REJECTED'}", flush=True)

    # 5. Secondary read (informational): expanded ensemble vs the recorded ensemble
    #    control (trend_ens_blend_63_126_252) — same machinery, deterministic.
    secondary: dict = {"note": "informational; does not bind (prereg §6)"}
    if "univexp_expanded_ens" in results and ENSEMBLE_CONTROL_PATH.exists():
        try:
            ens = json.loads(ENSEMBLE_CONTROL_PATH.read_text())
            ctrl = ens["books"]["trend_ens_blend_63_126_252"]
            mine = results["univexp_expanded_ens"]
            secondary.update({
                "control_source": str(ENSEMBLE_CONTROL_PATH.relative_to(ENGINE_DIR)),
                "control_sharpe": ctrl["metrics"]["sharpe"],
                "expanded_ens_sharpe": mine["metrics"]["sharpe"],
                "delta_sharpe": round(mine["metrics"]["sharpe"] - ctrl["metrics"]["sharpe"], 5),
                "control_cpcv_median": ctrl["cpcv"]["oos_sharpe_median"],
                "expanded_ens_cpcv_median": mine["cpcv"].get("oos_sharpe_median"),
                "control_dsr_at_n279": ctrl["gate"]["dsr"]["dsr"],
                "expanded_ens_dsr": verdicts["univexp_expanded_ens"]["dsr"].get("dsr"),
                "expanded_ens_dsr_deflation_n": used_trials,
            })
            print(f"SECONDARY (ensemble config): sharpe {ctrl['metrics']['sharpe']:.5f} -> "
                  f"{mine['metrics']['sharpe']:.5f} "
                  f"(delta {secondary['delta_sharpe']:+.5f}) | CPCV median "
                  f"{ctrl['cpcv']['oos_sharpe_median']} -> {mine['cpcv'].get('oos_sharpe_median')} | "
                  f"DSR {secondary['control_dsr_at_n279']:.4f}@279 -> "
                  f"{secondary['expanded_ens_dsr']:.4f}@{used_trials}", flush=True)
        except Exception as exc:  # noqa: BLE001
            secondary["error"] = f"{type(exc).__name__}: {exc}"
            print(f"secondary read unavailable: {secondary['error']}", flush=True)

    # 6. Per-new-instrument attribution (the deliverable's P&L table for the new names).
    attribution = {}
    for inst in NEW_INSTRUMENTS:
        attribution[inst] = {name: results[name]["per_instrument"].get(inst)
                             for name in results if inst in results[name]["per_instrument"]}
    for name in results:
        if name == CONTROL:
            continue
        d_pnl = round(results[name]["metrics"]["net_pnl"] - m_ctrl["net_pnl"], 2)
        d_tr = int(results[name]["metrics"]["n_trades"] - m_ctrl["n_trades"])
        print(f"attribution {name}: total net_pnl delta {d_pnl:+,} ({d_tr:+d} trades) "
              f"| new names: {json.dumps(attribution, default=str)}", flush=True)

    print("\n" + "=" * 72, flush=True)
    for name, v in verdicts.items():
        print(f"  {name}: VERDICT {'PASS' if v['passed'] else 'REJECT'}")
        for r in v["reasons"]:
            print(f"    - {r}")
    print(f"  PRE-REGISTERED RULE => {'EXPANSION ADOPTED' if adopted else 'EXPANSION REJECTED'}")
    print("=" * 72, flush=True)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "iteration",
        "holdout_start": args.holdout_start,
        "prereg": "engine/data_store/universe_expansion_2_prereg.md",
        "certified_anchor_max_risk_per_trade": CERTIFIED_MRPT,
        "new_instruments": NEW_INSTRUMENTS,
        "universes": {name: list(p.keys()) for name, p in panels.items()},
        "n_trials_before": n_before,
        "n_trials_used": used_trials,
        "ledger_recorded": not args.no_ledger,
        "pbo": pbo,
        "adoption": adoption,
        "secondary_ensemble_read": secondary,
        "new_instrument_attribution": attribution,
        "books": results,
        "verdicts": verdicts,
    }
    results_path.parent.mkdir(parents=True, exist_ok=True)
    with open(results_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"results written to {results_path}", flush=True)
    return 0 if adopted else 1


if __name__ == "__main__":
    sys.exit(main())
