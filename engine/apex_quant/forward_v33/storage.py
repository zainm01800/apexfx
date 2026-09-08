"""One namespaced atomic document, conditional writes and read-back verification."""
import os
from dataclasses import dataclass
from .spec import SPEC
from .state import validate_state, digest

@dataclass
class Remote:
    status: str
    payload: dict | None = None

def _settings(write=False):
    key=os.environ.get("SUPABASE_SERVICE_KEY") or (None if write else os.environ.get("SUPABASE_ANON_KEY"))
    if not key:raise RuntimeError("Supabase service credential required" if write else "Supabase read credential unavailable")
    url=os.environ.get("SUPABASE_URL","https://cuvchjhaojhmxfgczndy.supabase.co").rstrip("/")+"/rest/v1/apex_analyses"
    return url,dict(apikey=key,Authorization="Bearer "+key,**{"Content-Type":"application/json"})

def read(client):
    url,headers=_settings()
    response=client.get(url,headers=headers,params=dict(id="eq."+SPEC.runtime_id,select="feature_vector",limit="1"))
    if response.status_code!=200:raise RuntimeError(f"Authoritative V33 read failed: HTTP {response.status_code}")
    rows=response.json()
    if not isinstance(rows,list):raise RuntimeError("Malformed authoritative response")
    if not rows:return Remote("missing")
    payload=rows[0].get("feature_vector")
    if not isinstance(payload,dict) or payload.get("book_id")!="v33":raise RuntimeError("Invalid V33 payload")
    validate_state(payload.get("state"))
    return Remote("found",payload)

def write(client,payload,previous):
    state=payload["state"];validate_state(state)
    url,headers=_settings(True);headers["Prefer"]="return=representation"
    if previous is None:
        if state["revision"]!=1 or state["parent_state_sha256"] is not None:raise RuntimeError("Nonroot V33 initialization")
        row=dict(id=SPEC.runtime_id,user_id="apex_engine",symbol="BOOK_V33_FORWARD",timeframe="1d",direction="paper",
            feature_vector=payload,analysis_text="V33 joint portfolio: GBP100k forward paper only",verdict="EXPERIMENTAL_FORWARD_PAPER")
        response=client.post(url,headers=headers,json=[row])  # No upsert: existing rows cannot be replaced.
    else:
        if state["parent_state_sha256"]!=digest(previous) or state["revision"]!=previous["revision"]+1:
            raise RuntimeError("Invalid V33 write lineage")
        response=client.patch(url,headers=headers,
            params={"id":"eq."+SPEC.runtime_id,"feature_vector->state->>revision":"eq."+str(previous["revision"])},
            json={"feature_vector":payload})
    if response.status_code not in (200,201):raise RuntimeError(f"V33 conditional write failed: HTTP {response.status_code}")
    rows=response.json()
    if not isinstance(rows,list) or len(rows)!=1:raise RuntimeError("V33 stale writer rejected; state was not overwritten")
    saved=read(client)
    if saved.status!="found" or digest(saved.payload["state"])!=digest(state):raise RuntimeError("V33 durable read-back did not match")
