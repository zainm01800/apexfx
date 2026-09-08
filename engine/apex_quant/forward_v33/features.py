"""Pure signal formulas extracted unchanged from V22/V23; no historical or broker imports."""
from collections import Counter
import numpy as np
import pandas as pd
import exchange_calendars as xcals
UNIVERSE=("SPY","EFA","IYR","GSG","GLD","TLT","IEF","UUP")
NAMES=("global_relative_strength","multi_horizon_tsmom")
VOL_TARGETS={"lower":.08,"higher":.12}
FAST_NAMES=("residual5","residual5_filing_gap_excluded")

def build_features(panel):
    """Use completed scheduled month ends; no partial-month selection."""
    close = pd.DataFrame({s: panel[s].close for s in UNIVERSE})
    if close.isna().any().any() or not close.index.is_monotonic_increasing:
        raise ValueError("Missing or unordered panel")
    calendar = xcals.get_calendar("XNYS", start=str(close.index.min().date()),
                                 end=str((close.index.max() + pd.Timedelta(days=10)).date()))
    month_ends = []
    for day in close.index:
        following = calendar.next_session(day.tz_localize(None))
        if following.strftime("%Y-%m") != day.strftime("%Y-%m"):
            month_ends.append(day)
    monthly = close.loc[month_ends]
    momentum = {n: monthly / monthly.shift(n) - 1 for n in (1, 3, 6, 9, 12)}
    sma10 = monthly.rolling(10, min_periods=10).mean()
    rets = close.pct_change(fill_method=None)
    vol = rets.rolling(63, min_periods=63).std(ddof=1) * np.sqrt(252)
    atr = {}
    for symbol in UNIVERSE:
        f = panel[symbol]
        tr = pd.concat([f.high - f.low, (f.high - f.close.shift()).abs(),
                        (f.low - f.close.shift()).abs()], axis=1).max(axis=1)
        atr[symbol] = tr.rolling(20, min_periods=20).mean()
    atr = pd.DataFrame(atr)
    schedules = {name: {p: {} for p in VOL_TARGETS} for name in NAMES}
    facts = []
    for day in monthly.index:
        if any(momentum[n].loc[day].isna().any() for n in momentum):
            continue
        day_vol = vol.loc[day]
        if not np.isfinite(day_vol).all() or (day_vol <= 0).any():
            continue
        sample = rets.loc[:day].tail(63)
        if len(sample) != 63 or sample.isna().any().any():
            raise ValueError("Insufficient covariance history")
        raw_cov = sample.cov().loc[list(UNIVERSE), list(UNIVERSE)].to_numpy() * 252
        cov = .5 * raw_cov + .5 * np.diag(np.diag(raw_cov))
        scores = sum(momentum[n].loc[day] for n in momentum) / len(momentum)
        selected = sorted(UNIVERSE, key=lambda s: (-scores[s], s))[:3]
        inverse = 1 / day_vol.loc[selected]
        raw_rotation = pd.Series(0., index=UNIVERSE)
        raw_rotation.loc[selected] = inverse / inverse.sum()
        mask = monthly.loc[day] > sma10.loc[day]
        strength = sum(np.sign(momentum[n].loc[day]) for n in (1, 3, 12)) / 3
        raw_tsmom = strength / day_vol
        for name, raw in ((NAMES[0], raw_rotation), (NAMES[1], raw_tsmom)):
            v = raw.reindex(UNIVERSE).to_numpy()
            predicted = float(np.sqrt(v @ cov @ v))
            for profile, target in VOL_TARGETS.items():
                weights = raw * (target / predicted) if predicted > 0 else raw * 0
                if name == NAMES[0]:
                    weights = weights.where(mask, 0.)  # Cash slots are NOT re-levered.
                schedules[name][profile][day.isoformat()] = {
                    symbol: float(value) for symbol, value in weights.items() if value != 0
                }
        facts.append({"decision_date": day.isoformat(), "latest_month_end": day.isoformat(),
                      "earliest_return_month_end": monthly.index[monthly.index.get_loc(day)-12].isoformat(),
                      "covariance_first_session": sample.index[0].isoformat(),
                      "covariance_last_session": sample.index[-1].isoformat(),
                      "ranked_top_three": selected,
                      "trend_eligible": [s for s in selected if mask[s]],
                      "contains_outcomes": False})
    atrs = {day.isoformat(): {s: float(v) for s, v in row.items() if pd.notna(v)}
            for day, row in atr.iterrows()}
    return {"weights": schedules, "atr": atrs, "decision_facts": facts}



