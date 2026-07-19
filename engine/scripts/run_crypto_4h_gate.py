"""4h crypto trend sleeve: pre-registered gate run (BTC/USD, ETH/USD).

Implements data_store/crypto_4h_prereg_2026-07-17.md exactly. Thin glue over the
existing machinery (run_validation + CPCV/DSR/PBO, TrialLedger, Backtester) — no
new math lives here. Mirrors scripts/run_candidate_check.py's honesty rules:

  * ITERATION window strictly before 2025-01-01; the 2025+ holdout is never
    loaded (hard assert per series; it is BURNED for the trend family anyway).
  * 12 trials (2 instruments x 3 configs x 2 cost levels — cost levels recorded
    as DISTINCT trials, stricter than the 1h campaign) are recorded in the
    shared TrialLedger BEFORE any validation runs, under the ledger's file lock;
    every DSR below is deflated by the FINAL ledger count.
  * Local records only (Supabase posting deliberately skipped for this research
    sweep); per-run JSONs + summary.json in
    data_store/validation/crypto_4h_2026-07-17/.

Differences from run_candidate_check.py (why this script exists):
  * 4h crypto history comes from the Binance klines resample built by
    scripts/build_binance_4h.py — read directly, never merged into the shared
    parquet store the live daemon reads.
  * Two cost levels: config v5 as-is (1.5bps spread + 0.5bps slippage per side
    ~ 2.5bps round-trip) and a stressed 10bps round-trip (8bps spread + 1bps
    slippage per side) — the research basis says intraday edges are fee-fragile.
  * Managed exits (TradeManager): this tests the deployable sleeve, not the
    academic fixed-horizon bet (that was the 1h campaign's barrier-mode design).

Usage:
    cd engine && .venv-mac/bin/python scripts/run_crypto_4h_gate.py
"""

from __future__ import annotations

import json
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")
ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

try:  # keep parity with repo scripts: engine/.env carries Supabase/Oanda creds
    from dotenv import load_dotenv

    load_dotenv(ENGINE_DIR / ".env")
except ImportError:  # pragma: no cover
    pass

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from apex_quant.api.service import EngineService  # noqa: E402
from apex_quant.backtest.engine import Backtester  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor, clean  # noqa: E402
from apex_quant.validation.report import (  # noqa: E402
    PBO_THRESHOLD,
    DSR_THRESHOLD,
    default_factory,
    run_validation,
)
from apex_quant.validation.trials import TrialLedger  # noqa: E402

LEDGER_PATH = ENGINE_DIR / "data_store" / "validation" / "trial_ledger.json"
OUT_DIR = ENGINE_DIR / "data_store" / "validation" / "crypto_4h_2026-07-17"
HOLDOUT_START = pd.Timestamp("2025-01-01", tz="UTC")
MIN_BARS = 300
TIMEFRAME = "4h"
EXIT_MODE = "managed"   # the deployable sleeve: TradeManager trail/BE/partials/time-stop
FACTORY_NAME = "regime_gated_momentum"

INSTRUMENTS = {"BTC/USD": "BINANCE_BTC_USD_4h.parquet",
               "ETH/USD": "BINANCE_ETH_USD_4h.parquet"}

# Pre-registered grid (prereg doc, fixed before any run). Headline first.
# vol_window tracks momentum_lookback, mirroring default_param_grid's convention.
GRID = [
    {"momentum_lookback": 42, "vol_window": 42, "holding_horizon": 10,
     "reward_risk": 1.5, "regime_method": "rule_based", "timeframe": TIMEFRAME},
    {"momentum_lookback": 21, "vol_window": 21, "holding_horizon": 10,
     "reward_risk": 1.5, "regime_method": "rule_based", "timeframe": TIMEFRAME},
    {"momentum_lookback": 84, "vol_window": 84, "holding_horizon": 10,
     "reward_risk": 1.5, "regime_method": "rule_based", "timeframe": TIMEFRAME},
]

COST_LEVELS = ["rt2.5_config", "rt10_stress"]


