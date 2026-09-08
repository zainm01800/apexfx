"""Isolated synthetic forward lifecycle tests; no broker, credentials or network."""
from copy import deepcopy
from functools import lru_cache
import importlib
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from apex_quant.forward_v27b import state as st
from apex_quant.forward_v27b.spec import SYMBOLS, ETFS, CIKS, SPEC, SPEC_HASH
from apex_quant.forward_v27b.data import anchored_panel, extract_filings
from apex_quant.forward_v27b.features import build_features, build
from apex_quant.forward_v27b.storage import write
from apex_quant.forward_v14.data import XNYS, DataUnavailable


@lru_cache(None)
def market(end="2026-09-04"):
    dates=XNYS.sessions_in_range("2024-05-01",end).tz_localize("UTC")
    x=np.arange(len(dates));panel={}
    for j,s in enumerate(SYMBOLS):
        close=100+0.035*x+np.sin(x/(4+j%4))*.4+j*.5
        if s in CIKS:close=close-0.5*np.maximum(0,x-(len(x)-8))
        panel[s]=pd.DataFrame(dict(open=close,high=close+.15,low=close-.15,close=close),index=dates)
    fxdates=pd.date_range("2026-08-01",end,freq="B")
    fx=pd.DataFrame(dict(close=1.3,available_at_utc=fxdates.tz_localize("UTC")+pd.Timedelta(hours=18)),index=fxdates)
    return dict(panel=panel,fx=fx,events=[],latest=dates[-1],retrieved_at_utc=pd.Timestamp(end+"T21:00Z"),
                provenance={"source":"SYNTHETIC_TEST_ONLY"})


@lru_cache(None)
def seeded():
    return st.confirm_durable_decisions(st.advance(st.new_state("2026-09-05T10:00Z"),market(),"2026-09-05T10:01Z"),"2026-09-05T10:02Z")


def next_market():
    m=deepcopy(market());day=pd.Timestamp("2026-09-08",tz="UTC")
    for s,f in m["panel"].items():
        m["panel"][s]=pd.concat([f,pd.DataFrame([f.iloc[-1].to_dict()],index=[day])])
    m.update(latest=day,retrieved_at_utc=pd.Timestamp("2026-09-08T21:00Z"))
    return m


def test_activation_excludes_weekends_holidays_and_past_open():
    assert st.new_state("2026-09-05T10:00Z")["first_execution_session"]=="2026-09-08"
    assert st.new_state("2026-09-08T14:00Z")["first_execution_session"]=="2026-09-09"


def test_seed_has_no_imported_trades_or_profit():
    s=seeded();assert s["equity_gbp"]==100000;assert s["positions"]==s["trades"]==s["daily"]==[]
    assert len(s["bars"])==1
    assert list(s["decisions"])==["2026-09-08"]
    assert st.public_payload(s,"2026-09-05T10:02Z")["metadata"]["funded_qualified"] is False


def test_repeated_same_day_plans_do_not_change_decisions_or_capital():
    first=seeded();again=st.advance(first,market(),"2026-09-06T10:00Z")
    for key in ("decisions","bars","positions","trades","cash_gbp","equity_gbp"):assert first[key]==again[key]


def test_nonterminal_forward_holds_positions_and_reconciles_without_duplicate_profit():
    first=st.advance(seeded(),next_market(),"2026-09-08T21:01Z")
    assert first["positions"],"Synthetic case must exercise nonterminal open holdings"
    assert not any(t["exit_reason"]=="terminal" for t in first["trades"])
    again=st.advance(first,next_market(),"2026-09-08T22:01Z")
    for key in ("positions","trades","daily","cash_gbp","equity_gbp","events"):assert first[key]==again[key]
    assert abs(first["equity_gbp"]-100000-sum(t["net_pnl_gbp"] for t in first["trades"])-sum(t["unrealized_pnl_gbp"] for t in first["positions"]))<1e-6
    assert all(p["stop_price"]>0 for p in first["positions"])


def test_late_activation_cannot_invent_today_open_trade():
    s=st.advance(st.new_state("2026-09-08T14:00Z"),market(),"2026-09-08T14:01Z")
    assert not s["decisions"] and not s["positions"]


def test_write_deadline_discards_only_new_late_decisions():
    first=st.new_state("2026-09-05T10:00Z");s=seeded()
    assert not st.enforce_deadline(s,first,"2026-09-08T13:30Z")["decisions"]
    assert st.enforce_deadline(s,s,"2026-09-08T14:00Z")["decisions"]==s["decisions"]


def test_nonuniform_revision_fails_and_leaves_state_unchanged():
    s=seeded();before=st.digest(s);m=deepcopy(market())
    m["panel"]["SPY"].iloc[-1,1]+=1
    with pytest.raises(DataUnavailable,match="nonuniform"):st.advance(s,m,"2026-09-06T10:00Z")
    assert st.digest(s)==before


def test_uniform_adjustment_rebases_without_rewriting_saved_bars():
    s=seeded();m=deepcopy(market())
    m["panel"]={symbol:f*.5 for symbol,f in m["panel"].items()}
    normalized=anchored_panel(m,s)
    for symbol in SYMBOLS:pd.testing.assert_frame_equal(normalized[symbol],market()["panel"][symbol])


def test_missing_execution_session_cannot_be_skipped():
    s=seeded();m=next_market()
    for symbol,f in m["panel"].items():m["panel"][symbol]=f.rename(index={pd.Timestamp("2026-09-08",tz="UTC"):pd.Timestamp("2026-09-09",tz="UTC")})
    m.update(latest=pd.Timestamp("2026-09-09",tz="UTC"),retrieved_at_utc=pd.Timestamp("2026-09-09T21:00Z"))
    with pytest.raises(ValueError,match="missing/non-XNYS"):st.advance(s,m,"2026-09-09T21:01Z")


