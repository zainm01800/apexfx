"""Local and Supabase persistence for forward intraday books V24 and V30."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any

from .spec import BookSpec

DEFAULT_SUPABASE_URL = "https://cuvchjhaojhmxfgczndy.supabase.co"
TABLE = "apex_analyses"


@dataclass(frozen=True, slots=True)
class RemoteRead:
    status: str
    payload: dict | None = None
    detail: str = ""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def state_sha256(state: dict) -> str:
    return sha256(canonical_bytes(state)).hexdigest()


def load_local(path: Path | str, spec: BookSpec) -> dict | None:
    p = Path(path)
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    if data.get("book_id") != spec.book_id:
        raise ValueError("local state book_id mismatch")
    return data


def save_local(path: Path | str, state: dict, spec: BookSpec) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    temp = p.with_name(f"{p.name}.tmp{os.getpid()}")
    try:
        temp.write_text(
            json.dumps(state, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temp, p)
    finally:
        if temp.exists():
            temp.unlink()


def _url() -> str:
    base = os.environ.get("SUPABASE_URL", DEFAULT_SUPABASE_URL).rstrip("/")
    return f"{base}/rest/v1/{TABLE}"


def _headers(key: str, *, prefer: str | None = None) -> dict:
    result = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if prefer:
        result["Prefer"] = prefer
    return result


def fetch_remote(spec: BookSpec, *, client=None) -> RemoteRead:
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    if not key:
        return RemoteRead("unavailable", detail="no Supabase credential")
    owns_client = client is None
    if owns_client:
        import httpx
        client = httpx.Client(timeout=30)
    try:
        response = client.get(
            _url(),
            headers=_headers(key),
            params={
                "select": "feature_vector",
                "id": f"eq.{spec.runtime_id}",
                "limit": "1",
            },
        )
        if response.status_code != 200:
            return RemoteRead("unavailable", detail=f"HTTP {response.status_code}")
        rows = response.json()
        if not rows:
            return RemoteRead("missing")
        payload = rows[0].get("feature_vector")
        if not isinstance(payload, dict) or payload.get("book_id") != spec.book_id:
            return RemoteRead("unavailable", detail="malformed remote state")
        return RemoteRead("found", payload=payload)
    except Exception as exc:
        return RemoteRead("unavailable", detail=str(exc))
    finally:
        if owns_client:
            client.close()


def write_remote_verified(payload: dict, spec: BookSpec, *, expected_state_sha256: str, client=None) -> None:
    """Compare-and-swap an existing account; never create/reseed or overwrite an unavailable state."""
    if payload.get("book_id")!=spec.book_id:raise ValueError("runtime book identity mismatch")
    key=os.environ.get("SUPABASE_SERVICE_KEY")
    if not key:raise RuntimeError("SUPABASE_SERVICE_KEY required")
    owns_client=client is None
    if owns_client:
        import httpx
        client=httpx.Client(timeout=30)
    try:
        previous=fetch_remote(spec,client=client)
        if previous.status!="found":raise RuntimeError("Existing authoritative account unavailable; no upsert fallback")
        old=previous.payload.get("state")
        if not isinstance(old,dict) or state_sha256(old)!=expected_state_sha256:
            raise RuntimeError("Remote parent state changed")
        new=payload["state"]
        if new.get("parent_state_sha256")!=expected_state_sha256 or new["revision"]<=old["revision"]:
            raise RuntimeError("Invalid state parent/revision")
        for keyname in ("initial_equity","activation_recorded_at_utc","book_id"):
            if new.get(keyname)!=old.get(keyname):raise RuntimeError("Original account identity/activation cannot change")
        for keyname in ("daily","trades","events"):
            if new.get(keyname,[])[:len(old.get(keyname,[]))]!=old.get(keyname,[]):
                raise RuntimeError("Existing account history cannot be replaced")
        response=client.patch(_url(),headers=_headers(key,prefer="return=representation"),
            params={"id":f"eq.{spec.runtime_id}","feature_vector->state->>revision":f"eq.{old['revision']}","select":"feature_vector"},
            json={"feature_vector":payload})
        if response.status_code!=200 or len(response.json())!=1:
            raise RuntimeError("Concurrent state update or failed CAS; no unconditional retry")
        verified=fetch_remote(spec,client=client)
        if verified.status!="found" or state_sha256(verified.payload.get("state",{}))!=state_sha256(new):
            raise RuntimeError("Durable account content verification failed")
    finally:
        if owns_client:client.close()
