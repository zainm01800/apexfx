"""Run THIS book against the actual account rules from the Prop Firm Match listing.

The rules were read off a screenshot supplied by the account owner (100K, 1-step, stocks).
They are entered here as data; anything unclear is flagged rather than guessed.

The reason this matters: the book's forward p95 1-year drawdown is 12.0% and its realised
backtest drawdown is 14.3%. Every account on that listing has a MAX LOSS between 3% and 8%.
That is the binding constraint, and it is far tighter than the -8% static floor assumed in all
the earlier prop simulations.

Modelled per account, walked on the REAL historical return sequence (no bootstrap — IID
resampling was shown to overstate pass rates by ~26 points):
  * profit target
  * max loss, STATIC (from starting balance) or TRAILING (from equity peak)
  * daily loss limit  <- the engine has NO daily-stop implementation; this quantifies the gap
  * profit split, for the actual take-home once funded

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

#: name -> (target, daily_loss or None, max_loss, "static"|"trailing", split, price_usd)
ACCOUNTS = {
    "FundedElite 6%":      (0.06, 0.03, 0.06, "static",   0.80, 3.00),
    "Orion Funded":        (0.04, 0.04, 0.08, "trailing", 0.80, 3.50),
    "Crypto Fund Trader":  (0.06, None, 0.03, "trailing", 0.80, 118.15),
    "FundedElite 12%":     (0.12, 0.04, 0.06, "static",   0.80, 221.40),
    "WSFunded 10%":        (0.10, 0.04, 0.06, "static",   0.80, 264.50),
}
MAX_DAYS = 252 * 3
CAPITAL = 100_000.0


def book_returns(risk: float = None) -> pd.Series:
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
    rc = cfg.risk if risk is None else cfg.risk.model_copy(
        update={"max_risk_per_trade": risk})
    res = PortfolioBacktester(cfg, risk_manager=RiskManager(rc), exit_mode="managed",
                              slot_allocation="expected_value").run(
        pits, TrendBook(panel, **{"carry_filter": False, **COMMON_PARAMS,
                                  "momentum_lookback": 252}).strategies(),
        timeframes={k: "1d" for k in panel}, warmup=WARMUP, periods_per_year=252)
    return res.returns.dropna(), res.metrics


def walk(r: np.ndarray, start: int, target, daily, maxloss, mode) -> tuple[str, int]:
    eq, peak = 0.0, 0.0
    for i in range(start, min(start + MAX_DAYS, len(r))):
        day = r[i]
        if daily is not None and day <= -daily:
            return "daily_breach", i - start + 1
        eq = (1.0 + eq) * (1.0 + day) - 1.0
        peak = max(peak, eq)
        limit = -maxloss if mode == "static" else peak - maxloss
        if eq <= limit:
            return "blown", i - start + 1
        if eq >= target:
            return "pass", i - start + 1
    return "stalled", MAX_DAYS


def main() -> int:
    r_s, metrics = book_returns()
    r = r_s.to_numpy()
    tpm = metrics["n_trades"] / (len(r) / 21.0)

    print("=" * 104, flush=True)
    print("YOUR BOOK vs THE ACTUAL LISTED ACCOUNTS (real sequence, 0.75% risk)")
    print("=" * 104, flush=True)
    print(f"book: {metrics['n_trades']} trades over {len(r)/252:.1f}y = "
          f"{tpm:.1f} trades/month | forward p95 DD 12.0% | realised maxDD 14.3%")
    print(f"worst single DAY in the book: {r.min()*100:.2f}%   "
          f"worst 5-day run: {pd.Series(r).rolling(5).sum().min()*100:.2f}%\n")

    print(f"{'account':<21} {'target':>7} {'maxloss':>8} {'type':>9} {'daily':>6} | "
          f"{'PASS':>6} {'blown':>6} {'dailyX':>7} {'stall':>6} {'median':>7} {'£/mo funded':>12}")

    for name, (tgt, daily, ml, mode, split, price) in ACCOUNTS.items():
        out = [walk(r, s, tgt, daily, ml, mode) for s in range(0, len(r) - 252, 5)]
        n = len(out)
        p = sum(1 for o, _ in out if o == "pass") / n
        b = sum(1 for o, _ in out if o == "blown") / n
        dx = sum(1 for o, _ in out if o == "daily_breach") / n
        st = sum(1 for o, _ in out if o == "stalled") / n
        passes = [d for o, d in out if o == "pass"]
        med = float(np.median(passes)) / 21.0 if passes else float("nan")
        # once funded: monthly profit x split
        gbp = 587.0 * split
        print(f"{name:<21} {tgt*100:6.0f}% {ml*100:7.0f}% {mode:>9} "
              f"{(f'{daily*100:.0f}%' if daily else 'none'):>6} | "
              f"{p*100:5.1f}% {b*100:5.1f}% {dx*100:6.1f}% {st*100:5.1f}% "
              f"{med:6.1f}m {gbp:11.0f}")

    print("\n" + "=" * 104)
    print("NOTE: 'dailyX' = daily-loss breach. The engine has NO daily-stop implementation")
    print("(declared in config.prop.yaml, never built), so every one of those is an account")
    print("the live system would actually have lost.")
    print("=" * 104)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
