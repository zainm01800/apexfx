"""Archive/activate or advance isolated repaired A/B/C/R/S/F paper accounts.

No broker routes. --activate inserts new namespaces only and never imports old
profit. Normal runs require an authoritative restored document and fresh bars.
"""
from __future__ import annotations
import argparse
import copy
import importlib
from pathlib import Path
import sys
import pandas as pd
from dotenv import load_dotenv

ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ENGINE))
sys.path.insert(0,str(ENGINE/'scripts'))
load_dotenv(ENGINE/'.env')

from apex_quant.storage import repaired_paper as storage, paper_store
from apex_quant.models.paper_accounting import VERSION
from apex_quant.models.paper_readiness import require_daily_panel
from apex_quant.backtest.paper import PaperPortfolio
from apex_quant.config import get_config
from apex_quant.data import ParquetStore, clean, get_adapter

DRIVERS = {'a':'run_paper_portfolio','b':'run_paper_portfolio_challenger','c':'run_paper_portfolio_c',
           'r':'run_paper_portfolio_r','s':'run_paper_portfolio_s','f':'run_paper_portfolio_f'}


def initial_engine(book,now):
    if book in 'abc':
        d=importlib.import_module(DRIVERS[book])
        params=d.STATE_PARAMS if book=='c' else {**d.BOOK_PARAMS,**({'spill_L':d.SPILL_L} if book=='b' else {})}
        st=PaperPortfolio({}, {}, book=d.BOOK_LABEL,params=params,initial_equity=100000,account_currency='GBP').to_state()
        st.update(last_processed_date=str(now.date()),equity_curve=[[str(now.date()),100000.]])
        return st
    if book=='r':
        from apex_quant.research.book_r_forward import _new_state
        return _new_state(now,{})
    if book=='f':
        from apex_quant.models.book_f_forward import new_book_f_state
        return new_book_f_state(now)
    from apex_quant.models.book_s_session_smc import new_book_s_state
    st=new_book_s_state(now)
    st['last_processed_time']=now.strftime('%Y-%m-%d %H:%M:%S')
    st['equity_curve'][0]['timestamp']=st['last_processed_time']
    return st


def payload_for(book,engine,*,activated,revision,archive_sha,daily=None,now=None):
    now=now if now is not None else pd.Timestamp.now(tz='UTC')
    currency='GBP' if book in 'abc' else 'USD'
    risk={'a':get_config().risk.max_risk_per_trade,'b':.01,'c':.0085,'r':None,'s':.005,'f':.0034}[book]
    if book in 'abc':
        positions=[{'instrument':sym,**p} for sym,p in engine['open_positions'].items()]
        pending=[{'instrument':sym,**p} for sym,p in engine['pending'].items()]
        if daily is None:
            daily=[dict(date=str(now.date()),equity=100000.,cash=100000.,day_pnl=0.,cum_pnl=0.,drawdown_from_peak=0.,gross_exposure_x=0.,state_extra={'params':engine['params']})]
    elif book=='r':
        from apex_quant.research.book_r_forward import display_position_rows,display_daily_rows
        positions=display_position_rows(engine);daily=display_daily_rows(engine)
        pending=[{'instrument':sym,**p} for sym,p in engine['pending'].items()]
    elif book=='f':
        from apex_quant.models.book_f_forward import display_position_rows,display_daily_rows
        positions=display_position_rows(engine);daily=display_daily_rows(engine)
        pending=[{'instrument':sym,**p} for sym,p in engine['pending'].items()]
    else:
        positions=[{'instrument':sym,**p} for sym,p in engine['positions'].items()]
        daily=engine['equity_curve']
        pending=[{'instrument':sym,**p} for sym,p in engine['pending'].items()]
    state={'book_id':book,'accounting_version':VERSION,'activated_at_utc':activated,'revision':revision,'engine':engine}
    return {'schema_version':2,'book_id':book,'generated_at_utc':now.isoformat(),'state':state,
            'state_sha256':storage.digest(state),'daily':daily,'positions':positions,'trades':engine['trades'],'pending':pending,
            'metadata':{'book_id':book,'account_currency':currency,'initial_equity':100000.,'paper_only':True,
                        'broker_enabled':False,'atomic_snapshot':True,'accounting_version':VERSION,
                        'activation_recorded_at_utc':activated,'archive_id':storage.archive_id(book),'archive_sha256':archive_sha,
                        'status':'repaired_forward_paper','halted':engine.get('halted',False),
                        'trade_risk_fraction':risk,
                        'limitations':'Paper observation only. Generic bar/spread/FX proxies; no funded-compliance certification.'}}


