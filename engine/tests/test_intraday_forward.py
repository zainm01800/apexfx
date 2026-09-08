"""Offline settlement, parity and preserved-account regressions."""
import copy
from datetime import datetime,timezone
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd

ENGINE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ENGINE))
from apex_quant.forward_intraday import data,engine,storage,research_kernel
from apex_quant.forward_intraday.spec import BOOKS

NOW=pd.Timestamp('2026-09-09T00:00:00Z')
DAY='2026-09-08'

def bars(day=DAY):
    opening,closing=data.session_times(day)
    dates=pd.date_range(opening,closing,freq='min',inclusive='left')
    close=np.linspace(100,104,len(dates))
    return pd.DataFrame(dict(open=close,high=close+.1,low=close-.1,close=close,volume=1000.),index=dates)

def fixing(day=DAY):
    opening,_=data.session_times(day)
    return dict(rate=1.25,source_date=str((pd.Timestamp(day)-pd.Timedelta(days=1)).date()),
        available_at_utc=(opening-pd.Timedelta(hours=2)).isoformat(),cutoff_at_utc=opening.isoformat())

def warmup(day=DAY):
    return data.HistoricalWarmup(2.,.01,100.,dict.fromkeys(range(30,361,30),.001),1.25,
        str(data.XNYS.previous_session(day).date()),fixing(day))

def seed(book='v30'):
    return engine.new_state(BOOKS[book],datetime(2026,9,5,12,tzinfo=timezone.utc))

def load_runner():
    name='intraday_proposal_runner'
    spec=importlib.util.spec_from_file_location(name,ENGINE/'scripts'/'run_intraday_forward.py')
    mod=importlib.util.module_from_spec(spec);sys.modules[name]=mod;spec.loader.exec_module(mod);return mod

def test_verified_warmup_repair_requires_matching_neighbours_and_retrieval_time():
    from apex_quant.forward_intraday.verified_warmup_repair import repair_retained_warmup, STAMP, RETRIEVED
    frame=pd.DataFrame([[773.42999,773.45502,773.40002,773.44,4790],
                        [773.40308,773.46002,773.37,773.45001,130027]],
        columns=data.COLUMNS,index=[STAMP-pd.Timedelta(minutes=1),STAMP+pd.Timedelta(minutes=1)])
    original=frame.copy()
    old,evidence=repair_retained_warmup(frame,RETRIEVED-pd.Timedelta(seconds=1))
    assert len(old)==2 and not evidence
    fixed,evidence=repair_retained_warmup(frame,RETRIEVED)
    assert fixed.loc[STAMP].to_list()==[773.37,773.43,773.35,773.43,955]
    assert evidence[0]['historical_warmup_only']
    pd.testing.assert_frame_equal(frame,original)
    again,evidence=repair_retained_warmup(fixed,RETRIEVED)
    pd.testing.assert_frame_equal(again,fixed);assert not evidence
    frame.iloc[0,0]+=1
    unchanged,evidence=repair_retained_warmup(frame,RETRIEVED)
    assert len(unchanged)==2 and not evidence


def test_verified_warmup_repair_is_forbidden_for_execution_sessions():
    runner=load_runner();st=seed();record=data.freeze_session(bars(),DAY,0.,fixing(),{},NOW)
    record['source']['verified_historical_warmup_repairs']=[{'timestamp':DAY+'T17:54:00Z'}]
    with unittest.TestCase().assertRaises(data.DataUnavailable):
        runner.advance_existing(st,BOOKS['v30'],{'sessions':{DAY:record},'latest':DAY,'issues':{}},NOW)


def test_minute_bootstrap_chunks_are_bounded_and_preserve_raw_prices():
    calls=[]
    class Ticker:
        def history(self,**kwargs):
            calls.append(kwargs)
            assert kwargs['interval']=='1m'
            assert not any(kwargs[k] for k in ['auto_adjust','back_adjust','repair','prepost','actions'])
            start=pd.Timestamp(kwargs['start'],tz='UTC');end=pd.Timestamp(kwargs['end'],tz='UTC')
            assert end-start<=pd.Timedelta(days=7)
            return pd.DataFrame({'Open':[101.],'High':[102.],'Low':[100.],'Close':[101.5],'Volume':[10.]},index=[start])
    frame,issues=data.fetch_minute_chunks(Ticker(),'2026-08-11','2026-09-09')
    assert len(calls)==5 and len(frame)==5 and not issues
    assert list(frame.close)==[101.5]*5


