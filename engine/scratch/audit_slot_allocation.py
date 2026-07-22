"""Does ranking slots by expected value beat arbitrary iteration order?

The honest comparison is NOT against the certified ordering — that is the luckiest
of seven tested (Sharpe 0.863 vs a ~0.52 median). Beating luck is not the bar.
The bar is: across MANY random orderings, does expected-value allocation produce a
better and TIGHTER distribution than order-based allocation?

If EV allocation is genuinely better it should also be near-INVARIANT to ordering,
because the sort makes the input order irrelevant (ties break on instrument name).
That invariance is itself the proof the fix works.

Measurement only — no ledger charge.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

import numpy as np  # noqa: E402

from apex_quant.backtest.portfolio import PortfolioBacktester  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor, ParquetStore, clean  # noqa: E402
from run_portfolio_gate import (  # noqa: E402
    COMMON_PARAMS, DEFAULT_HOLDOUT_START, MIN_BARS, WARMUP, TrendBook, _utc,
)
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402

N_PERM = 5

cfg = get_config()
store = ParquetStore(cfg.store_path)
holdout = _utc(DEFAULT_HOLDOUT_START)
gate_order = EQUITY_CORE + [GOLD_ETC] + list(cfg.data.crypto) + FX_MAJORS_7

master = {}
for inst in gate_order:
    df = store.load(inst, "1d")
    if df.empty:
        continue
    df = clean(df)
    df = df[df.index < holdout]
    if len(df) >= MIN_BARS:
        master[inst] = df

params = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}


def run(order, mode):
    panel = {i: master[i] for i in order if i in master}
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    res = PortfolioBacktester(cfg, exit_mode="managed", slot_allocation=mode).run(
        pits, TrendBook(panel, **params).strategies(),
        timeframes={k: "1d" for k in panel}, warmup=WARMUP, periods_per_year=252)
    m = res.metrics
    return m["sharpe"], m["total_return"], m["n_trades"], m["max_drawdown"]


base = [i for i in gate_order if i in master]
rng = np.random.default_rng(7)
orders = [("gate", base)] + [(f"shuf{k+1}", list(rng.permutation(base))) for k in range(N_PERM)]

print("Slot allocation: arbitrary ORDER vs EXPECTED VALUE, across the same orderings.\n")
print(f"{'ordering':10s} {'ORDER sharpe':>13} {'ret':>8}   {'EV sharpe':>10} {'ret':>8}")
res = {"order": [], "expected_value": []}
for name, o in orders:
    so, ro, no, ddo = run(o, "order")
    se, re_, ne, dde = run(o, "expected_value")
    res["order"].append(so)
    res["expected_value"].append(se)
    print(f"{name:10s} {so:13.3f} {ro*100:7.1f}%   {se:10.3f} {re_*100:7.1f}%")

for k, v in res.items():
    v = np.array(v)
    print(f"\n  {k:15s} median {np.median(v):.3f}  min {v.min():.3f}  max {v.max():.3f}  "
          f"spread {v.max()-v.min():.3f}  sd {v.std():.3f}")
print("\n  A working fix shows a HIGHER median AND a much smaller spread —")
print("  the second is the real proof: the result stops depending on luck.")
