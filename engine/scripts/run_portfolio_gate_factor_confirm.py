"""Pre-registered portfolio-level gate: CROSS-ASSET FACTOR CONFIRMATION (one family,
two sub-sleeve configs) on the certified Book H gold trend book.

Pre-registration: engine/data_store/factor_confirmation_prereg.md (2026-07-28, written
BEFORE any run; the 3 trials are recorded before execution, dedup-safe). Mechanism
(Ehsani & Linnainmaa JF 2022, factor momentum): an asset's own trend persists when the
underlying FACTOR trends in the same direction and reverses when it does not. Two
independent sub-sleeve gates, each its own config, ONE experiment family:
  (a) EQUITY sleeve — stock/UCITS/sector-ETF entries only when ISWD.L's 63d trend
      agrees in sign (gold SGLD.L is not a stock: ungated);
  (b) ALT-CRYPTO sleeve — crypto entries except BTC only when BTC's 63d trend agrees
      in sign (BTC itself ungated — it IS the factor).
FX is excluded from the experiment entirely (no credible factor analog — ungated).

Exactly 3 pre-registered configs (the full selection set) on the certified Book H gold
panel (certified insertion order), certified params verbatim, certified risk anchor
max_risk_per_trade = 0.01:
  fac_control_252       entry_gate=None                          control / anchor hard-check
  fac_equity_iswd_63    entry_gate={"kind":"factor","sleeve":"equity"}   challenger (a)
  fac_crypto_btc_63     entry_gate={"kind":"factor","sleeve":"crypto"}   challenger (b)

Adoption (prereg section 5, binding): the FAMILY is adopted iff for BOTH challengers:
sleeve expectancy/trade strictly greater than control's on >= 12/15 CPCV paths
(equity config measured on equity-sleeve trades, crypto config on alt-crypto trades)
AND full-window Sharpe drop <= 0.05 AND full-window DSR > 0.95 at the full ledger
count; family PBO < 0.5 across the 3-config set. Any leg fails => family REJECTED
(per-sleeve outcomes reported honestly either way).

Iteration window only: strictly < 2025-01-01. Seed 42. Determinism: run twice,
byte-identical modulo generated_at and the ledger pre-state (the rerun dedups).

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_portfolio_gate_factor_confirm.py                 # full gate
    .venv-mac/bin/python scripts/run_portfolio_gate_factor_confirm.py --out <twin>    # determinism rerun
    .venv-mac/bin/python scripts/run_portfolio_gate_factor_confirm.py --instruments AAPL,MSFT,ISWD.L,BTC/USD,ETH/USD --no-ledger

Exit code 0 iff the family is ADOPTED under the pre-registered rule, 1 otherwise or if
the anchor hard-check fails.
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

from apex_quant.backtest.portfolio import PortfolioBacktester  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor, ParquetStore, clean  # noqa: E402
from apex_quant.strategies.entry_gates import trend_sign_series  # noqa: E402
from apex_quant.validation.metrics import (  # noqa: E402
    probability_of_backtest_overfitting,
    sharpe_ratio,
)
from apex_quant.validation.portfolio_report import run_portfolio_cpcv_trades  # noqa: E402
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
from run_portfolio_gate_cf_cvar import _monthly_tail_stats  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402

DEFAULT_RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "factor_confirmation_gate_2026-07-28.json"

UNIVERSE_GOLD = EQUITY_CORE + [GOLD_ETC]
GOLD_PARAMS = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}
CERTIFIED_MRPT = 0.01  # certified 2026-07-22 gap-aware anchor (see prereg header)

CONTROL = "fac_control_252"
EQ_CHALLENGER = "fac_equity_iswd_63"
CR_CHALLENGER = "fac_crypto_btc_63"
BOOKS = {
    CONTROL: None,
    EQ_CHALLENGER: {"kind": "factor", "sleeve": "equity", "lookback": 63},
    CR_CHALLENGER: {"kind": "factor", "sleeve": "crypto", "lookback": 63},
}
# challenger -> the sleeve whose expectancy it must improve (prereg section 5)
CHALLENGER_SLEEVE = {EQ_CHALLENGER: "equity", CR_CHALLENGER: "alt_crypto"}

# Pre-registered adoption thresholds (prereg section 5).
CPCV_PATHS_REQUIRED = 12     # of 15: challenger sleeve expectancy strictly greater
DSR_REQUIRED = 0.95          # at the full updated ledger count
PBO_REQUIRED = 0.5           # across the 3-config selection set
MAX_SHARPE_DROP = 0.05       # reduced trade count must not degrade book Sharpe materially

# Certified-anchor reproduction (book_h_gapaware_2026-07-22.json): the control MUST
# reproduce these numbers — hard-fail the run if it does not.
CERTIFIED_GOLD = {"sharpe": 0.8628380346245177, "profit_factor": 1.324524180228228,
                  "win_rate": 0.5577275503970678, "max_drawdown": 0.16315348773173277,
                  "n_trades": 1637, "total_return": 1.9255133668640645,
                  "expectancy_pnl": 120.44265729993896, "final_equity": 292551.33668640646}


def _cfg():
    cfg = copy.deepcopy(get_config())
    cfg.risk.max_risk_per_trade = CERTIFIED_MRPT
    return cfg


def _params(spec):
    return {**GOLD_PARAMS, "entry_gate": spec}


def _sleeve_of(inst: str, panel: dict, cfg) -> str:
    """Sleeve membership per prereg section 2: equity = equity class minus gold/sukuk;
    alt_crypto = crypto minus BTC; everything else (FX, BTC, gold) is ungated."""
    ac = cfg.asset_class_of(inst)
    if ac == "equity" and inst not in ("SGLD.L", "SPSK"):
        return "equity"
    if ac == "crypto" and inst != "BTC/USD":
        return "alt_crypto"
    return "ungated"


def _agreement_diagnostic(res, panel, cfg) -> dict:
    """Mechanism diagnostic (not verdict-binding): control trades per sleeve, split by
    whether the sleeve factor's 63d trend AGREED with the trade direction at entry."""
    idx = pd.DatetimeIndex(sorted(set().union(*[set(df.index) for df in panel.values()])))
    signs = {
        "equity": trend_sign_series(panel["ISWD.L"], 63).reindex(idx).ffill(),
        "alt_crypto": trend_sign_series(panel["BTC/USD"], 63).reindex(idx).ffill(),
    }
    out: dict[str, dict] = {}
    for sleeve in ("equity", "alt_crypto"):
        agree, disagree, undef = [], [], []
        for tr in res.trades:
            if _sleeve_of(tr.instrument, panel, cfg) != sleeve:
                continue
            t = pd.Timestamp(tr.entry_time)
            t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
            pos = idx.searchsorted(t, side="right") - 1
            fs = signs[sleeve].iloc[pos] if pos >= 0 else np.nan
            d = 1.0 if tr.direction == "long" else -1.0
            if not np.isfinite(fs):
                undef.append(float(tr.pnl))
            elif np.sign(fs) == d:
                agree.append(float(tr.pnl))
            else:
                disagree.append(float(tr.pnl))
        out[sleeve] = {
            k: {"n_trades": len(v),
                "expectancy_pnl": round(float(np.mean(v)), 2) if v else None,
                "win_rate": round(float(np.mean([x > 0 for x in v])), 4) if v else None}
            for k, v in (("agree", agree), ("disagree", disagree), ("undefined", undef))
        }
    return out


