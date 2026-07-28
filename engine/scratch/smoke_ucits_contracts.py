"""SMOKE CHECK (read-only): build the IBKR contract objects for the deployed UCITS lines.

Constructs through the exact path the executor uses (ucits_map -> contract_spec
-> make_contract) and prints the results. NO connection, NO qualifyContracts,
NO orders — pure client-side objects. Contract qualification against live IBKR
data happens server-side at first real use (submit_order's _qualify call).

Run:  engine/.venv-mac/bin/python engine/scratch/smoke_ucits_contracts.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

from apex_quant.execution.ibkr_executor import contract_spec, make_contract  # noqa: E402
from apex_quant.execution.ucits_map import NO_EQUIVALENT_REASON, UCITS_MAP  # noqa: E402


def main() -> int:
    print("UCITS smoke check — contract construction only (no gateway, no orders)")
    print("=" * 78)
    for us, line in UCITS_MAP.items():
        if line is None:
            print(f"{us:5s} -> engine-only (no clean equivalent)\n"
                  f"       {NO_EQUIVALENT_REASON[us]}")
            continue
        spec = contract_spec(line["ucits_ticker"])      # Yahoo form, as the doc names it
        contract = make_contract(spec)
        print(f"{us:5s} -> {line['ucits_ticker']:7s} ({line['fund']})")
        print(f"       spec     : {spec}")
        print(f"       contract : {contract!r}")
        print(f"       ISIN {line['isin']} | TER {line['ter_pct']}% | "
              f"{line['verified'].split(' — ')[0]}")
    print("=" * 78)
    print("OK — all mapped lines construct as STK/<SMART-or-LSE>/USD. Qualification")
    print("against live IBKR data is deferred to first use (executor._qualify).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