def test_missing_older_chunk_does_not_invent_history_or_drop_recent_data():
    class Ticker:
        def history(self,**kwargs):
            if kwargs['start']=='2026-08-25':raise RuntimeError('private URL must not appear')
            return bars('2026-09-02')
    frame,issues=data.fetch_minute_chunks(Ticker(),'2026-08-25','2026-09-08')
    assert len(frame)==390 and list(issues)==['2026-08-25']
    assert 'private' not in str(issues)


def test_retained_warmup_can_be_ready_without_backdated_trades():
    runner=load_runner();st=seed();now=pd.Timestamp('2026-09-08T16:00:00Z')
    days=data.required_warmup_sessions(DAY)[2]
    archive={str(d.date()):data.freeze_session(bars(str(d.date())),str(d.date()),0.,fixing(str(d.date())),{'source':'synthetic'},now) for d in days}
    m={'sessions':archive,'issues':{},'latest':'2026-09-04'}
    ready=runner.advance_existing(st,BOOKS['v30'],m,now)
    assert ready['status']=='ready_waiting_settled_session'
    for key in ['cash','trades','daily','activation_recorded_at_utc','last_processed_session']:
        assert ready[key]==st[key]
    m['sessions'].pop('2026-09-03')
    waiting=runner.advance_existing(st,BOOKS['v30'],m,now)
    assert waiting['status']=='waiting_for_frozen_warmup'
    assert waiting['trades']==[]


