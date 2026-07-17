"""TRUE BASELINE: live-equivalent portfolio backtest (2026-07-17).

Replicates the live paper-trading setup (scripts/run_live_paper_trading.py) inside
the portfolio backtester (apex_quant/backtest/portfolio.py) so book-level risk
caps actually bind:

  - Universe: the 22 forex pairs from config.yaml
  - Timeframes: 15m / 1h / 1d / 1w (each pair x TF = one "system", as live scans)
  - Per-pair params from data_store/high_frequency_optimized_configs.json
    (same mapping as _load_optimised_configs in the live script); 1w uses the
    live "position" style fallback (no optimized 1w entries exist)
  - Strategy: RegimeGatedMomentum(bypass_calibration=True) wrapped in
    MultiTimeframeMomentum with the live HTF mapping
    (15m->1h MA200, 1h->1d MA200, 1d->1w MA50, 1w->none)
  - Exits: TradeManager "managed" mode (partials @1R/1.5R, breakeven,
    chandelier trail, squeeze tighten, time stops)
  - Costs: config.yaml forex mechanics (1 pip spread + 0.5 bps slippage, 0 comm.)
  - Risk: RiskManager(cfg.risk) as of config.yaml v4 (3.0x gross / 1.5x cluster
    caps, 2% per-trade cap, kelly 0.20, vol target 10%, TF slot buckets)
  - Equity: 100,000 (GBP-denominated; note the backtester treats quote==account
    currency, i.e. no FX conversion to GBP -- documented in the report)

Known deliberate deviations from live (documented in the report):
  - use_regime=False: live calls RiskManager.permit() WITHOUT a regime label,
    so no regime aggression scaling at the risk layer (the strategy's internal
    regime gate is unchanged)
  - News-calendar filter stubbed out (portfolio.py calls permit() without t,
    which would evaluate the veto at real 'now' for every simulated bar)
  - Live's DeepSeek sentiment/structural vetoes and Bayesian sizer are not
    replicated (they are stateful/online); backtest uses fractional Kelly
  - Live MT4 order SL uses per-pair atr_stop_mult; live risk SIZING (and this
    backtest's stops) use config atr_stop_mult=2.5

Writes:
  data_store/baseline_portfolio_trades_2026-07-17.csv
  data_store/baseline_portfolio_metrics_2026-07-17.json
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))
load_dotenv(ENGINE_DIR / ".env")

from apex_quant.config import get_config
from apex_quant.data.store import ParquetStore
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.manager import RiskManager
from apex_quant.strategies.baseline import RegimeGatedMomentum
from apex_quant.strategies.multi_timeframe import MultiTimeframeMomentum
from apex_quant.backtest.portfolio import PortfolioBacktester
from apex_quant.backtest.result import compute_metrics

cfg = get_config()
PAIRS = list(cfg.data.instruments)
TFS = ["15m", "1h", "1d", "1w"]

NOW = pd.Timestamp.utcnow().tz_convert("UTC").floor("s")
WINDOW_START = (NOW - pd.Timedelta(days=365)).normalize()
print(f"window: {WINDOW_START} -> {NOW}", flush=True)

# Live lookback depths (scan_single_asset) + margin, so every indicator sees
# at least as much trailing history as it does live.
BUFFER = {"15m": pd.Timedelta(days=45), "1h": pd.Timedelta(days=360),
          "1d": pd.Timedelta(days=410), "1w": pd.Timedelta(days=1100)}
TF_SECONDS = {"15m": 900, "1h": 3600, "1d": 86400, "1w": 604800}

# ── Live parameter resolution (mirrors _load_optimised_configs) ──────────────
with open(ENGINE_DIR / "data_store" / "high_frequency_optimized_configs.json") as f:
    _opt = json.load(f)
LOOKUP = {}
for c in _opt:
    p = c["parameters"]
    if c.get("veto", False):
        LOOKUP[(c["symbol"], c["timeframe"])] = {"vetoed": True}
    else:
        LOOKUP[(c["symbol"], c["timeframe"])] = {
            "momentum_lookback": p.get("momentum_lookback", 28),
            "vol_window": p.get("vol_window", p.get("momentum_lookback", 28)),
            "holding_horizon": p.get("hold_horizon", p.get("holding_horizon", 24)),
            "atr_stop_mult": p.get("atr_stop_mult", 2.5),
            "reward_risk": p.get("reward_risk", 2.0),
            "warmup": max(p.get("momentum_lookback", 28) + 20, 60),
        }
FALLBACK = {  # == STYLE_PARAMS_FALLBACK in the live script
    "15m": {"momentum_lookback": 14, "vol_window": 14, "holding_horizon": 36, "warmup": 70, "atr_stop_mult": 2.5, "reward_risk": 1.5},
    "1h":  {"momentum_lookback": 24, "vol_window": 24, "holding_horizon": 72, "warmup": 80, "atr_stop_mult": 2.5, "reward_risk": 2.0},
    "1d":  {"momentum_lookback": 63, "vol_window": 63, "holding_horizon": 10, "warmup": 120, "atr_stop_mult": 3.0, "reward_risk": 2.0},
    "1w":  {"momentum_lookback": 126, "vol_window": 126, "holding_horizon": 40, "warmup": 180, "atr_stop_mult": 3.0, "reward_risk": 2.0},
}
HTF_MAP = {"15m": ("1h", 200), "1h": ("1d", 200), "1d": ("1w", 50), "1w": (None, 200)}


def params_for(sym, tf):
    """Live semantics: a vetoed (symbol, tf) is SKIPPED entirely by the scanner
    (scan_single_asset returns early). Only missing entries (i.e. 1w) fall back
    to style params."""
    p = LOOKUP.get((sym, tf))
    if p is not None and p.get("vetoed"):
        return None, "vetoed-skip"
    if p is None:
        return FALLBACK[tf], "fallback"
    return p, "optimized"


class _NoNews:
    """Stub: disable the economic-calendar veto (see module docstring)."""
    def check_veto(self, instrument, t):
        return (False, "")


# ── Build pits + strategies ──────────────────────────────────────────────────
store = ParquetStore()
pits, strategies, timeframes, param_audit = {}, {}, {}, {}
t0 = time.time()
for tf in TFS:
    for sym in PAIRS:
        key = f"{sym}@{tf}"
        df = store.load(sym, tf)
        if df.empty:
            print(f"[SKIP] {key}: no data", flush=True)
            continue
        df = df[df.index >= WINDOW_START - BUFFER[tf]]
        # Drop the last bar if incomplete (mirrors live scan_single_asset)
        if len(df):
            last = df.index[-1]
            if NOW < last + pd.Timedelta(seconds=TF_SECONDS[tf]):
                df = df.iloc[:-1]
        min_bars = 300 if tf == "1w" else 60
        if len(df) < min_bars:
            print(f"[SKIP] {key}: only {len(df)} bars", flush=True)
            continue
        pit = PointInTimeAccessor(df)
        p, src = params_for(sym, tf)
        if p is None:
            param_audit[key] = {"source": src}
            print(f"[SKIP] {key}: {src}", flush=True)
            continue
        base = RegimeGatedMomentum(
            momentum_lookback=p["momentum_lookback"],
            vol_window=p["vol_window"],
            holding_horizon=p["holding_horizon"],
            reward_risk=p["reward_risk"],
            regime_method="rule_based",
            timeframe=tf,
            bypass_calibration=True,
            instrument=sym,
        )
        htf_rule, htf_ma = HTF_MAP[tf]
        strat = MultiTimeframeMomentum(base_strategy=base, htf_rule=htf_rule,
                                       htf_ma_window=htf_ma, instrument=sym)
        strat.fit(pit, df.index)
        pits[key] = pit
        strategies[key] = strat
        timeframes[key] = tf
        param_audit[key] = {"source": src, **{k: p[k] for k in
                            ("momentum_lookback", "vol_window", "holding_horizon", "reward_risk")}}
        print(f"[OK] {key:18s} bars={len(df):6d} last={df.index[-1]} "
              f"({time.time()-t0:.0f}s)", flush=True)

print(f"\n{len(pits)} systems built in {time.time()-t0:.0f}s. Running portfolio backtest...", flush=True)

# ── Run ──────────────────────────────────────────────────────────────────────
risk = RiskManager(cfg.risk, news_filter=_NoNews())
bt = PortfolioBacktester(cfg, risk_manager=risk, use_regime=False, exit_mode="managed")
t1 = time.time()
res = bt.run(pits, strategies, timeframes=timeframes,
             start=WINDOW_START, end=NOW, warmup=60)
print(f"backtest done in {time.time()-t1:.0f}s", flush=True)
print(res.summary(), flush=True)

# ── Post-process ─────────────────────────────────────────────────────────────
eq_daily = res.equity.resample("1d").last().dropna()
daily_metrics = compute_metrics(eq_daily, res.trades, periods_per_year=252)

trades_rows = []
for t in res.trades:
    d = t.model_dump()
    d["tf"] = t.instrument.split("@")[-1]
    d["pair"] = t.instrument.split("@")[0]
    trades_rows.append(d)
tdf = pd.DataFrame(trades_rows)

per_tf = {}
if not tdf.empty:
    for tf, g in tdf.groupby("tf"):
        wins = g[g.pnl > 0].pnl
        losses = g[g.pnl <= 0].pnl
        pf = float(wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0 else None
        per_tf[tf] = {
            "n_trades": int(len(g)),
            "win_rate": float((g.pnl > 0).mean()),
            "net_pnl": float(g.pnl.sum()),
            "expectancy_pnl": float(g.pnl.mean()),
            "profit_factor": pf,
            "exit_reasons": g.exit_reason.value_counts().to_dict(),
        }

# Approximate gross leverage over time from the trade blotter
# (entry/exit dates are date-granular in the Trade record; partial exits make
# this an approximation -- noted in the report).
lev_max, lev_p95 = None, None
if not tdf.empty:
    days = eq_daily.index
    gross = pd.Series(0.0, index=days)
    for _, r in tdf.iterrows():
        try:
            d0 = pd.Timestamp(r["entry_time"]).tz_localize("UTC")
            d1 = pd.Timestamp(r["exit_time"]).tz_localize("UTC")
        except Exception:
            continue
        notional = abs(float(r["units"]) * float(r["entry_price"]))
        mask = (days >= d0) & (days <= d1)
        gross[mask] += notional
    lev = gross / eq_daily.clip(lower=1.0)
    lev_max, lev_p95 = float(lev.max()), float(lev.quantile(0.95))

out = {
    "run_at": NOW.isoformat(),
    "window": {"start": str(WINDOW_START), "end": str(NOW)},
    "n_systems": len(pits),
    "param_audit": param_audit,
    "metrics_union_timeline": res.metrics,
    "metrics_daily_resampled": daily_metrics,
    "per_timeframe": per_tf,
    "per_instrument": res.per_instrument,
    "constraint_log": res.constraint_log,
    "approx_gross_leverage": {"max": lev_max, "p95": lev_p95},
    "final_equity": float(res.equity.iloc[-1]) if len(res.equity) else None,
    "n_open_at_end": int((tdf.entry_time != tdf.exit_time).sum()) if not tdf.empty else 0,
}

trades_path = ENGINE_DIR / "data_store" / "baseline_portfolio_trades_2026-07-17.csv"
metrics_path = ENGINE_DIR / "data_store" / "baseline_portfolio_metrics_2026-07-17.json"
if not tdf.empty:
    tdf.to_csv(trades_path, index=False)
with open(metrics_path, "w") as f:
    json.dump(out, f, indent=2, default=str)
print(f"trades -> {trades_path}", flush=True)
print(f"metrics -> {metrics_path}", flush=True)
print(json.dumps({k: out[k] for k in ("per_timeframe", "constraint_log", "approx_gross_leverage")}, indent=2, default=str), flush=True)
print("DONE", flush=True)
