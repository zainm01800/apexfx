"""Sub-daily candidate validation: BTC/ETH close-momentum + USD fix-flow.

Thin glue over the existing machinery (run_validation + CPCV/DSR/PBO,
TrialLedger, Backtester) - no new math lives here. Mirrors
scripts/run_candidate_check.py's honesty rules:

  * ITERATION window strictly before 2025-01-01; the 2025+ holdout is never
    touched (hard assert per series).
  * Every distinct config is recorded in the shared TrialLedger BEFORE any
    validation runs, and every DSR below is deflated by the FINAL ledger count -
    harsher than deflating by this run's grid alone.
  * Reports are persisted to data_store/validation/intraday_2026-07-17/ (local
    only; Supabase posting is deliberately skipped for this research sweep).

Differences from run_candidate_check.py (why this script exists):
  * Crypto 1h history comes from the Binance klines cache built by
    scripts/fetch_binance_1h.py (Yahoo only has ~730d of 1h) - read directly,
    never merged into the shared parquet store the live daemon reads.
  * Exits are barrier-mode with wide stops so the TIME barrier binds: the
    academic trades being replicated are pure fixed-horizon timing bets, and the
    managed TradeManager (chandelier trail / breakeven / partials) would change
    what is being measured.
  * Crypto runs at two cost levels: config v5 as-is (1.5bps spread + 0.5bps
    slippage per side ~ 2.5bps round-trip) and a stressed 10bps round-trip
    (8bps spread + 1bps slippage per side), because the documented edge is
    fee-fragile (breakeven 3-10 bps/trade).

Usage:
    cd engine && .venv-mac/bin/python scripts/run_intraday_candidates.py
"""

from __future__ import annotations

import argparse
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
from apex_quant.data import PointInTimeAccessor, ParquetStore, clean, get_adapter  # noqa: E402
from apex_quant.validation.report import (  # noqa: E402
    PBO_THRESHOLD,
    DSR_THRESHOLD,
    run_validation,
)
from apex_quant.validation.trials import TrialLedger  # noqa: E402

LEDGER_PATH = ENGINE_DIR / "data_store" / "validation" / "trial_ledger.json"
OUT_DIR = ENGINE_DIR / "data_store" / "validation" / "intraday_2026-07-17"
HOLDOUT_START = pd.Timestamp("2025-01-01", tz="UTC")
MIN_BARS = 300
EXIT_MODE = "barrier"   # wide stops; the time barrier is the intended exit

CRYPTO_INSTRUMENTS = {"BTC/USD": "BINANCE_BTC_USD_1h.parquet",
                      "ETH/USD": "BINANCE_ETH_USD_1h.parquet"}
FX_INSTRUMENTS = ["EUR/USD", "USD/JPY"]          # headline fix-flow legs
FX_START = "2021-03-15"                          # depth of the OANDA 1h cache

CLOSE_MOM_GRID = [
    {"holding_horizon": 1, "vol_filter": True},    # headline: paper's conditioning
    {"holding_horizon": 2, "vol_filter": True},
    {"holding_horizon": 1, "vol_filter": False},
    {"holding_horizon": 2, "vol_filter": False},
]
FIX_FLOW_GRID = [
    {"holding_horizon": 1, "condition_on_premove": False},  # headline: naive short-USD
    {"holding_horizon": 2, "condition_on_premove": False},
    {"holding_horizon": 1, "condition_on_premove": True},
    {"holding_horizon": 2, "condition_on_premove": True},
]


def _close_mom_factory(**params):
    from apex_quant.strategies.intraday_close_momentum import IntradayCloseMomentum
    return IntradayCloseMomentum(**params)


def _fix_flow_factory(**params):
    from apex_quant.strategies.fix_flow import FixFlowReversal
    return FixFlowReversal(**params)


def _load_binance(inst: str, fname: str) -> pd.DataFrame:
    df = clean(pd.read_parquet(ENGINE_DIR / "data_store" / fname))
    df = df[df.index < HOLDOUT_START]
    assert not df.empty and df.index[-1] < HOLDOUT_START, f"{inst}: post-2024 data leaked"
    return df


