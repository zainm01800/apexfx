#!/usr/bin/env python3
"""V33 paper-only runner. Explicit activation, no historical profit import."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sys

ENGINE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ENGINE))
from dotenv import load_dotenv
load_dotenv(ENGINE/".env")
import httpx
from apex_quant.forward_v33.data import fetch_market
from apex_quant.forward_v33.state import new_state, advance, public_payload, digest, enforce_deadline, confirm_durable_decisions
from apex_quant.forward_v33.storage import read, write

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--activate",action="store_true",help="Allow initial GBP100k seed only if remote book absent")
    parser.add_argument("--dry-run",action="store_true",help="Read and validate, never persist")
    args=parser.parse_args(argv)
    with httpx.Client(timeout=60) as client:
        remote=read(client)
        previous=remote.payload["state"] if remote.status=="found" else None
        if previous is None and not args.activate:
            print("V33 has not been activated; no account created. Use explicit --activate.")
            return 2
        seed=previous or new_state()
        failed=False
        try:
            market=fetch_market()
            result=advance(seed,market)
            result=enforce_deadline(result,seed,datetime.now(timezone.utc))
            result.pop("runner_error",None)
        except Exception as exc:
            failed=True;result=deepcopy(seed)
            result.update(status="blocked",status_reason="Fresh inputs or execution validation failed; account preserved",
                runner_error=f"{type(exc).__name__}: {str(exc)[:400]}",last_checked_at_utc=datetime.now(timezone.utc).isoformat())
        if previous is not None:
            result.update(revision=previous["revision"]+1,parent_state_sha256=digest(previous))
        payload=public_payload(result)
        print(f"Book V33 | paper only | {result['status']} | equity GBP{result['equity_gbp']:.2f} | "
              f"sessions {len(result['daily'])} | open {len(payload['positions'])} | pending {len(payload['pending'])}")
        if failed:print(result["runner_error"])
        if args.dry_run:print("DRY RUN: no state written")
        else:
            write(client,payload,previous)
            confirmed=confirm_durable_decisions(result,datetime.now(timezone.utc))
            if digest(confirmed)!=digest(result):
                confirmed.update(revision=result["revision"]+1,parent_state_sha256=digest(result))
                write(client,public_payload(confirmed),result)
            print("Verified V33 atomic paper state saved; no other book changed")
        return 1 if failed else 0

if __name__=="__main__":raise SystemExit(main())