def _stressed_crypto_cfg(cfg):
    """10bps round-trip stress: 8bps spread + 1bps slippage per side."""
    c = cfg.model_copy(deep=True)
    c.asset_classes.crypto.spread_bps = 8.0
    c.asset_classes.crypto.slippage_bps = 1.0
    return c


def _load_4h(inst: str, fname: str) -> pd.DataFrame:
    df = clean(pd.read_parquet(ENGINE_DIR / "data_store" / fname))
    df = df[df.index < HOLDOUT_START]
    assert not df.empty and df.index[-1] < HOLDOUT_START, f"{inst}: post-2024 data leaked"
    return df


def _print_gate(rep, cost_tag: str) -> bool:
    v = rep.verdict
    dsr_val = rep.dsr.get("dsr", 0.0)
    pbo_val = rep.pbo.get("pbo")
    med_oos = rep.cpcv.get("oos_sharpe_median", 0.0)
    frac_pos = rep.cpcv.get("frac_positive", 0.0)
    n_paths = rep.cpcv.get("n_paths", 0)
    tag = lambda ok: "pass" if ok else "FAIL"
    print(f"  VERDICT [{cost_tag}]: {'PASS' if v['passed'] else 'REJECT'}")
    print(f"    [{tag(v['dsr_pass'])}] DSR {dsr_val:.3f} vs > {DSR_THRESHOLD} "
          f"(deflated by n_trials={rep.dsr.get('n_trials')})")
    print(f"    [{tag(v['pbo_pass'])}] PBO {pbo_val if pbo_val is not None else 'n/a'} "
          f"vs < {PBO_THRESHOLD}")
    print(f"    [{tag(v['cpcv_pass'])}] CPCV median OOS Sharpe {med_oos:.3f} vs > 0; "
          f"{frac_pos * 100:.0f}% of {n_paths} paths positive vs > 50%")
    return v["passed"]


def _config_stats(pit, inst: str, params: dict, cfg, weeks: float) -> dict:
    """Full-window trading stats for ONE recorded config: net bps/trade,
    expectancy, profit factor, trades/week, exit mix. No new trials — every
    (inst, config, cost) cell here is one of the 12 ledger-recorded trials."""
    strat = default_factory(**params)
    strat.fit(pit, pit.as_of(pit.end).index)
    res = Backtester(cfg, exit_mode=EXIT_MODE).run(
        pit, strat, instrument=inst, warmup=250, timeframe=TIMEFRAME)
    m = res.metrics
    rets = [t.return_pct for t in res.trades]
    exits: dict[str, int] = {}
    for t in res.trades:
        exits[t.exit_reason] = exits.get(t.exit_reason, 0) + 1
    n = m.get("n_trades", 0)
    return {
        "params": {k: params[k] for k in ("momentum_lookback", "vol_window")},
        "n_trades": n,
        "trades_per_week": round(n / weeks, 2) if weeks else None,
        "net_bps_per_trade": round(float(np.mean(rets)) * 1e4, 2) if rets else None,
        "win_rate": round(m.get("win_rate", 0.0), 4),
        "expectancy_pnl": round(m.get("expectancy_pnl", 0.0), 2),
        "expectancy_pct": round(m.get("expectancy_pct", 0.0) * 100, 4),
        "profit_factor": m.get("profit_factor"),
        "total_return_pct": round(m.get("total_return", 0.0) * 100, 2),
        "sharpe": round(m.get("sharpe", 0.0), 3),
        "max_drawdown_pct": round(m.get("max_drawdown", 0.0) * 100, 2),
        "exit_reasons": exits,
    }