def features(frame, spy):
    """Beta is frozen before the five-session formation window; no fit leakage."""
    if not frame.index.equals(spy.index):
        raise ValueError("feature calendars differ")
    stock_r = np.log(frame.close / frame.close.shift(1))
    market_r = np.log(spy.close / spy.close.shift(1))
    covariance = stock_r.rolling(126, min_periods=60).cov(market_r)
    variance = market_r.rolling(126, min_periods=60).var(ddof=1)
    beta = (covariance / variance.where(variance > 0)).shift(5)
    count = stock_r.where(market_r.notna()).rolling(126, min_periods=1).count().shift(5)
    residual = stock_r.rolling(5, min_periods=5).sum() - beta * market_r.rolling(5, min_periods=5).sum()
    tr = pd.concat([frame.high-frame.low, (frame.high-frame.close.shift()).abs(),
                    (frame.low-frame.close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(20, min_periods=20).mean()
    gap_flag = ((frame.open-frame.close.shift()).abs() > 2*atr.shift(1)).astype(int)
    gap_window = gap_flag.rolling(5, min_periods=5).max().fillna(0).astype(bool)
    return pd.DataFrame(dict(beta=beta, beta_observations=count, residual5=residual,
                             atr20=atr, mechanical_gap_window=gap_window))


def known_event_mask(dates, accepted, session_closes=None):
    """Only events after pre-formation close and <= decision close are known."""
    if session_closes is None:
        session_closes = pd.DatetimeIndex([xcals.get_calendar('XNYS').session_close(d.tz_localize(None)) for d in dates])
    if len(session_closes) != len(dates):
        raise ValueError("session-close length mismatch")
    stamps = pd.DatetimeIndex(pd.to_datetime(accepted, utc=True)).sort_values()
    mask = np.zeros(len(dates), dtype=bool)
    for i in range(5, len(dates)):
        mask[i] = stamps.searchsorted(session_closes[i], side="right") > stamps.searchsorted(session_closes[i-5], side="right")
    return mask


def build(panel, spy, events):
    NAMES = FAST_NAMES
    dates = next(iter(panel.values())).index
    closes = pd.DatetimeIndex([xcals.get_calendar('XNYS').session_close(d.tz_localize(None)) for d in dates])
    entries = {name: {} for name in NAMES}
    ats, rows = {}, []
    counts = Counter()
    for symbol, frame in sorted(panel.items()):
        f = features(frame, spy)
        accepted = [row["accepted_at_utc"] for row in events if row["symbol"] == symbol]
        event_mask = known_event_mask(dates, accepted, closes)
        for i, (date, row) in enumerate(f.iterrows()):
            if np.isfinite(row.atr20) and row.atr20 > 0:
                ats.setdefault(date.isoformat(), {})[symbol] = float(row.atr20)
            if not np.isfinite(row.residual5) or row.beta_observations < 60 or not np.isfinite(row.atr20):
                continue
            eligible = row.residual5 < 0
            filing, gap = bool(event_mask[i]), bool(row.mechanical_gap_window)
            if eligible:
                strength = -float(row.residual5)
                entries[NAMES[0]].setdefault(date.isoformat(), {})[symbol] = strength
                counts["baseline_eligible_stock_decisions"] += 1
                if not filing and not gap:
                    entries[NAMES[1]].setdefault(date.isoformat(), {})[symbol] = strength
                    counts["filtered_eligible_stock_decisions"] += 1
                else:
                    counts["excluded_eligible_stock_decisions"] += 1
                    counts["eligible_with_known_filing"] += int(filing)
                    counts["eligible_with_mechanical_gap"] += int(gap)
            rows.append(dict(symbol=symbol, date=date.isoformat(),
                             decision_at_utc=closes[i].isoformat(),
                             beta_end_date=dates[i-5].isoformat(),
                             beta_observations=int(row.beta_observations), beta=float(row.beta),
                             formation_start=dates[i-4].isoformat(), residual5=float(row.residual5),
                             decision_atr=float(row.atr20), eligible=bool(eligible),
                             known_filing_window=filing, mechanical_gap_window=gap))
    return dict(entry=entries, exits={name: {} for name in NAMES}, atr=ats,
                features=rows, events=events, controls=dict(counts))
