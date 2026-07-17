"""Sanity checks for VolManagedCarryTrend before it goes near the gate.

1. Kelly remap identity: fractional_kelly(p', b, kf) == f x fractional_kelly(p, b, kf)
2. Inert pass-through: median_window huge => signals identical to CarryTrendFilter
3. No lookahead (future bars): signals/proxy at t identical with/without post-t data
4. No lookahead (current bar): proxy/rets at t unchanged when bar t's close moves 3x
5. Determinism: identical equity curve across two full backtests
6. Activity: overlay actually damps / stands down on the EUR/USD iteration window

Run: cd engine && .venv-mac/bin/python scratch/sanity_vol_managed.py
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

import numpy as np
import pandas as pd

from apex_quant.backtest.engine import Backtester
from apex_quant.config import get_config
from apex_quant.data import PointInTimeAccessor, ParquetStore, clean
from apex_quant.risk.sizing import fractional_kelly, full_kelly
from apex_quant.risk.types import Direction
from apex_quant.strategies.carry_trend_filter import CarryTrendFilter
from apex_quant.strategies.vol_managed_overlay import VolManagedCarryTrend

fails = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'ok' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        fails.append(name)


print("1. Kelly remap identity")
rng = np.random.default_rng(42)
worst = 0.0
for _ in range(2000):
    p = float(rng.uniform(0.52, 0.82))
    b = float(rng.uniform(1.0, 2.5))
    f = float(rng.uniform(0.05, 1.0))
    kf = 0.2
    p2 = (f * full_kelly(p, b) * b + 1.0) / (b + 1.0)
    p2 = min(max(p2, 0.0), 1.0)
    got = fractional_kelly(p2, b, kf)
    want = f * fractional_kelly(p, b, kf)
    worst = max(worst, abs(got - want))
check("remap identity (2000 random p/b/f)", worst < 1e-12, f"max abs err {worst:.2e}")

print("2-6. loading EUR/USD 1d from the parquet store (iteration window only)")
cfg = get_config()
store = ParquetStore(cfg.store_path)
df = clean(store.load("EUR/USD", "1d"))
df = df[df.index < pd.Timestamp("2025-01-01", tz="UTC")]
print(f"  {len(df)} bars {df.index[0].date()} -> {df.index[-1].date()}")
pit = PointInTimeAccessor(df)
PARAMS = {"momentum_lookback": 126, "vol_window": 63, "holding_horizon": 21,
          "reward_risk": 1.5, "regime_method": "rule_based", "timeframe": "1d"}

print("2. Inert pass-through (median_window=10**9 => never active)")
base = CarryTrendFilter(**PARAMS)
base.fit(pit, pit.as_of(pit.end).index)
ovl = VolManagedCarryTrend(**PARAMS, median_window=10**9)
ovl.fit(pit, pit.as_of(pit.end).index)
sample_idx = list(range(300, len(df.index), 97))
mism = 0
for i in sample_idx:
    t = df.index[i]
    s1 = base.generate(pit, t, "EUR/USD")
    s2 = ovl.generate(pit, t, "EUR/USD")
    if (s1.direction != s2.direction or abs(s1.probability - s2.probability) > 1e-12
            or abs(s1.reward_risk - s2.reward_risk) > 1e-12):
        mism += 1
check("inert signals identical to base", mism == 0, f"{mism} mismatches over {len(sample_idx)} bars")

print("3. No lookahead — future bars")
T_POS = 1500
t = df.index[T_POS]
calls = list(range(T_POS - 400, T_POS + 1))
ovl_full = VolManagedCarryTrend(**PARAMS)
ovl_full.fit(pit, pit.as_of(pit.end).index)
sig_full = None
for i in calls:
    sig_full = ovl_full.generate(pit, df.index[i], "EUR/USD")
pit_trunc = PointInTimeAccessor(df.iloc[: T_POS + 1])
ovl_trunc = VolManagedCarryTrend(**PARAMS)
ovl_trunc.fit(pit_trunc, pit_trunc.as_of(pit_trunc.end).index)
sig_trunc = None
for i in calls:
    sig_trunc = ovl_trunc.generate(pit_trunc, df.index[i], "EUR/USD")
same_sig = (sig_full.direction == sig_trunc.direction
            and abs(sig_full.probability - sig_trunc.probability) < 1e-12
            and sig_full.rationale == sig_trunc.rationale)
same_proxy = (len(ovl_full._proxies) == len(ovl_trunc._proxies)
              and (not ovl_full._proxies or ovl_full._proxies[-1] == ovl_trunc._proxies[-1])
              and ovl_full._rets[-1] == ovl_trunc._rets[-1])
check("signal at t identical with/without post-t data", same_sig,
      f"{sig_full.direction.value} p={sig_full.probability:.4f} vs {sig_trunc.direction.value} p={sig_trunc.probability:.4f}")
check("proxy/rets at t identical with/without post-t data", same_proxy,
      f"n_proxies {len(ovl_full._proxies)} vs {len(ovl_trunc._proxies)}")

print("4. No lookahead — current bar (close x3 at t must not move proxy/rets)")
df2 = df.copy()
df2.loc[t, "close"] = df2.loc[t, "close"] * 3.0
pit2 = PointInTimeAccessor(df2)
ovl_mod = VolManagedCarryTrend(**PARAMS)
ovl_mod.fit(pit2, pit2.as_of(pit2.end).index)
for i in calls:
    ovl_mod.generate(pit2, df.index[i], "EUR/USD")
check("proxy at t independent of bar t", ovl_mod._proxies[-1] == ovl_full._proxies[-1],
      f"proxy {ovl_mod._proxies[-1]:.6f} vs {ovl_full._proxies[-1]:.6f}")
check("shadow rets independent of bar t", ovl_mod._rets[-1] == ovl_full._rets[-1])

print("5. Determinism + 6. Activity (full-window backtest x2)")
res = []
strats = []
for _ in range(2):
    s = VolManagedCarryTrend(**PARAMS)
    s.fit(pit, pit.as_of(pit.end).index)
    res.append(Backtester(cfg, exit_mode="managed").run(pit, s, "EUR/USD", warmup=250))
    strats.append(s)
check("equity identical across two runs", res[0].equity.equals(res[1].equity),
      f"n_trades {res[0].metrics.get('n_trades')} vs {res[1].metrics.get('n_trades')}")
s0 = strats[0]
print(f"  signals={s0.n_signals} scaled={s0.n_scaled} standdowns={s0.n_standdowns} "
      f"base vetoes={s0.base.n_vetoes}/{s0.base.n_signals}")
check("overlay emits signals", s0.n_signals > 0)
check("damping fired at least once", s0.n_scaled > 0)
check("stand-down fired at least once", s0.n_standdowns > 0, f"{s0.n_standdowns} stand-downs")
check("carry veto still active inside overlay", s0.base.n_vetoes > 0)
m = res[0].metrics
print(f"  overlay backtest: trades={m.get('n_trades')} sharpe={m.get('sharpe'):.3f} "
      f"total_return={m.get('total_return') * 100:.1f}% maxDD={m.get('max_drawdown') * 100:.1f}%")

print()
if fails:
    print("SANITY FAILURES:", fails)
    sys.exit(1)
print("ALL SANITY CHECKS PASSED")
