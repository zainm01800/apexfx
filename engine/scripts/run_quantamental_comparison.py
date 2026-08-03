"""Quantamental comparison: does a >=70 fundamental-score gate help the daily
equity momentum book? Strategy A (pure technical) vs Strategy B (gated).

OWNER-REQUESTED MEASUREMENT — NOT an adoption gate. No DSR/PBO verdict is
produced; exactly 1 measurement trial (kind: quantamental_comparison) is
recorded in the shared TrialLedger BEFORE the runs (see the prereg).

Pre-registration: engine/data_store/quantamental_prereg.md (locked 2026-08-03,
before any run — universe, scoring formula + boundary conventions, gate
membership, entry/sizing/exit rules, circuit breakers, window, costs, ETF
pass-through, seed). Nothing below may deviate from that document.

Certified machinery reused (nothing in apex_quant is modified):
  * engine/scripts/run_portfolio_gate.py's TrendBook stack —
    RegimeGatedMomentum(252) wrapped in MultiTimeframeMomentum(1w, 50) —
    run by apex_quant.backtest.portfolio.PortfolioBacktester
    (exit_mode="managed", TradeManager partials/breakeven/chandelier/time-exit).
  * The spec's entry rule (EMA20 > EMA50 + relative volume > 1.2x 20d average,
    long-only) enters through the certified DirectionalEntryGate seam
    (strategies/entry_gates.py) as precomputed point-in-time masks. State
    reading per prereg Amendment A1 (the literal fresh-cross reading is
    degenerate: 6 trades in 9 years).
  * The spec's sizing S = min(0.20 x Capital/(2.5 x ATR14), 0.065 x Capital)
    is a RiskManager config mapping: kelly off, max_risk_per_trade 0.20,
    atr_stop_mult 2.5 / atr_window 14 (certified defaults), vol-target ceiling
    raised inert, per-position notional cap 0.065 (certified step 8.5).
  * Circuit breakers: certified drawdown halt at 15%; the spec's fixed 50%
    scale-down at 10% DD is StepBreakerRiskManager below (the certified amber
    zone is a linear ramp, not a step) with the certified ramp emptied.
  * Costs: certified equity model (2.0bps spread -> 1.0bps half-spread + 1.0bps
    slippage per fill) + $1.09 commission per side (in-memory override only;
    config.yaml untouched).

Fundamental data: the spec named FMP, but FMP_API_KEY is not in engine/.env
(it lives only in the Vercel deployment environment — see
scripts/build_earnings_calendar.py). Substitution (locked in the prereg):
Yahoo Finance quoteSummary v10, cached one JSON per ticker in
engine/data_store/fundamentals_cache/. This script is CACHE-FIRST: with the
cache present it is fully offline and deterministic. --refresh-fundamentals
re-fetches (changes the locked gate => that is a NEW experiment, not this one).

Iteration discipline: bars STRICTLY before --holdout-start (2025-01-01). The
sealed 2025+ holdout is never loaded into a panel.

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_quantamental_comparison.py
    .venv-mac/bin/python scripts/run_quantamental_comparison.py --no-ledger   # smoke test

Exit code 0 always (measurement, no gate verdict); 1 on data/screen failures.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")
ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from apex_quant.backtest.portfolio import PortfolioBacktester  # noqa: E402
from apex_quant.config import get_config, set_global_seeds  # noqa: E402
from apex_quant.data import PointInTimeAccessor, ParquetStore, clean  # noqa: E402
from apex_quant.risk.manager import RiskManager  # noqa: E402
from apex_quant.strategies.baseline import RegimeGatedMomentum  # noqa: E402
from apex_quant.strategies.entry_gates import DirectionalEntryGate  # noqa: E402
from apex_quant.strategies.multi_timeframe import MultiTimeframeMomentum  # noqa: E402
from apex_quant.validation.trials import TrialLedger  # noqa: E402

LEDGER_PATH = ENGINE_DIR / "data_store" / "validation" / "trial_ledger.json"
RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "quantamental_comparison_2026-08-03.json"
PREREG_PATH = ENGINE_DIR / "data_store" / "quantamental_prereg.md"
FUND_CACHE = ENGINE_DIR / "data_store" / "fundamentals_cache"
DEFAULT_HOLDOUT_START = "2025-01-01"
WINDOW_START = "2016-01-04"             # common panel start (prereg §1); GME's 2010
                                        # backfill is clipped to the shared window

# ── Locked parameters (mirrors quantamental_prereg.md — do not drift) ─────────
UNIVERSE = ["NVDA", "MSFT", "AAPL", "PLTR", "TSM", "NFLX", "AMD", "META",
            "AMZN", "GME", "SPY", "QQQ"]
INDEX_ETFS = {"SPY", "QQQ"}            # pass-through, "N/A — index ETF" (prereg §2)
GATE_THRESHOLD = 70.0
MIN_BARS = 300                          # same floor as scripts/run_backtests.py
WARMUP = 300                            # covers 252-lookback (min_obs 253) + 1w x 50 HTF

# Technical stack (prereg §3)
MOMENTUM_LOOKBACK = 252
VOL_WINDOW = 63
HOLDING_HORIZON = 21                    # certified time-exit horizon
REWARD_RISK = 10.0                      # fixed target as deep backstop; ladder+trail govern
HTF_RULE = "1w"
HTF_MA_WINDOW = 50                      # certified 50-week SMA (deviation flagged in prereg)
EMA_FAST, EMA_SLOW, RELVOL_WINDOW, RELVOL_MIN = 20, 50, 20, 1.2

# Sizing / risk (prereg §4-§6) — in-memory copy of config, config.yaml untouched
RISK_OVERRIDES = {
    "kelly_fraction": 0.0,              # spec sizing, not Kelly
    "max_risk_per_trade": 0.20,         # spec 0.20 x Capital / (2.5 x ATR14) term
    "atr_stop_mult": 2.5,               # certified default; spec initial stop
    "atr_window": 14,                   # certified default; spec ATR14
    "target_portfolio_vol": 10.0,       # certified per-position vol ceiling made inert
    "max_position_notional_pct": 0.065, # spec 6.5%-of-equity notional term (step 8.5)
    "drawdown_breaker": 0.15,           # spec: 100% halt at 15% DD (certified breaker)
    "drawdown_reducing_limit": 0.15,    # empties the certified linear ramp; step below
}
COMMISSION_PER_SIDE = 1.09              # spec; certified equity spread/slippage unchanged
DD_STEP_REDUCE, DD_STEP_HALT, DD_STEP_SCALE = 0.10, 0.15, 0.50

# Drawdown-analysis windows (prereg §9, locked)
DD_WINDOWS = {
    "2018_Q4": ("2018-10-01", "2018-12-31"),
    "2020_COVID": ("2020-02-01", "2020-04-30"),
    "2022_bear": ("2022-01-01", "2022-12-31"),
}

BOOKS = {"A_pure_technical": {"fundamental_gate": False},
         "B_quantamental_70": {"fundamental_gate": True}}


# ── Fundamentals (cache-first; Yahoo quoteSummary on miss) ────────────────────
def _fetch_yahoo_fundamentals(ticker: str) -> dict:
    """One quoteSummary v10 fetch (crumb-authenticated). Network path — used only
    on cache miss or --refresh-fundamentals."""
    import httpx

    headers = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 "
                              "Safari/537.36")}
    with httpx.Client(headers=headers, timeout=25.0, follow_redirects=True) as c:
        c.get("https://fc.yahoo.com")  # cookie jar
        crumb = c.get("https://query1.finance.yahoo.com/v1/test/getcrumb").text.strip()
        url = (f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
               "?modules=summaryDetail,defaultKeyStatistics,financialData,price"
               f"&crumb={crumb}")
        r = c.get(url)
        r.raise_for_status()
        d = (r.json().get("quoteSummary", {}).get("result") or [{}])[0]

    def raw(*path):
        cur = d
        for p in path:
            cur = (cur or {}).get(p)
        return (cur or {}).get("raw") if isinstance(cur, dict) else None

    return {
        "ticker": ticker,
        "source": "yahoo_quoteSummary_v10",
        "as_of": datetime.now(timezone.utc).date().isoformat(),
        "trailing_pe": raw("summaryDetail", "trailingPE") or raw("defaultKeyStatistics", "trailingPE"),
        "gross_margins": raw("financialData", "grossMargins"),
        "revenue_growth_yoy": raw("financialData", "revenueGrowth"),
        "debt_to_equity_pct": raw("financialData", "debtToEquity"),
        "free_cash_flow": raw("financialData", "freeCashflow"),
        "long_name": raw("price", "longName"),
    }


def load_fundamentals(refresh: bool = False) -> dict[str, dict]:
    """Cache-first per-ticker JSON in data_store/fundamentals_cache/. ETFs are
    excluded by design (pass-through, prereg §2)."""
    FUND_CACHE.mkdir(parents=True, exist_ok=True)
    out: dict[str, dict] = {}
    for t in UNIVERSE:
        if t in INDEX_ETFS:
            continue
        p = FUND_CACHE / f"{t}.json"
        if p.exists() and not refresh:
            out[t] = json.loads(p.read_text())
            continue
        rec = _fetch_yahoo_fundamentals(t)
        p.write_text(json.dumps(rec, indent=2))
        out[t] = rec
        time.sleep(0.4)  # be polite on the network path
    return out


def score_ticker(rec: dict) -> dict:
    """The 0-100 score, 4 pillars x 25, boundary conventions per prereg §2."""
    pe = rec.get("trailing_pe")
    gm = rec.get("gross_margins")
    gr = rec.get("revenue_growth_yoy")
    de = rec.get("debt_to_equity_pct")
    fcf = rec.get("free_cash_flow")

    # Valuation (missing PE is treated like a negative PE: no credit — prereg §2)
    if pe is None or pe <= 0 or pe > 90:
        pe_pts = 0
    elif pe <= 30:
        pe_pts = 25
    elif pe <= 50:
        pe_pts = 18
    else:
        pe_pts = 10
    # Profitability (missing margin -> conservative 5)
    if gm is None or gm < 0.35:
        gm_pts = 5
    elif gm >= 0.60:
        gm_pts = 25
    else:
        gm_pts = 18
    # Growth (missing -> 0)
    if gr is None or gr < 0:
        gr_pts = 0
    elif gr >= 0.25:
        gr_pts = 25
    elif gr >= 0.10:
        gr_pts = 18
    else:
        gr_pts = 10
    # Balance sheet: FCF>0 -> +15; D/E <=100% -> +10, 100-200% -> +5
    bs_pts = (15 if (fcf is not None and fcf > 0) else 0) + (
        10 if (de is not None and de <= 100) else (5 if de <= 200 else 0) if de is not None else 0)

    total = pe_pts + gm_pts + gr_pts + bs_pts
    return {"pe_pts": pe_pts, "margin_pts": gm_pts, "growth_pts": gr_pts,
            "solvency_pts": bs_pts, "score": total, "pass_70": total >= GATE_THRESHOLD,
            "raw": {"trailing_pe": pe, "gross_margins": gm, "revenue_growth_yoy": gr,
                    "debt_to_equity_pct": de, "free_cash_flow": fcf},
            "as_of": rec.get("as_of"), "source": rec.get("source")}


# ── Strategy wiring (certified TrendBook stack + spec entry gate) ─────────────
def entry_masks(df: pd.DataFrame) -> tuple[set, set]:
    """(blocked_long, blocked_short) for the spec's entry rule, point-in-time:
    LONG allowed only on bars where EMA20 > EMA50 (bullish EMA structure) AND
    relative volume > 1.2x the 20d average (the trigger); SHORTs blocked on
    every bar (long-only, prereg §3). State reading per prereg Amendment A1."""
    close = df["close"].astype(float)
    ema_f = close.ewm(span=EMA_FAST, adjust=False).mean()
    ema_s = close.ewm(span=EMA_SLOW, adjust=False).mean()
    vol = df["volume"].astype(float)
    relvol_ok = (vol / vol.rolling(RELVOL_WINDOW).mean()) > RELVOL_MIN
    allowed = ((ema_f > ema_s) & relvol_ok).fillna(False)
    blocked_long = set(df.index[~allowed])
    blocked_short = set(df.index)
    return blocked_long, blocked_short


class QuantamentalBook:
    """The spec's equity momentum book as one portfolio-level model.

    Mirrors TrendBook (run_portfolio_gate.py): per-instrument
    RegimeGatedMomentum(252) -> MultiTimeframeMomentum(1w, 50) ->
    DirectionalEntryGate(EMA20>50 fresh cross + relvol > 1.2, long-only).
    ``gate_blocked`` names (fundamental score < 70 in book B) have every bar
    LONG-blocked as well — a static, pre-registered universe gate.
    """

    def __init__(self, panel: dict, *, gate_blocked: frozenset[str] = frozenset()) -> None:
        self.instruments = list(panel.keys())
        self._strategies = {}
        for inst in self.instruments:
            base = RegimeGatedMomentum(
                momentum_lookback=MOMENTUM_LOOKBACK,
                vol_window=VOL_WINDOW,
                holding_horizon=HOLDING_HORIZON,
                reward_risk=REWARD_RISK,
                regime_method="rule_based",
                timeframe="1d",
                instrument=inst,
                enable_mean_reversion=False,   # spec has no Bollinger leg; no gate bypass
            )
            mtf = MultiTimeframeMomentum(
                base_strategy=base, htf_rule=HTF_RULE,
                htf_ma_window=HTF_MA_WINDOW, instrument=inst,
            )
            bl, bs = entry_masks(panel[inst])
            if inst in gate_blocked:
                bl = bl | bs                    # fundamental gate: no longs anywhere
                label = "ema_relvol+fund70"
            else:
                label = "ema_relvol"
            self._strategies[inst] = DirectionalEntryGate(
                mtf, blocked_long=bl, blocked_short=bs, label=label)

    def strategies(self) -> dict:
        return dict(self._strategies)


class StepBreakerRiskManager(RiskManager):
    """Spec §6 circuit breakers on top of the certified risk layer.

    The certified three-state breaker is a LINEAR ramp between the reducing and
    halt thresholds; the spec wants a fixed 50% scale-down at 10% DD and a 100%
    halt at 15%. The halt is the certified breaker (drawdown_breaker=0.15, with
    the ramp zone emptied via drawdown_reducing_limit=0.15); this subclass adds
    the fixed x0.50 step for 10% <= DD < 15%, recorded as dd_step_scale=0.50.
    """

    def permit(self, signal, account, market, *, regime=None, t=None):
        pos = super().permit(signal, account, market, regime=regime, t=t)
        dd = account.drawdown
        if pos.permitted and DD_STEP_REDUCE - 1e-9 <= dd < DD_STEP_HALT - 1e-9:
            pos.units *= DD_STEP_SCALE
            pos.notional *= DD_STEP_SCALE
            pos.risk_fraction *= DD_STEP_SCALE
            pos.constraints_applied.append(f"dd_step_scale={DD_STEP_SCALE:.2f}")
            pos.rationale += (f" | spec DD step: size x{DD_STEP_SCALE:.2f} "
                              f"({DD_STEP_REDUCE:.0%} <= DD {dd:.1%} < {DD_STEP_HALT:.0%})")
        return pos


# ── Analysis helpers ───────────────────────────────────────────────────────────
def _utc(ts) -> pd.Timestamp:
    ts = pd.Timestamp(ts)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def window_maxdd(equity: pd.Series, start: str, end: str) -> float | None:
    """Window-local max peak-to-trough drawdown of the equity curve (peak resets
    at the window start — the drawdown EXPERIENCED inside the sell-off)."""
    seg = equity[(equity.index >= _utc(start)) & (equity.index <= _utc(end))]
    if seg.empty:
        return None
    return max(0.0, float(-(seg / seg.cummax() - 1.0).min()))


def window_entries(trades: list, start: str, end: str) -> dict:
    """Trades ENTERED inside the window: count, losers ('false breakouts' that
    closed red), win rate, summed P&L."""
    s, e = pd.Timestamp(start).date(), pd.Timestamp(end).date()
    inside = [t for t in trades if s <= pd.Timestamp(t.entry_time).date() <= e]
    losers = [t for t in inside if t.pnl < 0]
    return {"entries": len(inside), "false_breakouts": len(losers),
            "win_rate": (len(inside) - len(losers)) / len(inside) if inside else None,
            "pnl": round(sum(t.pnl for t in inside), 2)}


def perf_row(res, months: float) -> dict:
    m = res.metrics
    if m.get("insufficient_data"):
        return {"insufficient_data": True, "n_trades": m.get("n_trades", 0)}
    return {
        "n_trades": m["n_trades"],
        "win_rate": m["win_rate"],
        "total_net_return_pct": m["total_return"] * 100,
        "ann_return_pct": m["ann_return"] * 100,
        "sharpe": m["sharpe"],
        "max_dd_pct": m["max_drawdown"] * 100,
        "profit_factor": m.get("profit_factor"),
        "expectancy_per_trade": m["expectancy_pnl"],
        "net_pnl": m["net_pnl"],
        "gbp_per_month_100k": m["net_pnl"] / months if months > 0 else None,
        "final_equity": m["final_equity"],
    }


def print_screening_table(scores: dict[str, dict]) -> None:
    print("\nFUNDAMENTAL SCREENING TABLE (0-100, 4 pillars x 25; gate >= 70)")
    print(f"  as-of: {next(iter(scores.values()))['as_of']} | source: "
          f"{next(iter(scores.values()))['source']} (cached JSON per ticker)")
    hdr = f"  {'ticker':6s} {'P/E pts':>8s} {'margin':>7s} {'growth':>7s} {'solv':>6s} {'score':>6s}  gate"
    print(hdr + "\n  " + "-" * (len(hdr) + 8))
    for t in UNIVERSE:
        if t in INDEX_ETFS:
            print(f"  {t:6s} {'N/A — index ETF (pass-through, both books)':>50s}")
            continue
        s = scores[t]
        r = s["raw"]
        print(f"  {t:6s} {s['pe_pts']:>5d}({r['trailing_pe']:.1f}) "
              f"{s['margin_pts']:>4d}({r['gross_margins']*100:.0f}%) "
              f"{s['growth_pts']:>4d}({r['revenue_growth_yoy']*100:.0f}%) "
              f"{s['solvency_pts']:>3d} {s['score']:>6d}  "
              f"{'PASS' if s['pass_70'] else 'FAIL'}")
    blocked = [t for t in UNIVERSE if t not in INDEX_ETFS and not scores[t]["pass_70"]]
    print(f"  => book B trades {len(UNIVERSE) - len(blocked)}/{len(UNIVERSE)} names; "
          f"blocked: {', '.join(blocked) or 'none'}")


def print_perf_table(rows: dict[str, dict]) -> None:
    print("\nPERFORMANCE TABLE (iteration window, net of costs; £ book = 100,000)")
    cols = ["trades", "win%", "net ret%", "ann ret%", "Sharpe", "maxDD%",
            "PF", "expect/tr", "£/mo"]
    print(f"  {'book':22s}" + "".join(f"{c:>10s}" for c in cols))
    print("  " + "-" * (22 + 10 * len(cols)))
    for name, r in rows.items():
        if r.get("insufficient_data"):
            print(f"  {name:22s} insufficient data ({r['n_trades']} trades)")
            continue
        pf = f"{r['profit_factor']:.2f}" if r["profit_factor"] is not None else "inf"
        print(f"  {name:22s}{r['n_trades']:>10d}{r['win_rate']*100:>10.1f}"
              f"{r['total_net_return_pct']:>10.1f}{r['ann_return_pct']:>10.1f}"
              f"{r['sharpe']:>10.2f}{r['max_dd_pct']:>10.1f}{pf:>10s}"
              f"{r['expectancy_per_trade']:>10.2f}{r['gbp_per_month_100k']:>10.0f}")


# ── Main ───────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Quantamental gate A/B measurement "
                                             "(iteration window only; owner-requested, not an adoption gate).")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--holdout-start", default=DEFAULT_HOLDOUT_START,
                    help="iteration data is strictly before this date")
    ap.add_argument("--no-ledger", action="store_true",
                    help="smoke-test mode: do NOT record the measurement trial")
    ap.add_argument("--refresh-fundamentals", action="store_true",
                    help="re-fetch fundamentals (changes the locked gate => new experiment)")
    ap.add_argument("--out", default=str(RESULTS_PATH))
    args = ap.parse_args(argv)

    if not PREREG_PATH.exists():
        print(f"FATAL: prereg missing: {PREREG_PATH} — lock parameters before running.")
        return 1
    set_global_seeds(args.seed)

    # 1. Fundamentals + screening (cache-first; deterministic once cached).
    fund = load_fundamentals(refresh=args.refresh_fundamentals)
    scores = {t: score_ticker(rec) for t, rec in fund.items()}
    gate_blocked = frozenset(t for t, s in scores.items() if not s["pass_70"])
    print_screening_table(scores)

    # 2. Panel: iteration window ONLY (strictly < holdout-start).
    cfg0 = get_config()
    store = ParquetStore(cfg0.store_path)
    holdout_start = _utc(args.holdout_start)
    panel: dict[str, pd.DataFrame] = {}
    for inst in UNIVERSE:
        df = store.load(inst, "1d")
        if df.empty:
            print(f"skip {inst}: no cached 1d data")
            continue
        df = clean(df)
        df = df[(df.index < holdout_start) & (df.index >= _utc(WINDOW_START))]
        if len(df) < MIN_BARS:
            print(f"skip {inst}: {len(df)} bars in iteration window")
            continue
        panel[inst] = df
    if len(panel) < len(UNIVERSE):
        print("FATAL: universe incomplete in the store")
        return 1
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    timeframes = {k: "1d" for k in panel}

    # 3. Ledger: ONE measurement trial, recorded BEFORE any run (idempotent).
    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not args.no_ledger:
        ledger.record({
            "kind": "quantamental_comparison",
            "universe": "equity_12_nvda_msft_aapl_pltr_tsm_nflx_amd_meta_amzn_gme_spy_qqq",
            "timeframe": "1d",
            "books": {"A": "pure_technical", "B": f"fundamental_gate>={int(GATE_THRESHOLD)}"},
            "gate_blocked_b": sorted(gate_blocked),
            "window": f"iteration_strictly_before_{args.holdout_start}",
            "seed": args.seed,
            "prereg": "data_store/quantamental_prereg.md",
        })
        ledger.save(LEDGER_PATH)
    print(f"\nledger: n_trials {n_before} -> {TrialLedger.load(LEDGER_PATH).n_trials} "
          f"({'recorded' if not args.no_ledger else 'SMOKE, not recorded'}; "
          f"measurement trial — no DSR/PBO adoption verdict, by design)")

    # 4. Risk config: in-memory copy; config.yaml is never touched.
    cfg = copy.deepcopy(cfg0)
    for k, v in RISK_OVERRIDES.items():
        setattr(cfg.risk, k, v)
    cfg.asset_classes.equity.commission_per_trade = COMMISSION_PER_SIDE

    print("=" * 78, flush=True)
    print(f"QUANTAMENTAL COMPARISON 2026-08-03 | mode=ITERATION (strictly < {args.holdout_start})")
    print(f"universe: {len(panel)} names | window: "
          f"{min(df.index[0] for df in panel.values()).date()} -> "
          f"{max(df.index[-1] for df in panel.values()).date()} | seed={args.seed}")
    print(f"sizing: S = min(0.20 x Cap/(2.5 x ATR14), 0.065 x Cap notional) | "
          f"costs: 1.0bps half-spread + 1.0bps slippage + ${COMMISSION_PER_SIDE}/side")
    print(f"breakers: x{DD_STEP_SCALE} at {DD_STEP_REDUCE:.0%} DD, halt at {DD_STEP_HALT:.0%} DD "
          f"| gate: score >= {int(GATE_THRESHOLD)} blocks {sorted(gate_blocked) or 'none'} in book B")
    print("=" * 78, flush=True)

    # 5. Run both books through the certified portfolio engine.
    results: dict[str, dict] = {}
    equities: dict[str, pd.Series] = {}
    for name, spec in BOOKS.items():
        t0 = time.time()
        blocked = gate_blocked if spec["fundamental_gate"] else frozenset()
        model = QuantamentalBook(panel, gate_blocked=blocked)
        bt = PortfolioBacktester(cfg, risk_manager=StepBreakerRiskManager(cfg.risk),
                                 exit_mode="managed")
        res = bt.run(pits, model.strategies(), timeframes=timeframes,
                     warmup=WARMUP, periods_per_year=252)
        equities[name] = res.equity
        results[name] = {"spec": spec, "gate_blocked": sorted(blocked),
                         "res": res, "runtime_s": round(time.time() - t0, 1)}
        m = res.metrics
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {name}: "
              f"{time.time() - t0:.0f}s | {res.summary()}", flush=True)
        if not m.get("insufficient_data"):
            print(f"    expectancy={m['expectancy_pnl']:.2f}/trade | "
                  f"win_rate={m['win_rate']*100:.1f}% | PF={m.get('profit_factor')} | "
                  f"caps bound: {json.dumps(res.constraint_log)[:200]}", flush=True)

    # 6. Performance + drawdown analysis.
    first, last = (min(eq.index[0] for eq in equities.values()),
                   max(eq.index[-1] for eq in equities.values()))
    months = (last - first).days / 30.4375
    rows = {n: perf_row(r["res"], months) for n, r in results.items()}
    print_perf_table(rows)

    print(f"\nDRAWDOWN ANALYSIS (window-local peak-to-trough; false breakout = "
          f"trade entered in window, closed P&L < 0)")
    dd_out: dict[str, dict] = {}
    for wname, (ws, we) in DD_WINDOWS.items():
        dd_out[wname] = {}
        print(f"  {wname} [{ws} -> {we}]")
        for name, r in results.items():
            dd = window_maxdd(r["res"].equity, ws, we)
            ent = window_entries(r["res"].trades, ws, we)
            dd_out[wname][name] = {"max_dd_pct": (dd * 100 if dd is not None else None), **ent}
            dd_s = f"{dd*100:.1f}%" if dd is not None else "n/a"
            wr_s = f"{ent['win_rate']*100:.0f}%" if ent["win_rate"] is not None else "n/a"
            print(f"    {name:22s} window maxDD={dd_s:>6s} | entries={ent['entries']:>3d} "
                  f"false_breakouts={ent['false_breakouts']:>3d} win_rate={wr_s:>4s} "
                  f"pnl={ent['pnl']:>10.2f}")

    # 7. Persist + determinism hash (canonical payload, wall-clock excluded).
    def book_payload(r: dict) -> dict:
        res = r["res"]
        return {"spec": r["spec"], "gate_blocked": r["gate_blocked"],
                "metrics": res.metrics, "constraint_log": res.constraint_log,
                "per_instrument": res.per_instrument,
                "trades": [t.__dict__ for t in res.trades],
                "equity": {str(k): v for k, v in res.equity.items()}}

    canonical = {"seed": args.seed, "holdout_start": args.holdout_start,
                 "universe": UNIVERSE, "risk_overrides": RISK_OVERRIDES,
                 "commission_per_side": COMMISSION_PER_SIDE,
                 "screening": scores, "books": {n: book_payload(r) for n, r in results.items()},
                 "drawdown_windows": dd_out,
                 "perf_rows": rows, "months": months}
    blob = json.dumps(canonical, sort_keys=True, default=str).encode()
    digest = hashlib.sha256(blob).hexdigest()

    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "mode": "iteration", "note": "owner-requested measurement; NOT an adoption "
                                        "gate (no DSR/PBO verdict by design)",
           "ledger_recorded": not args.no_ledger, "n_trials_before": n_before,
           "result_sha256": digest, **canonical}
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nresults written to {out_path}")
    print(f"RESULT_SHA256: {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