def activate(book,now):
    # An existing new account is never reseeded, even after a partial retry.
    if storage.read(storage.runtime_id(book)) is not None:
        print(f'{book}: repaired account already exists; unchanged')
        return
    archived=storage.read(storage.archive_id(book))
    if archived is None:
        import httpx
        r=httpx.get('https://apexfx.vercel.app/api/paper',params={'book':book,'table':'state','limit':500,'edition':'legacy'},timeout=40)
        r.raise_for_status();snapshot=r.json()
        if snapshot.get('book_id')!=book or snapshot.get('metadata',{}).get('accounting_version')==VERSION:
            raise ValueError('Archive source is not the original legacy book')
        raw=None
        if book in 'crsf':
            ident=getattr(paper_store,'FALLBACK_ID_'+book.upper())
            raw=storage.read(ident)
        archived={'book_id':book,'archived_at_utc':now.isoformat(),'snapshot':snapshot,'raw_runtime':raw}
        storage.write(storage.archive_id(book),archived)
    initial=initial_engine(book,now)
    payload=payload_for(book,initial,activated=now.isoformat(),revision=1,archive_sha=storage.digest(archived),now=now)
    storage.write(storage.runtime_id(book),payload)
    print(f'{book}: legacy archive verified; separate {payload["metadata"]["account_currency"]}100,000 account activated; zero imported trades')


