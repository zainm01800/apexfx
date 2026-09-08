"""Isolated repaired-paper documents and immutable legacy archives.

Updates use a server-side hash predicate (compare-and-swap), not an upsert that
could overwrite a newer run. Original paper tables/runtime IDs are never written.
"""
from __future__ import annotations
import hashlib
import json
import os
import copy
import httpx
from ._keys import service_or_anon_key

BOOKS = "abcrsf"


def runtime_id(book):
    if book not in BOOKS or len(book) != 1:
        raise ValueError("Unknown repaired book")
    return f"__apex_book_{book}_repaired_v2__"


def archive_id(book):
    runtime_id(book)
    return f"__apex_book_{book}_archive_20260905__"


def legacy_digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()


def canonical_zeros(value):
    # PostgreSQL JSONB converts -0.0 to 0.0. Preserve every nonzero digit and
    # every integer/bool type; only the sign of floating zero is insignificant.
    if isinstance(value,dict):return {k:canonical_zeros(v) for k,v in value.items()}
    if isinstance(value,list):return [canonical_zeros(v) for v in value]
    if isinstance(value,tuple):return tuple(canonical_zeros(v) for v in value)
    return 0.0 if type(value) is float and value==0 else value


def digest(value):
    return legacy_digest(canonical_zeros(value))


def verified_signed_zero_repair(payload, now):
    """Recover only when the original hash proves one lost negative-zero sign.

    No numeric tolerance, guessed balance, arbitrary rehash or history reset.
    The caller must persist via CAS against the original stored hash.
    """
    if digest(payload['state'])==payload['state_sha256']:return None
    candidate=copy.deepcopy(payload['state']);matches=[]
    def visit(value,path):
        pairs=list(value.items()) if isinstance(value,dict) else list(enumerate(value)) if isinstance(value,list) else []
        for key,item in pairs:
            if isinstance(item,(dict,list)):visit(item,[*path,key])
            elif type(item) is float and item==0:
                value[key]=-0.0
                if legacy_digest(candidate)==payload['state_sha256']:matches.append([*path,key])
                value[key]=item
    visit(candidate,[])
    if len(matches)!=1:return None
    repaired=copy.deepcopy(payload)
    repaired['state']['revision']+=1
    repaired['state_sha256']=digest(repaired['state'])
    repaired['metadata']['signed_zero_hash_repair']={
        'previous_state_sha256':payload['state_sha256'],
        'proven_negative_zero_path':matches[0], 'repaired_at_utc':now.isoformat(),
        'method':'Exact original SHA256 recovered by restoring one negative-zero sign; monetary values unchanged',
    }
    return repaired


def url():
    return os.environ.get("SUPABASE_URL","https://cuvchjhaojhmxfgczndy.supabase.co").rstrip('/')+'/rest/v1/apex_analyses'


def headers(write=False):
    key = os.environ.get("SUPABASE_SERVICE_KEY") if write else service_or_anon_key()
    if not key:
        raise RuntimeError("Service credential required for paper writes")
    return {"apikey":key,"Authorization":"Bearer "+key,"Content-Type":"application/json"}


def read(identifier, client=None):
    if client is None:
        with httpx.Client(timeout=40) as c:
            return read(identifier,c)
    r=client.get(url(),params={"id":"eq."+identifier,"select":"feature_vector","limit":"1"},headers=headers())
    if r.status_code != 200:
        raise RuntimeError(f"Paper state read failed: HTTP {r.status_code}")
    rows=r.json()
    if not isinstance(rows,list):
        raise ValueError("Malformed paper state response")
    if not rows:
        return None
    payload=rows[0].get("feature_vector")
    if not isinstance(payload,dict):
        raise ValueError("Malformed paper state")
    return payload


def write(identifier,payload,*,previous_hash=None,client=None):
    if client is None:
        with httpx.Client(timeout=40) as c:
            return write(identifier,payload,previous_hash=previous_hash,client=c)
    digest(payload)  # reject NaN/infinity before any external write
    h={**headers(True),"Prefer":"return=representation"}
    if previous_hash is None:
        row={"id":identifier,"user_id":"apex_engine","symbol":"REPAIRED_PAPER",
             "timeframe":"1d","direction":"paper","feature_vector":payload,
             "analysis_text":"Versioned internal paper account; not funded-approved","verdict":"PAPER_ONLY"}
        r=client.post(url(),headers=h,json=[row])  # insert only: duplicate IDs fail
    else:
        r=client.patch(url(),headers=h,params={"id":"eq."+identifier,"feature_vector->>state_sha256":"eq."+previous_hash},
                       json={"feature_vector":payload})
    if r.status_code not in (200,201) or not isinstance(r.json(),list) or len(r.json()) != 1:
        raise RuntimeError(f"Paper write failed or concurrent state changed: HTTP {r.status_code}")
    actual=read(identifier,client)
    if digest(actual)!=digest(payload):
        raise RuntimeError("Paper write read-back did not match")