def main() -> int:
    cfg = get_config()
    service = EngineService(cfg)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # ---- plan the whole campaign; record ALL 12 trials BEFORE running --------
    campaign = []  # (inst, df, run_cfg, cost_tag)
    for inst, fname in INSTRUMENTS.items():
        df = _load_4h(inst, fname)
        campaign.append((inst, df, cfg, "rt2.5_config"))
        campaign.append((inst, df, _stressed_crypto_cfg(cfg), "rt10_stress"))

    with TrialLedger.locked(LEDGER_PATH) as ledger:
        n_before = ledger.n_trials
        for inst, _, _, cost_tag in campaign:
            for params in GRID:
                ledger.record({"instrument": inst, "timeframe": TIMEFRAME,
                               "factory": FACTORY_NAME, "params": params,
                               "cost": cost_tag})
        n_final = ledger.n_trials
    ledger = TrialLedger.load(LEDGER_PATH)  # fresh read-back for the deflation n
    n_final = ledger.n_trials

    print("=" * 78)
    print(f"CRYPTO 4h TREND SLEEVE | iteration window strictly < {HOLDOUT_START.date()} | "
          f"exit_mode={EXIT_MODE} | seed={cfg.seed}")
    print(f"ledger: {n_before} -> {n_final} trials ({n_final - n_before} new; "
          f"preregistered 12 = 2 instruments x 3 configs x 2 cost levels); "
          f"ALL DSRs deflated by final n={n_final}")
    print("=" * 78)

    summary = {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "prereg": "data_store/crypto_4h_prereg_2026-07-17.md",
               "ledger_before": n_before, "ledger_after": n_final,
               "holdout_start": str(HOLDOUT_START.date()),
               "timeframe": TIMEFRAME, "exit_mode": EXIT_MODE, "grid": GRID,
               "cost_levels": COST_LEVELS, "runs": []}
    n_pass = n_reject = 0

    for inst, df, run_cfg, cost_tag in campaign:
        klass = run_cfg.asset_class_of(inst)
        days = (df.index[-1] - df.index[0]).days
        years, weeks = days / 365.25, days / 7.0
        pit = PointInTimeAccessor(df)
        print(f"\n[{klass}] {inst} ({cost_tag}): {len(df)} bars "
              f"({pit.start.date()} -> {pit.end.date()}, {years:.1f}y) | "
              f"bars_per_year(4h)={run_cfg.bars_per_year(inst, TIMEFRAME):.0f}")

        if len(df) < MIN_BARS:
            print(f"  skip: {len(df)} bars < MIN_BARS"); continue
        try:
            rep = run_validation(pit, inst, strategy_factory=default_factory,
                                 param_grid=GRID, cfg=run_cfg,
                                 generated_for=str(pit.end.date()),
                                 n_trials=n_final, exit_mode=EXIT_MODE)
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR {type(e).__name__}: {e}")
            import traceback; traceback.print_exc()
            n_reject += 1
            continue

        ok = _print_gate(rep, cost_tag)

        per_config = []
        for params in GRID:
            st = _config_stats(pit, inst, params, run_cfg, weeks)
            per_config.append(st)
            print(f"    lb={st['params']['momentum_lookback']:>3}: {st['n_trades']} trades "
                  f"({st['trades_per_week']}/wk), net {st['net_bps_per_trade']} bps/trade, "
                  f"exp {st['expectancy_pct']}%/trade, PF {st['profit_factor']}, "
                  f"win {st['win_rate'] * 100:.0f}%, exits {st['exit_reasons']}")

        run_rec = {"instrument": inst, "cost_tag": cost_tag, "asset_class": klass,
                   "bars": len(df), "years": round(years, 2),
                   "headline_params": GRID[0],
                   "dsr": rep.dsr, "pbo": rep.pbo, "cpcv": rep.cpcv,
                   "verdict": rep.verdict, "per_config": per_config}
        summary["runs"].append(run_rec)

        run_label = f"trend4h__{inst.replace('/', '_')}__{cost_tag}"
        (OUT_DIR / f"{run_label}.json").write_text(json.dumps(rep.model_dump(), indent=2, default=str))
        if cost_tag == "rt2.5_config":  # keep the standard cache at config-cost results
            try:
                service.save_validation(rep.model_dump(), rep.strategy, rep.instrument)
            except Exception as e:  # noqa: BLE001
                print(f"  WARNING: local save failed: {type(e).__name__}: {e}")

        n_pass += 1 if ok else 0
        n_reject += 0 if ok else 1

    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print("\n" + "=" * 78)
    print(f"DONE: {n_pass} PASS, {n_reject} REJECT | ledger {n_before} -> {n_final} "
          f"| records in {OUT_DIR.relative_to(ENGINE_DIR)}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
