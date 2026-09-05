import copy
import json
import sys
from pathlib import Path
import httpx
import pandas as pd
import pytest
from apex_quant.storage import repaired_paper as store

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_repaired_paper import initial_engine,payload_for,activate


@pytest.mark.parametrize('book',list('abcrsf'))
def test_every_repaired_seed_has_zero_imported_performance(book):
    now=pd.Timestamp('2026-09-05T01:00:00Z')
    st=initial_engine(book,now)
    p=payload_for(book,st,activated=now.isoformat(),revision=1,archive_sha='archive',now=now)
    assert p['daily'][-1]['equity']==100000
    assert p['daily'][-1]['cash']==100000
    assert not p['positions'] and not p['trades'] and not p['pending']
    assert p['metadata']['paper_only'] and not p['metadata']['broker_enabled']
    assert p['state_sha256']==store.digest(p['state'])
    assert p['metadata']['account_currency']==('GBP' if book in 'abc' else 'USD')
    json.dumps(p,allow_nan=False)


def test_activation_cannot_reseed_existing_account(monkeypatch):
    monkeypatch.setattr(store,'read',lambda ident:{'existing':True})
    monkeypatch.setattr(store,'write',lambda *a,**k:pytest.fail('existing ledger must not be written'))
    activate('c',pd.Timestamp.now(tz='UTC'))


def test_state_read_failure_is_not_missing(monkeypatch):
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(503,json={'error':'offline'}))) as client:
        with pytest.raises(RuntimeError,match='read failed'):store.read(store.runtime_id('c'),client)


def test_compare_and_swap_and_readback(monkeypatch):
    monkeypatch.setenv('SUPABASE_SERVICE_KEY','unit-test-not-a-real-key')
    saved={'state_sha256':'old'};calls=[]
    def route(req):
        nonlocal saved
        calls.append(req)
        if req.method=='PATCH':
            assert req.url.params['feature_vector->>state_sha256']=='eq.old'
            saved=json.loads(req.content)['feature_vector']
            return httpx.Response(200,json=[{'feature_vector':saved}])
        return httpx.Response(200,json=[{'feature_vector':saved}])
    with httpx.Client(transport=httpx.MockTransport(route)) as client:
        store.write(store.runtime_id('c'),{'state_sha256':'new'},previous_hash='old',client=client)
    assert [r.method for r in calls]==['PATCH','GET']


def test_concurrent_update_is_rejected(monkeypatch):
    monkeypatch.setenv('SUPABASE_SERVICE_KEY','unit-test-not-a-real-key')
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json=[]))) as client:
        with pytest.raises(RuntimeError,match='concurrent'):store.write(store.runtime_id('c'),{'x':1},previous_hash='old',client=client)


def test_archive_write_is_insert_only(monkeypatch):
    monkeypatch.setenv('SUPABASE_SERVICE_KEY','unit-test-not-a-real-key')
    def route(req):
        assert req.method=='POST'
        assert 'resolution=merge-duplicates' not in req.headers.get('Prefer','')
        return httpx.Response(409,json={'error':'duplicate'})
    with httpx.Client(transport=httpx.MockTransport(route)) as client:
        with pytest.raises(RuntimeError):store.write(store.archive_id('c'),{'x':1},client=client)
