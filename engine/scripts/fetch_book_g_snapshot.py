#!/usr/bin/env python3
"""Fetch and freeze the adjusted daily OHLC panel for Book G.

The downloader is intentionally narrow: it makes the exact request frozen in
the Book G preregistration, validates a complete XNYS panel without filling any
missing observation, and writes one normalized parquet file plus a manifest.
Importing this module never imports ``yfinance`` or performs network I/O, which
allows the validation boundary to be tested before the sealed data are fetched.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

import exchange_calendars as xcals
import numpy as np
import pandas as pd


ENGINE_DIR = Path(__file__).resolve().parent.parent
PROTOCOL = ENGINE_DIR / "data_store" / "book_g_macro_guard_prereg_2026-09-04.md"
DEFAULT_SNAPSHOT = (
    ENGINE_DIR / "data_store" / "validation" / "book_g_inputs_2026-09-04.parquet"
)
DEFAULT_MANIFEST = DEFAULT_SNAPSHOT.with_suffix(".manifest.json")

DOWNLOAD_START = "2014-01-01"
DOWNLOAD_END_EXCLUSIVE = "2026-09-04"
INTERVAL = "1d"

BOOK_G_SYMBOLS = (
    "SPY",
    "XLK",
    "XLE",
    "XLV",
    "XLI",
    "XLF",
    "XLP",
    "XLU",
    "GLD",
    "TLT",
    "IEF",
    "SHY",
    "UUP",
)
SNAPSHOT_COLUMNS = ("date", "symbol", "open", "high", "low", "close")
PRICE_COLUMNS = ("open", "high", "low", "close")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp{os.getpid()}")
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _load_yfinance() -> ModuleType:
    """Import yfinance only when an explicit fetch is requested."""

    try:
        return importlib.import_module("yfinance")
    except ModuleNotFoundError as exc:  # pragma: no cover - environment-specific
        raise RuntimeError(
            "Book G snapshot retrieval requires the yfinance package"
        ) from exc


def expected_xnys_sessions() -> pd.DatetimeIndex:
    """Return every expected session covered by the frozen exclusive bounds."""

    end_inclusive = pd.Timestamp(DOWNLOAD_END_EXCLUSIVE) - pd.Timedelta(days=1)
    sessions = xcals.get_calendar("XNYS").sessions_in_range(
        pd.Timestamp(DOWNLOAD_START), end_inclusive
    )
    return pd.DatetimeIndex(sessions).tz_localize(None).normalize()


def _download_symbol(yfinance_module: Any, symbol: str) -> pd.DataFrame:
    """Make the exact frozen yfinance request for one symbol."""

    return yfinance_module.download(
        symbol,
        start=DOWNLOAD_START,
        end=DOWNLOAD_END_EXCLUSIVE,
        interval=INTERVAL,
        auto_adjust=True,
        actions=False,
        repair=False,
        threads=False,
        progress=False,
    )


def _single_symbol_columns(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Flatten either supported yfinance single-ticker column layout."""

    if not isinstance(frame.columns, pd.MultiIndex):
        return frame.copy()

    required = {"open", "high", "low", "close"}
    for level in range(frame.columns.nlevels):
        labels = frame.columns.get_level_values(level).astype(str)
        lowered = {label.casefold() for label in labels}
        if required.issubset(lowered):
            flattened = frame.copy()
            flattened.columns = labels
            if flattened.columns.duplicated().any():
                raise ValueError(f"{symbol}: ambiguous duplicate yfinance price columns")
            return flattened
    raise ValueError(f"{symbol}: unsupported yfinance multi-index column layout")


