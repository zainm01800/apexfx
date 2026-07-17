"""Rebuild all forex daily + weekly caches on the session-date convention.

D-C1 root fix (2026-07-17 consolidated audit). The old caches carried ~543
Sunday + 53-101 Saturday bars per pair because OANDA's 17:00-New-York daily
sessions were pinned to 00:00 UTC of the label date (yesterday's dedup fixed
time-of-day only, not the session->date mapping). This script:

  1. backs up the existing forex 1d/1w parquets to
     data_store/backup_weekend_fix_2026-07-17/,
  2. refetches full daily history per pair from OANDA (the fixed adapter:
     paginated, complete=false forming bars dropped),
  3. writes via ParquetStore.save, which remaps to session dates (Sunday
     17:00-NY session -> Monday; Mon-Fri only) and REJECTS off-calendar rows,
  4. rebuilds each weekly cache from the rebuilt daily (Mon-Fri weeks
     labelled at Monday, still-forming current week excluded),
  5. prints an audit table: rows before/after, weekend bars removed, and the
     bars-per-week distribution (must be 5 for full weeks).

Crypto/equity 1d files are NOT rebuilt here (different calendars; Yahoo
crypto daily has known holes — documented, not fabricated).

Usage:
    cd engine && .venv-mac/bin/python scripts/rebuild_daily_caches.py
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
from apex_quant.data.calendar import trim_forming_tail  # noqa: E402
from apex_quant.data.oanda_adapter import OandaAdapter  # noqa: E402
from apex_quant.data.store import ParquetStore  # noqa: E402

STORE_ROOT = ENGINE_DIR / "data_store"
BACKUP_DIR = STORE_ROOT / "backup_weekend_fix_2026-07-17"
FETCH_START = "2016-01-01"  # matches the coverage start of the old caches


def _weekend_count(idx: pd.DatetimeIndex) -> tuple[int, int]:
    dow = idx.dayofweek
    return int((dow == 6).sum()), int((dow == 5).sum())  # (Sun, Sat)


def _bars_per_week(idx: pd.DatetimeIndex) -> dict[int, int]:
    """Distribution {bars_in_week: n_weeks} over Mon-Fri session dates."""
    iso = idx.isocalendar()
    counts = iso.groupby([iso.year, iso.week]).size()
    return counts.value_counts().sort_index().to_dict()


def weekly_from_daily(daily: pd.DataFrame) -> pd.DataFrame:
    """Resample session-dated daily bars into Monday-labelled weekly bars.

    Label = Monday of the ISO week (even when a holiday shifts the week's
    first session), so the weekly frame obeys the forex 1w calendar. The
    still-forming current week is excluded by the caller via
    ``trim_forming_tail``.
    """
    iso = daily.index.isocalendar()
    keys = [iso.year, iso.week]
    grp = daily.groupby(keys, sort=True)
    weekly = grp.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    )
    first_dates = daily.index.to_series().groupby(keys).min()
    weekly.index = pd.DatetimeIndex(
        [d - pd.Timedelta(days=d.dayofweek) for d in first_dates], name="timestamp"
    )
    return weekly


def main() -> int:
    cfg = get_config()
    pairs: list[str] = list(cfg.data.instruments)
    store = ParquetStore(STORE_ROOT)
    adapter = OandaAdapter()
    now = pd.Timestamp(datetime.now(timezone.utc))
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Rebuilding {len(pairs)} forex 1d/1w caches at {now} (store: {STORE_ROOT})")
    print(f"Backup dir: {BACKUP_DIR}\n")

    rows = []
    for pair in pairs:
        slug = pair.replace("/", "_")
        rec: dict = {"pair": slug}
        for tf in ("1d", "1w"):
            src = STORE_ROOT / f"{slug}_{tf}.parquet"
            if src.exists():
                shutil.copy2(src, BACKUP_DIR / src.name)

        old_path = STORE_ROOT / f"{slug}_1d.parquet"
        old = pd.read_parquet(old_path) if old_path.exists() else None
        rec["d_before"] = len(old) if old is not None else 0
        rec["d_sun"], rec["d_sat"] = _weekend_count(old.index) if old is not None else (0, 0)

        fetched = adapter.get_history(pair, FETCH_START, now, "1d")
        store.save(pair, fetched, "1d")
        new = store.load(pair, "1d")
        rec["d_after"] = len(new)
        rec["d_sun_a"], rec["d_sat_a"] = _weekend_count(new.index)
        rec["d_start"], rec["d_end"] = str(new.index[0].date()), str(new.index[-1].date())

        weekly = trim_forming_tail(weekly_from_daily(new), pair, "1w")
        store.save(pair, weekly, "1w")
        wnew = store.load(pair, "1w")
        rec["w_after"] = len(wnew)
        rec["w_start"], rec["w_end"] = str(wnew.index[0].date()), str(wnew.index[-1].date())
        rec["w_nonmon"] = int((wnew.index.dayofweek != 0).sum())
        rec["bpw"] = _bars_per_week(new.index)
        rows.append(rec)
        print(
            f"{slug:<9} 1d {rec['d_before']:>5} -> {rec['d_after']:>5} rows "
            f"(weekend removed: Sun {rec['d_sun'] - rec['d_sun_a']}, Sat {rec['d_sat'] - rec['d_sat_a']}) "
            f"{rec['d_start']} -> {rec['d_end']} | 1w {rec['w_after']} rows "
            f"{rec['w_start']} -> {rec['w_end']} non-Mon={rec['w_nonmon']} bars/week={rec['bpw']}"
        )

    # audit table
    hdr = (
        f"{'pair':<10}{'d_before':>9}{'d_after':>9}{'sun_rm':>7}{'sat_rm':>7}"
        f"{'d_start':>12}{'d_end':>12}{'w_rows':>8}{'w_start':>12}{'w_end':>12}{'nonMon':>7}  bars/week_dist"
    )
    lines = [hdr, "-" * len(hdr)]
    for r in rows:
        lines.append(
            f"{r['pair']:<10}{r['d_before']:>9}{r['d_after']:>9}"
            f"{r['d_sun'] - r['d_sun_a']:>7}{r['d_sat'] - r['d_sat_a']:>7}"
            f"{r['d_start']:>12}{r['d_end']:>12}{r['w_after']:>8}"
            f"{r['w_start']:>12}{r['w_end']:>12}{r['w_nonmon']:>7}  {r['bpw']}"
        )
    total_sun = sum(r["d_sun"] - r["d_sun_a"] for r in rows)
    total_sat = sum(r["d_sat"] - r["d_sat_a"] for r in rows)
    lines.append("-" * len(hdr))
    lines.append(
        f"pairs: {len(rows)} | weekend bars removed: Sun {total_sun}, Sat {total_sat} "
        f"(remaining Sun {sum(r['d_sun_a'] for r in rows)}, Sat {sum(r['d_sat_a'] for r in rows)})"
    )
    bad = [r["pair"] for r in rows if r["d_sun_a"] or r["d_sat_a"] or r["w_nonmon"]]
    lines.append("FAIL: " + ", ".join(bad) if bad else "OK: all forex 1d caches are Mon-Fri; all 1w caches Monday-labelled.")
    report = "\n".join(lines)
    print("\n" + report)
    (STORE_ROOT / "rebuild_daily_audit_2026-07-17.log").write_text(report + "\n", encoding="utf-8")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
