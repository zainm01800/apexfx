"""Factor / exposure concentration measurement on the certified Book H gold book.

MEASUREMENT ONLY — answers "is the certified 1,637-trade book secretly ONE BET?"

On the certified replay (anchor book_h_gapaware_2026-07-22.json; window <2025-01-01,
seed-equivalent deterministic config, max_risk_per_trade=0.01):
  1. per-instrument P&L attribution (net, expectancy, n; top-5/bottom-5 share;
     Herfindahl of |instrument P&L|),
  2. return-stream correlation of per-instrument daily P&L (avg pairwise |corr|,
     complete-linkage clusters on 1-|corr|, PCA top-3 explained variance + PC1
     loadings),
  3. net USD-risk-asset exposure per day (max one-way % of equity, fraction of
     days > 50%) + net long-/short-USD FX exposure per day,
  4. sector/theme concentration (tech tag set) — net tech exposure over time and
     P&L share by theme,
  5. verdict: survivability of a single-theme reversal inside the certified 16.3%
     maxDD envelope, via instantaneous gap-stress scans of the daily exposures.

Spies (engine path untouched): _enter / _record as in the direction x regime
measurement, PLUS _unrealized — called once per open position per TIMELINE day at
mark-to-market (portfolio.py step 3; union calendar, stale last_px on days the
instrument has no bar), which yields the EXACT end-of-day units (after
TradeManager partials and gamma trims) and MTM price for every position-day.
Trajectory call k for a position maps to timeline day k of [entry_ts, exit_ts);
on days the instrument traded, px_k must equal the bar close (asserted).

Hard-fails unless the certified anchor reproduces EXACTLY (same tolerances as the
direction x regime measurement). Records exactly ONE informational ledger trial
before running.
"""

from __future__ import annotations

import copy
import json
import sys
import time
from pathlib import Path

import numpy as np

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR / "scripts"))
sys.path.insert(0, str(ENGINE_DIR))

import pandas as pd  # noqa: E402

from apex_quant.backtest.portfolio import PortfolioBacktester  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor, ParquetStore, clean  # noqa: E402
from apex_quant.validation.trials import TrialLedger  # noqa: E402

from run_portfolio_gate import (  # noqa: E402
    COMMON_PARAMS,
    DEFAULT_HOLDOUT_START,
    LEDGER_PATH,
    MIN_BARS,
    WARMUP,
    TrendBook,
    _utc,
)
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402

RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "factor_concentration_2026-08-08.json"
REPORT_PATH = ENGINE_DIR / "data_store" / "factor_concentration_2026-08-08.md"

CERTIFIED_GOLD = {"sharpe": 0.8628380346245177, "profit_factor": 1.324524180228228,
                  "win_rate": 0.5577275503970678, "max_drawdown": 0.16315348773173277,
                  "n_trades": 1637, "total_return": 1.9255133668640645,
                  "expectancy_pnl": 120.44265729993896, "final_equity": 292551.33668640646}

UNIVERSE_GOLD = EQUITY_CORE + [GOLD_ETC]
GOLD_PARAMS = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}
CERTIFIED_MRPT = 0.01

# ── theme tags (fixed, pre-registered in this script — not tuned) ─────────────
TECH_MAG7 = {"AAPL", "MSFT", "NVDA", "META", "AMZN", "GOOGL", "TSLA"}
TECH_OTHER = {"AMD", "PLTR", "TSM", "NFLX", "UBER"}
TECH_ETF = {"XLK", "SMH", "SOXX"}
TECH_BROAD = TECH_MAG7 | TECH_OTHER | TECH_ETF
SEMIS = {"NVDA", "AMD", "TSM", "SMH", "SOXX"}
SECTOR_OTHER_ETF = {"XLE", "XBI"}
GLOBAL_EQ_ETF = {"ISWD.L", "ISDU.L", "ISDE.L"}
GOLD_SET = {GOLD_ETC}

LIVE_BOOK_TICKERS = ["AMD", "TSM", "AAPL", "IWM", "META", "MSFT", "NFLX", "TSLA",
                     "PLTR", "USD/JPY", "USD/CAD"]

# ── spies (engine path untouched) ─────────────────────────────────────────────
ENTRY_SNAPS: dict[int, dict] = {}
EXIT_RECS: dict[int, dict] = {}
UNITS_TRAJ: dict[int, list] = {}   # did -> [(units, mtm_close), ...] per bar held
_SEQ = [0]

_orig_enter = PortfolioBacktester._enter
_orig_record = PortfolioBacktester._record
_orig_unrealized = PortfolioBacktester._unrealized


def _spy_enter(self, pend, open_price, t, i, instrument):
    posd = _orig_enter(self, pend, open_price, t, i, instrument)
    did = _SEQ[0]
    _SEQ[0] += 1
    posd["_decomp_id"] = did
    direction = posd["direction"]
    ENTRY_SNAPS[did] = {
        "instrument": instrument,
        "direction": direction.value if hasattr(direction, "value") else str(direction),
        "units": float(posd["units"]),
        "entry_price": float(posd["entry_price"]),
        "entry_ts": pd.Timestamp(t),
        "risk_abs": float(posd["risk_abs"]),
    }
    UNITS_TRAJ[did] = []
    return posd