def test_slow_and_fast_feature_keys_are_distinct():
    m=market();slow=build_features({s:m["panel"][s] for s in ETFS})
    fast=build({s:m["panel"][s] for s in CIKS},m["panel"]["SPY"],[])
    assert "global_relative_strength" in slow["weights"]
    assert set(fast["entry"])=={"residual5","residual5_filing_gap_excluded"}


def test_filing_acceptance_must_be_known_and_timezone_qualified():
    obj={"filings":{"recent":{"form":["8-K","10-Q"],"items":["2.02",""],"accessionNumber":["a","b"],
        "acceptanceDateTime":["2026-09-04T20:01:00Z","2026-07-30T12:00:00Z"],"filingDate":["2026-09-04","2026-07-30"]}}}
    events=extract_filings(obj,"AAPL",pd.Timestamp("2026-09-04T20:00Z"))
    assert [x["accession"] for x in events]==["b"]
    obj["filings"]["recent"]["acceptanceDateTime"][1]="2026-07-30T12:00:00"
    with pytest.raises(DataUnavailable,match="timezone"):extract_filings(obj,"AAPL",pd.Timestamp("2026-09-04T20:00Z"))


@pytest.mark.parametrize("key,value",[("book_id","v24"),("spec_sha256","changed"),("initial_equity",200000),("revision",0)])
def test_state_identity_fail_closed(key,value):
    s=deepcopy(seeded());s[key]=value
    with pytest.raises(ValueError):st.validate_state(s)


def test_saved_future_decision_evidence_is_rejected():
    s=deepcopy(seeded());s["decisions"]["2026-09-08"]["recorded_at_utc"]="2026-09-08T14:00Z"
    with pytest.raises(ValueError):st.validate_state(s)


@pytest.mark.parametrize("field,value",[("fast_entries",{"AAPL":100}),("slow_weights",{"SPY":2}),("fast_eligible",False),("slow_decision","2026-07-31T00:00:00+00:00")])
def test_executable_instruction_mutation_is_rejected(field,value):
    s=deepcopy(seeded());s["decisions"]["2026-09-08"][field]=value
    with pytest.raises(ValueError,match="instruction hash"):st.validate_state(s)


def test_eligible_session_cannot_be_moved_even_with_recomputed_hash():
    s=deepcopy(seeded());d=s["decisions"].pop("2026-09-08")
    d["eligible_fill_session"]="2026-09-09";d["instruction_sha256"]=st.instruction_digest(d)
    s["decisions"]["2026-09-09"]=d
    with pytest.raises(ValueError,match="next exchange session"):st.validate_state(s)


def test_stale_writer_cannot_overwrite_account(monkeypatch):
    monkeypatch.setenv("SUPABASE_SERVICE_KEY","TEST_ONLY")
    previous=seeded();state=deepcopy(previous);state.update(revision=2,parent_state_sha256=st.digest(previous))
    class Response:
        status_code=200
        def json(self):return []
    class Client:
        def patch(self,url,**kwargs):
            assert kwargs["params"]["feature_vector->state->>revision"]=="eq.1"
            assert "on_conflict" not in kwargs["params"]
            return Response()
    with pytest.raises(RuntimeError,match="stale writer"):write(Client(),st.public_payload(state),previous)


def test_blocked_empty_seed_recovers_flat_after_first_session_without_backfill():
    seed=st.new_state("2026-09-05T10:00Z")
    seed.update(status="blocked",runner_error="SEC unavailable")
    s=st.advance(seed,next_market(),"2026-09-08T21:01Z")
    assert s["equity_gbp"]==100000 and not s["positions"] and not s["trades"]
    assert len(s["daily"])==1 and s["last_processed_session"]=="2026-09-08"
    assert list(s["decisions"])==["2026-09-09"]


def test_late_startup_monthly_decision_never_rewrites_prior_flat_day():
    seed=st.new_state("2026-09-05T10:00Z")
    first=st.advance(seed,next_market(),"2026-09-08T21:01Z")
    confirmed=st.confirm_durable_decisions(first,"2026-09-08T21:02Z")
    m=next_market();day=pd.Timestamp("2026-09-09",tz="UTC")
    m["panel"]={s:pd.concat([f,pd.DataFrame([f.iloc[-1].to_dict()],index=[day])]) for s,f in m["panel"].items()}
    m.update(latest=day,retrieved_at_utc=pd.Timestamp("2026-09-09T21:00Z"))
    after=st.advance(confirmed,m,"2026-09-09T21:01Z")
    assert after["daily"][0]==first["daily"][0]
    assert all(st.iso_date(p["entry_date"])=="2026-09-09" for p in after["positions"])
    assert all(p["decision_recorded_at_utc"] is not None for p in after["positions"])


def test_plan_not_verified_durable_before_open_is_never_executable():
    unconfirmed=st.advance(st.new_state("2026-09-05T10:00Z"),market(),"2026-09-05T10:01Z")
    late=st.confirm_durable_decisions(unconfirmed,"2026-09-08T13:31Z")
    assert not late["decisions"]["2026-09-08"].get("durable_verified_at_utc")
    after=st.advance(late,next_market(),"2026-09-08T21:01Z")
    assert not after["positions"] and not after["trades"] and after["equity_gbp"]==100000
