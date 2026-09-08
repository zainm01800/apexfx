"""Advance existing V24/V30 accounts using settled, archived official cash sessions only."""
from __future__ import annotations
import argparse
import copy
from datetime import datetime,timezone
from pathlib import Path
import sys
from dotenv import load_dotenv
ENGINE_DIR=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ENGINE_DIR));load_dotenv(ENGINE_DIR/'.env')
from apex_quant.forward_intraday.data import (DataUnavailable,XNYS,fetch_settled_inputs,
    build_historical_warmup,thaw_session,required_warmup_sessions)
from apex_quant.forward_intraday.engine import (step_session,export_public_payload,
    first_eligible_session,validate_history,state_sha256)
from apex_quant.forward_intraday.spec import BOOKS
from apex_quant.forward_intraday.storage import fetch_remote,load_local,save_local,write_remote_verified

def local_path(book_id):return ENGINE_DIR/'data_store'/f'paper_portfolio_{book_id}'/'state.json'

def run_activate(spec,local_only=False,force=False):
    raise RuntimeError('Activation/reset disabled: this runner repairs and advances existing authoritative accounts only')

def advance_existing(state,spec,market,now):
    validate_history(state,spec)
    original=copy.deepcopy(state);st=copy.deepcopy(state)
    archive=st.setdefault('input_archive',{})
    for day,record in market['sessions'].items():
        repairs=record.get('source',{}).get('verified_historical_warmup_repairs',[])
        if any(r.get('timestamp','')[:10]==day for r in repairs) and day>=first_eligible_session(st):
            raise DataUnavailable('Historical warm-up repair cannot be used as an execution-session bar')
        if day not in archive:archive[day]=copy.deepcopy(record)
        # Later provider revisions never rewrite an already frozen execution/feature input.
    st['last_checked_at_utc']=now.isoformat()
    st.pop('runner_error',None)
    if not st.get('last_processed_session'):
        st['status']='waiting_for_frozen_warmup'
        st['data_readiness']='Collecting the required prior 15 official cash sessions; no assumed warmup'
    first=str(XNYS.next_session(st['last_processed_session']).date()) if st.get('last_processed_session') else first_eligible_session(st)
    if not st.get('last_processed_session') and first>market['latest']:
        prior,_,needed=required_warmup_sessions(first,require_noise=spec.strategy_variant=='noise_band')
        if len(prior)==15 and all(str(d.date()) in archive for d in needed):
            # No current-session bar or fixing is assumed. Actual execution still
            # calls build_historical_warmup and the full settlement/risk checks.
            for d in needed:thaw_session(archive[str(d.date())],now)
            st['status']='ready_waiting_settled_session'
            st['data_readiness']='Prior 15 cash sessions archived; awaiting next settled session and qualified FX'
    pending=list(XNYS.sessions_in_range(first,market['latest'])) if first<=market['latest'] else []
    for day in pending:
        label=str(day.date())
        if label not in archive:
            st['status']='blocked_missing_settled_session'
            st['data_readiness']=market.get('issues',{}).get(label,f'Missing frozen cash session {label}; no gap replay')
            break
        warmup=build_historical_warmup(archive,label,require_noise=spec.strategy_variant=='noise_band')
        st=step_session(st,spec,warmup,thaw_session(archive[label],now),label,now_utc=now)
    if st==original:return st
    st['parent_state_sha256']=state_sha256(original)
    st['revision']=max(original['revision']+1,st['revision'])
    st.setdefault('execution_repair',{'version':1,'mode':'settled_session_only',
        'adopted_at_utc':now.isoformat(),'original_activation_preserved':True})
    return st

def run_step(spec,local_only=False,dry_run=False):
    path=local_path(spec.book_id)
    if local_only:
        payload=load_local(path,spec)
        if payload is None:raise RuntimeError('Existing local account missing; no reseed')
    else:
        remote=fetch_remote(spec)
        if remote.status!='found':raise RuntimeError(f'Authoritative account {remote.status}; no local fallback or reseed')
        payload=remote.payload
    state=payload['state'];now=datetime.now(timezone.utc)
    error=None
    try:
        market=fetch_settled_inputs(now)
        new=advance_existing(state,spec,market,now)
    except Exception as exc:
        # A failed update may report health, but cannot mutate existing trades or cash.
        error=exc;new=copy.deepcopy(state)
        new.update(status='blocked_input_or_execution',runner_error=str(exc)[:1000],
            last_checked_at_utc=now.isoformat(),revision=state['revision']+1,
            parent_state_sha256=state_sha256(state))
    if new==state:
        print(f'[{spec.book_id}] No new settled session or first-seen input.');return
    output=export_public_payload(new,spec)
    print(f"[{spec.book_id}] {new.get('status')}: last={new.get('last_processed_session')}, cash=GBP{new['cash']:.2f}")
    if dry_run:
        print('Dry run: no persistence')
        if error:raise RuntimeError(str(error)) from error
        return
    if not local_only:write_remote_verified(output,spec,expected_state_sha256=state_sha256(state))
    save_local(path,output,spec)
    if error:raise RuntimeError(str(error)) from error

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--book',choices=['v24','v30','all'],default='all')
    parser.add_argument('--step',action='store_true');parser.add_argument('--local-only',action='store_true')
    parser.add_argument('--dry-run',action='store_true');parser.add_argument('--activate',action='store_true')
    parser.add_argument('--force',action='store_true');args=parser.parse_args()
    if args.activate or args.force:parser.error('Activation/reseed is disabled; preserve existing account')
    specs=BOOKS.values() if args.book=='all' else [BOOKS[args.book]]
    failed=False
    for spec in specs:
        try:
            if args.step:run_step(spec,args.local_only,args.dry_run)
            else:print(f'[{spec.book_id}] Settled-session paper reconstruction; --step advances existing account only.')
        except Exception as exc:
            failed=True;print(f'[{spec.book_id}] BLOCKED: {exc}',file=sys.stderr)
    if failed:raise SystemExit(1)
if __name__=='__main__':main()