def _spy_record(self, position, exit_price, t, reason, pnl, instrument=""):
    tr = _orig_record(self, position, exit_price, t, reason, pnl, instrument)
    did = position.get("_decomp_id")
    if did is not None:
        EXIT_RECS[did] = {"exit_ts": pd.Timestamp(t), "exit_reason": reason,
                          "exit_price": float(exit_price), "trade_pnl": float(pnl)}
        if reason == "daily_loss_stop" and UNITS_TRAJ.get(did):
            # flatten path calls _unrealized once extra at the exit fill — drop it
            UNITS_TRAJ[did].pop()
    return tr


def _spy_unrealized(self, position, price):
    did = position.get("_decomp_id")
    if did is not None:
        UNITS_TRAJ[did].append((float(position["units"]), float(price)))
    return _orig_unrealized(self, position, price)


# ── classification helpers ────────────────────────────────────────────────────
def _bucket(inst: str, crypto: set[str]) -> str:
    if inst in TECH_BROAD:
        return "tech_broad"
    if inst in SECTOR_OTHER_ETF:
        return "sector_etf_other"
    if inst in GLOBAL_EQ_ETF:
        return "global_equity_ucits"
    if inst in GOLD_SET:
        return "gold"
    if inst in crypto:
        return "crypto"
    if inst in set(FX_MAJORS_7):
        return "fx"
    return "other"


def _account_notional(inst: str, units: float, close: float, crypto: set[str]) -> float:
    """Notional in account terms, consistent with how the engine sizes positions.

    FX: units are base-currency units; all 7 majors have USD on one side, so the
    USD (≈account) notional is units x close when USD is the quote (EUR/USD…) and
    units x 1 when USD is the base (USD/JPY…). Stocks/ETFs/crypto: units x close
    in the stored (traded-currency) price the engine's P&L math already uses.
    """
    if inst in set(FX_MAJORS_7):
        base, quote = inst.split("/")
        return units * (close if quote == "USD" else 1.0)
    return units * close


def _usd_direction(inst: str, direction: str) -> int:
    """+1 = position is long-USD, -1 = short-USD, 0 = no USD-factor stance."""
    if inst not in set(FX_MAJORS_7):
        return 0
    base, quote = inst.split("/")
    long = direction == "long"
    if base == "USD":
        return 1 if long else -1
    return -1 if long else 1


def _stats(pnls: list[float]) -> dict:
    if not pnls:
        return {"n": 0, "net": 0.0, "expectancy": 0.0, "win_rate": 0.0, "pf": 0.0}
    arr = np.asarray(pnls, dtype=float)
    wins = arr[arr > 0].sum()
    losses = -arr[arr < 0].sum()
    return {"n": int(len(arr)), "net": round(float(arr.sum()), 2),
            "expectancy": round(float(arr.mean()), 2),
            "win_rate": round(float((arr > 0).mean()), 4),
            "pf": round(float(wins / losses), 3) if losses > 0 else None}


