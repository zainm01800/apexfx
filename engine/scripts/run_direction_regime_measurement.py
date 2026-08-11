"""Direction x regime measurement on the certified Book H gold book — MEASUREMENT ONLY.

Answers, on the certified 1,637-trade set (anchor book_h_gapaware_2026-07-22.json):
  E2: is the equity SHORT sleeve a net drag, does SPY-200dma state at entry condition it,
      how anomalous is the live 2026-07/08 short losing streak, and what would removing
      shorts have done (incl. 2022 crash insurance check)?
  E4-lite: the signed entry-timing delta (next-open fill minus decision close) x units x dir —
      the pure overnight gap the book pays/receives, by class and direction.

Hard-fails unless the certified anchor reproduces EXACTLY (same check as the exit
decomposition). Records exactly ONE informational ledger trial before running.
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

RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "direction_regime_measurement_2026-08-08.json"
REPORT_PATH = ENGINE_DIR / "data_store" / "direction_regime_measurement_2026-08-08.md"

CERTIFIED_GOLD = {"sharpe": 0.8628380346245177, "profit_factor": 1.324524180228228,
                  "win_rate": 0.5577275503970678, "max_drawdown": 0.16315348773173277,
                  "n_trades": 1637, "total_return": 1.9255133668640645,
                  "expectancy_pnl": 120.44265729993896, "final_equity": 292551.33668640646}

UNIVERSE_GOLD = EQUITY_CORE + [GOLD_ETC]
GOLD_PARAMS = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}
CERTIFIED_MRPT = 0.01

ETFS = {"SPY", "QQQ", "IWM", "GLD", "TLT", "XLF", "XLK", "XLE", "XBI", "SMH", "SOXX",
        "ARKK", "DIA", "VTI", "EEM", "EFA", "HYG", "LQD", "SLV", "USO", "XLV", "XLP",
        "XLY", "XLI", "XLU", "XLRE", "XLC", "VOO", "IVV"}

# ── spies (engine path untouched) ─────────────────────────────────────────────
ENTRY_SNAPS: dict[int, dict] = {}
EXIT_RECS: dict[int, dict] = {}
_SEQ = [0]

_orig_enter = PortfolioBacktester._enter
_orig_record = PortfolioBacktester._record


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
        "entry_time": str(pd.Timestamp(t).date()),
        "entry_idx": int(i),
        "dec_close": float(pend.get("dec", float("nan"))),
        "initial_stop": float(posd["initial_stop"]),
        "target": float(posd["target"]),
        "risk_abs": float(posd["risk_abs"]),
    }
    return posd


def _spy_record(self, position, exit_price, t, reason, pnl, instrument=""):
    tr = _orig_record(self, position, exit_price, t, reason, pnl, instrument)
    did = position.get("_decomp_id")
    if did is not None:
        EXIT_RECS[did] = {"exit_time": str(pd.Timestamp(t).date()),
                          "exit_reason": reason, "trade_pnl": float(pnl)}
    return tr


def _asset_class(inst: str, crypto: set[str]) -> str:
    if inst in crypto:
        return "crypto"
    if inst in set(FX_MAJORS_7):
        return "fx"
    if inst == GOLD_ETC:
        return "metals"
    if inst.upper() in ETFS:
        return "etf"
    return "equity_single"


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
                   "kind": "direction_regime_measurement", "max_risk_per_trade": CERTIFIED_MRPT,
                   "informational": True,
                   "params": {**GOLD_PARAMS,
                              "measures": ["direction_split", "spy200dma_conditioning",
                                           "short_sleeve_streaks", "no_shorts_counterfactual",
                                           "entry_timing_delta"]}})
    ledger.save(LEDGER_PATH)
    print(f"ledger n_trials {n_before} -> {ledger.n_trials} (1 informational trial)", flush=True)

    PortfolioBacktester._enter = _spy_enter
    PortfolioBacktester._record = _spy_record
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

    paired = [(ENTRY_SNAPS[did], EXIT_RECS[did]) for did in EXIT_RECS if did in ENTRY_SNAPS]
    print(f"trades paired: {len(paired)}", flush=True)

    # ── assemble per-trade frame ──────────────────────────────────────────────
    crypto_set = set(crypto)
    rows = []
    for snap, rec in paired:
        sign = 1.0 if snap["direction"] == "long" else -1.0
        rows.append({
            "instrument": snap["instrument"],
            "cls": _asset_class(snap["instrument"], crypto_set),
            "dir": snap["direction"],
            "entry_date": snap["entry_time"],
            "exit_date": rec["exit_time"],
            "exit_reason": rec["exit_reason"],
            "pnl": rec["trade_pnl"],
            "risk_abs": snap["risk_abs"],
            "r": rec["trade_pnl"] / snap["risk_abs"] if snap["risk_abs"] else np.nan,
            "entry_delta_gbp": (snap["entry_price"] - snap["dec_close"]) * snap["units"] * sign
                               if np.isfinite(snap["dec_close"]) else np.nan,
        })
    tr = pd.DataFrame(rows)
    tr["entry_date"] = pd.to_datetime(tr["entry_date"])
    tr["year"] = tr["entry_date"].dt.year

    # SPY 200dma state at the DECISION bar (the trading day before the open-fill date).
    spy_df = store.load("SPY", "1d")
    spy = clean(spy_df)["close"] if not spy_df.empty else None
    if spy is not None:
        spy200 = spy.rolling(200).mean()
        above = (spy > spy200).shift(1)  # decision-bar state for a next-open fill
        state = above.reindex(spy.index)
        def _spy_state(d):
            idx = state.index.searchsorted(pd.Timestamp(d, tz="UTC"))
            idx = min(idx, len(state) - 1)
            v = state.iloc[idx]
            return "above" if v else "below"
        tr["spy_state"] = tr["entry_date"].map(_spy_state)
    else:
        tr["spy_state"] = "unknown"

    out: dict = {"kind": "direction_regime_measurement", "informational": True,
                 "anchor": {k: m[k] for k in CERTIFIED_GOLD}, "n_trades": int(len(tr))}

    # E2.1 direction split, overall + by class
    out["direction_overall"] = {d: _stats(tr.loc[tr["dir"] == d, "pnl"].tolist())
                                for d in ("long", "short")}
    out["direction_by_class"] = {
        f"{c}|{d}": _stats(tr.loc[(tr["cls"] == c) & (tr["dir"] == d), "pnl"].tolist())
        for c in sorted(tr["cls"].unique()) for d in ("long", "short")}

    # E2.2 direction x SPY-200dma (equity single-names focus, but report all)
    eq = tr[tr["cls"] == "equity_single"]
    out["equity_dir_x_spy200"] = {
        f"{d}|spy_{s}": _stats(eq.loc[(eq["dir"] == d) & (eq["spy_state"] == s), "pnl"].tolist())
        for d in ("long", "short") for s in ("above", "below")}
    out["all_dir_x_spy200"] = {
        f"{d}|spy_{s}": _stats(tr.loc[(tr["dir"] == d) & (tr["spy_state"] == s), "pnl"].tolist())
        for d in ("long", "short") for s in ("above", "below")}

    # E2.3 short-sleeve rolling 20-trade streaks (equity single-name shorts)
    sh = eq[eq["dir"] == "short"].sort_values("exit_date")
    streaks = sh["pnl"].rolling(20).sum().dropna()
    out["short_streaks"] = {
        "n_windows": int(len(streaks)),
        "frac_negative": round(float((streaks < 0).mean()), 4) if len(streaks) else None,
        "p05": round(float(streaks.quantile(0.05)), 2) if len(streaks) else None,
        "median": round(float(streaks.median()), 2) if len(streaks) else None,
        "worst": round(float(streaks.min()), 2) if len(streaks) else None,
    }
    live_streak = -(545 + 764 + 511 + 1189)  # NFLX, MSFT, AMZN, PLTR stops (4 trades)
    out["live_streak_context"] = {
        "live_4_stop_sum": live_streak,
        "note": "live streak is 4 stopped shorts (~-£3,009 incl. open marks); compare to "
                "rolling windows of the SAME length, not the 20-trade ones",
        "hist_4trade_short_windows_p05": round(float(
            sh["pnl"].rolling(4).sum().dropna().quantile(0.05)), 2) if len(sh) else None,
        "hist_4trade_short_windows_worst": round(float(
            sh["pnl"].rolling(4).sum().dropna().min()), 2) if len(sh) else None,
    }

    # E2.4 counterfactuals (per-trade lens, informational)
    full_net = float(tr["pnl"].sum())
    no_eq_shorts = float(tr.loc[~((tr["cls"] == "equity_single") & (tr["dir"] == "short")), "pnl"].sum())
    no_eq_shorts_above = float(tr.loc[~((tr["cls"] == "equity_single") & (tr["dir"] == "short")
                                        & (tr["spy_state"] == "above")), "pnl"].sum())
    out["counterfactual_net"] = {
        "certified": round(full_net, 2),
        "no_equity_shorts": round(no_eq_shorts, 2),
        "no_equity_shorts_when_spy_above_200": round(no_eq_shorts_above, 2),
    }
    for label, frame in (("certified", tr),
                         ("no_equity_shorts", tr[~((tr["cls"] == "equity_single") & (tr["dir"] == "short"))]),
                         ("no_eq_shorts_spy_above", tr[~((tr["cls"] == "equity_single") & (tr["dir"] == "short") & (tr["spy_state"] == "above"))])):
        y22 = frame.loc[frame["year"] == 2022, "pnl"].sum()
        out["counterfactual_net"].setdefault("y2022", {})[label] = round(float(y22), 2)

    # E2.5 short sleeve by calendar year
    out["short_sleeve_by_year"] = {
        int(y): round(float(g["pnl"].sum()), 2) for y, g in sh.groupby("year")}

    # E4-lite entry-timing delta
    ed = tr["entry_delta_gbp"].dropna()
    out["entry_timing_delta"] = {
        "total_gbp": round(float(ed.sum()), 2),
        "mean_per_trade": round(float(ed.mean()), 2),
        "median_per_trade": round(float(ed.median()), 2),
        "n": int(len(ed)),
        "by_dir": {d: round(float(tr.loc[tr["dir"] == d, "entry_delta_gbp"].sum()), 2)
                   for d in ("long", "short")},
        "by_class": {c: round(float(tr.loc[tr["cls"] == c, "entry_delta_gbp"].sum()), 2)
                     for c in sorted(tr["cls"].unique())},
        "reading_guide": "|mean| < ~£5/trade vs £120.44 expectancy => fill timing immaterial",
    }

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_PATH, "w") as fh:
        json.dump(out, fh, indent=2, default=str)

    # ── report ────────────────────────────────────────────────────────────────
    def fmt_table(d: dict, title: str) -> str:
        lines = [f"### {title}", "", "| cell | n | net £ | expectancy £ | win% | PF |",
                 "|---|---|---|---|---|---|"]
        for k, v in d.items():
            lines.append(f"| {k} | {v['n']} | {v['net']:,.0f} | {v['expectancy']:,.2f} | "
                         f"{v['win_rate']*100:.1f} | {v.get('pf') or '—'} |")
        return "\n".join(lines)

    md = [f"# Direction x Regime Measurement — 2026-08-08", "",
          f"Certified anchor reproduced EXACT (Sharpe {m['sharpe']:.5f}, {m['n_trades']} trades). "
          f"Informational measurement — no gate, no strategy change.", "",
          fmt_table(out["direction_overall"], "Direction split (all classes)"), "",
          fmt_table(out["direction_by_class"], "Direction x asset class"), "",
          fmt_table(out["equity_dir_x_spy200"], "Single-name equities: direction x SPY-200dma at entry"), "",
          fmt_table(out["all_dir_x_spy200"], "All instruments: direction x SPY-200dma at entry"), "",
          f"## Short-sleeve streaks", "```json",
          json.dumps(out["short_streaks"], indent=2), "```", "",
          f"## Live streak context", "```json",
          json.dumps(out["live_streak_context"], indent=2), "```", "",
          f"## Counterfactuals (per-trade lens)", "```json",
          json.dumps(out["counterfactual_net"], indent=2), "```", "",
          f"## Short sleeve net P&L by year", "```json",
          json.dumps(out["short_sleeve_by_year"], indent=2), "```", "",
          f"## E4-lite entry-timing delta (gap paid on next-open fills)", "```json",
          json.dumps(out["entry_timing_delta"], indent=2), "```", ""]
    REPORT_PATH.write_text("\n".join(md))
    print(f"wrote {RESULTS_PATH.name} + {REPORT_PATH.name}", flush=True)
    print(json.dumps(out["direction_overall"], indent=2))
    print(json.dumps(out["equity_dir_x_spy200"], indent=2))
    print(json.dumps(out["entry_timing_delta"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
