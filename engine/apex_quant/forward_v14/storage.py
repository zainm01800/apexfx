"""Atomic local and namespaced Supabase persistence for V14 paper state."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any

from .spec import BookSpec
from .state import validate_state


DEFAULT_SUPABASE_URL = "https://cuvchjhaojhmxfgczndy.supabase.co"
TABLE = "apex_analyses"


@dataclass(frozen=True, slots=True)
class RemoteRead:
    status: str
    payload: dict | None = None
    detail: str = ""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def state_sha256(state: dict) -> str:
    return sha256(canonical_bytes(state)).hexdigest()


def load_local(path: Path, spec: BookSpec) -> dict | None:
    path = Path(path)
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    validate_state(value, spec)
    return value


def save_local(path: Path, state: dict, spec: BookSpec) -> None:
    validate_state(state, spec)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp{os.getpid()}")
    try:
        temporary.write_text(
            json.dumps(state, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


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
    """Return found/missing/unavailable without conflating network failure."""

    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    if not key:
        return RemoteRead("unavailable", detail="no Supabase read credential")
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
            return RemoteRead(
                "unavailable",
                detail=f"Supabase runtime read returned HTTP {response.status_code}",
            )
        rows = response.json()
        if not rows:
            return RemoteRead("missing")
        payload = rows[0].get("feature_vector")
        if not isinstance(payload, dict) or not isinstance(payload.get("state"), dict):
            return RemoteRead("unavailable", detail="runtime row has malformed feature_vector")
        try:
            validate_state(payload["state"], spec)
        except (TypeError, ValueError) as exc:
            return RemoteRead("unavailable", detail=f"runtime state validation failed: {exc}")
        return RemoteRead("found", payload=payload)
    except Exception as exc:
        return RemoteRead("unavailable", detail=f"Supabase runtime read failed: {exc}")
    finally:
        if owns_client:
            client.close()


def write_remote_verified(payload: dict, spec: BookSpec, *, client=None) -> None:
    """Upsert with service-role auth, then read back and compare the durable state."""

    validate_state(payload.get("state"), spec)
    if payload.get("book_id") != spec.book_id:
        raise ValueError("runtime payload book identity mismatch")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not key:
        raise RuntimeError("SUPABASE_SERVICE_KEY is required for V14 runtime writes")
    owns_client = client is None
    if owns_client:
        import httpx

        client = httpx.Client(timeout=30)
    try:
        # Optimistic lineage check.  Workflow-level concurrency serializes the
        # intended writer; this preflight also catches stale/manual invocations.
        current = fetch_remote(spec, client=client)
        desired_state = payload["state"]
        if current.status == "unavailable":
            raise RuntimeError(f"Supabase preflight failed: {current.detail}")
        if current.status == "missing":
            if desired_state.get("parent_state_sha256") is not None or desired_state.get("revision") != 1:
                raise RuntimeError("refusing to create a non-root V14 state")
        else:
            current_hash = state_sha256(current.payload["state"])
            desired_hash = state_sha256(desired_state)
            if current_hash == desired_hash:
                return  # idempotent retry after a previously successful write
            if desired_state.get("parent_state_sha256") != current_hash:
                raise RuntimeError("authoritative state advanced since this run restored it")
            if int(desired_state.get("revision", 0)) != int(current.payload["state"].get("revision", 0)) + 1:
                raise RuntimeError("V14 state revision is not the next authoritative revision")
        row = {
            "id": spec.runtime_id,
            "user_id": "apex_engine",
            "symbol": f"BOOK_{spec.book_id.upper()}_V14",
            "timeframe": "1d",
            "direction": "paper",
            "feature_vector": payload,
            "analysis_text": f"{spec.label} GBP100k experimental forward-paper runtime",
            "verdict": "EXPERIMENTAL_FORWARD_PAPER",
        }
        response = client.post(
            _url(),
            headers=_headers(
                key, prefer="resolution=merge-duplicates,return=minimal"
            ),
            json=[row],
        )
        if response.status_code not in (200, 201, 204):
            raise RuntimeError(
                f"Supabase runtime upsert returned HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )
        verified = fetch_remote(spec, client=client)
        if verified.status != "found" or verified.payload is None:
            raise RuntimeError(f"Supabase write verification failed: {verified.detail}")
        expected_hash = state_sha256(payload["state"])
        actual_hash = state_sha256(verified.payload["state"])
        if actual_hash != expected_hash:
            raise RuntimeError("Supabase write verification returned a different state")
    finally:
        if owns_client:
            client.close()