def _path_sleeve_expectancies(cpcv: dict, sleeve: str, panel: dict, cfg) -> list[dict]:
    out = []
    for p in cpcv["paths"]:
        pnls = [t["pnl"] for t in p["trades"] if _sleeve_of(t["instrument"], panel, cfg) == sleeve]
        out.append({"sharpe": p["sharpe"], "test_start": p["test_start"], "test_end": p["test_end"],
                    "n_trades": len(pnls),
                    "expectancy_pnl": float(np.mean(pnls)) if pnls else None})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pre-registered gate: cross-asset factor "
                                             "confirmation on the certified Book H gold book.")
    ap.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START)
    ap.add_argument("--instruments", default="",
                    help="comma-separated subset of the gold universe (smoke testing; "
                         "must include ISWD.L and BTC/USD — the factors)")
    ap.add_argument("--no-ledger", action="store_true",
                    help="smoke-test mode: do NOT record trials; DSR still deflates by the "
                         "count the run WOULD have used (current + 3)")
    ap.add_argument("--out", default=str(DEFAULT_RESULTS_PATH),
                    help="results JSON path (use a twin path for the determinism rerun)")
    args = ap.parse_args(argv)

    base_cfg = get_config()
    store = ParquetStore(base_cfg.store_path)
    holdout_start = _utc(args.holdout_start)
    subset = {s.strip() for s in args.instruments.split(",") if s.strip()}
    if subset and not {"ISWD.L", "BTC/USD"} <= subset:
        print("smoke subsets must include the factor instruments ISWD.L and BTC/USD")
        return 1
    results_path = Path(args.out)

    crypto = list(base_cfg.data.crypto)
    wanted = sorted(set(UNIVERSE_GOLD) | set(crypto) | set(FX_MAJORS_7))
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
    # The certified panel preserves the BOOK'S insertion order (EQUITY_CORE first), NOT
    # load order — the certified numbers are ordering-sensitive.
    panel = {inst: master[inst] for inst in UNIVERSE_GOLD + crypto + FX_MAJORS_7 if inst in master}
    if len(panel) < 2:
        print("need >= 2 instruments for a portfolio gate")
        return 1
    if "ISWD.L" not in panel or "BTC/USD" not in panel:
        print("factor instruments ISWD.L / BTC/USD must be in the panel")
        return 1
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    timeframes = {k: "1d" for k in panel}

    # Record the 3 pre-registered trials BEFORE running (exact keys dedup on re-runs).
    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not args.no_ledger:
        for name, spec in BOOKS.items():
            ledger.record({"book": name, "universe": "book_h_gold_39", "timeframe": "1d",
                           "factory": "trend_book_mtf", "kind": "factor_confirmation_gate",
                           "max_risk_per_trade": CERTIFIED_MRPT,
                           "params": _params(spec)})
        ledger.save(LEDGER_PATH)
    used_trials = ledger.n_trials if not args.no_ledger else n_before + len(BOOKS)

    print("=" * 72, flush=True)
    print(f"FACTOR-CONFIRMATION GATE (BOOK H GOLD, mrpt={CERTIFIED_MRPT}) 2026-07-28 "
          f"| mode=ITERATION (strictly < {args.holdout_start})")
    print(f"universe: {len(panel)} instruments | configs: {list(BOOKS)}")
    print(f"ledger n_trials {n_before} -> {ledger.n_trials if not args.no_ledger else n_before}"
          f" | DSR deflation uses n_trials={used_trials}")
    print("=" * 72, flush=True)

    # 1. Full-window run per config (the entry gate is the ONLY difference).
    results: dict[str, dict] = {}
    returns_by_book: dict[str, pd.Series] = {}
    res_by_book = {}
    for name, spec in BOOKS.items():
        cfg = _cfg()
        t_start = time.time()
        model = TrendBook(panel, **_params(spec))
        res = PortfolioBacktester(cfg, exit_mode="managed").run(
            pits, model.strategies(), timeframes=timeframes,
            warmup=WARMUP, periods_per_year=252,
        )
        res_by_book[name] = res
        rets = res.returns
        returns_by_book[name] = rets
        m = res.metrics
        n_veto = sum(getattr(s, "n_vetoes", 0) for s in model._strategies.values())
        results[name] = {"params": _params(spec), "metrics": m,
                         "constraint_log": res.constraint_log,
                         "per_instrument": res.per_instrument,
                         "gate_vetoes": n_veto,
                         "monthly_tail": _monthly_tail_stats(res, cfg.backtest.initial_equity),
                         "full_window_sharpe_per_period": sharpe_ratio(rets, periods_per_year=1)}
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] full run {name}: "
              f"{time.time() - t_start:.0f}s | {res.summary()} | vetoes={n_veto}", flush=True)

    # Certified-anchor reproduction: control must reproduce book_h_gapaware_2026-07-22.json
    # (gold) — hard-fail the run if it does not.
    if not args.instruments:
        m0 = results[CONTROL]["metrics"]
        mismatch = {k: (m0[k], v) for k, v in CERTIFIED_GOLD.items()
                    if abs(m0[k] - v) > (0.5 if k in ("n_trades",) else
                                          1e-6 * max(1.0, abs(v)))}
        if mismatch:
            print(f"CERTIFIED REPRODUCTION FAILED: {mismatch}", flush=True)
            return 1
        print("certified-anchor reproduction: EXACT "
              f"(sharpe {m0['sharpe']:.5f}, {m0['n_trades']} trades, "
              f"final_equity {m0['final_equity']:.2f})", flush=True)

    # Mechanism diagnostic: control trades per sleeve by factor agreement at entry.
    agreement = _agreement_diagnostic(res_by_book[CONTROL], panel, base_cfg)
    print(f"mechanism diagnostic (control trades by factor agreement): {agreement}", flush=True)

    # 2. PBO across the 3-config selection set (standing overlapping-family caveat).
    aligned = pd.concat(list(returns_by_book.values()), axis=1).dropna()
    M = aligned.to_numpy()
    pbo = (probability_of_backtest_overfitting(M, n_splits=base_cfg.validation.pbo.n_splits, seed=base_cfg.seed)
           if M.shape[1] >= 2 and M.shape[0] >= 40 else {"pbo": None, "note": "insufficient matrix"})
    print(f"PBO across {M.shape[1]} configs: {pbo}", flush=True)

    # 3. CPCV per config (the same 15 folds) WITH per-path trade lists for the sleeve
    #    expectancy legs.
    trial_sharpes = [results[n]["full_window_sharpe_per_period"] for n in BOOKS]
    verdicts: dict[str, dict] = {}
    path_stats: dict[str, dict] = {}
    for name, spec in BOOKS.items():
        cfg = _cfg()
        t_start = time.time()
        cpcv = run_portfolio_cpcv_trades(
            panel, pits, lambda p, **kw: TrendBook(p, **kw), _params(spec),
            cfg=cfg, timeframes=timeframes, warmup=WARMUP, horizon=HORIZON,
            periods_per_year=252, exit_mode="managed",
        )
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] CPCV {name}: "
              f"{time.time() - t_start:.0f}s | paths={cpcv['oos_sharpe_paths']}", flush=True)
        path_stats[name] = {s: _path_sleeve_expectancies(cpcv, s, panel, cfg)
                            for s in ("equity", "alt_crypto")}
        # _gate embeds the cpcv dict it receives — pass the SLIM version (no raw
        # per-path trade lists) so the persisted verdict stays a compact record.
        cpcv_slim = {k: v for k, v in cpcv.items() if k != "paths"}
        verdicts[name] = _gate(name, returns_by_book[name], trial_sharpes, pbo, cpcv_slim, used_trials)
        results[name]["cpcv"] = cpcv_slim
        results[name]["cpcv_paths_by_sleeve"] = path_stats[name]
        results[name]["gate"] = {k: v for k, v in verdicts[name].items() if k != "reasons"}

    # 4. The pre-registered adoption rule (prereg section 5, binding): BOTH challengers.
    base_m = results[CONTROL]["metrics"]
    leg_pbo = bool(pbo.get("pbo") is not None and pbo["pbo"] < PBO_REQUIRED)
    per_challenger: dict[str, dict] = {}
    for chal, sleeve in CHALLENGER_SLEEVE.items():
        chal_m = results[chal]["metrics"]
        paired = []
        n_improved = 0
        for pb, pc in zip(path_stats[CONTROL][sleeve], path_stats[chal][sleeve]):
            improved = (pb["expectancy_pnl"] is not None and pc["expectancy_pnl"] is not None
                        and pc["expectancy_pnl"] > pb["expectancy_pnl"])
            n_improved += int(improved)
            paired.append({"test_start": pb["test_start"], "test_end": pb["test_end"],
                           "control_exp": pb["expectancy_pnl"], "challenger_exp": pc["expectancy_pnl"],
                           "control_n": pb["n_trades"], "challenger_n": pc["n_trades"],
                           "improved": improved})
        leg_exp = n_improved >= CPCV_PATHS_REQUIRED
        leg_dsr = bool(verdicts[chal]["dsr"].get("dsr", 0.0) > DSR_REQUIRED)
        sharpe_drop = base_m["sharpe"] - chal_m["sharpe"]
        leg_sharpe = bool(sharpe_drop <= MAX_SHARPE_DROP)
        per_challenger[chal] = {
            "sleeve": sleeve, "n_paths_expectancy_improved": n_improved,
            "leg_expectancy": leg_exp, "leg_dsr": leg_dsr, "leg_sharpe_noise": leg_sharpe,
            "sharpe_drop": round(float(sharpe_drop), 5), "paired_paths": paired,
            "passed": bool(leg_exp and leg_dsr and leg_sharpe and leg_pbo),
        }
    adopted = bool(all(c["passed"] for c in per_challenger.values()))
    adoption = {"pbo_leg": leg_pbo, "per_challenger": per_challenger, "adopted": adopted}

    print("\n" + "=" * 72, flush=True)
    for name, v in verdicts.items():
        print(f"  {name}: VERDICT {'PASS' if v['passed'] else 'REJECT'}")
        for r in v["reasons"]:
            print(f"    - {r}")
    print("  ADOPTION RULE (prereg section 5, binding):")
    print(f"    PBO leg (family): {pbo.get('pbo')} < {PBO_REQUIRED}? {leg_pbo}")
    for chal, e in per_challenger.items():
        print(f"    {chal} [{e['sleeve']}]: expectancy {e['n_paths_expectancy_improved']}/15 "
              f"(>= {CPCV_PATHS_REQUIRED}? {e['leg_expectancy']}) | DSR "
              f"{verdicts[chal]['dsr'].get('dsr', 0):.4f} > {DSR_REQUIRED}? {e['leg_dsr']} | "
              f"Sharpe drop {e['sharpe_drop']:.5f} <= {MAX_SHARPE_DROP}? {e['leg_sharpe_noise']} "
              f"=> {'PASSES' if e['passed'] else 'FAILS'}")
    print(f"  DECISION: {'ADOPT factor-confirmation family' if adopted else 'FAMILY REJECTED — certified ungated entries stand'}")
    print("=" * 72, flush=True)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "iteration",
        "holdout_start": args.holdout_start,
        "prereg": "engine/data_store/factor_confirmation_prereg.md",
        "kind": "factor_confirmation_gate",
        "certified_anchor_max_risk_per_trade": CERTIFIED_MRPT,
        "universe": list(panel.keys()),
        "n_trials_before": n_before,
        "n_trials_used": used_trials,
        "ledger_recorded": not args.no_ledger,
        "pbo": pbo,
        "mechanism_diagnostic_agreement": agreement,
        "adoption": adoption,
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
