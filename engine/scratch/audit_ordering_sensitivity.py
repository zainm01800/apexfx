"""AUDIT: how much of the book's result is an artifact of instrument ORDER?

PortfolioBacktester evaluates same-bar candidates in dict insertion order and
provisionally books each permitted one, so once the 10-slot swing bucket fills
every later candidate that bar is vetoed (`timeframe_bucket_full` fires 18,147
times in the certified book). That means slots go to whichever instrument happens
to be earlier in the dict — NOT to the best signal.

If shuffling the order materially moves Sharpe, then part of the certified result
is luck of iteration order, and slot allocation is worth fixing properly.

Measurement only — no strategy change, no ledger charge.
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

N_PERM = 6

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


def run(order):
    panel = {i: master[i] for i in order if i in master}
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    res = PortfolioBacktester(cfg, exit_mode="managed").run(
        pits, TrendBook(panel, **params).strategies(),
        timeframes={k: "1d" for k in panel}, warmup=WARMUP, periods_per_year=252)
    m = res.metrics
    return m["sharpe"], m["total_return"], m["n_trades"], m["max_drawdown"]


print("Same book, same data, same seed — ONLY the instrument iteration order differs.\n")
rows = []
s, r, n, dd = run([i for i in gate_order if i in master])
rows.append(("gate order", s, r, n, dd))
print(f"  {'gate order':16s} sharpe {s:6.3f}  ret {r*100:7.1f}%  trades {n:5d}  maxDD {dd*100:5.1f}%")

rng = np.random.default_rng(42)
base = [i for i in gate_order if i in master]
for k in range(N_PERM):
    perm = list(rng.permutation(base))
    s, r, n, dd = run(perm)
    rows.append((f"shuffle {k+1}", s, r, n, dd))
    print(f"  {'shuffle '+str(k+1):16s} sharpe {s:6.3f}  ret {r*100:7.1f}%  trades {n:5d}  maxDD {dd*100:5.1f}%")

sh = [x[1] for x in rows]
rt = [x[2] for x in rows]
print(f"\n  Sharpe  min {min(sh):.3f}  max {max(sh):.3f}  spread {max(sh)-min(sh):.3f}  sd {np.std(sh):.3f}")
print(f"  Return  min {min(rt)*100:.0f}%  max {max(rt)*100:.0f}%  spread {(max(rt)-min(rt))*100:.0f} pts")
print("\n  If the spread is large, the certified number is partly luck of ordering,")
print("  and allocating slots by SIGNAL STRENGTH (Signal.probability) is a real fix.")
