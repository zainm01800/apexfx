"""Deterministic state machine and execution engine for SPY intraday forward books."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
from math import isfinite
from typing import Any

import numpy as np
import pandas as pd

from .data import HistoricalWarmup, DataUnavailable, validate_session_minutes, session_times, XNYS
from . import research_kernel
from dataclasses import asdict
from .signals import (
    SignalDecision,
    compute_v24_signal,
    compute_v30_signal,
    entry_units,
)
from .spec import BOOKS, BookSpec, Profile, PROFILES, SCHEMA_VERSION


def state_sha256(state: dict) -> str:
    raw = json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def new_state(spec: BookSpec, now_utc: datetime | None = None) -> dict:
    """Create a pristine £100,000 GBP paper forward-trading state."""
    stamp = (now_utc or datetime.now(timezone.utc)).isoformat()
    profile = PROFILES["higher_5_12"]
    daily_floor = spec.initial_equity_gbp * (1.0 - spec.daily_loss_fraction)
    max_floor = spec.initial_equity_gbp * (1.0 - spec.maximum_loss_fraction)
    int_daily_floor = spec.initial_equity_gbp * (1.0 - spec.daily_loss_fraction * (1.0 - profile.buffer))
    int_max_floor = spec.initial_equity_gbp * (1.0 - spec.maximum_loss_fraction * (1.0 - profile.buffer))

    seed_daily = {
        "date": stamp[:10],
        "equity": spec.initial_equity_gbp,
        "cash": spec.initial_equity_gbp,
        "day_pnl": 0.0,
        "cum_pnl": 0.0,
        "open_pnl": 0.0,
        "drawdown_from_peak": 0.0,
        "external_daily_floor": daily_floor,
        "external_maximum_floor": max_floor,
        "internal_daily_floor": int_daily_floor,
        "internal_maximum_floor": int_max_floor,
        "is_seed": True,
        "trades": 0,
    }

    state = {
        "schema_version": SCHEMA_VERSION,
        "book_id": spec.book_id,
        "strategy_id": spec.strategy_id,
        "strategy_variant": spec.strategy_variant,
        "profile": spec.profile,
        "revision": 1,
        "parent_state_sha256": None,
        "initial_equity": spec.initial_equity_gbp,
        "cash": spec.initial_equity_gbp,
        "equity": spec.initial_equity_gbp,
        "peak": spec.initial_equity_gbp,
        "cost_total_gbp": 0.0,
        "halted": False,
        "status": "active_forward_paper",
        "activation_recorded_at_utc": stamp,
        "first_eligible_decision_session": stamp[:10],
        "last_processed_session": None,
        "last_data_as_of": stamp,
        "position": None,  # Flat active position
        "daily": [seed_daily],
        "trades": [],
        "events": [
            {
                "sequence": 1,
                "timestamp": stamp,
                "event": "account_activated",
                "initial_equity_gbp": spec.initial_equity_gbp,
                "book_id": spec.book_id,
            }
        ],
        "pending": [],
    }
    return state


def mark_equity(cash: float, pos: dict | None, price: float, fx: float, fee_rate: float, liquidation: bool = False) -> float:
    """Calculate marked equity in GBP given current price."""
    value = cash
    if pos:
        gross = pos["units"] * pos["direction"] * (price - pos["entry_price"]) / fx
        value += gross
        if liquidation:
            exit_fee = pos["units"] * price * fee_rate / fx
            value -= exit_fee
    return float(value)



def first_eligible_session(state):
    activated=pd.Timestamp(state['activation_recorded_at_utc'])
    if activated.tzinfo is None:raise DataUnavailable('Activation timestamp lacks timezone')
    label=activated.tz_convert('America/New_York').tz_localize(None).normalize()
    day=XNYS.date_to_session(label,direction='next')
    if XNYS.is_session(label) and activated>session_times(day)[0]:
        day=XNYS.next_session(day)
    declared=state.get('first_eligible_decision_session')
    if declared and pd.Timestamp(declared).tz_localize(None)>pd.Timestamp(day):
        day=XNYS.date_to_session(pd.Timestamp(declared).tz_localize(None),direction='next')
    return str(pd.Timestamp(day).date())

def validate_history(state,spec):
    if state.get('book_id')!=spec.book_id or state.get('initial_equity')!=spec.initial_equity_gbp:
        raise DataUnavailable('Preserved book identity or original seed differs')
    dates=[d['date'] for d in state.get('daily',[]) if not d.get('is_seed')]
    if dates!=sorted(set(dates)):raise DataUnavailable('Legacy duplicate/out-of-order history needs review; no automatic rewrite')
    if state.get('position') is not None:raise DataUnavailable('Legacy active position needs review; no invented overnight flatten')
    if dates and state.get('last_processed_session')!=dates[-1]:raise DataUnavailable('Stored session watermark disagrees with history')
    if not dates and state.get('last_processed_session') is not None:raise DataUnavailable('Stored watermark has no matching session')
    if not np.isfinite([state['cash'],state['equity'],state['peak']]).all() or abs(state['cash']-state['equity'])>1e-6:
        raise DataUnavailable('Invalid preserved flat-account equity')

def assemble_session(spec,warmup,bars,day):
    opening,closing=session_times(day);normal=len(bars)==390
    info=warmup.fx_info
    if not info:raise DataUnavailable('Publication-qualified fixing provenance required')
    decision_map={}
    ready=not warmup.unavailable_reason and np.isfinite([warmup.volatility_14,warmup.prior_close]).all() and warmup.volatility_14>0
    if spec.strategy_variant=='noise_band':
        ready=ready and set(range(30,361,30)).issubset(warmup.noise_sigmas)
    else:ready=ready and np.isfinite(warmup.atr_14) and warmup.atr_14>0
    if normal and ready:
        typical=(bars.high+bars.low+bars.close)/3
        vwap=(typical*bars.volume).cumsum()/bars.volume.cumsum()
        for offset in range(spec.evaluation_start_offset,spec.evaluation_end_offset+1,spec.evaluation_interval_minutes):
            previous=offset-1;stamp=bars.index[previous].isoformat()
            if spec.strategy_variant=='noise_band':
                sig=compute_v24_signal(offset,float(bars.close.iloc[previous]),float(bars.open.iloc[0]),
                    warmup.prior_close,warmup.noise_sigmas[offset],float(vwap.iloc[previous]),warmup.volatility_14,stamp)
            else:
                sig=compute_v30_signal(offset,float(bars.close.iloc[previous]),float(bars.open.iloc[0]),
                    warmup.prior_close,warmup.atr_14,warmup.volatility_14,stamp)
            if sig is not None:
                decision_map[offset]={**asdict(sig),'daily_volatility':sig.volatility,
                    'decision_at':bars.index[offset],'decision_bar_start':bars.index[previous],
                    'today_open':float(bars.open.iloc[0])}
                if spec.strategy_variant=='atr_open_stop':decision_map[offset]['barrier']=float(bars.open.iloc[0])
    return dict(date=day,times=bars.index,bars=bars,normal=normal,decisions=decision_map,
        fx=warmup.fx_rate,fx_info=info,missing=[],five_minute_returns={}),bool(ready)

def step_session(state,spec,warmup,session_bars,session_date,*,now_utc=None):
    """Append one fully settled official session, once; existing history is never replayed."""
    validate_history(state,spec)
    if state.get('last_processed_session') and session_date<=state['last_processed_session']:
        return copy.deepcopy(state)
    expected=str(pd.Timestamp(XNYS.next_session(state['last_processed_session'])).date()) if state.get('last_processed_session') else first_eligible_session(state)
    if session_date!=expected:raise DataUnavailable(f'Expected next preserved session {expected}; refusing gap/backfill {session_date}')
    bars=validate_session_minutes(session_bars,session_date,now_utc)
    session,ready=assemble_session(spec,warmup,bars,session_date)
    st=copy.deepcopy(state)
    result=research_kernel.replay([session],start=session_date,end=session_date,
        initial=st['initial_equity'],starting_cash=st['cash'],prior_peak=st['peak'],already_halted=st.get('halted',False),
        lot_id_offset=len(st.get('trades',[])),profile=PROFILES['higher_5_12'],
        cost=(spec.fee_bps_each_side,spec.stop_slippage_bps),variant='baseline' if spec.strategy_variant=='noise_band' else 'atr_open_stop')
    clean=research_kernel.clean
    daily=clean(result['daily'].iloc[0].to_dict())
    trades=clean(result['trades'].to_dict('records'))
    for trade in trades:trade['direction']='LONG' if trade['direction']==1 else 'SHORT'
    for ev in clean(result['events'].to_dict('records')):
        st['events'].append({**ev,'sequence':len(st['events'])+1,'event':ev['kind'],'timestamp':ev['time']})
    record={**daily,'is_seed':False,'cum_pnl':daily['cash']-st['initial_equity'],'open_pnl':0.,
        'drawdown_from_peak':float(result['metrics']['peak_drawdown']),
        'conservative_intraday_drawdown':float(result['metrics']['peak_drawdown']),
        'external_daily_floor':daily['daily_floor'],
        'external_maximum_floor':daily['maximum_floor'],'data_readiness':'ready' if ready else (warmup.unavailable_reason or 'invalid_warmup'),
        'execution_mode':'settled_session_paper_reconstruction','source_provenance':clean(warmup.provenance),
        'fx_info':copy.deepcopy(warmup.fx_info)}
    st['parent_state_sha256']=state_sha256(state)
    st['daily'].append(record);st['trades'].extend(trades)
    st.update(cash=daily['cash'],equity=daily['equity'],peak=daily['peak'],halted=daily['halted'],
        position=None,pending=[],last_processed_session=session_date,last_data_as_of=session_times(session_date)[1].isoformat(),
        revision=st['revision']+1,data_readiness=record['data_readiness'],
        status='halted_internal_guard' if daily['halted'] else 'active_settled_paper' if ready else 'waiting_for_frozen_warmup')
    st['cost_total_gbp']+=daily['fees_gbp']+daily['stop_slippage_cost_gbp']
    return st

def export_public_payload(state: dict, spec: BookSpec) -> dict:
    """Format authoritative payload for Supabase / API layer."""
    st = copy.deepcopy(state)
    stamp = datetime.now(timezone.utc).isoformat()
    daily = st["daily"]
    latest = daily[-1] if daily else {}

    # Format open position for public API
    public_positions = []
    if st.get("position"):
        p = st["position"]
        public_positions.append({
            "instrument": spec.symbol,
            "direction": "LONG" if p["direction"] == 1 else "SHORT",
            "units": p["units"],
            "entry_price": p["entry_price"],
            "last_px": p.get("last_px", p["entry_price"]),
            "stop_price": p["stop"],
            "initial_stop": p.get("initial_stop", p["stop"]),
            "unrealized_pnl_gbp": p.get("unrealized_pnl_gbp", 0.0),
            "entry_time": p["entry_time"],
        })

    metadata = {
        "book_id": spec.book_id,
        "label": spec.label,
        "strategy_id": spec.strategy_id,
        "strategy_variant": spec.strategy_variant,
        "profile": spec.profile,
        "account_currency": "GBP",
        "initial_equity": spec.initial_equity_gbp,
        "paper_only": True,
        "broker_enabled": False,
        "funded_qualified": False,
        "experimental": True,
        "status": "halted_internal_guard" if st.get("halted") else st.get("status", "waiting_for_settled_session"),
        "data_readiness": st.get("data_readiness", "waiting_for_frozen_warmup"),
        "execution_mode": "settled_session_paper_reconstruction",
        "minute_archive_sessions": len(st.get("input_archive", {})),
        "last_checked_at_utc": st.get("last_checked_at_utc"),
        "runner_status": "blocked" if st.get("status", "").startswith("blocked") else "ok",
        "runner_error": st.get("runner_error"),
        "conservative_max_drawdown": max((r.get("drawdown_from_peak", 0.) for r in daily), default=0.),
        "close_drawdown": latest.get("drawdown_close", 0.),
        "activation_recorded_at_utc": st.get("activation_recorded_at_utc"),
        "first_eligible_decision_session": st.get("first_eligible_decision_session"),
        "last_processed_session": st.get("last_processed_session"),
        "last_data_as_of": st.get("last_data_as_of"),
        "session_count": max(0, len(daily) - 1),
        "current_equity": float(latest.get("equity", spec.initial_equity_gbp)),
        "cash": float(latest.get("cash", spec.initial_equity_gbp)),
        "open_pnl": float(latest.get("open_pnl", 0.0)),
        "external_daily_floor": float(latest.get("external_daily_floor", 95000.0)),
        "external_maximum_floor": float(latest.get("external_maximum_floor", 88000.0)),
        "internal_daily_floor": float(latest.get("internal_daily_floor", 96250.0)),
        "internal_maximum_floor": float(latest.get("internal_maximum_floor", 91000.0)),
        "cost_total_gbp": float(st.get("cost_total_gbp", 0.0)),
        "warning": "Experimental forward paper forward test. No broker execution, real money, or funded-account qualification.",
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "book_id": spec.book_id,
        "generated_at_utc": stamp,
        "state": st,
        "daily": daily,
        "positions": public_positions,
        "trades": st.get("trades", []),
        "pending": st.get("pending", []),
        "metadata": metadata,
    }