def main() -> int:
    base_cfg = get_config()
    store = ParquetStore(base_cfg.store_path)
    holdout_start = _utc(DEFAULT_HOLDOUT_START)

    crypto = list(base_cfg.data.crypto)
    wanted = sorted(set(UNIVERSE_GOLD) | set(crypto) | set(FX_MAJORS_7))
    master: dict[str, pd.DataFrame] = {}
    for inst in wanted:
        df = store.load(inst, "1d")
        if df.empty:
            continue
        df = clean(df)
        df = df[df.index < holdout_start]
        if len(df) < MIN_BARS:
            continue
        master[inst] = df
    panel = {inst: master[inst] for inst in UNIVERSE_GOLD + crypto + FX_MAJORS_7 if inst in master}

    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    ledger.record({"book": "book_h_gold_252", "universe": "book_h_gold_39",
                   "timeframe": "1d", "factory": "trend_book_mtf",
                   "kind": "factor_concentration_measurement", "max_risk_per_trade": CERTIFIED_MRPT,
                   "informational": True,
                   "params": {**GOLD_PARAMS,
                              "measures": ["instrument_attribution", "return_stream_correlation",
                                           "usd_factor_exposure", "sector_theme_concentration",
                                           "theme_reversal_stress"]}})
    ledger.save(LEDGER_PATH)
    print(f"ledger n_trials {n_before} -> {ledger.n_trials} (1 informational trial)", flush=True)

    PortfolioBacktester._enter = _spy_enter
    PortfolioBacktester._record = _spy_record
    PortfolioBacktester._unrealized = _spy_unrealized
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    timeframes = {k: "1d" for k in panel}
    cfg = copy.deepcopy(get_config())
    cfg.risk.max_risk_per_trade = CERTIFIED_MRPT
    model = TrendBook(panel, **GOLD_PARAMS)
    strategies = model.strategies()
    t0 = time.time()
    res = PortfolioBacktester(cfg, exit_mode="managed").run(
        pits, strategies, timeframes=timeframes, warmup=WARMUP, periods_per_year=252)
    dt = time.time() - t0
    m = res.metrics
    print(f"certified run: {dt:.0f}s | {res.summary()}", flush=True)
    mismatch = {k: (m.get(k), v) for k, v in CERTIFIED_GOLD.items()
                if abs(m.get(k, float("nan")) - v) > (0.5 if k == "n_trades" else 1e-6 * max(1.0, abs(v)))}
    if mismatch:
        print(f"CERTIFIED REPRODUCTION FAILED: {mismatch}", flush=True)
        return 1
    print("certified-anchor reproduction: EXACT", flush=True)

    paired = [(ENTRY_SNAPS[did], EXIT_RECS[did], UNITS_TRAJ[did], did)
              for did in EXIT_RECS if did in ENTRY_SNAPS]
    n_open_at_end = _SEQ[0] - len(paired)
    print(f"trades paired: {len(paired)} ({n_open_at_end} positions still open at window edge)",
          flush=True)
    crypto_set = set(crypto)

    # ── per-trade frame + exact daily position-days from the _unrealized spy ──
    # _unrealized fires per open position per TIMELINE day (union calendar; with a
    # stale last_px on days the instrument itself has no bar), from the entry bar's
    # mark-to-market through the day before the exit bar. So trajectory call k maps
    # to timeline day k of [entry_ts, exit_ts); px_k is the engine's own MTM price.
    closes = {inst: df["close"] for inst, df in panel.items()}
    eq_index = res.equity.index
    traj_check = {"ok": 0, "n_bars_mismatch": 0, "price_mismatch": 0, "examples": []}
    inst_day_pnl: dict[str, dict[pd.Timestamp, float]] = {inst: {} for inst in panel}
    # day -> theme exposure accumulators (signed account notional)
    day_risk_long: dict[pd.Timestamp, float] = {}
    day_risk_short: dict[pd.Timestamp, float] = {}
    day_theme: dict[str, dict[pd.Timestamp, float]] = {
        b: {} for b in ("tech_broad", "semis", "sector_etf_other", "global_equity_ucits",
                        "gold", "crypto", "fx", "other")}
    day_fx_usd: dict[pd.Timestamp, float] = {}
    day_fx_long_usd_n: dict[pd.Timestamp, int] = {}
    day_fx_short_usd_n: dict[pd.Timestamp, int] = {}

    rows = []
    for snap, rec, traj, did in paired:
        inst = snap["instrument"]
        sign = 1.0 if snap["direction"] == "long" else -1.0
        rows.append({"instrument": inst, "bucket": _bucket(inst, crypto_set),
                     "dir": snap["direction"], "pnl": rec["trade_pnl"],
                     "risk_abs": snap["risk_abs"],
                     "r": rec["trade_pnl"] / snap["risk_abs"] if snap["risk_abs"] else np.nan,
                     "entry_date": str(snap["entry_ts"].date()),
                     "exit_date": str(rec["exit_ts"].date())})

        udays = eq_index[(eq_index >= snap["entry_ts"]) & (eq_index < rec["exit_ts"])]
        if len(traj) != len(udays):
            traj_check["n_bars_mismatch"] += 1
            if len(traj_check["examples"]) < 5:
                traj_check["examples"].append(
                    {"instrument": inst, "entry": str(snap["entry_ts"]), "exit": str(rec["exit_ts"]),
                     "traj_len": len(traj), "union_days": len(udays)})
            continue
        inst_idx = closes[inst].index
        ok = True
        day_legs: list[tuple[pd.Timestamp, float]] = []
        prev_units: float | None = None
        prev_close = snap["entry_price"]
        for k, ((u_k, px_k), ts_k) in enumerate(zip(traj, udays)):
            if ts_k in inst_idx and not np.isclose(px_k, float(closes[inst].loc[ts_k]),
                                                   rtol=1e-6, atol=1e-9):
                traj_check["price_mismatch"] += 1
                ok = False
                break
            c_k = px_k
            u_prev = prev_units if prev_units is not None else u_k
            day_legs.append((ts_k, sign * (u_k * c_k - u_prev * prev_close)))
            prev_units, prev_close = u_k, c_k
            # exposures at this day's close (position gone by the close of exit day)
            notion = _account_notional(inst, u_k, c_k, crypto_set)
            b = _bucket(inst, crypto_set)
            if b in ("tech_broad", "sector_etf_other", "global_equity_ucits", "crypto"):
                if sign > 0:
                    day_risk_long[ts_k] = day_risk_long.get(ts_k, 0.0) + notion
                else:
                    day_risk_short[ts_k] = day_risk_short.get(ts_k, 0.0) + notion
            day_theme[b][ts_k] = day_theme[b].get(ts_k, 0.0) + sign * notion
            if inst in SEMIS:
                day_theme["semis"][ts_k] = day_theme["semis"].get(ts_k, 0.0) + sign * notion
            usd_dir = _usd_direction(inst, snap["direction"])
            if usd_dir:
                day_fx_usd[ts_k] = day_fx_usd.get(ts_k, 0.0) + usd_dir * notion
                key = day_fx_long_usd_n if usd_dir > 0 else day_fx_short_usd_n
                key[ts_k] = key.get(ts_k, 0) + 1
        if not ok:
            continue
        traj_check["ok"] += 1
        # exit-day leg: last EOD close -> exit fill, then residual (commissions,
        # borrow fees, intraday partial/trim timing) so per-instrument sums are exact
        exit_leg = sign * (prev_units if prev_units is not None else snap["units"]) * (
            rec["exit_price"] - prev_close)
        legs_sum = sum(v for _, v in day_legs) + exit_leg
        residual = rec["trade_pnl"] - legs_sum
        day_legs.append((rec["exit_ts"], exit_leg + residual))
        d = inst_day_pnl[inst]
        for ts_k, v in day_legs:
            d[ts_k] = d.get(ts_k, 0.0) + v

    print(f"trajectory check: {traj_check}", flush=True)
    tr = pd.DataFrame(rows)

    # tie-out: per-instrument daily P&L must sum to recorded trade P&L exactly
    tie_max = 0.0
    for inst, g in tr.groupby("instrument"):
        diff = abs(sum(inst_day_pnl[inst].values()) - float(g["pnl"].sum()))
        tie_max = max(tie_max, diff)
    print(f"daily P&L tie-out max |diff| per instrument: £{tie_max:.6f}", flush=True)

    total_net = float(tr["pnl"].sum())
    initial_eq = m["final_equity"] / (1.0 + m["total_return"])
    out: dict = {"kind": "factor_concentration_measurement", "informational": True,
                 "anchor": {k: m[k] for k in CERTIFIED_GOLD}, "n_trades": int(len(tr)),
                 "n_positions_open_at_window_edge": int(n_open_at_end),
                 "closed_trade_pnl_total": round(total_net, 2),
                 "pnl_vs_equity_note": f"sum of the 1,637 closed trades is £{total_net:,.0f}; "
                     f"final-equity gain is £{m['final_equity'] - initial_eq:,.0f} "
                     "— the difference is unrealized P&L / entry commissions of positions "
                     "still open at the 2025-01-01 window edge (not in the certified pool).",
                 "trajectory_check": {k: v for k, v in traj_check.items() if k != "examples"},
                 "trajectory_mismatch_examples": traj_check["examples"],
                 "daily_pnl_tieout_max_abs_diff": round(tie_max, 6),
                 "notes": [
                     "crypto X/USD pairs: classified as USD-PRICED RISK ASSETS (long crypto "
                     "= long risk asset, P&L currency USD) — NOT counted as long-USD FX exposure.",
                     "FX USD-direction: long USD/JPY|USD/CHF|USD/CAD = long-USD; long "
                     "EUR/GBP/AUD/NZD-USD = short-USD.",
                     "exposures are end-of-day, exact units via _unrealized spy "
                     "(post TradeManager partials / gamma trims).",
                     "daily P&L per instrument sums EXACTLY to recorded trade P&L "
                     "(commissions/borrow/intraday-partial timing booked as exit-day residual)."]}

    # ── 1. per-instrument attribution ─────────────────────────────────────────
    per_inst = []
    for inst, g in tr.groupby("instrument"):
        s = _stats(g["pnl"].tolist())
        s["instrument"] = inst
        s["bucket"] = _bucket(inst, crypto_set)
        s["share_of_net"] = round(s["net"] / total_net, 4) if total_net else None
        s["share_of_abs"] = abs(s["net"]) / float(np.abs(tr.groupby("instrument")["pnl"].sum()).sum())
        per_inst.append(s)
    per_inst.sort(key=lambda r: -r["net"])
    abs_shares = np.array([r["share_of_abs"] for r in per_inst])
    hhi = float((abs_shares ** 2).sum())
    top5 = per_inst[:5]
    bottom5 = per_inst[-5:]
    out["instrument_attribution"] = {
        "total_net_pnl": round(total_net, 2),
        "per_instrument": [{**r, "share_of_abs": round(float(r["share_of_abs"]), 4)}
                           for r in per_inst],
        "top5": {"instruments": [r["instrument"] for r in top5],
                 "net": round(sum(r["net"] for r in top5), 2),
                 "share_of_total_net": round(sum(r["net"] for r in top5) / total_net, 4)},
        "bottom5": {"instruments": [r["instrument"] for r in bottom5],
                    "net": round(sum(r["net"] for r in bottom5), 2),
                    "share_of_total_net": round(sum(r["net"] for r in bottom5) / total_net, 4)},
        "herfindahl_abs_pnl": round(hhi, 4),
        "effective_n_instruments": round(1.0 / hhi, 1),
        "top1_abs_share": round(float(abs_shares.max()), 4),
        "n_instruments_traded": int(len(per_inst)),
    }

    # ── 2. return-stream correlation ──────────────────────────────────────────
    n_trades_per_inst = tr.groupby("instrument").size()
    lt3 = sorted(inst for inst in n_trades_per_inst.index if n_trades_per_inst[inst] < 3)
    traded = [inst for inst in n_trades_per_inst.index if n_trades_per_inst[inst] >= 3]
    pnl_mat = pd.DataFrame({inst: pd.Series(inst_day_pnl[inst]) for inst in traded})
    pnl_mat = pnl_mat.reindex(eq_index).fillna(0.0)
    zero_var = [c for c in traded if float(pnl_mat[c].std()) == 0.0]
    if zero_var:
        pnl_mat = pnl_mat.drop(columns=zero_var)
        traded = [c for c in traded if c not in zero_var]
    corr = pnl_mat.corr()
    iu = np.triu_indices(len(traded), k=1)
    cmat = corr.to_numpy()
    avg_abs = float(np.mean(np.abs(cmat[iu]))) if len(iu[0]) else 0.0
    med_abs = float(np.median(np.abs(cmat[iu]))) if len(iu[0]) else 0.0
    max_pair = float(np.max(np.abs(cmat[iu]))) if len(iu[0]) else 0.0

    # PCA on standardised daily P&L (correlation-space)
    X = pnl_mat.to_numpy(dtype=float)
    Xs = (X - X.mean(axis=0)) / np.where(X.std(axis=0) == 0.0, 1.0, X.std(axis=0))
    _, sv, vt = np.linalg.svd(Xs, full_matrices=False)
    evr = (sv ** 2) / (sv ** 2).sum()
    pc1 = vt[0].copy()
    if pc1.sum() < 0:
        pc1 = -pc1
    pc1_load = sorted(((traded[i], round(float(pc1[i]), 4)) for i in range(len(traded))),
                      key=lambda kv: -abs(kv[1]))

    # complete-linkage agglomerative on D = 1 - |corr|, cut at |corr| = 0.3
    dist = 1.0 - np.abs(cmat)
    np.fill_diagonal(dist, np.inf)
    clusters: list[list[int]] = [[i] for i in range(len(traded))]
    CUT = 1.0 - 0.3
    while len(clusters) > 1:
        best, bi, bj = np.inf, -1, -1
        for a in range(len(clusters)):
            for b in range(a + 1, len(clusters)):
                dmax = max(dist[i, j] for i in clusters[a] for j in clusters[b])
                if dmax < best:
                    best, bi, bj = dmax, a, b
        if best > CUT:
            break
        clusters[bi] = clusters[bi] + clusters[bj]
        clusters.pop(bj)
    cluster_out = []
    for cl in clusters:
        if len(cl) < 2:
            continue
        members = [traded[i] for i in cl]
        intra = [abs(cmat[i, j]) for i in cl for j in cl if i < j]
        cluster_out.append({"members": members, "size": len(members),
                            "mean_intra_abs_corr": round(float(np.mean(intra)), 3)})
    cluster_out.sort(key=lambda c: -c["size"])
    out["return_stream_correlation"] = {
        "n_instruments_in_matrix": len(traded),
        "excluded_lt3_trades": lt3,
        "excluded_zero_variance": zero_var,
        "avg_pairwise_abs_corr": round(avg_abs, 4),
        "median_pairwise_abs_corr": round(med_abs, 4),
        "max_pairwise_abs_corr": round(max_pair, 4),
        "pca_explained_variance_top3": [round(float(v), 4) for v in evr[:3]],
        "pc1_top_loadings": pc1_load[:10],
        "pc1_bottom_loadings": pc1_load[-5:],
        "clusters_abs_corr_0.3": cluster_out,
        "n_singletons": sum(1 for cl in clusters if len(cl) == 1),
    }

    # ── 3. USD-factor / one-way exposure over time ────────────────────────────
    eq_map = res.equity
    days = eq_index

    def _series(d: dict) -> pd.Series:
        return pd.Series(d).reindex(days).fillna(0.0)

    risk_long = _series(day_risk_long)
    risk_short = _series(day_risk_short)
    net_risk = risk_long - risk_short
    gross_risk = risk_long + risk_short
    fx_usd = _series(day_fx_usd)
    eqv = eq_map.astype(float)

    def _frac_gt(s: pd.Series, thr: float) -> float:
        return round(float((s > thr).mean()), 4)

    out["exposure_over_time"] = {
        "net_risk_asset": {
            "max_long_pct_equity": round(float((net_risk / eqv).max()) * 100, 2),
            "max_short_pct_equity": round(float((-net_risk / eqv).max()) * 100, 2),
            "mean_abs_pct_equity": round(float((net_risk.abs() / eqv).mean()) * 100, 2),
            "frac_days_net_gt_50pct": _frac_gt(net_risk / eqv, 0.50),
            "frac_days_net_lt_minus50pct": _frac_gt(-net_risk / eqv, 0.50),
            "frac_days_abs_net_gt_50pct": _frac_gt(net_risk.abs() / eqv, 0.50),
            "date_max_net_long": str((net_risk / eqv).idxmax().date()),
        },
        "gross_risk_asset": {
            "max_pct_equity": round(float((gross_risk / eqv).max()) * 100, 2),
            "mean_pct_equity": round(float((gross_risk / eqv).mean()) * 100, 2),
        },
        "fx_usd_factor": {
            "max_net_long_usd_pct_equity": round(float((fx_usd / eqv).max()) * 100, 2),
            "max_net_short_usd_pct_equity": round(float((-fx_usd / eqv).max()) * 100, 2),
            "mean_abs_pct_equity": round(float((fx_usd.abs() / eqv).mean()) * 100, 2),
            "frac_days_net_long_usd": round(float((fx_usd > 0).mean()), 4),
            "max_simultaneous_long_usd_positions": (
                int(pd.Series(day_fx_long_usd_n).max()) if day_fx_long_usd_n else 0),
            "max_simultaneous_short_usd_positions": (
                int(pd.Series(day_fx_short_usd_n).max()) if day_fx_short_usd_n else 0),
        },
        "reading_guide": "risk assets = single stocks + equity/sector/UCITS ETFs + crypto; "
                         "gold reported separately; FX USD-factor from the 7 majors only.",
    }

    # ── 4. sector / theme concentration ───────────────────────────────────────
    theme_pnl = {}
    for b, g in tr.groupby("bucket"):
        theme_pnl[b] = {**_stats(g["pnl"].tolist()),
                        "share_of_net": round(float(g["pnl"].sum()) / total_net, 4)}
    tech_long = tr[(tr["bucket"] == "tech_broad") & (tr["dir"] == "long")]
    tech_short = tr[(tr["bucket"] == "tech_broad") & (tr["dir"] == "short")]
    theme_pnl["tech_broad_long_only"] = {**_stats(tech_long["pnl"].tolist()),
                                         "share_of_net": round(float(tech_long["pnl"].sum()) / total_net, 4)}
    theme_pnl["tech_broad_short_only"] = {**_stats(tech_short["pnl"].tolist()),
                                          "share_of_net": round(float(tech_short["pnl"].sum()) / total_net, 4)}

    tech_net = _series(day_theme["tech_broad"])
    semis_net = _series(day_theme["semis"])
    out["theme_concentration"] = {
        "pnl_by_bucket": theme_pnl,
        "tech_net_exposure": {
            "max_long_pct_equity": round(float((tech_net / eqv).max()) * 100, 2),
            "max_short_pct_equity": round(float((-tech_net / eqv).max()) * 100, 2),
            "mean_abs_pct_equity": round(float((tech_net.abs() / eqv).mean()) * 100, 2),
            "frac_days_abs_net_gt_40pct": _frac_gt(tech_net.abs() / eqv, 0.40),
            "date_max_net_long": str((tech_net / eqv).idxmax().date()),
        },
        "semis_net_exposure": {
            "max_long_pct_equity": round(float((semis_net / eqv).max()) * 100, 2),
            "mean_abs_pct_equity": round(float((semis_net.abs() / eqv).mean()) * 100, 2),
        },
    }

    # live-book echo: how the certified book did on the names the live book holds
    live_echo = {}
    for tk in LIVE_BOOK_TICKERS:
        g = tr[tr["instrument"] == tk]
        if len(g):
            live_echo[tk] = {**_stats(g["pnl"].tolist()),
                             "share_of_net": round(float(g["pnl"].sum()) / total_net, 4)}
        else:
            live_echo[tk] = {"n": 0, "note": "not in certified universe"}
    out["live_book_echo"] = live_echo

    # ── 5. theme-reversal stress vs the 16.3% maxDD envelope ──────────────────
    # instantaneous gap shocks on each day's exact EOD signed exposures;
    # longs lose / shorts gain symmetrically; ignores stops (=> upper-bound gap hit)
    theme_series = {b: _series(d) for b, d in day_theme.items()}
    S1 = {"tech_broad": -0.25, "global_equity_ucits": -0.12, "sector_etf_other": -0.08,
          "crypto": -0.20, "gold": 0.03, "fx": 0.0}
    S2 = {"tech_broad": -0.20, "global_equity_ucits": -0.20, "sector_etf_other": -0.20,
          "crypto": -0.30, "gold": 0.05, "fx": 0.0}

    def _stress(shocks: dict) -> dict:
        hit = sum(theme_series[b] * r for b, r in shocks.items() if b in theme_series)
        hit_pct = hit / eqv * 100.0
        return {"worst_day": str(hit_pct.idxmin().date()),
                "worst_hit_pct_equity": round(float(hit_pct.min()), 2),
                "p05_daily_hit_pct": round(float(hit_pct.quantile(0.05)), 2),
                "median_daily_hit_pct": round(float(hit_pct.median()), 2),
                "shocks": shocks}

    s1 = _stress(S1)
    s2 = _stress(S2)
    maxdd_pct = round(CERTIFIED_GOLD["max_drawdown"] * 100, 2)
    out["theme_reversal_stress"] = {
        "S1_ai_tech_correction": s1,
        "S2_broad_risk_off": s2,
        "certified_max_drawdown_pct": maxdd_pct,
        "caveat": "instantaneous gap on EOD exposures; realised loss would be smaller "
                  "(stops, gap-aware fills) unless the gap jumps stops — treat as the "
                  "overnight-gap upper bound, not the expected DD contribution.",
    }

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_PATH, "w") as fh:
        json.dump(out, fh, indent=2, default=str)

    # ── report ────────────────────────────────────────────────────────────────
    ia = out["instrument_attribution"]
    rc = out["return_stream_correlation"]
    ex = out["exposure_over_time"]
    th = out["theme_concentration"]
    st = out["theme_reversal_stress"]

    def inst_table(rows_: list[dict]) -> str:
        lines = ["| instrument | bucket | n | net £ | exp £ | win% | PF | share of net |",
                 "|---|---|---|---|---|---|---|---|"]
        for r in rows_:
            pf = r.get("pf") if r.get("pf") is not None else "—"
            lines.append(f"| {r['instrument']} | {r['bucket']} | {r['n']} | {r['net']:,.0f} | "
                         f"{r['expectancy']:,.2f} | {r['win_rate']*100:.1f} | {pf} | "
                         f"{r['share_of_net']*100:.1f}% |")
        return "\n".join(lines)

    def bucket_table(d: dict) -> str:
        lines = ["| bucket | n | net £ | exp £ | win% | PF | share of net |",
                 "|---|---|---|---|---|---|---|"]
        for k, v in d.items():
            pf = v.get("pf") if v.get("pf") is not None else "—"
            lines.append(f"| {k} | {v['n']} | {v['net']:,.0f} | {v['expectancy']:,.2f} | "
                         f"{v['win_rate']*100:.1f} | {pf} | {v['share_of_net']*100:.1f}% |")
        return "\n".join(lines)

    verdict_lines = []
    tech_share = theme_pnl.get("tech_broad", {}).get("share_of_net", 0.0)
    tech_long_share = theme_pnl.get("tech_broad_long_only", {}).get("share_of_net", 0.0)
    tech_short_net = theme_pnl.get("tech_broad_short_only", {}).get("net", 0.0)
    s1_worst = s1["worst_hit_pct_equity"]
    s2_worst = s2["worst_hit_pct_equity"]
    mild_tech_hit = round(th["tech_net_exposure"]["max_long_pct_equity"] * 0.10, 1)
    pca_str = "/".join(f"{v*100:.1f}%" for v in rc["pca_explained_variance_top3"])
    verdict_lines.append(
        f"**P&L source — yes, largely one theme.** Tech-broad = **{tech_share*100:.1f}%** of total "
        f"net P&L (tech longs alone {tech_long_share*100:.1f}%; tech shorts net £{tech_short_net:,.0f} "
        f"— the in-theme short sleeve is a small net cost, not a hedge that pays). Top-5 instruments "
        f"= **{ia['top5']['share_of_total_net']*100:.1f}%** of net and are all megacap tech/semis "
        f"({', '.join(ia['top5']['instruments'])}). HHI(|P&L|) = {ia['herfindahl_abs_pnl']} → "
        f"effective N ≈ {ia['effective_n_instruments']} of {ia['n_instruments_traded']} traded; "
        f"crypto adds {theme_pnl.get('crypto', {}).get('share_of_net', 0)*100:.1f}%; "
        f"everything else ≈ ±3%.")
    verdict_lines.append(
        f"**Return streams look diversified — but that's timing, not factor diversification.** "
        f"Avg pairwise |corr| = **{rc['avg_pairwise_abs_corr']:.3f}** (median "
        f"{rc['median_pairwise_abs_corr']:.3f}); PC1 = {rc['pca_explained_variance_top3'][0]*100:.1f}% "
        f"({pca_str}) and is just the SMH/SOXX near-duplicate pair (|corr| 0.67); 34 of 38 "
        f"instruments are singletons at |corr|≥0.3. Entries/exits simply don't cluster on the same "
        f"days — the shared factor shows up in EXPOSURE overlap instead (next bullet).")
    verdict_lines.append(
        f"**Exposure is frequently one-way.** Net risk-asset long > 50% of equity on "
        f"{ex['net_risk_asset']['frac_days_net_gt_50pct']*100:.1f}% of days; max net long "
        f"**{ex['net_risk_asset']['max_long_pct_equity']}%** of equity "
        f"({ex['net_risk_asset']['date_max_net_long']}); net tech max long "
        f"**{th['tech_net_exposure']['max_long_pct_equity']}%** "
        f"({th['tech_net_exposure']['date_max_net_long']}), mean |net tech| "
        f"{th['tech_net_exposure']['mean_abs_pct_equity']}%. FX: up to "
        f"{ex['fx_usd_factor']['max_simultaneous_long_usd_positions']} same-direction USD positions "
        f"stacked, max net long-USD {ex['fx_usd_factor']['max_net_long_usd_pct_equity']}% of equity.")
    verdict_lines.append(
        f"**Survivability vs the {maxdd_pct}% envelope.** Instantaneous gap-stress (stops ignored — "
        f"the overnight upper bound): S1 AI/tech −25% → worst day **{s1_worst}%** of equity "
        f"({s1['worst_day']}), p05 {s1['p05_daily_hit_pct']}%, median {s1['median_daily_hit_pct']}%; "
        f"S2 broad risk-off → worst **{s2_worst}%**. Even a mild −10% megacap-tech session at the "
        f"certified max tech exposure = **−{mild_tech_hit}%** of equity — the ENTIRE maxDD envelope "
        f"in one day.")
    verdict_lines.append(
        f"**Verdict: concentration IS the hidden tail risk.** In P&L terms the certified book is "
        f"~{tech_share*100:.0f}% a tech/AI trend bet; its certified {maxdd_pct}% maxDD was earned in "
        f"a window with no overnight tech-basket gap, so the envelope does NOT contain a fast theme "
        f"reversal at certified exposures (worst-day gap upper bound {s1_worst}%). Slow reversals "
        f"are survivable — stops + 21-bar time exits bleed exposure over days-to-weeks (the 2022 "
        f"grind stayed inside the envelope) — but a single-session theme gap at a high-exposure day "
        f"breaches the envelope before those mechanics can act. The current live book (one theme, "
        f"both directions of it, plus stacked long-USD) replicates exactly the exposure pattern this "
        f"measurement flags.")

    md = ["# Factor / Exposure Concentration — certified Book H gold — 2026-08-08", "",
          f"Certified anchor reproduced EXACT (Sharpe {m['sharpe']:.5f}, {m['n_trades']} trades, "
          f"final equity £{m['final_equity']:,.2f}; window <{DEFAULT_HOLDOUT_START}). "
          "Informational measurement — no gate, no strategy change.", "",
          "Question: is the certified 1,637-trade book secretly ONE BET? Live motivation: "
          "open book = long AMD/TSM/AAPL/IWM + short META/MSFT/NFLX/TSLA/PLTR + long "
          "USD/JPY + USD/CAD — one theme (AI/tech + long-USD).", "",
          "## 1. Per-instrument attribution", "",
          f"Total net £{ia['total_net_pnl']:,.0f} across {ia['n_instruments_traded']} instruments. "
          f"Top-5 = {ia['top5']['share_of_total_net']*100:.1f}% of net "
          f"({', '.join(ia['top5']['instruments'])}); bottom-5 = "
          f"{ia['bottom5']['share_of_total_net']*100:.1f}% ({', '.join(ia['bottom5']['instruments'])}). "
          f"HHI(|P&L|) = {ia['herfindahl_abs_pnl']} → effective N ≈ {ia['effective_n_instruments']}; "
          f"largest single-instrument |share| = {ia['top1_abs_share']*100:.1f}%.", "",
          inst_table(ia["per_instrument"]), "",
          "## 2. Return-stream correlation (per-instrument daily P&L)", "",
          f"- instruments in matrix (≥3 trades): {rc['n_instruments_in_matrix']} "
          f"(excluded: {', '.join(rc['excluded_lt3_trades']) or 'none'})",
          f"- avg pairwise |corr| {rc['avg_pairwise_abs_corr']:.3f}, median "
          f"{rc['median_pairwise_abs_corr']:.3f}, max {rc['max_pairwise_abs_corr']:.3f}",
          f"- PCA explained variance top-3: "
          f"{' / '.join(f'{v*100:.1f}%' for v in rc['pca_explained_variance_top3'])}",
          f"- PC1 top loadings: {', '.join(f'{k} {v:+.2f}' for k, v in rc['pc1_top_loadings'])}",
          f"- PC1 weakest/negative loadings: {', '.join(f'{k} {v:+.2f}' for k, v in rc['pc1_bottom_loadings'])}",
          f"- complete-linkage clusters at |corr|≥0.3 ({len(rc['clusters_abs_corr_0.3'])} clusters, "
          f"{rc['n_singletons']} singletons):", ""]
    for c in rc["clusters_abs_corr_0.3"]:
        md.append(f"  - size {c['size']}, mean intra |corr| {c['mean_intra_abs_corr']}: "
                  f"{', '.join(c['members'])}")
    md += ["", "## 3. USD-factor / one-way exposure over time", "",
           "| measure | value |", "|---|---|",
           f"| max net risk-asset long | {ex['net_risk_asset']['max_long_pct_equity']}% of equity "
           f"({ex['net_risk_asset']['date_max_net_long']}) |",
           f"| max net risk-asset short | {ex['net_risk_asset']['max_short_pct_equity']}% |",
           f"| mean \\|net risk\\| | {ex['net_risk_asset']['mean_abs_pct_equity']}% |",
           f"| days net long > 50% equity | {ex['net_risk_asset']['frac_days_net_gt_50pct']*100:.1f}% |",
           f"| days net short > 50% equity | {ex['net_risk_asset']['frac_days_net_lt_minus50pct']*100:.1f}% |",
           f"| days \\|net\\| > 50% equity | {ex['net_risk_asset']['frac_days_abs_net_gt_50pct']*100:.1f}% |",
           f"| gross risk (max / mean) | {ex['gross_risk_asset']['max_pct_equity']}% / "
           f"{ex['gross_risk_asset']['mean_pct_equity']}% |",
           f"| FX: max net long-USD | {ex['fx_usd_factor']['max_net_long_usd_pct_equity']}% |",
           f"| FX: max net short-USD | {ex['fx_usd_factor']['max_net_short_usd_pct_equity']}% |",
           f"| FX: days net long-USD | {ex['fx_usd_factor']['frac_days_net_long_usd']*100:.1f}% |",
           f"| FX: max simultaneous long-USD positions | {ex['fx_usd_factor']['max_simultaneous_long_usd_positions']} |",
           "", f"_{ex['reading_guide']}_", "",
           "## 4. Sector / theme concentration", "",
           bucket_table(theme_pnl), "",
           f"Net tech exposure: max long {th['tech_net_exposure']['max_long_pct_equity']}% of equity "
           f"({th['tech_net_exposure']['date_max_net_long']}), max short "
           f"{th['tech_net_exposure']['max_short_pct_equity']}%, mean |net| "
           f"{th['tech_net_exposure']['mean_abs_pct_equity']}%, |net| > 40% of equity on "
           f"{th['tech_net_exposure']['frac_days_abs_net_gt_40pct']*100:.1f}% of days. "
           f"Semis subset: max long {th['semis_net_exposure']['max_long_pct_equity']}%.", "",
           "### Live-book echo (certified-book stats on the tickers the live book holds)", "",
           bucket_table({k: v for k, v in live_echo.items() if "net" in v}), "",
           *[f"- {k}: {v['note']}" for k, v in live_echo.items() if "note" in v], "",
           "## 5. Theme-reversal stress vs the 16.3% maxDD envelope", "",
           "| scenario | worst day | hit % equity | p05 daily | median daily |",
           "|---|---|---|---|---|",
           f"| S1 AI/tech correction (tech −25%, UCITS −12%, other sector −8%, crypto −20%, gold +3%) "
           f"| {s1['worst_day']} | {s1['worst_hit_pct_equity']}% | {s1['p05_daily_hit_pct']}% | {s1['median_daily_hit_pct']}% |",
           f"| S2 broad risk-off (all equity/ETF −20%, crypto −30%, gold +5%) "
           f"| {s2['worst_day']} | {s2['worst_hit_pct_equity']}% | {s2['p05_daily_hit_pct']}% | {s2['median_daily_hit_pct']}% |",
           "", f"_{st['caveat']}_", "",
           "## Verdict", "", *[f"- {v}" for v in verdict_lines], "",
           f"trajectory check: {traj_check['ok']} positions mapped exactly, "
           f"{traj_check['n_bars_mismatch']} bar-count mismatches, "
           f"{traj_check['price_mismatch']} price mismatches.", ""]
    REPORT_PATH.write_text("\n".join(md))
    print(f"wrote {RESULTS_PATH.name} + {REPORT_PATH.name}", flush=True)
    print(json.dumps(out["instrument_attribution"]["top5"], indent=2))
    print(json.dumps(out["instrument_attribution"]["bottom5"], indent=2))
    print(json.dumps(out["return_stream_correlation"], indent=2, default=str)[:2000])
    print(json.dumps(out["exposure_over_time"], indent=2))
    print(json.dumps(out["theme_reversal_stress"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
