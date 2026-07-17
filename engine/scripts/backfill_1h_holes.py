"""Backfill the documented 1h holes in the forex caches (2026-07-17 audit).

Fills, for every forex ``*_1h.parquet``:

  * **2022-H1** — for files whose coverage starts after 2022-01-03 (the
    cross pairs whose caches begin 2022-07-17) this is a leading backfill;
    for files with 2021 coverage it is the mid-range hole 2021-12-31 ->
    2022-07-17.
  * **2024-H1** — the mid-range hole ending 2024-07-10 (present in every
    file; in the cross files it is part of the larger 2023-02 -> 2024-07
    gap, which is filled in full).
  * every other consecutive-bar gap > 4 days (e.g. the 2023-02 -> 2023-03
    and 2025-01 -> 2025-05/07 gaps), so the caches end up hole-free for the
    full span OANDA serves.

Data comes from the fixed OANDA adapter (paginated; ``complete=false``
forming bars dropped). The merge+save goes through ``ParquetStore.save``,
whose session-calendar validation also PURGES vendor junk weekend bars from
the old Yahoo-era segments (all Saturday bars, Sunday bars before 21:00 UTC,
Friday bars at/after 22:00 UTC) — reported per file below. Existing files
are backed up to ``backup_weekend_fix_2026-07-17/1h_pre_backfill/`` first.

A gap that remains after the fill is one OANDA itself has no candles for
(market-closed stretches, e.g. full Christmas weeks) — audited as such.

Usage:
    cd engine && .venv-mac/bin/python scripts/backfill_1h_holes.py
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

try:
    from dotenv import load_dotenv

    load_dotenv(ENGINE_DIR / ".env")
except ImportError:  # pragma: no cover - dotenv is a repo dep; degrade gracefully
    pass

from apex_quant.config import get_config  # noqa: E402
from apex_quant.data.oanda_adapter import OandaAdapter  # noqa: E402
from apex_quant.data.store import ParquetStore  # noqa: E402

STORE_ROOT = ENGINE_DIR / "data_store"
BACKUP_DIR = STORE_ROOT / "backup_weekend_fix_2026-07-17" / "1h_pre_backfill"
GAP_MIN = pd.Timedelta(days=4)  # > longest legit forex closure (holiday weeks)
EPOCH = pd.Timestamp("2022-01-01", tz="UTC")  # documented 2022-H1 hole start


def _gaps(idx: pd.DatetimeIndex) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Consecutive-bar gaps larger than GAP_MIN: [(prev_bar, next_bar), ...]."""
    out = []
    for prev, cur in zip(idx[:-1], idx[1:]):
        if cur - prev > GAP_MIN:
            out.append((prev, cur))
    return out


def _weekend_junk(idx: pd.DatetimeIndex) -> int:
    dow, hour = idx.dayofweek, idx.hour
    return int(((dow == 5) | ((dow == 6) & (hour < 21)) | ((dow == 4) & (hour >= 22))).sum())


def main() -> int:
    cfg = get_config()
    pairs: list[str] = list(cfg.data.instruments)
    store = ParquetStore(STORE_ROOT)
    adapter = OandaAdapter()
    now = pd.Timestamp(datetime.now(timezone.utc))
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Backfilling 1h holes for {len(pairs)} pairs at {now} (store: {STORE_ROOT})")
    print(f"Backup dir: {BACKUP_DIR}\n")

    rows = []
    failures: list[str] = []
    for pair in pairs:
        slug = pair.replace("/", "_")
        path = STORE_ROOT / f"{slug}_1h.parquet"
        old = pd.read_parquet(path)
        shutil.copy2(path, BACKUP_DIR / path.name)

        gaps_before = _gaps(old.index)
        junk_before = _weekend_junk(old.index)

        # Build the fetch intervals: documented 2022-H1 leading hole + every gap.
        intervals: list[tuple[pd.Timestamp, pd.Timestamp]] = []
        if old.index[0] > EPOCH + pd.Timedelta(days=3):
            intervals.append((EPOCH, old.index[0] - pd.Timedelta(hours=1)))
        for prev, nxt in gaps_before:
            intervals.append((prev + pd.Timedelta(hours=1), nxt - pd.Timedelta(hours=1)))

        parts = []
        filled_note = []
        for s, e in intervals:
            if e < s:
                continue
            try:
                df = adapter.get_history(pair, s, e, "1h")
            except Exception as exc:  # network/HTTP failure: record, keep old data
                filled_note.append(f"{s.date()}->{e.date()}: FETCH FAILED ({exc})")
                failures.append(f"{slug} {s.date()}->{e.date()}: {exc}")
                continue
            filled_note.append(f"{s.date()}->{e.date()}: +{len(df)} bars")
            if len(df):
                parts.append(df)

        combined = pd.concat([old] + parts) if parts else old
        store.save(pair, combined, "1h")
        new = store.load(pair, "1h")

        gaps_after = _gaps(new.index)
        rows.append(
            {
                "pair": slug,
                "before": len(old),
                "after": len(new),
                "added": len(new) - len(old) + 0,  # net incl. purged junk
                "junk_purged": junk_before - _weekend_junk(new.index),
                "gaps_before": gaps_before,
                "gaps_after": gaps_after,
                "notes": filled_note,
                "start": str(new.index[0]),
                "end": str(new.index[-1]),
            }
        )
        print(
            f"{slug:<9} rows {len(old):>6} -> {len(new):>6} | gaps {len(gaps_before)} -> "
            f"{len(gaps_after)} | junk purged {rows[-1]['junk_purged']:>5} | "
            f"{rows[-1]['start'][:16]} -> {rows[-1]['end'][:16]}"
        )
        for note in filled_note:
            print(f"    {note}")
        for prev, nxt in gaps_after:
            print(f"    REMAINING GAP (market-closed per OANDA): {prev} -> {nxt}")

    # audit table
    hdr = f"{'pair':<10}{'rows_before':>12}{'rows_after':>12}{'junk_purged':>12}{'gaps_before':>12}{'gaps_after':>11}  {'start':>17}{'end':>17}"
    lines = [hdr, "-" * len(hdr)]
    for r in rows:
        lines.append(
            f"{r['pair']:<10}{r['before']:>12}{r['after']:>12}{r['junk_purged']:>12}"
            f"{len(r['gaps_before']):>12}{len(r['gaps_after']):>11}  {r['start'][:16]:>17}{r['end'][:16]:>17}"
        )
    lines.append("-" * len(hdr))
    lines.append(
        f"pairs: {len(rows)} | total rows added (net): {sum(r['after'] - r['before'] for r in rows)} "
        f"| weekend junk purged: {sum(r['junk_purged'] for r in rows)} "
        f"| gaps: {sum(len(r['gaps_before']) for r in rows)} -> {sum(len(r['gaps_after']) for r in rows)}"
    )
    if failures:
        lines.append(f"FETCH FAILURES ({len(failures)}):")
        lines += [f"  {f}" for f in failures]
    report = "\n".join(lines)
    print("\n" + report)
    (STORE_ROOT / "backfill_1h_audit_2026-07-17.log").write_text(report + "\n", encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
