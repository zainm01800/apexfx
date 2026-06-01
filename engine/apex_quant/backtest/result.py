"""Backtest result container + performance metrics.

Returns are computed from the *strategy* equity curve (mark-to-market), not
buy-and-hold. Metrics are deliberately plain and auditable - the validation
layer (CPCV/DSR/PBO) is what decides whether any of this is real.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from pydantic import BaseModel


class Trade(BaseModel):
    instrument: str
    direction: str
    entry_time: str
    entry_price: float
    exit_time: str
    exit_price: float
    units: float
    pnl: float
    return_pct: float
    exit_reason: str


def compute_metrics(equity: pd.Series, trades: list[Trade], periods_per_year: int = 252) -> dict:
    if len(equity) < 2:
        return {"n_trades": len(trades), "insufficient_data": True}

    rets = equity.pct_change().dropna()
    e0, e1 = float(equity.iloc[0]), float(equity.iloc[-1])
    n_years = len(equity) / periods_per_year
    ann_return = (e1 / e0) ** (1 / n_years) - 1 if n_years > 0 and e0 > 0 else 0.0
    ann_vol = float(rets.std(ddof=1) * np.sqrt(periods_per_year)) if len(rets) > 1 else 0.0
    sharpe = float(rets.mean() / rets.std(ddof=1) * np.sqrt(periods_per_year)) if rets.std(ddof=1) > 0 else 0.0

    dd = equity / equity.cummax() - 1.0
    max_dd = float(-dd.min())
    calmar = ann_return / max_dd if max_dd > 0 else 0.0

    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_rate = len(wins) / len(trades) if trades else 0.0
    profit_factor = (sum(wins) / abs(sum(losses))) if losses else (float("inf") if wins else 0.0)
    avg_trade = float(np.mean([t.return_pct for t in trades])) if trades else 0.0

    return {
        "total_return": e1 / e0 - 1,
        "ann_return": ann_return,
        "ann_vol": ann_vol,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "calmar": calmar,
        "n_trades": len(trades),
        "win_rate": win_rate,
        "profit_factor": profit_factor if np.isfinite(profit_factor) else None,
        "avg_trade_return": avg_trade,
        "final_equity": e1,
    }


@dataclass
class BacktestResult:
    instrument: str
    equity: pd.Series
    trades: list[Trade] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    @property
    def returns(self) -> pd.Series:
        return self.equity.pct_change().dropna()

    def summary(self) -> str:
        m = self.metrics
        if m.get("insufficient_data"):
            return f"{self.instrument}: insufficient data ({m.get('n_trades',0)} trades)"
        return (
            f"{self.instrument}: ret={m['total_return']*100:.1f}% "
            f"ann={m['ann_return']*100:.1f}% vol={m['ann_vol']*100:.1f}% "
            f"sharpe={m['sharpe']:.2f} maxDD={m['max_drawdown']*100:.1f}% "
            f"trades={m['n_trades']} win={m['win_rate']*100:.0f}%"
        )

    def to_dict(self, equity_points: int = 250) -> dict:
        """API-friendly: downsampled equity curve + metrics + trade count."""
        eq = self.equity
        if len(eq) > equity_points:
            step = len(eq) // equity_points
            eq = eq.iloc[::step]
        return {
            "instrument": self.instrument,
            "metrics": self.metrics,
            "equity_curve": [
                {"t": ts.strftime("%Y-%m-%d"), "equity": round(float(v), 2)}
                for ts, v in eq.items()
            ],
            "n_trades": len(self.trades),
        }
