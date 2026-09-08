"""V27B one-cash slow-monthly/fast-five-session synthetic-CFD research replay.

Private extension of the sealed V22B post-fee replay, never imported or patched
in place. Sleeves share all equity, cash floors, cost accounting and portfolio
maintenance. Fast lots keep independent original holding clocks through partial
reductions, while only slow lots trail. No historical signals are computed here.

Frozen execution choices: 90% of ALL risk/gross/name ceilings at additions;
new slow lots own initial 3ATR stops and trail at 3ATR; fast lots own fixed
2.5ATR stops. Existing stops never loosen; reductions are
pro-rata within a symbol (maintenance across the portfolio); desired units use
pre-rebalance marked equity and actual additions obey combined post-fee caps.
A stop at a rebalance open blocks that symbol's same-open resurrection. A stop
before a month-end close may use that later close's new decision next session.
Daily equity is marked price-change P&L, no principal FX exposure. Opening and
adverse guard observations include liquidation fees. Possible internal touches
latch/block and surviving lots exit at the actual known close, never a made-up
floor price. Drawdown uses the prior observed end-of-day marked-equity peak.
No production, network, broker, strategy selection or data retrieval imports.
"""
from dataclasses import dataclass
from math import isfinite
from typing import Mapping
import numpy as np
import pandas as pd
import exchange_calendars as xcals

from .cash_guard import Rules,floors,latch

PROFILES={
    'lower':dict(daily=.03,maximum=.07,original_maximum=.06,instrument=.0075,aggregate=.0225,gross=2.,name=.75),
    'higher':dict(daily=.05,maximum=.12,original_maximum=.10,instrument=.01,aggregate=.03375,gross=2.,name=.75),
}
FAST_PROFILES={'lower':dict(aggregate=.015,gross=1.5,name=.5),
               'higher':dict(aggregate=.0255,gross=2.,name=.75)}

@dataclass(frozen=True)
class ReplayConfig:
    profile: str='lower'
    start: str | None=None
    end: str | None=None
    fee_bps: float=5.
    stop_slippage_bps: float=5.
    annual_holding_rate: float=0.
    annual_short_borrow_rate: float=.02
    initial_gbp: float=100000.
    entry_utilization: float=.9
    stop_atr: float=3.
    minimum_new_notional_gbp: float=1000.
    minimum_new_risk_gbp: float=25.
    fast_stop_atr: float=2.5
    fast_holding_sessions: int=5
    terminal_flat: bool=True
    def __post_init__(self):
        if self.profile not in PROFILES:raise ValueError('Unknown profile')
        for k in ('fee_bps','stop_slippage_bps','annual_holding_rate','annual_short_borrow_rate','initial_gbp','entry_utilization','stop_atr','minimum_new_notional_gbp','minimum_new_risk_gbp','fast_stop_atr'):
            v=float(getattr(self,k))
            if not isfinite(v) or v<0:raise ValueError(f'Invalid {k}')
        if self.initial_gbp<=0 or self.stop_atr<=0 or not 0<self.entry_utilization<=1:raise ValueError('Invalid capital/stop/utilization')
        if self.fee_bps>=10000 or self.stop_slippage_bps>=10000:raise ValueError('Cost rate must be below one')
        if self.fast_stop_atr<=0 or self.fast_holding_sessions!=5:raise ValueError('Invalid fixed fast stop/holding duration')

@dataclass
class ReplayResult:
    daily: pd.DataFrame
    trades: pd.DataFrame
    events: pd.DataFrame
    metrics: dict
    positions: list

def _date(x):
    t=pd.Timestamp(x)
    return (t.tz_localize('UTC') if t.tzinfo is None else t.tz_convert('UTC')).normalize()

def _schedule(x):
    out={}
    for k,v in x.items():
        d=_date(k)
        if d in out:raise ValueError('Duplicate normalized schedule date')
        out[d]=dict(v)
    return out

