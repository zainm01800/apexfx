"""Write backtest/validation results to the Supabase knowledge base.

Reuses the same project + public anon key as the JS app's /api/memory (the anon
key is already shipped to the browser, so it's not a secret). Override with
SUPABASE_URL / SUPABASE_ANON_KEY env vars if needed. Upserts on the row id so
re-running a config refreshes its latest result rather than duplicating.
"""

from __future__ import annotations

import os

_SUPA_URL = os.environ.get("SUPABASE_URL", "https://dtiuwllodzqpbwohzrgj.supabase.co").rstrip("/")
_SUPA_ANON = os.environ.get(
    "SUPABASE_ANON_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImR0aXV3bGxvZHpxcGJ3b2h6cmdqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA1MDAwODYsImV4cCI6MjA5NjA3NjA4Nn0.fxOdfqskMpwVYIP2aL1LbeSgOMFfv3223IjzM6ldi5k",
)
_TABLE = f"{_SUPA_URL}/rest/v1/apex_backtests"


def _num(x):
    try:
        return None if x is None else round(float(x), 6)
    except (TypeError, ValueError):
        return None


def backtest_row(report: dict, *, config_label: str, timeframe: str = "1d") -> dict:
    """Flatten a ValidationReport dict into a knowledge-base row."""
    dsr = report.get("dsr", {}) or {}
    pbo = report.get("pbo", {}) or {}
    cpcv = report.get("cpcv", {}) or {}
    verdict = report.get("verdict", {}) or {}
    inst = report.get("instrument", "?")
    strat = report.get("strategy", "?")
    return {
        "id": f"{inst}|{strat}|{config_label}",
        "instrument": inst,
        "strategy": strat,
        "config_label": config_label,
        "timeframe": timeframe,
        "passed": bool(verdict.get("passed", False)),
        "dsr": _num(dsr.get("dsr")),
        "pbo": _num(pbo.get("pbo")),
        "oos_sharpe_median": _num(cpcv.get("oos_sharpe_median")),
        "frac_positive": _num(cpcv.get("frac_positive")),
        "n_paths": int(cpcv.get("n_paths") or 0),
        "observed_sharpe_ann": _num(dsr.get("observed_sharpe_ann")),
        "config_version": int(report.get("config_version") or 0),
        "generated_for": report.get("generated_for", ""),
    }


def upsert_backtests(rows: list[dict]) -> bool:
    """Upsert rows (merge on the primary key). Returns True on success."""
    if not rows:
        return True
    try:
        import httpx

        with httpx.Client(timeout=20) as c:
            r = c.post(
                _TABLE,
                headers={
                    "apikey": _SUPA_ANON,
                    "Authorization": f"Bearer {_SUPA_ANON}",
                    "Content-Type": "application/json",
                    "Prefer": "resolution=merge-duplicates,return=minimal",
                },
                json=rows,
            )
            return r.status_code in (200, 201, 204)
    except Exception:
        return False


def post_backtest(report: dict, *, config_label: str, timeframe: str = "1d") -> bool:
    return upsert_backtests([backtest_row(report, config_label=config_label, timeframe=timeframe)])