def normalize_symbol_frame(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Normalize one downloaded symbol to date/symbol/OHLC long-form rows."""

    if symbol not in BOOK_G_SYMBOLS:
        raise ValueError(f"unexpected Book G symbol: {symbol}")
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError(f"{symbol}: yfinance returned no rows")

    flattened = _single_symbol_columns(frame, symbol)
    by_casefold = {str(column).casefold(): column for column in flattened.columns}
    missing_columns = [name for name in PRICE_COLUMNS if name not in by_casefold]
    if missing_columns:
        raise ValueError(
            f"{symbol}: yfinance response is missing OHLC columns: "
            + ", ".join(missing_columns)
        )

    dates = pd.DatetimeIndex(pd.to_datetime(flattened.index, errors="coerce"))
    if dates.isna().any():
        raise ValueError(f"{symbol}: yfinance response contains an invalid date")
    if dates.tz is not None:
        # Daily yfinance bars are labelled by exchange-local session date.  Drop
        # the timezone without shifting that semantic date.
        dates = dates.tz_localize(None)
    dates = dates.normalize()

    normalized = pd.DataFrame({"date": dates})
    normalized.insert(1, "symbol", symbol)
    for name in PRICE_COLUMNS:
        normalized[name] = pd.to_numeric(
            flattened[by_casefold[name]].to_numpy(), errors="coerce"
        ).astype(float)
    normalized = normalized.loc[:, SNAPSHOT_COLUMNS].sort_values(
        "date", kind="stable"
    )
    normalized.reset_index(drop=True, inplace=True)

    if normalized["date"].duplicated().any():
        duplicates = normalized.loc[normalized["date"].duplicated(), "date"]
        raise ValueError(
            f"{symbol}: duplicate session date {duplicates.iloc[0].date().isoformat()}"
        )

    prices = normalized.loc[:, PRICE_COLUMNS].to_numpy(dtype=float)
    if not np.isfinite(prices).all():
        raise ValueError(f"{symbol}: OHLC contains a non-finite value")
    if (prices <= 0.0).any():
        raise ValueError(f"{symbol}: OHLC contains a non-positive value")
    if (
        normalized["high"]
        < normalized.loc[:, ["open", "close"]].max(axis=1)
    ).any():
        raise ValueError(f"{symbol}: high is below open or close")
    if (
        normalized["low"]
        > normalized.loc[:, ["open", "close"]].min(axis=1)
    ).any():
        raise ValueError(f"{symbol}: low is above open or close")
    return normalized


def validate_panel(
    frames: dict[str, pd.DataFrame],
    *,
    expected_sessions: pd.DatetimeIndex | None = None,
) -> pd.DataFrame:
    """Validate all symbols against one complete, unfilled XNYS date panel."""

    actual_symbols = set(frames)
    required_symbols = set(BOOK_G_SYMBOLS)
    missing_symbols = sorted(required_symbols - actual_symbols)
    extra_symbols = sorted(actual_symbols - required_symbols)
    if missing_symbols or extra_symbols:
        detail: list[str] = []
        if missing_symbols:
            detail.append("missing symbols: " + ", ".join(missing_symbols))
        if extra_symbols:
            detail.append("unexpected symbols: " + ", ".join(extra_symbols))
        raise ValueError("Book G symbol set mismatch (" + "; ".join(detail) + ")")

    expected = pd.DatetimeIndex(
        expected_xnys_sessions() if expected_sessions is None else expected_sessions
    )
    if expected.tz is not None:
        expected = expected.tz_localize(None)
    expected = expected.normalize().sort_values()
    if expected.empty or expected.duplicated().any():
        raise ValueError("expected XNYS session index is empty or duplicated")

    normalized_parts: list[pd.DataFrame] = []
    for symbol in BOOK_G_SYMBOLS:
        part = normalize_symbol_frame(frames[symbol], symbol)
        actual = pd.DatetimeIndex(part["date"])
        missing = expected.difference(actual)
        unexpected = actual.difference(expected)
        if len(missing) or len(unexpected):
            detail = []
            if len(missing):
                preview = ", ".join(date.date().isoformat() for date in missing[:5])
                detail.append(f"missing expected XNYS sessions: {preview}")
            if len(unexpected):
                preview = ", ".join(
                    date.date().isoformat() for date in unexpected[:5]
                )
                detail.append(f"non-XNYS sessions: {preview}")
            raise ValueError(f"{symbol}: " + "; ".join(detail))
        normalized_parts.append(part)

    panel = pd.concat(normalized_parts, ignore_index=True)
    panel = panel.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)
    panel = panel.loc[:, SNAPSHOT_COLUMNS]
    if panel.duplicated(["date", "symbol"]).any():
        raise ValueError("Book G panel contains duplicate date-symbol rows")
    expected_rows = len(expected) * len(BOOK_G_SYMBOLS)
    if len(panel) != expected_rows:
        raise ValueError(
            f"Book G panel has {len(panel)} rows; expected {expected_rows}"
        )
    return panel


def fetch_snapshot(
    snapshot_path: Path,
    manifest_path: Path,
    *,
    force: bool = False,
    yfinance_module: Any | None = None,
    expected_sessions: pd.DatetimeIndex | None = None,
    retrieved_at_utc: datetime | None = None,
) -> dict[str, Any]:
    """Download, validate and atomically publish the Book G snapshot artifacts.

    ``yfinance_module`` and ``expected_sessions`` are dependency-injection seams
    for offline tests.  The CLI exposes neither, so production retrieval always
    uses the real package and the complete frozen XNYS calendar.
    """

    snapshot_path = Path(snapshot_path)
    manifest_path = Path(manifest_path)
    if not PROTOCOL.is_file():
        raise FileNotFoundError(f"missing frozen Book G protocol: {PROTOCOL}")
    if snapshot_path.resolve() == manifest_path.resolve():
        raise ValueError("snapshot and manifest paths must differ")
    if not force and (snapshot_path.exists() or manifest_path.exists()):
        raise FileExistsError(
            "Book G snapshot or manifest already exists; use --force deliberately"
        )

    yf = _load_yfinance() if yfinance_module is None else yfinance_module
    version = getattr(yf, "__version__", None)
    if not isinstance(version, str) or not version.strip():
        raise RuntimeError("cannot record the required yfinance package version")

    downloaded: dict[str, pd.DataFrame] = {}
    for symbol in BOOK_G_SYMBOLS:
        downloaded[symbol] = _download_symbol(yf, symbol)
    expected = (
        expected_xnys_sessions()
        if expected_sessions is None
        else pd.DatetimeIndex(expected_sessions)
    )
    if expected.tz is not None:
        expected = expected.tz_localize(None)
    expected = expected.normalize().sort_values()
    panel = validate_panel(downloaded, expected_sessions=expected)

    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_snapshot = snapshot_path.with_name(
        f"{snapshot_path.name}.tmp{os.getpid()}"
    )
    try:
        panel.to_parquet(temporary_snapshot, index=False)
        os.replace(temporary_snapshot, snapshot_path)
    finally:
        if temporary_snapshot.exists():
            temporary_snapshot.unlink()

    retrieved = retrieved_at_utc or datetime.now(timezone.utc)
    if retrieved.tzinfo is None or retrieved.utcoffset() is None:
        raise ValueError("retrieved_at_utc must be timezone-aware")
    retrieved = retrieved.astimezone(timezone.utc)
    row_counts = {
        symbol: int((panel["symbol"] == symbol).sum()) for symbol in BOOK_G_SYMBOLS
    }
    coverage = {
        symbol: {
            "first_date": panel.loc[panel["symbol"] == symbol, "date"]
            .min()
            .date()
            .isoformat(),
            "last_date": panel.loc[panel["symbol"] == symbol, "date"]
            .max()
            .date()
            .isoformat(),
            "rows": row_counts[symbol],
        }
        for symbol in BOOK_G_SYMBOLS
    }
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "kind": "book_g_yfinance_adjusted_ohlc_snapshot",
        "protocol_path": str(PROTOCOL),
        "protocol_sha256": _sha256_file(PROTOCOL),
        "retrieved_at_utc": retrieved.isoformat(),
        "request": {
            "library": "yfinance",
            "library_version": version,
            "download_mode": "one_symbol_per_call_in_declared_order",
            "symbols": list(BOOK_G_SYMBOLS),
            "start": DOWNLOAD_START,
            "end_exclusive": DOWNLOAD_END_EXCLUSIVE,
            "interval": INTERVAL,
            "auto_adjust": True,
            "actions": False,
            "repair": False,
            "threads": False,
            "progress": False,
        },
        "calendar": {
            "name": "XNYS",
            "expected_first_session": expected.min().date().isoformat(),
            "expected_last_session": expected.max().date().isoformat(),
            "expected_sessions": int(len(expected)),
            "missing_rows_allowed": 0,
            "forward_fill": False,
        },
        "coverage": coverage,
        "row_counts": row_counts,
        "snapshot": {
            "path": str(snapshot_path.resolve()),
            "columns": list(SNAPSHOT_COLUMNS),
            "rows": int(len(panel)),
            "bytes": snapshot_path.stat().st_size,
            "sha256": _sha256_file(snapshot_path),
        },
        "limitations": (
            "Adjusted Yahoo ETF bars are a research total-return proxy, not "
            "broker-native executable bid/ask or CE(S)T intraday equity data."
        ),
    }
    _atomic_text(
        manifest_path,
        json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch the frozen Book G yfinance OHLC snapshot"
    )
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    manifest = fetch_snapshot(
        args.snapshot,
        args.manifest,
        force=args.force,
    )
    print(
        f"snapshot: {args.snapshot} ({manifest['snapshot']['sha256']})",
        flush=True,
    )
    print(f"manifest: {args.manifest}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