def _fetch_oanda_window(adapter, inst: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Fetch [start, end] via the OANDA adapter in ~150-day CALENDAR segments.

    The adapter's own pagination advances chunk_end in bar-count units and then
    breaks on the first sub-4800-candle chunk, so a single multi-year call only
    ever returns the first ~200 days. Segmented calls of ~150 days (< 4800 H1
    candles each) sidestep that without touching shared adapter code."""
    frames = []
    seg = pd.Timedelta(days=150)
    cur = start
    while cur < end:
        nxt = min(cur + seg, end)
        frames.append(adapter.get_history(inst, str(cur), str(nxt), "1h"))
        cur = nxt + pd.Timedelta(hours=1)
    frames = [f for f in frames if f is not None and not f.empty]
    return pd.concat(frames) if frames else pd.DataFrame()


def _load_fx(store: ParquetStore, adapter, inst: str) -> pd.DataFrame:
    """OANDA 1h for [FX_START, 2025-01-01). Cache first; the cache has known
    holes inside the iteration window (2022-H1 everywhere; 2024-H1 for
    JPY/GBP/CHF), so gap-fill via the adapter and merge IN MEMORY ONLY - never
    write back to the store (the live daemon reads those files concurrently).
    On adapter failure, degrade to the cache as-is and say so."""
    cached = clean(store.load(inst, "1h"))
    start = pd.Timestamp(FX_START, tz="UTC")
    end = HOLDOUT_START - pd.Timedelta(hours=1)   # last in-window bar label: 2024-12-31 23:00
    try:
        fetched = _fetch_oanda_window(adapter, inst, start, end)
        merged = pd.concat([cached, fetched])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        df = clean(merged)
        note = f" (adapter gap-fill: {len(cached)} cached + {len(fetched)} fetched)"
    except Exception as e:  # noqa: BLE001 - offline/creds: degrade to the cache
        df = cached
        note = f" (adapter fetch FAILED: {type(e).__name__}: {e}; using cache with holes)"
    df = df[(df.index >= start) & (df.index < HOLDOUT_START)]
    assert not df.empty and df.index[-1] < HOLDOUT_START, f"{inst}: post-2024 data leaked"
    return df, note


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


def _diagnostics(pit, inst: str, factory, headline: dict, cfg, years: float) -> dict:
    """Headline-config trading stats: trades/yr, net bps/trade, win rate, plus
    pre/post-2021 subperiod Sharpes (same recorded config - no new trial)."""
    bt = Backtester(cfg, exit_mode=EXIT_MODE)

    def run_slice(start=None, end=None):
        strat = factory(**headline)
        strat.fit(pit, pit.as_of(pit.end).index)
        return bt.run(pit, strat, instrument=inst, start=start, end=end, warmup=250)

    res = run_slice()
    m = res.metrics
    rets = [t.return_pct for t in res.trades]
    out = {
        "n_trades": m.get("n_trades", 0),
        "trades_per_year": round(m.get("n_trades", 0) / years, 1) if years else None,
        "net_bps_per_trade": round(float(np.mean(rets)) * 1e4, 2) if rets else None,
        "win_rate": round(m.get("win_rate", 0.0), 4),
        "exit_reasons": {},
        "subperiods": {},
    }
    for t in res.trades:
        out["exit_reasons"][t.exit_reason] = out["exit_reasons"].get(t.exit_reason, 0) + 1

    split = pd.Timestamp("2021-01-01", tz="UTC")
    for label, (s, e) in {"pre_2021": (None, split), "post_2021": (split, None)}.items():
        if pit.start >= split and label == "pre_2021":
            out["subperiods"][label] = None
            continue
        sub = run_slice(start=s, end=e)
        n = sub.metrics.get("n_trades", 0)
        out["subperiods"][label] = {
            "sharpe": round(sub.metrics.get("sharpe", 0.0), 3),
            "n_trades": n,
        } if n >= 5 else {"sharpe": None, "n_trades": n}
    return out


def _stressed_crypto_cfg(cfg):
    """10bps round-trip stress: 8bps spread + 1bps slippage per side."""
    c = cfg.model_copy(deep=True)
    c.asset_classes.crypto.spread_bps = 8.0
    c.asset_classes.crypto.slippage_bps = 1.0
    return c


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sub-daily candidate validation (iteration window only).")
    ap.add_argument("--candidate", action="append", choices=["close_momentum", "fix_flow"],
                    help="repeatable; default runs both candidates")
    args = ap.parse_args(argv)

    cfg = get_config()
    adapter = get_adapter(cfg.data.provider)
    store = ParquetStore(cfg.store_path)
    service = EngineService(cfg)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # ---- plan the whole campaign; record every config BEFORE running ---------
    only = set(args.candidate or ["close_momentum", "fix_flow"])
    campaign = []  # (candidate, inst, factory_name, factory, grid, df, cfg, cost_tag, data_note)
    for inst, fname in CRYPTO_INSTRUMENTS.items():
        if "close_momentum" not in only:
            continue
        df = _load_binance(inst, fname)
        campaign.append(("close_momentum", inst, "intraday_close_momentum", _close_mom_factory,
                         CLOSE_MOM_GRID, df, cfg, "rt2.5_config", "binance_cache"))
        campaign.append(("close_momentum", inst, "intraday_close_momentum", _close_mom_factory,
                         CLOSE_MOM_GRID, df, _stressed_crypto_cfg(cfg), "rt10_stress", "binance_cache"))
    for inst in FX_INSTRUMENTS:
        if "fix_flow" not in only:
            continue
        df, note = _load_fx(store, adapter, inst)
        campaign.append(("fix_flow", inst, "fix_flow_reversal", _fix_flow_factory,
                         FIX_FLOW_GRID, df, cfg, "config_v5", note))

    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    seen = set()
    for _, inst, fname_, _, grid, _, _, _, _ in campaign:
        for params in grid:
            key = json.dumps({"instrument": inst, "timeframe": "1h",
                              "factory": fname_, "params": params}, sort_keys=True, default=str)
            if key not in seen:  # cost levels share a key; record once
                seen.add(key)
                ledger.record({"instrument": inst, "timeframe": "1h",
                               "factory": fname_, "params": params})
    ledger.save(LEDGER_PATH)
    n_final = ledger.n_trials

    print("=" * 78)
    print(f"INTRADAY CANDIDATES | iteration window strictly < {HOLDOUT_START.date()} | "
          f"exit_mode={EXIT_MODE}")
    print(f"ledger: {n_before} -> {n_final} trials ({len(seen)} new); "
          f"ALL DSRs deflated by final n={n_final}")
    print("=" * 78)

    summary = {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "ledger_before": n_before, "ledger_after": n_final,
               "holdout_start": str(HOLDOUT_START.date()), "exit_mode": EXIT_MODE,
               "runs": []}
    n_pass = n_reject = 0

    for candidate, inst, fname_, factory, grid, df, run_cfg, cost_tag, data_note in campaign:
        klass = run_cfg.asset_class_of(inst)
        years = (df.index[-1] - df.index[0]).days / 365.25
        pit = PointInTimeAccessor(df)
        print(f"\n[{klass}] {inst} ({candidate}, {cost_tag}): {len(df)} bars "
              f"({pit.start.date()} -> {pit.end.date()}, {years:.1f}y){data_note}")

        if len(df) < MIN_BARS:
            print(f"  skip: {len(df)} bars < MIN_BARS"); continue
        try:
            rep = run_validation(pit, inst, strategy_factory=factory, param_grid=grid,
                                 cfg=run_cfg, generated_for=str(pit.end.date()),
                                 n_trials=n_final, exit_mode=EXIT_MODE)
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR {type(e).__name__}: {e}")
            import traceback; traceback.print_exc()
            n_reject += 1
            continue

        ok = _print_gate(rep, cost_tag)
        diag = _diagnostics(pit, inst, factory, grid[0], run_cfg, years)
        print(f"    headline {grid[0]}: {diag['n_trades']} trades "
              f"({diag['trades_per_year']}/yr), net {diag['net_bps_per_trade']} bps/trade, "
              f"win {diag['win_rate'] * 100:.0f}%, exits {diag['exit_reasons']}")
        print(f"    subperiods: {diag['subperiods']}")

        run_rec = {"candidate": candidate, "instrument": inst, "cost_tag": cost_tag,
                   "asset_class": klass, "bars": len(df), "years": round(years, 2),
                   "data_note": data_note,
                   "headline_params": grid[0], "grid": grid,
                   "dsr": rep.dsr, "pbo": rep.pbo, "cpcv": rep.cpcv,
                   "verdict": rep.verdict, "diagnostics": diag}
        summary["runs"].append(run_rec)

        # persist: per-run JSON (complete record) + the loop's standard local cache
        run_label = f"{candidate}__{inst.replace('/', '_')}__{cost_tag}"
        (OUT_DIR / f"{run_label}.json").write_text(json.dumps(rep.model_dump(), indent=2, default=str))
        if cost_tag != "rt10_stress":  # keep the standard cache at config-cost results
            try:
                service.save_validation(rep.model_dump(), rep.strategy, rep.instrument)
            except Exception as e:  # noqa: BLE001
                print(f"  WARNING: local save failed: {type(e).__name__}: {e}")

        n_pass += 1 if ok else 0
        n_reject += 0 if ok else 1

    # merge into any existing summary (partial --candidate reruns must not erase
    # results for the candidate that was not re-run)
    summary_path = OUT_DIR / "summary.json"
    if summary_path.exists():
        try:
            prior = json.loads(summary_path.read_text())
            keep = [r for r in prior.get("runs", [])
                    if (r["candidate"], r["instrument"], r["cost_tag"])
                    not in {(c, i, t) for c, i, _, _, _, _, _, t, _ in campaign}]
            summary["runs"] = keep + summary["runs"]
            summary["ledger_before"] = prior.get("ledger_before", n_before)
        except Exception:  # noqa: BLE001 - a corrupt summary must not kill the run
            pass
    summary_path.write_text(json.dumps(summary, indent=2, default=str))
    print("\n" + "=" * 78)
    print(f"DONE: {n_pass} PASS, {n_reject} REJECT | ledger {n_before} -> {n_final} "
          f"| records in {OUT_DIR.relative_to(ENGINE_DIR)}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
