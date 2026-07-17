"""One-off store migration: de-duplicate day-boundary contamination (2026-07-17).

Root cause: the parquet cache merged two daily-bar conventions — OANDA labels
D/W candles at the 17:00 New York close (21:00 UTC summer / 22:00 UTC winter),
Yahoo at 00:00 UTC — and the store de-duplicated on the *exact* timestamp, so
the same trading day was cached twice under two timestamps (2,752 rows involved
in EUR_USD_1d alone).

Policy (documented, mirrors the run_candidate_check.py workaround so existing
validated results are reproduced exactly):
  * 1d/1w files: sort by timestamp (stable), collapse rows sharing a UTC
    calendar date keeping the LAST row — i.e. the later-timestamped bar
    (OANDA 21:00/22:00) wins over the midnight (Yahoo) bar for the same date —
    then normalise surviving bars to 00:00 UTC, the single store convention
    now enforced on write by ``ParquetStore`` (see ``normalize_day_bars``).
  * Intraday files: collapse exact-timestamp duplicates only (keep last);
    files without any are left untouched.

A full backup was taken first: data_store/backup_pre_dedup_2026-07-17/.

Usage:
    cd engine && .venv-mac/bin/python scripts/dedup_store_day_bars.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

from apex_quant.data.store import _DAY_TIMEFRAMES, ParquetStore  # noqa: E402

STORE_ROOT = ENGINE_DIR / "data_store"
_TF_RE = re.compile(r"_(\d+[mMhHdDwW])\.parquet$")  # timeframe = trailing "<n><unit>" token


def _timeframe_of(path: Path) -> str:
    m = _TF_RE.search(path.name)
    return m.group(1) if m else ""


def main() -> int:
    store = ParquetStore(STORE_ROOT)
    files = sorted(p for p in STORE_ROOT.glob("*.parquet") if p.is_file())
    rows = []
    n_rewritten = 0
    for p in files:
        tf = _timeframe_of(p)
        df = pd.read_parquet(p)  # raw read: validate_ohlcv would raise on dupes
        df = df.sort_index(kind="mergesort")  # stable: keep-last is deterministic
        n_before = len(df)

        if tf in _DAY_TIMEFRAMES:
            dup_mask = df.index.normalize().duplicated(keep=False)
            drop_mask = df.index.normalize().duplicated(keep="last")
        else:
            dup_mask = df.index.duplicated(keep=False)
            drop_mask = df.index.duplicated(keep="last")
        involved_before = int(dup_mask.sum())
        removed = int(drop_mask.sum())

        if removed or tf in _DAY_TIMEFRAMES:
            kept = df[~drop_mask]
            # save() normalises day bars to 00:00 UTC and re-validates (idempotent)
            store.save(p.stem.rsplit("_", 1)[0], kept, tf)
            n_rewritten += 1
            after = pd.read_parquet(p)
            if tf in _DAY_TIMEFRAMES:
                involved_after = int(after.index.normalize().duplicated(keep=False).sum())
                off_midnight = int((after.index != after.index.normalize()).sum())
            else:
                involved_after = int(after.index.duplicated(keep=False).sum())
                off_midnight = 0
        else:
            involved_after, off_midnight = 0, 0

        rows.append((p.name, tf, n_before, involved_before, removed,
                     involved_after, off_midnight))

    # audit table (every file; also persisted next to the store for the record)
    hdr = f"{'file':<26}{'tf':<5}{'rows':>7}{'dup_rows_before':>16}{'removed':>9}{'dup_after':>10}{'off_00UTC':>10}"
    lines = [hdr, "-" * len(hdr)]
    for name, tf, n_before, inv_before, removed, inv_after, off in rows:
        lines.append(f"{name:<26}{tf:<5}{n_before:>7}{inv_before:>16}{removed:>9}{inv_after:>10}{off:>10}")
    lines.append("-" * len(hdr))
    total_inv = sum(r[3] for r in rows)
    total_removed = sum(r[4] for r in rows)
    bad_after = [r[0] for r in rows if r[5] or r[6]]
    n_dirty_before = sum(1 for r in rows if r[3])
    lines.append(f"files scanned: {len(rows)} | rewritten: {n_rewritten} | "
                 f"files with dupes before: {n_dirty_before} | dup rows involved: {total_inv} | removed: {total_removed}")
    if bad_after:
        lines.append(f"FAIL: {len(bad_after)} file(s) still dirty: {bad_after}")
    else:
        lines.append("OK: every cached parquet is now unique per period; day bars pinned to 00:00 UTC.")
    report = "\n".join(lines)
    print(report)
    (STORE_ROOT / "dedup_audit_2026-07-17.log").write_text(report + "\n", encoding="utf-8")
    return 1 if bad_after else 0


if __name__ == "__main__":
    sys.exit(main())