def _prepare(panel,fx,cfg,weights,entries,atrs,slow_symbols,fast_symbols):
    if not panel:raise ValueError('Empty panel')
    if len(panel)>39:raise ValueError('Maximum 39 instruments')
    slow_symbols=set(slow_symbols);fast_symbols=set(fast_symbols)
    if slow_symbols&fast_symbols or slow_symbols|fast_symbols!=set(panel):raise ValueError('Sleeves must exactly partition panel')
    frames={}
    for s,f in sorted(panel.items()):
        a=f.copy();a.index=pd.DatetimeIndex([_date(d) for d in a.index]);a=a.sort_index()
        if a.index.has_duplicates or not {'open','high','low','close'}.issubset(a):raise ValueError('Invalid OHLC schema')
        z=a[['open','high','low','close']].to_numpy(float)
        tolerance=np.max(np.abs(z),axis=1)*1e-12
        if (not np.isfinite(z).all() or (z<=0).any() or (z[:,1]+tolerance<z[:,[0,2,3]].max(axis=1)).any()
            or (z[:,2]-tolerance>z[:,[0,1,3]].min(axis=1)).any()):raise ValueError('Invalid OHLC values')
        frames[str(s)]=a
    dates=next(iter(frames.values())).index
    if len(dates)<2 or any(not f.index.equals(dates) for f in frames.values()):raise ValueError('Incomplete unequal calendars')
    plans=_schedule(weights);entries=_schedule(entries)
    first_calendar=min([dates[0],*plans])
    cal=xcals.get_calendar('XNYS',start=str(first_calendar.date()),end=str((dates[-1]+pd.offsets.MonthEnd(1)+pd.Timedelta(days=7)).date()))
    expected=cal.sessions_in_range(dates[0].tz_localize(None),dates[-1].tz_localize(None)).tz_localize('UTC')
    if not dates.equals(expected):raise ValueError('Panel has missing/non-XNYS sessions')
    active=dates[(dates>=(_date(cfg.start) if cfg.start else dates[0]))&(dates<=(_date(cfg.end) if cfg.end else dates[-1]))]
    if not len(active):raise ValueError('Empty active segment')
    # Validate all active FX observations before any execution or schedules.
    f=fx.copy();f.index=pd.DatetimeIndex([_date(d) for d in f.index])
    if f.index.has_duplicates or not {'rate','source_date','available_at_utc','cutoff_at_utc'}.issubset(f):raise ValueError('Invalid FX schema')
    f=f.reindex(active);rates=pd.to_numeric(f.rate,errors='coerce')
    source=pd.to_datetime(f.source_date,utc=True);available=pd.to_datetime(f.available_at_utc,utc=True);cutoff=pd.to_datetime(f.cutoff_at_utc,utc=True)
    age=(pd.Series(active,index=active)-source.dt.normalize()).dt.days
    opens=pd.Series([cal.session_open(d.tz_localize(None)) for d in active],index=active)
    if (not np.isfinite(rates).all() or (rates<=0).any() or source.isna().any() or available.isna().any() or cutoff.isna().any()
        or not (source.to_numpy()<active.to_numpy()).all() or (age<1).any() or (age>6).any()
        or (available>cutoff).any() or (cutoff>opens).any() or not (cutoff.dt.normalize().to_numpy()==active.to_numpy()).all()):
        raise ValueError('Missing/stale/unpublished FX at session open')
    atr=_schedule(atrs)
    for day,w in plans.items():
        if not cal.is_session(day.tz_localize(None)) or day>dates[-1]:raise ValueError('Weight decision is not an eligible XNYS session')
        nxt=cal.next_session(day.tz_localize(None))
        if nxt.month==day.month:raise ValueError('Weight decision is not actual scheduled month-end')
        if any(s not in slow_symbols or not isfinite(float(v)) or float(v)<0 for s,v in w.items()):raise ValueError('Invalid long-only slow weight vector')
    for day,values in entries.items():
        if day not in dates:raise ValueError('Fast decision is not a panel session')
        if any(s not in fast_symbols or not isfinite(float(v)) or float(v)<0 for s,v in values.items()):raise ValueError('Invalid long-only fast score vector')
    return frames,dates,active,rates,source,available,cutoff,plans,entries,atr

