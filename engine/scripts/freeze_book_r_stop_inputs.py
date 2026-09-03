#!/usr/bin/env python3
"""Freeze the exact Book R OHLCV panel used by the 2026-09-03 stop study."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

from apex_quant.research.book_r_usd_etf import USD_ETF_UNIVERSE, common_panel  # noqa: E402


OUT = ENGINE_DIR / "data_store" / "validation" / "book_r_stop_inputs_2026-09-03.parquet"
MANIFEST = ENGINE_DIR / "data_store" / "validation" / "book_r_stop_inputs_2026-09-03.manifest.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if OUT.exists() or MANIFEST.exists():
        raise FileExistsError("frozen Book R stop inputs already exist; refusing to overwrite")
    source: dict[str, dict] = {}
    raw: dict[str, pd.DataFrame] = {}
    for instrument in USD_ETF_UNIVERSE:
        path = ENGINE_DIR / "data_store" / f"{instrument}_1d.parquet"
        frame = pd.read_parquet(path)
        raw[instrument] = frame
        source[instrument] = {
            "path": str(path.relative_to(ENGINE_DIR.parent)),
            "sha256": sha256(path),
            "rows": int(len(frame)),
            "start": str(frame.index.min()),
            "end": str(frame.index.max()),
        }
    panel = common_panel(raw, USD_ETF_UNIVERSE)
    rows = []
    for instrument, frame in panel.items():
        piece = frame[["open", "high", "low", "close", "volume"]].copy()
        piece.insert(0, "instrument", instrument)
        piece.insert(1, "timestamp", piece.index)
        rows.append(piece.reset_index(drop=True))
    frozen = pd.concat(rows, ignore_index=True).sort_values(["instrument", "timestamp"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frozen.to_parquet(OUT, index=False, compression="zstd")
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "pre-result frozen input for Book R-252 stop-overlay study",
        "universe": list(USD_ETF_UNIVERSE),
        "common_rows_per_instrument": int(len(next(iter(panel.values())))),
        "common_start": str(next(iter(panel.values())).index.min()),
        "common_end": str(next(iter(panel.values())).index.max()),
        "source_files": source,
        "snapshot": {
            "path": str(OUT.relative_to(ENGINE_DIR.parent)),
            "sha256": sha256(OUT),
            "bytes": OUT.stat().st_size,
            "rows": int(len(frozen)),
        },
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps(manifest["snapshot"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
