"""Instrumented subset run (2025-07-17 -> 2025-09-30) to measure TRUE gross
leverage actually used by the live-equivalent portfolio book.

Wraps RiskManager.permit to record book gross notional at every permitted
entry, both raw (as the implemented cap sees it -- quote-currency magnitude)
and FX-corrected to GBP (static 2026-07-16 rates). No repo code modified.
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

cfg = get_config()
PAIRS = list(cfg.data.instruments)
TFS = ["15m", "1h", "1d"]  # 1w inert (see baseline runner docstring)

NOW = pd.Timestamp("2026-07-17 02:00", tz="UTC")
WINDOW_START = pd.Timestamp("2025-07-17", tz="UTC")
WINDOW_END = pd.Timestamp("2025-09-30", tz="UTC")
BUFFER = {"15m": pd.Timedelta(days=45), "1h": pd.Timedelta(days=360), "1d": pd.Timedelta(days=410)}
TF_SECONDS = {"15m": 900, "1h": 3600, "1d": 86400}
RATES = json.load(open(ENGINE_DIR / "scratch" / "quote_to_gbp_rates.json"))

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
            "reward_risk": p.get("reward_risk", 2.0),
        }
HTF_MAP = {"15m": ("1h", 200), "1h": ("1d", 200), "1d": ("1w", 50)}


class _NoNews:
    def check_veto(self, instrument, t):
        return (False, "")


class LeverageProbe(RiskManager):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.max_lev_raw = 0.0
        self.max_lev_gbp = 0.0
        self.lev_series = []

    @staticmethod
    def _gbp_notional(instrument_key, notional):
        pair = str(instrument_key).split("@")[0]
        q = pair.split("/")[-1] if "/" in pair else "GBP"
        return notional * RATES.get(q, 1.0)

    def permit(self, signal, account, market, **kw):
        pos = super().permit(signal, account, market, **kw)
        if pos.permitted:
            gross_raw = sum(p.notional for p in (account.open_positions or [])) + pos.notional
            gross_gbp = sum(self._gbp_notional(p.instrument, p.notional)
                            for p in (account.open_positions or [])) \
                        + self._gbp_notional(pos.instrument, pos.notional)
            eq = max(account.equity, 1e-9)
            self.max_lev_raw = max(self.max_lev_raw, gross_raw / eq)
            self.max_lev_gbp = max(self.max_lev_gbp, gross_gbp / eq)
            self.lev_series.append((gross_raw / eq, gross_gbp / eq))
        return pos


store = ParquetStore()
pits, strategies, timeframes = {}, {}, {}
t0 = time.time()
for tf in TFS:
    for sym in PAIRS:
        key = f"{sym}@{tf}"
        df = store.load(sym, tf)
        df = df[df.index >= WINDOW_START - BUFFER[tf]]
        if len(df) and NOW < df.index[-1] + pd.Timedelta(seconds=TF_SECONDS[tf]):
            df = df.iloc[:-1]
        pit = PointInTimeAccessor(df)
        p = LOOKUP.get((sym, tf))
        if p is None or p.get("vetoed"):
            continue  # live skips vetoed systems entirely
        base = RegimeGatedMomentum(
            momentum_lookback=p["momentum_lookback"], vol_window=p["vol_window"],
            holding_horizon=p["holding_horizon"], reward_risk=p["reward_risk"],
            regime_method="rule_based", timeframe=tf, bypass_calibration=True,
            instrument=sym)
        htf_rule, htf_ma = HTF_MAP[tf]
        strat = MultiTimeframeMomentum(base_strategy=base, htf_rule=htf_rule,
                                       htf_ma_window=htf_ma, instrument=sym)
        strat.fit(pit, df.index)
        pits[key], strategies[key], timeframes[key] = pit, strat, tf
print(f"built {len(pits)} systems in {time.time()-t0:.0f}s", flush=True)

probe = LeverageProbe(cfg.risk, news_filter=_NoNews())
bt = PortfolioBacktester(cfg, risk_manager=probe, use_regime=False, exit_mode="managed")
res = bt.run(pits, strategies, timeframes=timeframes,
             start=WINDOW_START, end=WINDOW_END, warmup=60)
print(res.summary(), flush=True)
lv = np.array(probe.lev_series) if probe.lev_series else np.zeros((1, 2))
print(json.dumps({
    "max_leverage_raw_as_capped": float(probe.max_lev_raw),
    "max_leverage_gbp_corrected": float(probe.max_lev_gbp),
    "p50_leverage_gbp_at_entries": float(np.percentile(lv[:, 1], 50)),
    "p95_leverage_gbp_at_entries": float(np.percentile(lv[:, 1], 95)),
    "n_permitted_entries": int(len(lv)),
    "subset_window": [str(WINDOW_START), str(WINDOW_END)],
    "subset_net_pnl_raw": float(res.metrics.get("net_pnl", 0)),
    "subset_n_trades": int(res.metrics.get("n_trades", 0)),
}, indent=1), flush=True)
print("DONE", flush=True)