def replay(panel:Mapping,fx:pd.DataFrame,weights:Mapping,entries:Mapping,atrs:Mapping,cfg:ReplayConfig,*,slow_symbols,fast_symbols,slow_execution_by_session=None)->ReplayResult:
    frames,dates,active,rates,source,available,cutoff,plans,entries,atr=_prepare(panel,fx,cfg,weights,entries,atrs,slow_symbols,fast_symbols)
    profile=PROFILES[cfg.profile];fee=cfg.fee_bps/10000;slip=cfg.stop_slippage_bps/10000
    rules=Rules(cfg.initial_gbp,profile['daily'],profile['maximum'],'static')
    positions=[];cash=cfg.initial_gbp;previous_equity=cash;peak=cash;halted=False;next_id=1
    daily=[];trades=[];events=[];stopped={};indices={d:i for i,d in enumerate(dates)}
    seed_plans=[d for d in plans if d<active[0]];seed_plan=max(seed_plans) if seed_plans else None
    slow_execution=None if slow_execution_by_session is None else {_date(k):_date(v) for k,v in slow_execution_by_session.items()}
    arr={s:f[['open','high','low','close']].to_numpy(float) for s,f in frames.items()}
    def event(day,phase,kind,**extra):events.append(dict(sequence=len(events)+1,date=day.isoformat(),phase=phase,event=kind,**extra))
    def marked(prices,rate):return cash+sum(l['direction']*l['units']*(prices[l['symbol']]-l['entry_price'])/rate for l in positions)
    def liquidation(prices,rate):return marked(prices,rate)-sum(l['units']*prices[l['symbol']]/rate*fee for l in positions)
    def risks(prices,rate,sleeve=None):
        by_r={};by_g={}
        for l in positions:
            if sleeve is not None and l['sleeve']!=sleeve:continue
            s=l['symbol'];sf=l['stop_price']*(1-l['direction']*slip)
            r=max(0,l['direction']*(prices[s]-sf))*l['units']/rate+sf*l['units']/rate*fee
            by_r[s]=by_r.get(s,0.)+r;by_g[s]=by_g.get(s,0.)+prices[s]*l['units']/rate
        return sum(by_r.values()),sum(by_g.values()),by_r,by_g
    def atr_value(day,symbol):
        value=float(atr.get(day,{}).get(symbol,np.nan))
        if not isfinite(value) or value<=0:raise ValueError(f'Missing causal ATR: {day} {symbol}')
        return value
    def close_piece(l,units,price,day,phase,reason,rate,totals,benchmark=None):
        nonlocal cash,minimum,blocked,halted,fast_open_full
        before=l['units'];units=min(float(units),before)
        if units<=0:return
        full=units>=before*(1-1e-12)
        if full:units=before
        fraction=units/before
        ef=l['entry_fee_remaining_gbp'] if full else l['entry_fee_remaining_gbp']*fraction
        hc=l['holding_remaining_gbp'] if full else l['holding_remaining_gbp']*fraction
        bc=l['borrow_remaining_gbp'] if full else l['borrow_remaining_gbp']*fraction
        gross=l['direction']*units*(price-l['entry_price'])/rate;xf=units*price/rate*fee
        slippage=0. if benchmark is None else l['direction']*units*(benchmark-price)/rate
        cash+=gross-xf;totals['exit_fees_gbp']+=xf;totals['stop_slippage_cost_gbp']+=slippage
        trades.append(dict(lot_id=l['lot_id'],sleeve=l['sleeve'],symbol=l['symbol'],direction=l['direction'],units=units,initial_units=l['initial_units'],
            decision_date=l['decision_date'],entry_date=l['entry_date'],entry_price=l['entry_price'],entry_fx=l['entry_fx'],
            entry_session_index=l['entry_session_index'],due_session_index=l['due_session_index'],
            reserved_holding_calendar_days=l['reserved_holding_calendar_days'],reserved_holding_gbp=l['reserved_holding_gbp']*units/l['initial_units'],
            initial_stop_price=l['initial_stop_price'],stop_price=l['stop_price'],decision_atr=l['decision_atr'],
            entry_fee_gbp=ef,holding_cost_gbp=hc,borrow_cost_gbp=bc,exit_date=day.isoformat(),exit_phase=phase,exit_reason=reason,
            exit_price=price,unslipped_exit_price=price if benchmark is None else benchmark,exit_fx=rate,gross_pnl_gbp=gross,
            exit_fee_gbp=xf,stop_slippage_cost_gbp=slippage,net_pnl_gbp=gross-xf-ef-hc-bc,
            initial_total_risk_gbp=l['original_initial_risk_gbp']*units/l['initial_units'],lot_fully_closed=full))
        event(day,phase,'exit',lot_id=l['lot_id'],sleeve=l['sleeve'],symbol=l['symbol'],units=units,price=price,reason=reason,exit_fee_gbp=xf,gross_pnl_gbp=gross,lot_fully_closed=full)
        if full and phase=='open' and l['sleeve']=='fast':fast_open_full=True
        if full:positions.remove(l)
        else:
            l['units']-=units;l['entry_fee_remaining_gbp']-=ef;l['holding_remaining_gbp']-=hc;l['borrow_remaining_gbp']-=bc
        if phase=='open':
            value=liquidation(op,rate);minimum=min(minimum,value)
            blocked=blocked or value<=limits.internal_daily;halted=latch(halted,value,limits)
    def flatten(prices,day,phase,reason,rate,totals):
        for l in list(positions):close_piece(l,l['units'],prices[l['symbol']],day,phase,reason,rate,totals)
    def maintenance(prices,day,rate,limits,totals):
        e=marked(prices,rate);r,g,rs,gs=risks(prices,rate);cost=g*fee;after_full=e-cost
        constraints=[('aggregate_risk',r,profile['aggregate']),('gross',g,profile['gross'])]
        constraints += [('instrument_risk:'+s,v,profile['instrument']) for s,v in rs.items()]
        constraints += [('name:'+s,v,profile['name']) for s,v in gs.items()]
        bounds=[]
        for label,value,cap in constraints:
            if value>cap*e+1e-7:
                denominator=value-cap*cost
                bounds.append((label,max(0,cap*after_full/denominator) if denominator>0 else 0.))
        if r>max(0,e-limits.internal)+1e-7:
            bounds.append(('cash_headroom',max(0,(after_full-limits.internal)/(r-cost)) if r>cost else 0.))
        if not bounds:return False
        scale=max(0,min(1,min(x for _,x in bounds)))*(1-1e-12)
        event(day,'open','maintenance_reduction',retain_fraction=scale,reasons=[k for k,_ in bounds],equity_gbp=e,risk_gbp=r,gross_gbp=g)
        for l in list(positions):close_piece(l,l['units']*(1-scale),prices[l['symbol']],day,'open','maintenance_reduce',rate,totals)
        post=marked(prices,rate);rr,gg,rs,gs=risks(prices,rate)
        assert rr<=profile['aggregate']*post+1e-6 and gg<=profile['gross']*post+1e-6
        assert max(rs.values(),default=0)<=profile['instrument']*post+1e-6 and max(gs.values(),default=0)<=profile['name']*post+1e-6
        assert rr<=max(0,post-limits.internal)+1e-6
        return True
    def observe(value,limits):return value<=limits.internal_daily,value<=limits.internal_maximum

    for day in active:
        i=indices[day];rate=float(rates.loc[day]);op={s:float(a[i,0]) for s,a in arr.items()};cp={s:float(a[i,3]) for s,a in arr.items()}
        balance_start=cash;equity_start=previous_equity;prior_peak=peak;limits=floors(rules,balance_start,equity_start,peak)
        totals=dict(entry_fees_gbp=0.,exit_fees_gbp=0.,holding_cost_gbp=0.,borrow_cost_gbp=0.,stop_slippage_cost_gbp=0.)
        blocked=halted;gap_symbols=set();did_maintenance=False;had_position=bool(positions);fast_open_full=False
        for l in positions:
            elapsed=(day-dates[i-1]).days;notional=l['units']*arr[l['symbol']][i-1,3]/rate
            holding=notional*cfg.annual_holding_rate*elapsed/365;borrow=notional*cfg.annual_short_borrow_rate*elapsed/365 if l['direction']<0 else 0.
            l['holding_remaining_gbp']+=holding;l['borrow_remaining_gbp']+=borrow;cash-=holding+borrow
            totals['holding_cost_gbp']+=holding;totals['borrow_cost_gbp']+=borrow
        raw_open=marked(op,rate);raw_liq=liquidation(op,rate);minimum=min(equity_start,raw_liq)
        blocked=blocked or raw_liq<=limits.internal_daily;halted=latch(halted,raw_liq,limits)
        for l in list(positions):
            s=l['symbol']
            if l['direction']*(op[s]-l['stop_price'])<=0:
                close_piece(l,l['units'],op[s]*(1-l['direction']*slip),day,'open','gap_stop',rate,totals,op[s])
                stopped[s]=day;gap_symbols.add(s)
        after_gap=liquidation(op,rate);minimum=min(minimum,after_gap)
        blocked=blocked or after_gap<=limits.internal_daily;halted=latch(halted,after_gap,limits)
        if blocked or halted:
            flatten(op,day,'open','maximum_guard' if halted else 'daily_guard',rate,totals)
        else:
            for l in list(positions):
                if l['sleeve']=='fast' and i>=l['due_session_index']:
                    close_piece(l,l['units'],op[l['symbol']],day,'open','time_exit',rate,totals)
            if halted or blocked:flatten(op,day,'open','time_exit_cost_guard',rate,totals)
        if positions and not (halted or blocked):
            did_maintenance=maintenance(op,day,rate,limits,totals)
            after_maintenance=liquidation(op,rate);minimum=min(minimum,after_maintenance)
            halted=latch(halted,after_maintenance,limits);blocked=blocked or after_maintenance<=limits.internal_daily
            if halted or blocked:flatten(op,day,'open','cost_guard',rate,totals)
        decision=None
        if slow_execution is not None:
            decision=slow_execution.get(day)
            if decision is not None and (decision not in plans or decision>=day):raise ValueError('Invalid saved slow execution date')
        elif i>0 and dates[i-1] in plans:decision=dates[i-1]
        elif day==active[0]:decision=seed_plan
        if decision is not None and not (halted or blocked):
            reference=marked(op,rate);desired={s:float(w)*reference*rate/op[s] for s,w in plans[decision].items() if float(w)!=0}
            event(day,'open','rebalance',decision_date=decision.isoformat(),reference_equity_gbp=reference,target_weights=plans[decision])
            # Reductions and sign flips settle first, proportionally across lots.
            for s in sorted({l['symbol'] for l in positions if l['sleeve']=='slow'}):
                lots=[l for l in positions if l['symbol']==s];direction=lots[0]['direction'];current=sum(l['units'] for l in lots)
                target=desired.get(s,0.)*direction
                reduction=current if target<=0 else max(0,current-target)
                if reduction>=current or reduction*op[s]/rate>=cfg.minimum_new_notional_gbp:
                    fraction=min(1,reduction/current)
                    for l in list(lots):close_piece(l,l['units']*fraction,op[s],day,'open','rebalance_exit' if fraction==1 else 'rebalance_reduce',rate,totals)
            e=marked(op,rate);minimum=min(minimum,liquidation(op,rate));halted=latch(halted,minimum,limits);blocked=blocked or minimum<=limits.internal_daily
            if positions and not (halted or blocked):
                did_maintenance=maintenance(op,day,rate,limits,totals) or did_maintenance
            e=marked(op,rate)
            candidates=[]
            if not (halted or blocked):
                for s,target_signed in sorted(desired.items()):
                    if s in gap_symbols or stopped.get(s,pd.Timestamp.min.tz_localize('UTC'))>decision:
                        event(day,'open','addition_rejected',symbol=s,reason='stop_embargo',decision_date=decision.isoformat());continue
                    direction=1 if target_signed>0 else -1;held=[l for l in positions if l['symbol']==s]
                    assert all(l['direction']==direction for l in held),'Opposite lots survived sign flip'
                    units=max(0,abs(target_signed)-sum(l['units'] for l in held));notional=units*op[s]/rate
                    if notional<cfg.minimum_new_notional_gbp:continue
                    if i==0:raise ValueError('No previous completed session for ATR')
                    av=atr_value(dates[i-1],s);stop=op[s]-direction*cfg.stop_atr*av
                    if stop<=0:
                        event(day,'open','addition_rejected',symbol=s,reason='nonpositive_stop');continue
                    sf=stop*(1-direction*slip);entry_fee=notional*fee
                    stoprisk=units*max(0,direction*(op[s]-sf))/rate+units*sf/rate*fee
                    candidates.append(dict(symbol=s,direction=direction,units=units,stop_price=stop,decision_atr=av,
                        gross=notional,entry_fee=entry_fee,reserved_risk=stoprisk+entry_fee,stop_risk=stoprisk))
            if candidates:
                held_r,held_g,held_rs,held_gs=risks(op,rate);F=sum(c['entry_fee'] for c in candidates);R=sum(c['reserved_risk'] for c in candidates);G=sum(c['gross'] for c in candidates)
                u=cfg.entry_utilization;bounds=[('target',1.),('aggregate_risk',(u*profile['aggregate']*e-held_r)/(R+u*profile['aggregate']*F)),
                    ('gross',(u*profile['gross']*e-held_g)/(G+u*profile['gross']*F)),('cash_headroom',(e-limits.internal-held_r)/R)]
                bysymbol={c['symbol']:c for c in candidates}
                for s in set(held_rs)|set(bysymbol):
                    c=bysymbol.get(s);r=0. if c is None else c['reserved_risk'];g=0. if c is None else c['gross']
                    for label,current,new,cap in (('instrument_risk',held_rs.get(s,0.),r,u*profile['instrument']),('name',held_gs.get(s,0.),g,u*profile['name'])):
                        denominator=new+cap*F
                        if denominator>0:bounds.append((label+':'+s,(cap*e-current)/denominator))
                scale=max(0,min(v for _,v in bounds))*(1-1e-12)
                accepted=[c for c in candidates if c['gross']*scale>=cfg.minimum_new_notional_gbp]
                totalfee=sum(c['entry_fee']*scale for c in accepted);post_equity=e-totalfee
                event(day,'open','addition_batch',decision_date=decision.isoformat(),scale=scale,binding_constraints=[k for k,v in bounds if v<=scale+1e-9],pre_entry_equity_gbp=e,post_entry_equity_gbp=post_equity)
                for c in candidates:
                    if c not in accepted:event(day,'open','addition_rejected',symbol=c['symbol'],reason='minimum_notional_or_budget')
                for c in accepted:
                    units=c['units']*scale;ef=c['entry_fee']*scale;cash-=ef;totals['entry_fees_gbp']+=ef
                    l=dict(lot_id=next_id,sleeve='slow',symbol=c['symbol'],direction=c['direction'],units=units,initial_units=units,
                        entry_session_index=i,due_session_index=None,reserved_holding_calendar_days=0,reserved_holding_gbp=0.,
                        decision_date=decision.isoformat(),entry_date=day.isoformat(),entry_price=op[c['symbol']],entry_fx=rate,
                        initial_stop_price=c['stop_price'],stop_price=c['stop_price'],decision_atr=c['decision_atr'],
                        original_initial_risk_gbp=c['reserved_risk']*scale,entry_fee_remaining_gbp=ef,holding_remaining_gbp=0.,borrow_remaining_gbp=0.)
                    positions.append(l);next_id+=1
                    event(day,'open','entry',lot_id=l['lot_id'],sleeve='slow',symbol=l['symbol'],direction=l['direction'],units=units,price=l['entry_price'],stop_price=l['stop_price'],
                        decision_date=decision.isoformat(),atr_source_date=dates[i-1].isoformat(),entry_fee_gbp=ef,initial_total_risk_gbp=l['original_initial_risk_gbp'],post_entry_equity_gbp=post_equity)
                if accepted:
                    rr,gg,rs,gs=risks(op,rate)
                    assert rr<=u*profile['aggregate']*post_equity+1e-6 and gg<=u*profile['gross']*post_equity+1e-6
                    assert max(rs.values(),default=0)<=u*profile['instrument']*post_equity+1e-6 and max(gs.values(),default=0)<=u*profile['name']*post_equity+1e-6
                    assert rr<=post_equity-limits.internal+1e-6
            if halted or blocked:flatten(op,day,'open','rebalance_cost_guard',rate,totals)
            elif positions:
                # V22B: reduction fees can lower remaining holdings' hard caps,
                # even when the rebalance accepts no additions.
                did_maintenance = maintenance(op,day,rate,limits,totals) or did_maintenance
                after_rebalance = liquidation(op,rate)
                minimum = min(minimum,after_rebalance)
                halted = latch(halted,after_rebalance,limits)
                blocked = blocked or after_rebalance<=limits.internal_daily
                if halted or blocked:
                    flatten(op,day,'open','rebalance_cost_guard',rate,totals)
        # Fast requests use only the immediately preceding completed decision;
        # no close-time budget or account from a different sleeve is carried in.
        previous=dates[i-1] if i>0 else None
        fast_held={l['symbol'] for l in positions if l['sleeve']=='fast'}
        if not (halted or blocked or fast_open_full) and previous in entries and len(fast_held)<4:
            selected=sorted(((s,float(v)) for s,v in entries[previous].items() if float(v)>0 and s not in fast_held),key=lambda x:(-x[1],x[0]))[:4-len(fast_held)]
            candidates=[];reserve_days=(dates[min(i+cfg.fast_holding_sessions,indices[active[-1]])]-day).days
            if not cfg.terminal_flat:
                calendar=xcals.get_calendar('XNYS')
                due=day.tz_localize(None)
                for _ in range(cfg.fast_holding_sessions):due=calendar.next_session(due)
                reserve_days=(pd.Timestamp(due).tz_localize('UTC')-day).days
            for s,score in selected:
                av=atr_value(previous,s);stop=op[s]-cfg.fast_stop_atr*av
                if stop<=0:
                    event(day,'open','fast_batch_rejected',reason='nonpositive_stop',symbol=s);candidates=[];break
                sf=stop*(1-slip);holding_r=reserve_days/365*cfg.annual_holding_rate
                stop_r=max(0,op[s]-sf)/op[s]+sf/op[s]*fee
                candidates.append(dict(symbol=s,score=score,decision_atr=av,stop_price=stop,stop_r=stop_r,holding_r=holding_r,reserved_r=stop_r+holding_r+fee))
            if candidates:
                e=marked(op,rate);fr,fg,frs,fgs=risks(op,rate,'fast');p=FAST_PROFILES[cfg.profile];m=len(candidates)
                R=sum(c['reserved_r'] for c in candidates);F=m*fee
                request_bounds=[('fast_risk',(.8*p['aggregate']*e-fr)/(R+.8*p['aggregate']*F)),
                    ('fast_gross',(.8*p['gross']*e-fg)/(m+.8*p['gross']*F))]
                for s in set(fgs)|{c['symbol'] for c in candidates}:
                    added=1. if any(c['symbol']==s for c in candidates) else 0.
                    denominator=added+.8*p['name']*F
                    if denominator>0:request_bounds.append(('fast_name:'+s,(.8*p['name']*e-fgs.get(s,0.))/denominator))
                requested=max(0,min(v for _,v in request_bounds))
                # A common notional preserves the unchanged rank-selected batch.
                # All-in reserve includes new entry costs and planned financing;
                # held risk is current marked-to-costed-stop risk, as in V16.
                hr,hg,hrs,hgs=risks(op,rate);u=cfg.entry_utilization
                bounds=request_bounds+[
                    ('global_risk',(u*profile['aggregate']*e-hr)/(R+u*profile['aggregate']*F)),
                    ('global_gross',(u*profile['gross']*e-hg)/(m+u*profile['gross']*F)),
                    ('cash_headroom',(e-limits.internal-hr)/R)]
                bysymbol={c['symbol']:c for c in candidates}
                for s in set(hrs)|set(bysymbol):
                    c=bysymbol.get(s);r=0. if c is None else c['reserved_r'];g=0. if c is None else 1.
                    for label,current,new,cap in (('instrument_risk',hrs.get(s,0.),r,u*profile['instrument']),('name',hgs.get(s,0.),g,u*profile['name'])):
                        denominator=new+cap*F
                        if denominator>0:bounds.append((label+':'+s,(cap*e-current)/denominator))
                notional=max(0,min(v for _,v in bounds))*(1-1e-12)
                accepted=notional>=cfg.minimum_new_notional_gbp and notional*min(c['reserved_r'] for c in candidates)>=cfg.minimum_new_risk_gbp
                event(day,'open','fast_addition_batch',decision_date=previous.isoformat(),symbols=[c['symbol'] for c in candidates],requested_per_name_gbp=requested,
                    accepted_per_name_gbp=notional if accepted else 0.,pre_entry_equity_gbp=e,accepted=accepted,
                    binding_constraints=[k for k,v in bounds if v<=notional+1e-7],reserved_holding_calendar_days=reserve_days)
                if not accepted:event(day,'open','fast_batch_rejected',reason='minimum_notional_or_risk',selected_symbols=[c['symbol'] for c in candidates])
                else:
                    post_equity=e-m*notional*fee
                    for c in candidates:
                        s=c['symbol'];units=notional*rate/op[s];ef=notional*fee;cash-=ef;totals['entry_fees_gbp']+=ef
                        l=dict(lot_id=next_id,sleeve='fast',symbol=s,direction=1,units=units,initial_units=units,
                            decision_date=previous.isoformat(),entry_date=day.isoformat(),entry_price=op[s],entry_fx=rate,entry_session_index=i,due_session_index=i+cfg.fast_holding_sessions,
                            initial_stop_price=c['stop_price'],stop_price=c['stop_price'],decision_atr=c['decision_atr'],
                            reserved_holding_calendar_days=reserve_days,reserved_holding_gbp=notional*c['holding_r'],
                            original_initial_risk_gbp=notional*c['reserved_r'],entry_fee_remaining_gbp=ef,holding_remaining_gbp=0.,borrow_remaining_gbp=0.)
                        positions.append(l);next_id+=1
                        event(day,'open','entry',lot_id=l['lot_id'],sleeve='fast',symbol=s,direction=1,units=units,price=op[s],stop_price=l['stop_price'],
                            decision_date=previous.isoformat(),atr_source_date=previous.isoformat(),entry_fee_gbp=ef,initial_total_risk_gbp=l['original_initial_risk_gbp'],
                            post_entry_equity_gbp=post_equity,due_session_index=l['due_session_index'],reserved_holding_gbp=l['reserved_holding_gbp'])
                    rr,gg,rs,gs=risks(op,rate)
                    assert rr<=u*profile['aggregate']*post_equity+1e-6 and gg<=u*profile['gross']*post_equity+1e-6
                    assert max(rs.values(),default=0)<=u*profile['instrument']*post_equity+1e-6 and max(gs.values(),default=0)<=u*profile['name']*post_equity+1e-6
                    assert rr<=post_equity-limits.internal+1e-6
        elif fast_open_full and previous in entries:
            event(day,'open','fast_batch_blocked',reason='fast_lot_fully_closed_at_open')
        # One final global check covers either sleeve's fee effects, including
        # a fast-only day and a slow rebalance that accepts no additions.
        value=liquidation(op,rate);minimum=min(minimum,value);halted=latch(halted,value,limits);blocked=blocked or value<=limits.internal_daily
        if positions and not (halted or blocked):did_maintenance=maintenance(op,day,rate,limits,totals) or did_maintenance
        if halted or blocked:flatten(op,day,'open','post_open_cost_guard',rate,totals)
        post_equity=marked(op,rate);post_risk,post_gross,post_rs,post_gs=risks(op,rate)
        fast_post_risk,fast_post_gross,_,_=risks(op,rate,'fast');slow_post_risk,slow_post_gross,_,_=risks(op,rate,'slow')
        post_open_slow_lots=sum(l['sleeve']=='slow' for l in positions);post_open_fast_lots=sum(l['sleeve']=='fast' for l in positions)
        assert post_open_fast_lots<=4 and len({l['symbol'] for l in positions if l['sleeve']=='fast'})==post_open_fast_lots
        had_position=had_position or bool(positions)
        minimum=min(minimum,liquidation(op,rate));adverse=cash;stops=[]
        for l in positions:
            s=l['symbol'];worst=float(arr[s][i,2] if l['direction']>0 else arr[s][i,1]);touched=l['direction']*(worst-l['stop_price'])<=0
            price=l['stop_price']*(1-l['direction']*slip) if touched else worst
            adverse+=l['direction']*l['units']*(price-l['entry_price'])/rate-l['units']*price/rate*fee
            if touched:stops.append(l)
        minimum=min(minimum,adverse);daily_hit,maximum_hit=observe(adverse,limits)
        if daily_hit or maximum_hit:event(day,'intraday','possible_internal_touch',adverse_equity_gbp=adverse,daily=daily_hit,maximum=maximum_hit)
        blocked=blocked or daily_hit;halted=latch(halted,adverse,limits)
        for l in list(stops):
            stopped[l['symbol']]=day
            close_piece(l,l['units'],l['stop_price']*(1-l['direction']*slip),day,'intraday','stop',rate,totals,l['stop_price'])
        close_liq=liquidation(cp,rate);minimum=min(minimum,close_liq);halted=latch(halted,close_liq,limits);blocked=blocked or close_liq<=limits.internal_daily
        terminal=cfg.terminal_flat and day==active[-1]
        if halted or blocked or terminal:flatten(cp,day,'close','maximum_guard' if halted else 'daily_guard' if blocked else 'terminal',rate,totals)
        end_equity=marked(cp,rate);minimum=min(minimum,end_equity);halted=latch(halted,end_equity,limits);blocked=blocked or end_equity<=limits.internal_daily
        for l in positions:
            if l['sleeve']!='slow':continue
            candidate=cp[l['symbol']]-l['direction']*cfg.stop_atr*atr_value(day,l['symbol'])
            new=max(l['stop_price'],candidate) if l['direction']>0 else min(l['stop_price'],candidate)
            if new!=l['stop_price']:
                event(day,'close','trailing_stop',lot_id=l['lot_id'],sleeve='slow',symbol=l['symbol'],old_stop=l['stop_price'],new_stop=new,effective_after=day.isoformat())
                l['stop_price']=new
        daily.append(dict(date=day,day_start_balance_gbp=balance_start,day_start_equity_gbp=equity_start,prior_eod_peak_gbp=prior_peak,
            fx_rate=rate,fx_source_date=source.loc[day].isoformat(),fx_available_at_utc=available.loc[day].isoformat(),fx_cutoff_at_utc=cutoff.loc[day].isoformat(),
            external_daily_floor_gbp=limits.external_daily,external_maximum_floor_gbp=limits.external_maximum,internal_daily_floor_gbp=limits.internal_daily,internal_maximum_floor_gbp=limits.internal_maximum,
            original_external_maximum_floor_gbp=cfg.initial_gbp*(1-profile['original_maximum']),opening_equity_gbp=raw_open,conservative_min_equity_gbp=minimum,
            end_balance_gbp=cash,end_equity_gbp=end_equity,day_pnl_gbp=end_equity-equity_start,**totals,
            post_open_equity_gbp=post_equity,post_open_risk_gbp=post_risk,post_open_gross_gbp=post_gross,
            post_open_slow_risk_gbp=slow_post_risk,post_open_fast_risk_gbp=fast_post_risk,post_open_slow_gross_gbp=slow_post_gross,post_open_fast_gross_gbp=fast_post_gross,
            post_open_slow_lot_count=post_open_slow_lots,post_open_fast_lot_count=post_open_fast_lots,fast_full_open_exit=fast_open_full,
            post_open_risk_fraction=post_risk/max(post_equity,1e-12),post_open_symbol_risk_fraction=max(post_rs.values(),default=0)/max(post_equity,1e-12),
            post_open_gross_fraction=post_gross/max(post_equity,1e-12),post_open_name_fraction=max(post_gs.values(),default=0)/max(post_equity,1e-12),
            open_lot_count=len(positions),open_instrument_count=len({l['symbol'] for l in positions}),open_symbols=','.join(sorted({l['symbol'] for l in positions})),
            had_position_during_day=had_position,
            halted=halted,day_blocked=blocked,maintenance_reduction=did_maintenance,
            possible_external_daily_touch=minimum<=limits.external_daily,possible_external_maximum_touch=minimum<=limits.external_maximum,
            possible_original_maximum_touch=minimum<=cfg.initial_gbp*(1-profile['original_maximum']),
            possible_internal_daily_touch=minimum<=limits.internal_daily,possible_internal_maximum_touch=minimum<=limits.internal_maximum))
        if not cfg.terminal_flat:
            daily[-1]['open_net_pnl_gbp']=sum(l['direction']*l['units']*(cp[l['symbol']]-l['entry_price'])/rate-l['entry_fee_remaining_gbp']-l['holding_remaining_gbp']-l['borrow_remaining_gbp'] for l in positions)
        previous_equity=end_equity;peak=max(peak,end_equity)
    df=pd.DataFrame(daily).set_index('date');tf=pd.DataFrame(trades);ef=pd.DataFrame(events)
    final_equity=previous_equity
    net=final_equity-cfg.initial_gbp
    open_net=sum(l['direction']*l['units']*(cp[l['symbol']]-l['entry_price'])/rate-l['entry_fee_remaining_gbp']-l['holding_remaining_gbp']-l['borrow_remaining_gbp'] for l in positions)
    attribution=abs(net-(float(tf.net_pnl_gbp.sum()) if len(tf) else 0.)-open_net)
    if (cfg.terminal_flat and positions) or attribution>1e-6:raise AssertionError(f'Attribution failed: {attribution}')
    ret=(df.day_pnl_gbp/df.day_start_equity_gbp.where(df.day_start_equity_gbp>0)).fillna(0.)
    years=max((active[-1]-active[0]).days/365.25,len(active)/252);monthly=df.day_pnl_gbp.groupby(df.index.strftime('%Y-%m')).sum()
    completed_ids=set(tf.loc[tf.lot_fully_closed,'lot_id']) if len(tf) else set()
    lotprofits=tf.loc[tf.lot_id.isin(completed_ids)].groupby('lot_id').net_pnl_gbp.sum() if len(tf) else pd.Series(dtype=float)
    recovery=0;longest_recovery=0
    for value,oldpeak in zip(df.end_equity_gbp,df.prior_eod_peak_gbp):
        recovery=recovery+1 if value<oldpeak-1e-7 else 0;longest_recovery=max(longest_recovery,recovery)
    metrics=dict(account_currency='GBP',research_only=True,true_blind=False,funded_qualified=False,initial_gbp=cfg.initial_gbp,final_gbp=final_equity,net_gbp=net,
        total_return=net/cfg.initial_gbp,average_monthly_gbp=net/(years*12),cagr=(final_equity/cfg.initial_gbp)**(1/years)-1 if final_equity>0 else -1.,
        daily_sharpe=float(ret.mean()/ret.std(ddof=1)*np.sqrt(252)) if len(ret)>1 and ret.std(ddof=1)>0 else 0.,
        max_conservative_drawdown=float((1-df.conservative_min_equity_gbp/df.prior_eod_peak_gbp).clip(lower=0).max()),
        max_close_drawdown=float((1-df.end_equity_gbp/df.prior_eod_peak_gbp).clip(lower=0).max()),worst_day_gbp=float(df.day_pnl_gbp.min()),
        closed_lots=len(lotprofits),exit_fills=len(tf),lot_win_rate=float((lotprofits>0).mean()) if len(lotprofits) else 0.,
        profit_factor=float(lotprofits[lotprofits>0].sum()/-lotprofits[lotprofits<0].sum()) if (lotprofits<0).any() else None,
        monthly_pnl_gbp={k:float(v) for k,v in monthly.items()},positive_month_fraction=float((monthly>0).mean()),losing_month_fraction=float((monthly<0).mean()),
        worst_month_gbp=float(monthly.min()),best_month_gbp=float(monthly.max()),longest_recovery_sessions=longest_recovery,
        permanent_halt=bool(halted),terminal_flat=not bool(positions),attribution_error_gbp=attribution,maintenance_reduction_days=int(df.maintenance_reduction.sum()),
        average_gross_exposure=float(df.post_open_gross_fraction.mean()),max_post_open_risk_fraction=float(df.post_open_risk_fraction.max()),
        max_post_open_symbol_risk_fraction=float(df.post_open_symbol_risk_fraction.max()),max_post_open_gross_fraction=float(df.post_open_gross_fraction.max()),max_post_open_name_fraction=float(df.post_open_name_fraction.max()),
        original_maximum_touch_days=int(df.possible_original_maximum_touch.sum()),active_position_days=int(df.had_position_during_day.sum()),
        end_of_day_position_days=int((df.open_lot_count>0).sum()),
        last_entry_date=ef.loc[ef.event=='entry','date'].max() if len(ef) and (ef.event=='entry').any() else None)
    for key in ('entry_fees_gbp','exit_fees_gbp','holding_cost_gbp','borrow_cost_gbp','stop_slippage_cost_gbp'):metrics[key]=float(df[key].sum())
    for flag in ('external_daily','external_maximum','internal_daily','internal_maximum'):
        mask=df['possible_'+flag+'_touch'];metrics['possible_'+flag+'_touch_days']=int(mask.sum());metrics['first_possible_'+flag+'_touch']=df.index[mask][0].isoformat() if mask.any() else None
    return ReplayResult(df,tf,ef,metrics,positions)