def advance(book,old,now):
    st=copy.deepcopy(old['state']['engine'])
    d=importlib.import_module(DRIVERS[book])
    store=ParquetStore(get_config().store_path)
    cutoff=now.normalize()
    daily=copy.deepcopy(old['daily'])
    if book in 'abc':
        instruments=[s for s in d.BOOK_EQUITIES+d.BOOK_CRYPTO+d.FX_MAJORS_7 if s not in d.EXCLUDED]
        adapter=get_adapter('yahoo')
        panel={}
        for sym in instruments:
            frame=clean(d._top_up(store,adapter,sym,cutoff,now))
            panel[sym]=frame[frame.index<cutoff]
            if len(panel[sym])<d.MIN_BARS:
                raise ValueError(f'{sym}: insufficient pinned strategy history')
        require_daily_panel(panel,instruments,cutoff)
        model=d.TrendBook(panel,**d.BOOK_PARAMS);strategies=model.strategies()
        if book=='b':
            spy=clean(d._top_up(store,adapter,d.GATE_SYMBOL,cutoff,now));spy=spy[spy.index<cutoff]
            require_daily_panel({d.GATE_SYMBOL:spy},[d.GATE_SYMBOL],cutoff)
            gated=tuple(s for s in panel if s in set(d.BOOK_CRYPTO)|set(d.FX_MAJORS_7))
            risk_on=d.risk_on_map(spy['close'],panel,gated,d.SPILL_L)
            for sym in gated:
                strategies[sym]=d.SpilloverGate(strategies[sym],risk_on[sym],sym)
        stepper=PaperPortfolio(panel,strategies,cfg=d._cfg() if book in 'bc' else get_config(),warmup=d.WARMUP,
                              book=d.BOOK_LABEL,state=st,account_currency='GBP',fx_panel=panel,halt_drawdown=d.HALT_DRAWDOWN)
        recs=stepper.advance(cutoff)
        if not recs:return None
        daily.extend(d._daily_rows(stepper,recs,None))
        st=stepper.to_state()
    elif book=='r':
        from apex_quant.research.book_r_forward import advance_book_r_forward
        adapter=get_adapter('yahoo');panel={}
        for sym in d.USD_ETF_UNIVERSE:
            frame=clean(d._top_up(store,adapter,sym,cutoff,now));panel[sym]=frame[frame.index<cutoff]
            if len(panel[sym])<d.MIN_BARS:raise ValueError(f'{sym}: insufficient history')
        require_daily_panel(panel,d.USD_ETF_UNIVERSE,cutoff)
        panel=d.common_panel(panel,d.USD_ETF_UNIVERSE)
        index=next(iter(panel.values())).index
        st,rows=advance_book_r_forward(panel,st,month_end_sessions=d._xnys_month_ends(index[0],cutoff+pd.Timedelta(days=40)))
        if not rows:return None
    elif book=='f':
        panel=d.load_panel(store)
        st,rows=d.advance_book_f_forward(st,panel,cutoff-pd.Timedelta(nanoseconds=1))
        if not rows:return None
    else:
        hourly,day=d.load_panels(store)
        eligible=(now-pd.Timedelta(minutes=2)).floor('h')-pd.Timedelta(hours=1)
        st,rows=d.advance_book_s_forward(st,hourly,day,eligible)
        if not rows:return None
    return payload_for(book,st,activated=old['state']['activated_at_utc'],revision=old['state']['revision']+1,
                       archive_sha=old['metadata']['archive_sha256'],daily=daily,now=now)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--book',choices=list(DRIVERS)+['all'],required=True)
    parser.add_argument('--activate',action='store_true')
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args(argv)
    if args.activate and args.dry_run:raise ValueError('Choose activation or dry-run, not both')
    books=list(DRIVERS) if args.book=='all' else [args.book]
    failures=[]
    for book in books:
        try:
            now=pd.Timestamp.now(tz='UTC')
            if args.activate:
                activate(book,now);continue
            old=storage.read(storage.runtime_id(book))
            if old is None:raise ValueError('Repaired account is not activated; no automatic seed')
            if old.get('book_id')!=book or old['metadata'].get('accounting_version')!=VERSION or storage.digest(old['state'])!=old['state_sha256']:
                raise ValueError('Repaired document identity/hash mismatch')
            new=advance(book,old,now)
            if new is None:
                if not args.dry_run:
                    checked=copy.deepcopy(old)
                    checked['metadata'].update(runner_status='fresh_no_new_session',runner_checked_at=now.isoformat(),runner_error=None)
                    storage.write(storage.runtime_id(book),checked,previous_hash=old['state_sha256'])
                print(f'{book}: fresh inputs verified; no post-activation bar to advance');continue
            if not args.dry_run:
                new['metadata'].update(runner_status='advanced',runner_checked_at=now.isoformat(),runner_error=None)
                storage.write(storage.runtime_id(book),new,previous_hash=old['state_sha256'])
            print(f'{book}: {"dry-run" if args.dry_run else "durably saved"} revision {new["state"]["revision"]}')
        except Exception as exc:
            failures.append(book)
            print(f'{book}: BLOCKED — {type(exc).__name__}: {exc}')
            if not args.dry_run and not args.activate:
                try:
                    current=storage.read(storage.runtime_id(book))
                    if current is not None:
                        blocked=copy.deepcopy(current)
                        blocked['metadata'].update(runner_status='blocked',runner_checked_at=now.isoformat(),runner_error=str(exc)[:300])
                        storage.write(storage.runtime_id(book),blocked,previous_hash=current['state_sha256'])
                except Exception:
                    print(f'{book}: unable to persist operational failure status; trading state not replaced')
    return 1 if failures else 0


if __name__=='__main__':
    raise SystemExit(main())