class TestForwardIntraday(unittest.TestCase):
    def test_complete_once_preserves_account_and_reconciles(self):
        for book in BOOKS:
            initial=seed(book);snapshot=copy.deepcopy(initial)
            result=engine.step_session(initial,BOOKS[book],warmup(),bars(),DAY,now_utc=NOW)
            self.assertEqual(initial,snapshot);self.assertIsNone(result['position'])
            self.assertEqual(result['activation_recorded_at_utc'],initial['activation_recorded_at_utc'])
            self.assertEqual(result['daily'][0],initial['daily'][0])
            self.assertAlmostEqual(result['cash']-initial['cash'],sum(t['net_pnl_gbp'] for t in result['trades']))
            self.assertEqual(result,engine.step_session(result,BOOKS[book],warmup(),bars()*2,DAY,now_utc=NOW))

    def test_partial_missing_duplicate_unordered_naive_rejected(self):
        f=bars()
        candidates=[f.iloc[:60],f.drop(f.index[17]),pd.concat([f.iloc[:1],f]),f.iloc[::-1],f.set_axis(f.index.tz_localize(None))]
        for bad in candidates:
            with self.subTest(size=len(bad)),self.assertRaises(data.DataUnavailable):
                engine.step_session(seed(),BOOKS['v30'],warmup(),bad,DAY,now_utc=NOW)

    def test_full_looking_but_unsettled_rejected(self):
        with self.assertRaises(data.DataUnavailable):
            engine.step_session(seed(),BOOKS['v30'],warmup(),bars(),DAY,now_utc='2026-09-08T20:15:00Z')

    def test_official_holiday_and_early_close(self):
        self.assertFalse(data.is_us_market_hours(datetime(2026,9,7,15,tzinfo=timezone.utc)))
        with self.assertRaises(data.DataUnavailable):data.session_times('2026-09-07')
        day='2026-11-27';f=bars(day);self.assertEqual(len(f),210)
        st=engine.new_state(BOOKS['v30'],datetime(2026,11,26,12,tzinfo=timezone.utc))
        result=engine.step_session(st,BOOKS['v30'],warmup(day),f,day,now_utc='2026-11-28T00:00:00Z')
        self.assertEqual(result['trades'],[]);self.assertFalse(result['daily'][-1]['normal_session'])

    def test_activation_after_open_and_missing_session_never_backfilled(self):
        st=engine.new_state(BOOKS['v30'],datetime(2026,9,8,15,tzinfo=timezone.utc))
        self.assertEqual(engine.first_eligible_session(st),'2026-09-09')
        with self.assertRaises(data.DataUnavailable):engine.step_session(st,BOOKS['v30'],warmup(),bars(),DAY,now_utc=NOW)
        with self.assertRaises(data.DataUnavailable):engine.step_session(seed(),BOOKS['v30'],warmup('2026-09-09'),bars('2026-09-09'),'2026-09-09',now_utc='2026-09-10T00:00:00Z')

    def test_missing_noise_never_uses_fallback(self):
        w=warmup();w=data.HistoricalWarmup(w.atr_14,w.volatility_14,w.prior_close,{},w.fx_rate,w.as_of_session,w.fx_info)
        result=engine.step_session(seed('v24'),BOOKS['v24'],w,bars(),DAY,now_utc=NOW)
        self.assertEqual(result['trades'],[]);self.assertEqual(result['status'],'waiting_for_frozen_warmup')

    def test_signal_uses_completed_previous_minute_and_next_open(self):
        f=bars();f.loc[:,:]=[100.,100.1,99.9,100.,1000.]
        f.iloc[29]=[100.,102.2,99.9,102.,1000.]
        f.iloc[30:]=[102.5,102.6,102.4,102.5,1000.]
        result=engine.step_session(seed(),BOOKS['v30'],warmup(),f,DAY,now_utc=NOW)
        t=result['trades'][0]
        self.assertEqual(t['entry_time'],f.index[30].isoformat());self.assertEqual(t['entry_price'],102.5)
        self.assertEqual(t['decision_bar_start'],f.index[29].isoformat())

    def test_last_minute_flat_open_precedes_later_intrabar_stop(self):
        f=bars();f.iloc[-1,2]=1.
        result=engine.step_session(seed(),BOOKS['v30'],warmup(),f,DAY,now_utc=NOW)
        self.assertEqual(result['trades'][-1]['exit_reason'],'scheduled_flat')
        self.assertEqual(result['trades'][-1]['exit_price'],f.open.iloc[-1])

    def test_drawdown_retains_intraday_adverse_observation(self):
        st=seed();f=bars();w=warmup()
        session,_=engine.assemble_session(BOOKS['v30'],w,f,DAY)
        expected=research_kernel.replay([session],start=DAY,end=DAY,profile='higher_5_12',variant='atr_open_stop')
        result=engine.step_session(st,BOOKS['v30'],w,f,DAY,now_utc=NOW)
        row=result['daily'][-1]
        self.assertEqual(row['drawdown_from_peak'],expected['metrics']['peak_drawdown'])
        self.assertGreater(row['drawdown_from_peak'],row['drawdown_close'])
        self.assertEqual(engine.export_public_payload(result,BOOKS['v30'])['metadata']['conservative_max_drawdown'],row['drawdown_from_peak'])

    def test_runner_records_failure_without_rewriting_account(self):
        runner=load_runner();st=seed();payload=engine.export_public_payload(st,BOOKS['v30'])
        with patch.object(runner,'fetch_remote',return_value=storage.RemoteRead('found',payload=payload)),patch.object(runner,'fetch_settled_inputs',side_effect=data.DataUnavailable('missing feed')),patch.object(runner,'write_remote_verified') as write,patch.object(runner,'save_local'):
            with self.assertRaisesRegex(RuntimeError,'missing feed'):runner.run_step(BOOKS['v30'])
            new=write.call_args.args[0]['state']
            for key in ('cash','equity','peak','daily','trades','events','activation_recorded_at_utc'):
                self.assertEqual(new[key],st[key])
            self.assertEqual(new['status'],'blocked_input_or_execution')
            self.assertEqual(new['revision'],st['revision']+1)

    def test_entry_bar_stop_active_and_gap_stop_beats_signal(self):
        f=bars();f.loc[:,:]=[100.,100.1,99.9,100.,1000.]
        f.iloc[29]=[100.,102.1,99.9,102.,1000.];f.iloc[30]=[102.,102.1,99.,102.,1000.]
        result=engine.step_session(seed(),BOOKS['v30'],warmup(),f,DAY,now_utc=NOW)
        self.assertEqual(result['trades'][0]['exit_reason'],'stop')
        self.assertEqual(result['trades'][0]['exit_time'],f.index[30].isoformat())
        session,_=engine.assemble_session(BOOKS['v24'],warmup(),bars(),DAY)
        session['bars'].loc[:,:]=[102.,102.1,101.9,102.,1000.]
        session['decisions']={30:dict(direction=1,barrier=101.,daily_volatility=.01,decision_at=session['times'][30],decision_bar_start=session['times'][29]),
            60:dict(direction=0,barrier=None,daily_volatility=.01,decision_at=session['times'][60],decision_bar_start=session['times'][59])}
        session['bars'].iloc[60]=[99.,100.,98.,99.,1000.]
        r=research_kernel.replay([session],start=DAY,end=DAY,profile='higher_5_12')
        self.assertEqual(r['trades'].iloc[0].exit_reason,'gap_stop')
        self.assertAlmostEqual(r['trades'].iloc[0].exit_price,99*(1-.0001))

    def test_original_seed_floor_not_reset_to_current_cash(self):
        st=seed();st.update(cash=98000.,equity=98000.)
        result=engine.step_session(st,BOOKS['v30'],warmup(),bars(),DAY,now_utc=NOW)
        row=result['daily'][-1]
        self.assertEqual(row['external_maximum_floor'],88000.)
        self.assertEqual(row['external_daily_floor'],93000.)
        self.assertAlmostEqual(result['cash']-98000.,sum(t['net_pnl_gbp'] for t in result['trades']))

    def test_legacy_duplicate_history_and_open_position_fail_closed(self):
        st=seed();st['position']={'units':1}
        with self.assertRaises(data.DataUnavailable):engine.validate_history(st,BOOKS['v30'])
        st=seed();st['daily'] += [dict(date=DAY,is_seed=False)]*2;st['last_processed_session']=DAY
        with self.assertRaises(data.DataUnavailable):engine.validate_history(st,BOOKS['v30'])

    def test_archive_hash_revision_and_exact_prior_window(self):
        dates=list(data.XNYS.sessions_in_range('2026-08-12',DAY));archive={}
        for i,d in enumerate(dates):
            day=str(d.date());f=bars(day)
            f[['open','high','low','close']]*=1+i*.01
            archive[day]=data.freeze_session(f,day,.25 if i==len(dates)-1 else 0.,fixing(day),{'provider':'synthetic'},NOW)
        w=data.build_historical_warmup(archive,DAY)
        self.assertIsNone(w.unavailable_reason);self.assertEqual(len(w.noise_sigmas),12)
        prior=str(dates[-2].date());self.assertEqual(w.prior_close,data.thaw_session(archive[prior]).close.iloc[-1]-.25)
        changed=copy.deepcopy(archive);del changed[str(dates[-8].date())]
        self.assertEqual(data.build_historical_warmup(changed,DAY).unavailable_reason,'insufficient_frozen_cash_minute_history')
        bad=copy.deepcopy(archive[DAY]);bad['bars'][0][1]+=1
        with self.assertRaises(data.DataUnavailable):data.thaw_session(bad)

    def test_fx_never_fallback_stale_or_future(self):
        for info in ({},dict(fixing(),source_date='2026-08-01'),dict(fixing(),available_at_utc='2026-09-08T20:00:00Z')):
            record=data.freeze_session(bars(),DAY,0.,info,{},NOW)
            with self.assertRaises(data.DataUnavailable):data.build_historical_warmup({DAY:record},DAY)

    def test_runner_preserves_frozen_input_and_idempotency(self):
        runner=load_runner();record=data.freeze_session(bars(),DAY,0.,fixing(),{},NOW)
        market=dict(sessions={DAY:record},issues={},latest=DAY)
        st=seed();new=runner.advance_existing(st,BOOKS['v30'],market,NOW)
        self.assertEqual(new['cash'],100000.);self.assertEqual(new['status'],'waiting_for_frozen_warmup')
        changed=copy.deepcopy(market);changed['sessions'][DAY]['bars'][0][1]+=20
        self.assertEqual(runner.advance_existing(new,BOOKS['v30'],changed,NOW),new)
        self.assertEqual(new['parent_state_sha256'],engine.state_sha256(st))

    def test_runner_remote_unavailable_never_loads_local_or_reseeds(self):
        runner=load_runner()
        with patch.object(runner,'fetch_remote',return_value=storage.RemoteRead('unavailable')),patch.object(runner,'load_local') as local:
            with self.assertRaises(RuntimeError):runner.run_step(BOOKS['v30'])
            local.assert_not_called()
        with self.assertRaises(RuntimeError):runner.run_activate(BOOKS['v30'],force=True)

    def test_cas_refuses_wrong_parent_and_verifies_exact_content(self):
        old=seed();new=engine.step_session(old,BOOKS['v30'],warmup(),bars(),DAY,now_utc=NOW)
        payload=engine.export_public_payload(new,BOOKS['v30'])
        class Response:
            status_code=200
            def __init__(self,value):self.value=value
            def json(self):return self.value
        class Client:
            def __init__(self):self.state=engine.export_public_payload(old,BOOKS['v30']);self.patches=0
            def get(self,*args,**kw):return Response([{'feature_vector':self.state}])
            def patch(self,*args,**kw):
                self.patches+=1
                assert kw['params']['feature_vector->state->>revision']=='eq.1'
                self.state=kw['json']['feature_vector'];return Response([{'feature_vector':self.state}])
        with patch.dict('os.environ',{'SUPABASE_SERVICE_KEY':'offline-test'}):
            client=Client()
            with self.assertRaises(RuntimeError):storage.write_remote_verified(payload,BOOKS['v30'],expected_state_sha256='wrong',client=client)
            self.assertEqual(client.patches,0)
            storage.write_remote_verified(payload,BOOKS['v30'],expected_state_sha256=engine.state_sha256(old),client=client)
            self.assertEqual(client.patches,1)

if __name__=='__main__':unittest.main()
