"""SCREEN: residual (idiosyncratic) momentum vs total-return momentum.

Motivation is theory, not parameter search. Blitz/Huij/Martens and Blitz/Hanauer/Vidojevic
report that ranking on the residual from a factor regression rather than on total return
roughly DOUBLES the momentum Sharpe (gross monthly 0.48 vs 0.25) - not by earning more, but
by roughly halving volatility, because the common market factor is removed from the ranking.

Why this matters for THIS book specifically: the breadth sweep showed extra positions have
NEGATIVE edge, which is what you see when every "independent" bet is really the same bet -
market beta. Residualising removes the shared factor, so bets should become genuinely
independent. If that is right, residual momentum should raise Sharpe AND make breadth useful
again, fixing the two findings together.

Also tests Daniel-Moskowitz style crash protection: momentum crashes are forecastable, occurring
in "panic" states (market below trend + high realised vol). Their result is that scaling on
variance ALONE - which is exactly what the portfolio_vol_target overlay did, and why it failed -
is insufficient; the mean forecast is what adds value.

THIS IS A SCREEN, NOT A GATE. Costs are applied at the engine's own equity rate, but there are
no stops, no slot caps, and no CPCV/DSR/PBO here. A positive result means "build it properly in
the engine and pre-register it", nothing more. No ledger charge.
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

from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import ParquetStore, clean  # noqa: E402

from run_portfolio_gate import DEFAULT_HOLDOUT_START, MIN_BARS, _utc  # noqa: E402
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402

# The engine's own equity cost: (0.5*spread_bps + slippage_bps)/1e4 per fill = 2bps,
# i.e. 4bps round trip. Applied per unit of turnover (one-way), so 2bps.
COST_BPS_ONE_WAY = 2.0
LOOKBACK = 252      # 12 months
SKIP = 21           # skip most recent month (short-term reversal), standard 12-1
VOL_WIN = 63
REBAL = 21          # monthly rebalance -> low turnover, unlike the 2,218%/yr pandas toy


def load_panel() -> pd.DataFrame:
    cfg = get_config()
    store = ParquetStore(cfg.store_path)
    holdout = _utc(DEFAULT_HOLDOUT_START)
    closes = {}
    for inst in EQUITY_CORE + [GOLD_ETC] + list(cfg.data.crypto) + FX_MAJORS_7:
        df = store.load(inst, "1d")
        if df.empty:
            continue
        df = clean(df)
        df = df[df.index < holdout]
        if len(df) >= MIN_BARS:
            closes[inst] = df["close"]
    return pd.DataFrame(closes).sort_index()


def stats(r: pd.Series, label: str, rf: float = 0.0) -> dict:
    r = r.dropna()
    ann = float(r.mean() * 252)
    vol = float(r.std(ddof=1) * np.sqrt(252))
    sh = (ann - rf) / vol if vol > 0 else 0.0
    eq = (1 + r).cumprod()
    dd = float(abs(((eq - eq.cummax()) / eq.cummax()).min()))
    yrs = len(r) / 252
    cagr = float(eq.iloc[-1]) ** (1 / yrs) - 1 if yrs > 0 else 0.0
    # forward 1-year p95 drawdown, same bootstrap the gates use
    rng = np.random.default_rng(42)
    sim = np.cumprod(1 + rng.choice(r.to_numpy(), size=(20000, 252), replace=True), axis=1)
    pk = np.maximum.accumulate(sim, axis=1)
    fdd = ((pk - sim) / pk).max(axis=1)
    p95 = float(np.percentile(fdd, 95))
    print(f"  {label:<34} Sharpe {sh:6.3f}  CAGR {cagr*100:6.2f}%  "
          f"£{cagr*100000/12:6.0f}/mo  btDD {dd*100:5.1f}%  fwdP95 {p95*100:5.1f}%")
    return {"sharpe": sh, "cagr": cagr, "gbp_mo": cagr * 100000 / 12,
            "bt_dd": dd, "fwd_p95": p95}


def backtest(rank_score: pd.DataFrame, rets: pd.DataFrame, vol: pd.DataFrame,
             top_n: int, gate: pd.Series | None = None) -> pd.Series:
    """Long top-N by score, inverse-vol weighted, rebalanced every REBAL bars, costed."""
    sel = rank_score.rank(axis=1, ascending=False) <= top_n
    w = sel.astype(float) * (1.0 / vol.clip(lower=0.05))
    w = w.div(w.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
    # hold weights between rebalances
    mask = pd.Series(False, index=w.index)
    mask.iloc[::REBAL] = True
    w = w.where(mask, np.nan).ffill().fillna(0.0)
    if gate is not None:
        w = w.mul(gate.reindex(w.index).ffill().fillna(1.0), axis=0)
    turnover = w.diff().abs().sum(axis=1)
    gross = (w.shift(1) * rets).sum(axis=1)
    return gross - turnover * COST_BPS_ONE_WAY / 1e4


def main() -> int:
    close = load_panel()
    rets = close.pct_change().fillna(0.0)
    vol = rets.rolling(VOL_WIN).std() * np.sqrt(252)

    print("=" * 92)
    print(f"SCREEN — residual vs total momentum | {close.shape[1]} instruments | "
          f"{len(close)} bars ({len(close)/252:.1f}y) | costs {COST_BPS_ONE_WAY}bps/side")
    print("=" * 92)

    # --- market factor = equal-weight cross-sectional mean return ------------------
    mkt = rets.mean(axis=1)

    # --- TOTAL momentum: 12-1 cumulative return, vol-normalised -------------------
    total_mom = (close.shift(SKIP) / close.shift(SKIP + LOOKBACK) - 1.0) / vol.clip(lower=0.05)

    # --- RESIDUAL momentum: rolling beta to the market, cumulate the residual -----
    # beta_i = cov(r_i, mkt)/var(mkt) over LOOKBACK; residual r_i - beta_i*mkt.
    var_m = mkt.rolling(LOOKBACK).var()
    resid_cum, resid_vol = {}, {}
    for c in rets.columns:
        beta = rets[c].rolling(LOOKBACK).cov(mkt) / var_m
        resid = rets[c] - beta * mkt
        resid_cum[c] = resid.shift(SKIP).rolling(LOOKBACK).sum()
        resid_vol[c] = resid.rolling(LOOKBACK).std() * np.sqrt(252)
    resid_cum = pd.DataFrame(resid_cum)
    resid_vol = pd.DataFrame(resid_vol)
    # Blitz et al. standardise the residual by its OWN volatility - that is the step
    # that halves strategy vol and roughly doubles Sharpe.
    resid_mom = resid_cum / resid_vol.clip(lower=0.05)

    # --- Daniel-Moskowitz panic-state gate ----------------------------------------
    # Crashes cluster where the market is below trend AND realised vol is high. Their
    # point is that scaling on VARIANCE ALONE is insufficient - which is exactly what
    # the portfolio_vol_target overlay did, and why it failed.
    mkt_eq = (1 + mkt).cumprod()
    bear = mkt_eq < mkt_eq.rolling(252).mean()
    mkt_vol = mkt.rolling(VOL_WIN).std() * np.sqrt(252)
    panic = (bear & (mkt_vol > mkt_vol.rolling(252).median())).shift(1).fillna(False)
    gate = pd.Series(np.where(panic, 0.5, 1.0), index=rets.index)

    out = {}
    for top_n in (3, 5, 8, 12):
        print(f"\n--- top {top_n} ---")
        out[f"total_{top_n}"] = stats(backtest(total_mom, rets, vol, top_n),
                                      f"total-return momentum (top {top_n})")
        out[f"resid_{top_n}"] = stats(backtest(resid_mom, rets, vol, top_n),
                                      f"RESIDUAL momentum (top {top_n})")
        out[f"resid_gate_{top_n}"] = stats(backtest(resid_mom, rets, vol, top_n, gate=gate),
                                           f"RESIDUAL + panic gate (top {top_n})")

    print("\n" + "=" * 92)
    best = max(out.items(), key=lambda kv: kv[1]["sharpe"])
    print(f"BEST SHARPE: {best[0]} -> {best[1]['sharpe']:.3f}, "
          f"£{best[1]['gbp_mo']:.0f}/mo, fwd p95 DD {best[1]['fwd_p95']*100:.1f}%")
    hit = {k: v for k, v in out.items() if v["gbp_mo"] >= 800 and v["fwd_p95"] <= 0.11}
    print(f"Configs at >=£800/mo INSIDE an 11% wall: {len(hit)}")
    for k, v in sorted(hit.items(), key=lambda kv: -kv[1]["sharpe"]):
        print(f"   {k}: £{v['gbp_mo']:.0f}/mo  Sharpe {v['sharpe']:.3f}  "
              f"fwd p95 {v['fwd_p95']*100:.1f}%")
    print("\nEngine baseline for reference: Sharpe 0.922, £413/mo, fwd p95 8.2%")
    print("A SCREEN ONLY — no stops, no slot caps, no CPCV/DSR/PBO. Promising means "
          "'build it in the engine and pre-register', not 'adopt'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
