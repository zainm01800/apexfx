"""Funded-account and entry-bar realism diagnostics for Book C.

This is not a parameter-selection gate.  It uses the frozen pre-2025 panel and
compares only the certified control with the two defensive variants already
registered by ``book_c_deep_audit_prereg_2026-08-19.md``.

Daily closes cannot reveal intraday equity-limit violations, and daily equity
changes are only a proxy for FTMO's closed-P&L Best Day calculation.  Results are
therefore optimistic lower bounds, not a representation that an account passes.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

from apex_quant.backtest.portfolio import PortfolioBacktester
from apex_quant.data import PointInTimeAccessor
from apex_quant.risk.types import Direction

from run_book_c_deep_audit import (
    BASELINE,
    CALENDAR_PPY,
    PARAMS,
    SPECS,
    WARMUP,
    TrendBook,
    _cfg,
    _load_panel,
    _result_payload,
    _tm,
)


DEFAULT_OUT = ENGINE_DIR / "data_store" / "validation" / "book_c_funded_diagnostics_2026-08-19.json"
ACCOUNT = 100_000.0


class EntryBarCensusBacktester(PortfolioBacktester):
    """Certified engine plus a read-only census of what its entry day contains."""

    def __init__(self, panel, *args, **kwargs):
        self._audit_panel = panel
        self.entry_bar_events: list[dict] = []
        super().__init__(*args, **kwargs)

    def _enter(self, pend, open_price, t, i, instrument):
        pos = super()._enter(pend, open_price, t, i, instrument)
        row = self._audit_panel[instrument].loc[t]
        is_long = pos["direction"] == Direction.LONG
        stop_hit = bool(row["low"] <= pos["stop"]) if is_long else bool(row["high"] >= pos["stop"])
        target_hit = bool(row["high"] >= pos["target"]) if is_long else bool(row["low"] <= pos["target"])
        risk_dist = abs(pos["entry_price"] - pos["initial_stop"])
        p1 = pos["entry_price"] + risk_dist if is_long else pos["entry_price"] - risk_dist
        p1_hit = bool(row["high"] >= p1) if is_long else bool(row["low"] <= p1)
        self.entry_bar_events.append({
            "date": str(pd.Timestamp(t).date()),
            "instrument": instrument,
            "direction": pos["direction"].value,
            "stop_hit": stop_hit,
            "target_hit": target_hit,
            "both_stop_and_target": stop_hit and target_hit,
            "first_partial_hit": p1_hit,
        })
        return pos


def _run_with_entry_census(panel):
    cfg = _cfg(SPECS[BASELINE]["changes"])
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    model = TrendBook(panel, **PARAMS)
    bt = EntryBarCensusBacktester(
        panel, cfg, exit_mode="managed", trade_manager=_tm(SPECS[BASELINE])
    )
    res = bt.run(
        pits,
        model.strategies(),
        timeframes={k: "1d" for k in panel},
        warmup=WARMUP,
        periods_per_year=CALENDAR_PPY,
    )
    return res, bt.entry_bar_events


def _run_variant(panel, name):
    cfg = _cfg(SPECS[name]["changes"])
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    model = TrendBook(panel, **PARAMS)
    return PortfolioBacktester(
        cfg, exit_mode="managed", trade_manager=_tm(SPECS[name])
    ).run(
        pits,
        model.strategies(),
        timeframes={k: "1d" for k in panel},
        warmup=WARMUP,
        periods_per_year=CALENDAR_PPY,
    )


def _phase(
    returns: np.ndarray,
    start: int,
    *,
    target_pct: float,
    daily_loss_pct: float,
    trailing_loss: bool,
    best_day_rule: bool,
    max_days: int = 365,
) -> dict:
    equity = ACCOUNT
    prior_peak = ACCOUNT
    positive_sum = 0.0
    best_day = 0.0
    active_days = 0
    end = min(len(returns), start + max_days)
    for j in range(start, end):
        prior_balance = equity
        pnl = equity * float(returns[j])
        equity += pnl
        if abs(pnl) > 1e-9:
            active_days += 1
        if pnl > 0:
            positive_sum += pnl
            best_day = max(best_day, pnl)

        daily_floor = prior_balance - daily_loss_pct * ACCOUNT
        loss_floor = prior_peak - 0.10 * ACCOUNT if trailing_loss else 0.90 * ACCOUNT
        if equity <= daily_floor:
            return {"status": "fail", "reason": "daily_loss", "days": j - start + 1,
                    "next": j + 1, "end_equity": equity}
        if equity <= loss_floor:
            return {"status": "fail", "reason": "max_loss", "days": j - start + 1,
                    "next": j + 1, "end_equity": equity}

        share = best_day / positive_sum if positive_sum > 0 else 1.0
        consistency_ok = not best_day_rule or share <= 0.50
        min_days_ok = active_days >= (1 if best_day_rule else 4)
        if equity >= ACCOUNT * (1.0 + target_pct) and consistency_ok and min_days_ok:
            return {"status": "pass", "reason": "target", "days": j - start + 1,
                    "next": j + 1, "end_equity": equity, "best_day_share_proxy": share}
        prior_peak = max(prior_peak, equity)

    return {"status": "timeout", "reason": "window_end", "days": end - start,
            "next": end, "end_equity": equity}


def _rolling_evaluations(series: pd.Series) -> dict:
    rets = series.dropna().to_numpy(dtype=float)

    one_step = []
    # Equal 365-observation opportunity for every start; daily starts overlap and
    # are descriptive scenarios rather than independent statistical trials.
    for start in range(max(0, len(rets) - 365 + 1)):
        one_step.append(_phase(
            rets, start, target_pct=0.10, daily_loss_pct=0.03,
            trailing_loss=True, best_day_rule=True,
        ))

    two_step = []
    for start in range(max(0, len(rets) - 730 + 1)):
        p1 = _phase(
            rets, start, target_pct=0.10, daily_loss_pct=0.05,
            trailing_loss=False, best_day_rule=False,
        )
        if p1["status"] != "pass":
            two_step.append({"status": p1["status"], "reason": f"phase1_{p1['reason']}",
                             "days": p1["days"]})
            continue
        p2 = _phase(
            rets, p1["next"], target_pct=0.05, daily_loss_pct=0.05,
            trailing_loss=False, best_day_rule=False,
        )
        two_step.append({
            "status": p2["status"],
            "reason": "passed_both" if p2["status"] == "pass" else f"phase2_{p2['reason']}",
            "days": p1["days"] + p2["days"],
        })

    def summary(rows):
        statuses = Counter(x["status"] for x in rows)
        reasons = Counter(x["reason"] for x in rows)
        passed_days = [x["days"] for x in rows if x["status"] == "pass"]
        resolved = statuses["pass"] + statuses["fail"]
        return {
            "n_overlapping_starts": len(rows),
            "statuses": dict(statuses),
            "reasons": dict(reasons),
            "pass_rate_all_starts": statuses["pass"] / len(rows) if rows else None,
            "pass_rate_resolved": statuses["pass"] / resolved if resolved else None,
            "median_days_to_pass": float(np.median(passed_days)) if passed_days else None,
        }

    return {"ftmo_1_step_proxy": summary(one_step), "ftmo_2_step_proxy": summary(two_step)}


def _entry_summary(events):
    keys = ("stop_hit", "target_hit", "both_stop_and_target", "first_partial_hit")
    by_inst = defaultdict(lambda: Counter(total=0))
    totals = Counter(total=len(events))
    examples = []
    for event in events:
        by_inst[event["instrument"]]["total"] += 1
        for key in keys:
            if event[key]:
                totals[key] += 1
                by_inst[event["instrument"]][key] += 1
        if (event["stop_hit"] or event["target_hit"]) and len(examples) < 20:
            examples.append(event)
    return {
        "totals": dict(totals),
        "fractions": {k: totals[k] / len(events) if events else None for k in keys},
        "by_instrument": {k: dict(v) for k, v in by_inst.items()},
        "first_20_barrier_examples": examples,
        "interpretation": (
            "The certified loop begins management on the bar after entry. Stop/target/partial "
            "touches on the entry bar are therefore omitted; OHLC cannot resolve ordering when "
            "both barriers touch."
        ),
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)
    panel = _load_panel(include_post_2025=False)

    control, events = _run_with_entry_census(panel)
    results = {BASELINE: control}
    for name in ("book_c_notional15", "book_c_risk075"):
        results[name] = _run_variant(panel, name)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_end_exclusive": "2025-01-01",
        "funded_rule_model": {
            "account": ACCOUNT,
            "window_observations_per_phase": 365,
            "one_step": "10% target; fixed $3k daily loss; $10k EOD trailing loss; 50% Best Day proxy",
            "two_step": "10% then 5% target; fixed $5k daily loss; static $10k max loss; >=4 nonzero-return days proxy",
            "limitations": (
                "Optimistic close-only proxy. Intraday floating equity, CE(S)T boundaries, exact "
                "trade-opening-day counts, closed-P&L Best Day accounting, withdrawals and fees are omitted. "
                "Rolling starts overlap and are not independent trials."
            ),
        },
        "variants": {
            name: {
                "metrics": _result_payload(res)["metrics_calendar_365"],
                "rolling_funded_proxies": _rolling_evaluations(res.returns),
            }
            for name, res in results.items()
        },
        "entry_bar_census": _entry_summary(events),
    }
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({
        "out": str(path),
        "funded": {k: v["rolling_funded_proxies"] for k, v in payload["variants"].items()},
        "entry_bar_totals": payload["entry_bar_census"]["totals"],
    }, indent=2))


if __name__ == "__main__":
    main()
