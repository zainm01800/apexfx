"""How many months to pass a two-phase prop challenge, from THIS book's real trades?

`scratch/prop_risk_sweep.py` answers a similar question from hand-specified R-multiples
("45% -> -1.0R, 40% -> +0.9R, 15% -> +2.8R"). That is a stylised stand-in. This rebuilds the
same simulation from the ACTUAL per-trade R-multiples the engine produces at the live config,
so the fat tails, the real win rate and the real skew all come from measured trades.

Firm rules modelled (config.prop.yaml + the standard two-step contract):
  * phase 1 target +8%, phase 2 target +5%
  * static floor -8% from the phase start balance (hard fail)
  * daily-loss rule omitted: at <=1.5% risk and ~0.5 trades/day the worst plausible day is
    ~2 concurrent stops = 2-3%, below the firm's 5% cap, so it cannot bind (matches the
    prior MC finding in prop_risk_sweep.py)
  * prop risk-per-trade is 1.0% (config.prop.yaml), NOT the 0.75% config.yaml now runs

Two edge profiles, because a backtest is not a promise:
  * "backtest"  — the measured R distribution as-is
  * "haircut"   — every WINNER scaled to 70%, losers unchanged. A blunt, deliberately
                  pessimistic stand-in for live slippage/regime decay.

MEASUREMENT ONLY - no ledger charge.
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
from apex_quant.data import PointInTimeAccessor, ParquetStore, clean  # noqa: E402
from apex_quant.risk.manager import RiskManager  # noqa: E402

from run_portfolio_gate import (  # noqa: E402
    COMMON_PARAMS, DEFAULT_HOLDOUT_START, MIN_BARS, WARMUP, TrendBook, _utc,
)
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402

N_PATHS = 20000
MAX_TRADES = 2000          # generous cap; a phase that needs more has effectively failed
PHASE1, PHASE2 = 0.08, 0.05
FLOOR = -0.08
RNG = np.random.default_rng(42)


def book_r_multiples() -> tuple[np.ndarray, float]:
    """Per-trade R-multiples and trades/month, from the live-config backtest."""
    cfg = get_config()
    store = ParquetStore(cfg.store_path)
    holdout = _utc(DEFAULT_HOLDOUT_START)
    panel = {}
    for inst in EQUITY_CORE + [GOLD_ETC] + list(cfg.data.crypto) + FX_MAJORS_7:
        df = store.load(inst, "1d")
        if df.empty:
            continue
        df = clean(df)[lambda d: d.index < holdout]
        if len(df) >= MIN_BARS:
            panel[inst] = df
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    res = PortfolioBacktester(
        cfg, risk_manager=RiskManager(cfg.risk), exit_mode="managed",
        slot_allocation="expected_value",
    ).run(pits, TrendBook(panel, **{"carry_filter": False, **COMMON_PARAMS,
                                    "momentum_lookback": 252}).strategies(),
          timeframes={k: "1d" for k in panel}, warmup=WARMUP, periods_per_year=252)

    # R = trade P&L as a multiple of the risk budgeted AT THAT TRADE'S ENTRY.
    #
    # Normalising by INITIAL equity would inflate R badly: the backtest compounds to ~£241k,
    # so a late trade's £1,000 profit is only half the R of an identical early one. A prop
    # challenge does not compound over its short life, so R must be equity-relative or the
    # pass odds come out far too optimistic.
    risk_per_trade = cfg.risk.max_risk_per_trade
    eq = res.equity
    r_list = []
    for t in res.trades:
        ts = pd.Timestamp(t.entry_time)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        idx = eq.index.searchsorted(ts, side="right") - 1
        eq_at_entry = float(eq.iloc[max(0, min(idx, len(eq) - 1))])
        if eq_at_entry > 0:
            r_list.append(t.pnl / (risk_per_trade * eq_at_entry))
    r = np.asarray(r_list, dtype=float)
    months = len(res.equity) / 21.0
    return r, len(r) / months


def apply_profile(r: np.ndarray, profile: str) -> np.ndarray:
    if profile == "backtest":
        return r
    out = r.copy()
    out[out > 0] *= 0.70          # winners haircut 30%; losers unchanged
    return out


def run_phase(r_pool: np.ndarray, risk: float, target: float) -> tuple[float, np.ndarray]:
    """Vectorised: returns P(pass) and the trade count at which each path passed."""
    eq = np.zeros(N_PATHS)
    active = np.ones(N_PATHS, dtype=bool)
    passed = np.zeros(N_PATHS, dtype=bool)
    t_pass = np.full(N_PATHS, MAX_TRADES, dtype=int)

    for i in range(MAX_TRADES):
        if not active.any():
            break
        draw = RNG.choice(r_pool, size=int(active.sum()), replace=True) * risk
        eq[active] += draw
        hit = active & (eq >= target)
        passed[hit] = True
        t_pass[hit] = i + 1
        active = active & ~hit & (eq > FLOOR)
    return float(passed.mean()), t_pass[passed]


def main() -> int:
    r, tpm = book_r_multiples()
    print("=" * 88)
    print("PROP CHALLENGE — TIME TO PASS, from this book's ACTUAL trade distribution")
    print("=" * 88)
    print(f"trades sampled      {len(r)}")
    print(f"trades per month    {tpm:.1f}")
    print(f"win rate            {float((r > 0).mean())*100:.1f}%")
    print(f"mean R              {r.mean():+.3f}   median R {np.median(r):+.3f}")
    print(f"best / worst R      {r.max():+.2f} / {r.min():+.2f}")
    print(f"rules               phase1 +{PHASE1*100:.0f}%, phase2 +{PHASE2*100:.0f}%, "
          f"floor {FLOOR*100:.0f}% from phase start")

    for profile in ("backtest", "haircut"):
        pool = apply_profile(r, profile)
        print(f"\n--- edge profile: {profile}  (mean R {pool.mean():+.3f}) ---")
        print(f"{'risk':>6} {'P(ph1)':>7} {'P(ph2)':>7} {'P(both)':>8} "
              f"{'median months':>14} {'p90 months':>11} {'P(pass|2 tries)':>16}")
        for risk in (0.0075, 0.010, 0.0125, 0.015):
            p1, t1 = run_phase(pool, risk, PHASE1)
            p2, t2 = run_phase(pool, risk, PHASE2)
            p_both = p1 * p2
            if len(t1) and len(t2):
                med = (np.median(t1) + np.median(t2)) / tpm
                p90 = (np.percentile(t1, 90) + np.percentile(t2, 90)) / tpm
            else:
                med = p90 = float("nan")
            two = 1 - (1 - p_both) ** 2
            star = "  <- prop config" if abs(risk - 0.010) < 1e-9 else ""
            print(f"{risk*100:5.2f}% {p1*100:6.1f}% {p2*100:6.1f}% {p_both*100:7.1f}% "
                  f"{med:13.1f}m {p90:10.1f}m {two*100:15.1f}%{star}")

    print("\n" + "=" * 88)
    print("median months = 50% of PASSING paths take this long or less. It ignores paths that")
    print("blow the -8% floor, which is why P(both) matters more than the month count.")
    print("=" * 88)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
