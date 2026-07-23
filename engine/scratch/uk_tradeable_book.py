"""What does the book earn using ONLY instruments a UK retail IBKR account can actually trade?

Per data_store/ucits_mapping_2026-07-20.md and the KID_BLOCKED list in run_ibkr_mirror.py:
  * Plain shares/ADRs      -> TRADEABLE (not PRIIPs products).
  * US-domiciled ETFs      -> BLOCKED (PRIIPs/KID, IBKR error 201). Book H still holds FIVE:
                              XLK, XLE, XBI, SMH, SOXX.
  * UCITS ETFs (.L)        -> TRADEABLE. Book H already uses ISWD.L, ISDU.L, ISDE.L, SGLD.L.
  * Spot crypto            -> BLOCKED for UK retail at IBKR (FCA). The mapping doc calls the
                              crypto sleeve "paper-only until a separate venue decision".
  * Spot FX (IDEALPRO)     -> TRADEABLE.

So the honest UK-retail-at-IBKR book is 12 shares + 4 UCITS + 7 FX = 23 instruments, and it
loses the entire crypto sleeve (27% of trades, +£30,510 gross in the full book).

Three variants measured, all at the live 0.75% config:
  A. full 39            — what the gate certified, NOT all tradeable
  B. UK strict (23)     — IBKR UK retail only: no US ETFs, no crypto
  C. UK + crypto (34)   — if crypto moves to a different venue (Kraken/Coinbase etc.)

MEASUREMENT ONLY - no ledger charge. Adopting any of these is a pre-registered universe change.
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

#: US-domiciled ETFs still in Book H — PRIIPs/KID blocked for UK retail (IBKR error 201).
US_ETF_BLOCKED = {"XLK", "XLE", "XBI", "SMH", "SOXX"}


def fwd(returns: pd.Series, wall: float = 0.12, seed: int = 42) -> dict:
    r = returns.dropna().to_numpy()
    rng = np.random.default_rng(seed)
    eq = np.cumprod(1.0 + rng.choice(r, size=(20000, 252), replace=True), axis=1)
    pk = np.maximum.accumulate(eq, axis=1)
    dd = ((pk - eq) / pk).max(axis=1)
    return {"p95": float(np.percentile(dd, 95)), "breach": float((dd > wall).mean())}


def main() -> int:
    cfg = get_config()
    store = ParquetStore(cfg.store_path)
    holdout = _utc(DEFAULT_HOLDOUT_START)

    equities = EQUITY_CORE + [GOLD_ETC]
    crypto = list(cfg.data.crypto)
    uk_equities = [i for i in equities if i not in US_ETF_BLOCKED]

    universes = {
        "A. full 39 (certified)": equities + crypto + FX_MAJORS_7,
        "B. UK strict (no US ETF, no crypto)": uk_equities + FX_MAJORS_7,
        "C. UK + crypto elsewhere": uk_equities + crypto + FX_MAJORS_7,
    }

    params = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}

    print("=" * 100, flush=True)
    print("UK-TRADEABLE BOOK — what can a UK retail IBKR account ACTUALLY run?", flush=True)
    print(f"  blocked US ETFs still in Book H: {', '.join(sorted(US_ETF_BLOCKED))}", flush=True)
    print(f"  spot crypto: BLOCKED for UK retail at IBKR (FCA) — {len(crypto)} instruments",
          flush=True)
    print("=" * 100, flush=True)
    print(f"{'universe':<38} {'n':>3} {'CAGR':>7} {'£/mo':>7} {'Sharpe':>7} {'btDD':>6} "
          f"{'fwdP95':>7} {'P(>12%)':>8} {'trades':>7}", flush=True)

    for label, names in universes.items():
        panel = {}
        for inst in names:
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
        ).run(pits, TrendBook(panel, **params).strategies(),
              timeframes={k: "1d" for k in panel}, warmup=WARMUP, periods_per_year=252)

        m, eq = res.metrics, res.equity
        cagr = float(eq.iloc[-1] / eq.iloc[0]) ** (252.0 / len(eq)) - 1
        d = fwd(res.returns)
        print(f"{label:<38} {len(panel):>3} {cagr*100:6.2f}% {cagr*100000/12:7.0f} "
              f"{m['sharpe']:7.3f} {m['max_drawdown']*100:5.1f}% {d['p95']*100:6.1f}% "
              f"{d['breach']*100:7.1f}% {m['n_trades']:7d}", flush=True)

        if label.startswith("B"):
            gbp = cagr * 100000 / 12
            print(f"\n  -> capital needed for £700/mo at this CAGR: "
                  f"£{700*12/cagr:,.0f}" if cagr > 0 else "  -> CAGR <= 0", flush=True)
            print(f"  -> capital needed for £1,000/mo:               "
                  f"£{1000*12/cagr:,.0f}\n" if cagr > 0 else "", flush=True)

    print("=" * 100, flush=True)
    print("Adopting any of these is a PRE-REGISTERED universe change, not an edit.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
