"""Why did residual momentum go from £731 (screen) to £197 (gate)?

Two competing explanations:
  A. THE STOPS. The screen was a hold-to-rebalance book with no stops. The engine exits on an
     ATR stop. Residual winners have LOWER raw momentum by construction (the market component
     is regressed out), so they sit closer to their stop and whipsaw out, realising losses the
     screen never took and then missing the recovery.
  B. THE SIGNAL. Residual momentum simply has less per-trade edge, and the screen's £731 was an
     artifact of a construction that never had to survive a stop.

Discriminating test: widen `atr_stop_mult` until stops effectively stop binding. Under (A) the
return should recover toward the screen. Under (B) it should not move much, and the gate number
stands.

DIAGNOSTIC ONLY — no ledger charge, nothing adopted. Any config that looked good here would
still need its own prereg and gate.
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
import pandas as pd  # noqa: E402

from apex_quant.backtest.portfolio import PortfolioBacktester  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor  # noqa: E402
from apex_quant.risk.manager import RiskManager  # noqa: E402
from apex_quant.strategies.cross_sectional import CrossSectionalMomentum  # noqa: E402
from apex_quant.strategies.residual_momentum import ResidualMomentum  # noqa: E402

from run_portfolio_gate import DEFAULT_HOLDOUT_START, WARMUP, _utc  # noqa: E402
from run_portfolio_gate_book_r import build_panel, MIN_NAMES, TOP_N  # noqa: E402

STOP_MULTS = [2.0, 4.0, 8.0, 20.0]
HOLDS = [21, 63]


def forward_p95(returns: pd.Series, seed: int = 42) -> float:
    r = returns.dropna().to_numpy()
    if len(r) < 100:
        return float("nan")
    rng = np.random.default_rng(seed)
    eq = np.cumprod(1.0 + rng.choice(r, size=(20000, 252), replace=True), axis=1)
    pk = np.maximum.accumulate(eq, axis=1)
    return float(np.percentile(((pk - eq) / pk).max(axis=1), 95))


def main() -> int:
    cfg = get_config()
    panel = build_panel(cfg, _utc(DEFAULT_HOLDOUT_START))
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    tfs = {k: "1d" for k in panel}

    print("=" * 104, flush=True)
    print(f"DIAGNOSTIC — is it the STOPS or the SIGNAL? | {len(panel)} instruments", flush=True)
    print("  screen (no stops, hold-to-rebalance): residual Sharpe 1.123, £731/mo", flush=True)
    print("  gate   (ATR stop 2.0, managed exits): residual Sharpe 0.454, £197/mo", flush=True)
    print("=" * 104, flush=True)
    print(f"{'signal':<10} {'atrStop':>8} {'hold':>5} {'CAGR':>7} {'£/mo':>7} {'Sharpe':>7} "
          f"{'btDD':>6} {'fwdP95':>7} {'trades':>7} {'win%':>6} {'stopHits':>9}", flush=True)

    for label, build in (
        ("residual", lambda: ResidualMomentum(
            panel, lookback=252, skip=21, vol_window=63, top_n=TOP_N,
            min_universe=MIN_NAMES, holding_horizon=21, timeframe="1d")),
        ("total", lambda: CrossSectionalMomentum(
            panel, lookback=252, vol_window=63,
            long_frac=TOP_N / max(1, len(panel)), short_frac=0.0, allow_short=False,
            min_universe=MIN_NAMES, holding_horizon=21, timeframe="1d")),
    ):
        for mult in STOP_MULTS:
            for hold in HOLDS:
                rc = cfg.risk.model_copy(update={
                    "max_risk_per_trade": 0.0050,
                    "max_concurrent_trades": TOP_N,
                    "max_swing_slots": TOP_N,
                    "atr_stop_mult": mult,
                })
                model = build()
                for s in model.strategies().values():
                    s.holding_horizon = hold
                res = PortfolioBacktester(
                    cfg, risk_manager=RiskManager(rc), exit_mode="managed",
                    slot_allocation="expected_value",
                ).run(pits, model.strategies(), timeframes=tfs,
                      warmup=WARMUP, max_hold=hold, periods_per_year=252)

                m, eq = res.metrics, res.equity
                cagr = float(eq.iloc[-1] / eq.iloc[0]) ** (252.0 / len(eq)) - 1
                stops = sum(1 for t in res.trades
                            if "stop" in str(getattr(t, "exit_reason", "")).lower())
                print(f"{label:<10} {mult:8.1f} {hold:5d} {cagr*100:6.2f}% "
                      f"{cagr*100000/12:7.0f} {m['sharpe']:7.3f} {m['max_drawdown']*100:5.1f}% "
                      f"{forward_p95(res.returns)*100:6.1f}% {m['n_trades']:7d} "
                      f"{m['win_rate']*100:5.1f}% "
                      f"{stops:6d}/{m['n_trades']:<4d}", flush=True)
        print(flush=True)

    print("=" * 104, flush=True)
    print("If residual's CAGR climbs sharply as the stop widens -> the STOPS were the problem,", flush=True)
    print("and a hold-to-rebalance execution model is worth pre-registering.", flush=True)
    print("If it stays flat -> the SIGNAL is weak in this engine and £197 is the honest number.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
