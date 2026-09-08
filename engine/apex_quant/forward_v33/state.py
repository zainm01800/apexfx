"""Durable pre-open decisions and immutable session observations.

Replay ALWAYS starts from the original cash seed. It never adds a replay's
profits to current cash, and never terminal-flattens a running paper account.
Previously recorded decisions and observed execution bars cannot be rewritten.
"""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import numpy as np
import pandas as pd

from ..forward_v14.data import (DataUnavailable, XNYS, PRICE_COLUMNS, utc_timestamp,
    session_label, session_open_utc, next_session, select_fx, iso_date)
from .spec import SPEC, SPEC_HASH, CONTRACT, ETFS, CIKS, SYMBOLS
from .features import build_features, build
from .kernel import replay, ReplayConfig
from .data import anchored_panel
from .cost_filter import filter_scores, cost_to_stop


def clean(value):
    if isinstance(value,dict):return {str(k):clean(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [clean(v) for v in value]
    if isinstance(value,(pd.Timestamp,np.datetime64)):return pd.Timestamp(value).isoformat()
    if isinstance(value,(np.bool_,np.integer)):return value.item()
    if isinstance(value,(float,np.floating)):
        if not np.isfinite(value):return None
        return float(value)
    return value


def canonical(value):
    return json.dumps(clean(value),sort_keys=True,separators=(",",":"),allow_nan=False).encode()


def digest(value):return sha256(canonical(value)).hexdigest()


def instruction_digest(decision):
    # Confirmation is appended only after the exact original instruction was read back.
    return digest({k:v for k,v in decision.items() if k not in ("instruction_sha256","durable_verified_at_utc")})


def new_state(now=None):
    now=utc_timestamp(now or datetime.now(timezone.utc))
    day=session_label(now)
    while not XNYS.is_session(day) or session_open_utc(day)<=now:
        day+=pd.Timedelta(days=1)
    return dict(book_id="v33",spec_sha256=SPEC_HASH,revision=1,parent_state_sha256=None,
        activated_at_utc=now.isoformat(),first_execution_session=iso_date(day),
        last_processed_session=None,last_input_session=None,initial_equity=100000.,
        equity_gbp=100000.,cash_gbp=100000.,halted=False,status="waiting_for_inputs",
        status_reason="Waiting for a complete fresh price, filing and FX bundle",
        bars={},fx={},atrs={},decisions={},daily=[],trades=[],positions=[],events=[],
        provenance={},last_checked_at_utc=now.isoformat(),source_revisions=[])


def validate_state(state,spec=SPEC):
    if not isinstance(state,dict) or state.get("book_id")!="v33" or state.get("spec_sha256")!=SPEC_HASH:
        raise ValueError("V33 state identity/specification mismatch")
    if state.get("initial_equity")!=100000 or not isinstance(state.get("revision"),int) or state["revision"]<1:
        raise ValueError("V33 cash seed/revision mismatch")
    for key in ("bars","fx","atrs","decisions"):
        if not isinstance(state.get(key),dict):raise ValueError(f"V33 malformed {key}")
    for key in ("positions","trades","daily","events"):
        if not isinstance(state.get(key),list):raise ValueError(f"V33 malformed {key}")
    for key in ("cash_gbp","equity_gbp"):
        if not isinstance(state.get(key),(int,float)) or not np.isfinite(state[key]):raise ValueError(key)
    for eligible,decision in state["decisions"].items():
        if eligible!=decision.get("eligible_fill_session") or eligible!=iso_date(next_session(decision["decision_date"])):
            raise ValueError("Decision eligible session is not its next exchange session")
        if decision.get("instruction_sha256")!=instruction_digest(decision):
            raise ValueError("Frozen executable instruction hash mismatch")
        if utc_timestamp(decision["recorded_at_utc"])>=session_open_utc(eligible):
            raise ValueError("Decision was not recorded before its eligible open")
        if decision["input_sha256"]!=digest(decision["evidence"]):raise ValueError("Decision evidence hash mismatch")
        confirmed=decision.get("durable_verified_at_utc")
        if confirmed is not None and not utc_timestamp(decision["recorded_at_utc"])<=utc_timestamp(confirmed)<session_open_utc(eligible):
            raise ValueError("Decision durability was not verified before open")


def _snapshot(panel,day):
    return {symbol:[float(v) for v in panel[symbol].loc[day,list(PRICE_COLUMNS)]] for symbol in SYMBOLS}


def _enrich(lot,state,mark=None,fx=None):
    row=deepcopy(lot)
    entry=iso_date(row["entry_date"])
    record=state["decisions"].get(entry,{})
    row.update(instrument=row["symbol"],decision_recorded_at_utc=record.get("recorded_at_utc"),
        signal_evidence=record.get("evidence",{}).get(row["symbol"],{}),
        strategy_sleeve=row["sleeve"],stop_atr_multiple=3 if row["sleeve"]=="slow" else 2.5,
        partials_policy="Proportional risk reductions; monthly ETF rebalancing",
        management_rule="Monthly rebalance / 3 ATR trailing stop" if row["sleeve"]=="slow" else "Fixed 2.5 ATR stop / five-session time exit",
        signal_rationale="Top-three monthly relative strength; inverse-volatility allocation and 10-month trend filter" if row["sleeve"]=="slow" else "Negative five-session beta-adjusted stock return; filing, gap and prior-close stressed-cost exclusions passed; half stock-request budgets")
    if row["sleeve"]=="fast":row["scheduled_exit_session"]=iso_date(next_session(entry,5))
    if mark is not None:
        sf=row["stop_price"]*(1-.0005)
        row.update(last_price=mark,unrealized_pnl_gbp=row["units"]*(mark-row["entry_price"])/fx-row["entry_fee_remaining_gbp"]-row["holding_remaining_gbp"]-row["borrow_remaining_gbp"],
            current_risk_gbp=row["units"]*(max(0,mark-sf)+sf*.0005)/fx)
    return row


def _rebuild(state):
    active=[d for d in sorted(state["bars"]) if iso_date(d)>=state["first_execution_session"]]
    if not active:return
    dates=pd.to_datetime(sorted(state["bars"]),utc=True)
    panel={s:pd.DataFrame([state["bars"][d][s] for d in sorted(state["bars"])],index=dates,columns=PRICE_COLUMNS) for s in SYMBOLS}
    fx=pd.DataFrame.from_dict(state["fx"],orient="index");fx.index=pd.to_datetime(fx.index,utc=True)
    weights={};entries={};slow_execution={}
    for eligible,d in state["decisions"].items():
        if not d.get("durable_verified_at_utc"):continue
        if iso_date(d["decision_date"])<iso_date(dates[0]):continue
        if d.get("slow_decision") is not None:
            weights[d["slow_decision"]]=d["slow_weights"]
            slow_execution[eligible]=d["slow_decision"]
        if d.get("fast_eligible"):entries[d["decision_date"]]=d["fast_entries"]
    result=replay(panel,fx,weights,entries,state["atrs"],
        ReplayConfig(profile="higher",start=state["first_execution_session"],terminal_flat=False),
        slow_symbols=ETFS,fast_symbols=CIKS,slow_execution_by_session=slow_execution)
    daily=[]
    for stamp,row in result.daily.iterrows():
        raw=clean(row.to_dict());equity=raw["end_equity_gbp"];cash=raw["end_balance_gbp"]
        daily.append(dict(**raw,date=stamp.isoformat(),equity_gbp=equity,cash_gbp=cash,
            external_daily_floor=raw["external_daily_floor_gbp"],external_maximum_floor=90000.,
            drawdown_from_peak=max(0,1-raw["conservative_min_equity_gbp"]/raw["prior_eod_peak_gbp"]),open_pnl_gbp=raw["open_net_pnl_gbp"]))
    if state["daily"] and daily[:len(state["daily"])]!=state["daily"]:
        raise ValueError("New inputs would rewrite a previously saved forward session")
    new_trades=[_enrich(t,state) for t in clean(result.trades.to_dict("records"))]
    if state["trades"] and new_trades[:len(state["trades"])]!=state["trades"]:
        raise ValueError("New inputs would rewrite a previously saved exit")
    last=daily[-1];stamp=active[-1];rate=state["fx"][stamp]["rate"]
    state.update(daily=daily,trades=new_trades,
        positions=[_enrich(l,state,state["bars"][stamp][l["symbol"]][3],rate) for l in clean(result.positions)],
        events=clean(result.events.to_dict("records")),equity_gbp=last["equity_gbp"],cash_gbp=last["cash_gbp"],
        halted=bool(last["halted"]),last_processed_session=iso_date(stamp),metrics=clean(result.metrics))
    # Closed net includes reduction pieces; open net includes still-unallocated fees.
    closed=sum(t["net_pnl_gbp"] for t in state["trades"])
    opened=sum(t["unrealized_pnl_gbp"] for t in state["positions"])
    if abs(state["equity_gbp"]-100000-closed-opened)>1e-6:raise ValueError("V33 P&L reconciliation failed")
    state["open_pnl_gbp"]=opened
    state["daily"][-1]["open_pnl_gbp"]=opened


def advance(previous,market,now=None):
    validate_state(previous)
    now=utc_timestamp(now or datetime.now(timezone.utc))
    state=deepcopy(previous);panel=anchored_panel(market,state);latest=market["latest"]
    if now<market["retrieved_at_utc"]:raise DataUnavailable("Future retrieval timestamp")
    slow=build_features({s:panel[s] for s in ETFS})
    fast=build({s:panel[s] for s in CIKS},panel["SPY"],market["events"])
    if not slow["weights"]["global_relative_strength"]["higher"]:
        raise DataUnavailable("Fewer than 13 eligible completed month ends")
    first_label=pd.Timestamp(state["first_execution_session"])
    initial_history_start=min(latest,pd.Timestamp(XNYS.previous_session(first_label)).tz_localize("UTC"))
    for day in panel["SPY"].index:
        if day>latest:continue
        key=day.isoformat()
        if state["bars"] and day<=pd.Timestamp(max(state["bars"])):continue
        if not state["bars"] and day<initial_history_start:continue
        state["bars"][key]=_snapshot(panel,day)
        state["atrs"][key]={**slow["atr"].get(key,{}),**fast["atr"].get(key,{})}
        if iso_date(day)>=state["first_execution_session"]:
            state["fx"][key]=select_fx(market["fx"],day,SPEC)
    _rebuild(state)
    eligible=next_session(latest)
    # No historical pending replay: candidates may only be persisted before open.
    if session_open_utc(eligible)>now and iso_date(eligible) not in state["decisions"] and not state["halted"]:
        key=latest.isoformat();weights=slow["weights"]["global_relative_strength"]["higher"]
        monthly=max(weights)
        initial=not state["decisions"] and not state["positions"] and not state["trades"]
        slow_key=monthly if initial else key if key in weights else None
        values=fast["entry"]["residual5_filing_gap_excluded"].get(key,{})
        evidence={r["symbol"]:r for r in fast["features"] if r["date"]==key}
        prior_closes={s:float(panel[s].loc[latest,"close"]) for s in values}
        values=filter_scores(values,prior_closes,fast["atr"].get(key,{}))
        for symbol in values:
            evidence[symbol].update(prior_close=prior_closes[symbol],prior_price_date=key,
                stressed_cost_to_stop=cost_to_stop(prior_closes[symbol],fast["atr"][key][symbol]),
                maximum_cost_to_stop=.10,fast_budget_scale=.5)
        if slow_key:
            for symbol,weight in weights[slow_key].items():evidence[symbol]=dict(monthly_weight=weight,monthly_decision=slow_key,stop_atr20=slow["atr"][key][symbol])
        state["decisions"][iso_date(eligible)]=dict(recorded_at_utc=now.isoformat(),decision_date=key,
            eligible_fill_session=iso_date(eligible),slow_decision=slow_key,slow_weights=weights[slow_key] if slow_key else {},
            fast_eligible=True,fast_entries=values,evidence=clean(evidence),input_sha256=digest(evidence),
            provenance=market["provenance"])
        decision=state["decisions"][iso_date(eligible)]
        decision["instruction_sha256"]=instruction_digest(decision)
    state.update(last_input_session=iso_date(latest),last_checked_at_utc=now.isoformat(),
                 provenance=market["provenance"],status="halted" if state["halted"] else "forward_paper_active",
                 status_reason="Permanent internal loss guard" if state["halted"] else "Saved decisions await their eligible open; fills are reconciled after the completed session")
    validate_state(state)
    return state


def enforce_deadline(state,previous,now):
    state=deepcopy(state)
    old=set(previous["decisions"])
    for eligible in list(state["decisions"]):
        if eligible not in old and utc_timestamp(now)>=session_open_utc(eligible):
            del state["decisions"][eligible]
            state["status_reason"]="Decision persistence missed the next-open deadline; no retrospective entry permitted"
    return state


def confirm_durable_decisions(state,verified_at):
    """Called ONLY after a successful authoritative read-back of the entire plan.

    The confirmation may be persisted later; its timestamp attests that the
    unchanged complete plan was already durably readable before opening.
    Unconfirmed plans are never executable, even after a runner failure.
    """
    out=deepcopy(state);stamp=utc_timestamp(verified_at)
    for eligible,d in out["decisions"].items():
        if not d.get("durable_verified_at_utc") and stamp<session_open_utc(eligible):
            d["durable_verified_at_utc"]=stamp.isoformat()
    validate_state(out)
    return out


def public_payload(state,now=None):
    validate_state(state)
    now=utc_timestamp(now or datetime.now(timezone.utc))
    pending=[]
    for eligible,d in state["decisions"].items():
        if state["halted"]:break
        if state["last_processed_session"] and eligible<=state["last_processed_session"]:continue
        held={p["symbol"] for p in state["positions"] if p["sleeve"]=="fast"}
        selected=sorted(((s,v) for s,v in d["fast_entries"].items() if s not in held),key=lambda x:(-x[1],x[0]))[:max(0,4-len(held))]
        for sleeve,names in (("slow",list(d["slow_weights"])),("fast",[s for s,_ in selected])):
            for symbol in names:
                pending.append(dict(symbol=symbol,instrument=symbol,direction=1,sleeve=sleeve,
                    decision_durability="verified_before_open" if d.get("durable_verified_at_utc") else "unconfirmed_not_executable",
                    decision_date=d["slow_decision"] if sleeve=="slow" else d["decision_date"],
                    decision_recorded_at_utc=d["recorded_at_utc"],eligible_fill_session=eligible,
                    signal_evidence=d["evidence"].get(symbol,{}),stop_atr_multiple=3 if sleeve=="slow" else 2.5,
                    management_rule="Monthly rebalance / trailing stop" if sleeve=="slow" else "Five-session exit / fixed stop",
                    partials_policy="Proportional risk reductions; no profit-target partials",
                    signal_rationale="Candidate only: shared portfolio budgets and the actual opening gap determine whether a fill is allowed"))
    daily=state["daily"] or [dict(date=state["activated_at_utc"],equity_gbp=100000.,cash_gbp=100000.,
        day_pnl_gbp=0.,open_pnl_gbp=0.,drawdown_from_peak=0.,is_seed=True,external_daily_floor=95000.,external_maximum_floor=90000.)]
    metadata=dict(book_id="v33",profile=CONTRACT["profile"],label=SPEC.label,account_currency="GBP",initial_equity=100000,
        paper_only=True,broker_enabled=False,research_only=True,funded_qualified=False,true_blind=False,
        activation_recorded_at_utc=state["activated_at_utc"],last_processed_session=state["last_processed_session"],
        last_input_session=state["last_input_session"],last_checked_at_utc=state["last_checked_at_utc"],
        first_eligible_execution_session=state["first_execution_session"],session_count=len(state["daily"]),
        status=state["status"],runner_status="blocked" if state["status"]=="blocked" else "ok",
        runner_error=state.get("runner_error"),spec_sha256=SPEC_HASH,contract=CONTRACT,
        evidence_note="Post-selection research. Forward inputs are a new adjusted-ETF/stock proxy feed, not the old sealed historical inputs.",
        execution_note="Pre-open recorded signals; simulated daily-bar fills settled after the close. No broker orders.")
    return clean(dict(schema_version=1,book_id="v33",generated_at_utc=now.isoformat(),metadata=metadata,
        state=state,daily=daily,positions=state["positions"],trades=state["trades"],pending=pending))
