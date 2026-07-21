"""Blind backtest review — an anonymized LLM judge scores gate results it cannot identify.

Purpose: any LLM (this one, DeepSeek, all of them) KNOWS 2016-2024 market history, so a
review of "NVDA momentum 2016-2024" is hindsight-anchored by construction. This harness
strips everything identifiable — strategy names, instruments, calendar dates — and mixes
the real candidates with seeded NULL DECOYS (zero-edge stat blocks). The judge must score
pure statistical evidence. If it scores decoys as highly as real books, the judge itself
is exposed as noise and the packet says so.

ADVISORY ONLY: the quantitative gate (CPCV/DSR/PBO + prereg + ledger) remains the sole
authority. A blind judge can never promote a book the gate rejected.

Writes data_store/blind_review/blind_review_<UTC date>.json containing the blinded packet,
the judge's raw verdict, the unblinding map, and the model used.

Usage:
    cd engine
    .venv-mac/bin/python scripts/blind_backtest_review.py                       # Book H gate
    .venv-mac/bin/python scripts/blind_backtest_review.py --gate data_store/validation/book_i_gate_2026-07-20.json
    .venv-mac/bin/python scripts/blind_backtest_review.py --dry-run             # packet only, no LLM call
"""

from __future__ import annotations

import argparse
import json
import os
import string
import sys
from datetime import datetime, timezone
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ENGINE_DIR / ".env")

import httpx  # noqa: E402
import numpy as np  # noqa: E402

DEFAULT_GATE = ENGINE_DIR / "data_store" / "validation" / "book_h_gate_2026-07-19.json"
OUT_DIR = ENGINE_DIR / "data_store" / "blind_review"
SEED = 42
DECOYS_PER_REAL = 2


def _stats_block(m: dict, paths: list[float]) -> dict:
    """The judge sees exactly this — nothing identifiable."""
    return {
        "full_window_sharpe": round(float(m.get("sharpe", 0.0)), 3),
        "profit_factor": round(float(m.get("profit_factor") or 0.0), 3),
        "win_rate": round(float(m.get("win_rate", 0.0)), 3),
        "max_drawdown": round(float(m.get("max_drawdown", 0.0)), 3),
        "expectancy_pct_per_trade": round(float(m.get("expectancy_pct", 0.0)) * 100, 4),
        "n_trades": int(m.get("n_trades", 0)),
        "oos_cv_sharpe_paths": [round(float(p), 4) for p in paths],
    }


def make_decoy(rng: np.random.Generator, template: dict) -> dict:
    """A zero-edge candidate wearing the same clothes as a real one."""
    n_paths = len(template["oos_cv_sharpe_paths"]) or 15
    path_std = float(np.std(template["oos_cv_sharpe_paths"])) or 0.03
    return {
        "full_window_sharpe": round(float(rng.normal(0.0, 0.25)), 3),
        "profit_factor": round(float(1.0 + rng.normal(0.0, 0.06)), 3),
        "win_rate": round(float(rng.normal(0.50, 0.02)), 3),
        "max_drawdown": round(float(abs(rng.normal(0.28, 0.06))), 3),
        "expectancy_pct_per_trade": round(float(rng.normal(0.0, 0.05)), 4),
        "n_trades": int(template["n_trades"] * rng.uniform(0.8, 1.2)),
        "oos_cv_sharpe_paths": [round(float(x), 4)
                                for x in rng.normal(0.0, path_std, n_paths)],
    }


def build_packet(gate: dict, seed: int = SEED) -> dict:
    """Blinded candidates (real + decoys, shuffled) + the unblinding map."""
    rng = np.random.default_rng(seed)
    entries = []
    for name, book in gate.get("books", {}).items():
        paths = (book.get("cpcv") or {}).get("oos_sharpe_paths") or []
        entries.append(("REAL::" + name, _stats_block(book.get("metrics", {}), paths)))
    reals = [e[1] for e in entries]
    for i in range(DECOYS_PER_REAL * max(1, len(reals))):
        entries.append((f"NULL::decoy_{i}", make_decoy(rng, reals[i % len(reals)])))
    order = rng.permutation(len(entries))
    labels = list(string.ascii_uppercase)
    blinded, unblinding = {}, {}
    for lab, idx in zip(labels, order):
        truth, stats = entries[int(idx)]
        blinded[f"CANDIDATE_{lab}"] = stats
        unblinding[f"CANDIDATE_{lab}"] = truth
    return {"blinded": blinded, "unblinding": unblinding}


PROMPT = """You are reviewing anonymized systematic-trading backtest candidates.
All instrument names, strategy descriptions, and calendar dates have been removed —
you CANNOT know what markets or periods these are, so judge ONLY the statistics.
`oos_cv_sharpe_paths` are per-path out-of-sample Sharpe ratios (per period, not
annualized) from combinatorial purged cross-validation; other fields are full-window.
Some candidates are REAL strategies; some are ZERO-EDGE decoys built from noise.

For EACH candidate, in JSON only:
{"scores": {"CANDIDATE_X": {"score_0_to_10": n, "rationale": "one line"}},
 "fund_with_real_money": ["..."], "suspected_decoys": ["..."]}

Candidates:
"""


def call_judge(blinded: dict) -> tuple[str, str]:
    """DeepSeek first (user-selected), Groq fallback. Returns (model, raw_text)."""
    body = PROMPT + json.dumps(blinded, indent=1)
    ds_key = os.environ.get("APEX_AI__DEEPSEEK_API_KEY", "")
    if ds_key:
        model = os.environ.get("APEX_AI__DEEPSEEK_MODEL", "deepseek-chat")
        r = httpx.post("https://api.deepseek.com/chat/completions",
                       headers={"Authorization": f"Bearer {ds_key}"},
                       json={"model": model, "temperature": 0.0,
                             "messages": [{"role": "user", "content": body}]},
                       timeout=120)
        if r.status_code == 200:
            return f"deepseek/{model}", r.json()["choices"][0]["message"]["content"]
        print(f"  deepseek HTTP {r.status_code} — falling back to groq")
    gq_key = os.environ.get("GROQ_API_KEY", "")
    if not gq_key:
        raise RuntimeError("no judge available: neither DeepSeek nor Groq key set")
    r = httpx.post("https://api.groq.com/openai/v1/chat/completions",
                   headers={"Authorization": f"Bearer {gq_key}"},
                   json={"model": "llama-3.3-70b-versatile", "temperature": 0.0,
                         "messages": [{"role": "user", "content": body}]},
                   timeout=120)
    r.raise_for_status()
    return "groq/llama-3.3-70b-versatile", r.json()["choices"][0]["message"]["content"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Anonymized LLM review of a gate results JSON (advisory only).")
    ap.add_argument("--gate", default=str(DEFAULT_GATE))
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--dry-run", action="store_true", help="build + save the blinded packet, skip the LLM")
    args = ap.parse_args(argv)

    gate = json.loads(Path(args.gate).read_text(encoding="utf-8"))
    packet = build_packet(gate, seed=args.seed)
    n_real = sum(1 for v in packet["unblinding"].values() if v.startswith("REAL::"))
    print(f"blinded packet: {len(packet['blinded'])} candidates "
          f"({n_real} real, {len(packet['blinded']) - n_real} decoys) from {Path(args.gate).name}")

    model, verdict = ("none (dry-run)", None) if args.dry_run else call_judge(packet["blinded"])
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "gate_file": str(Path(args.gate).relative_to(ENGINE_DIR)),
        "seed": args.seed,
        "judge_model": model,
        "advisory_note": ("Blind review is ADVISORY. The quantitative gate (CPCV/DSR/PBO, prereg, "
                          "ledger) is the only authority; a judge can never promote a rejected book."),
        "blinded_packet": packet["blinded"],
        "judge_verdict_raw": verdict,
        "unblinding_map": packet["unblinding"],
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"blind_review_{datetime.now(timezone.utc).date().isoformat()}.json"
    out_path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"saved {out_path.relative_to(ENGINE_DIR)} | judge: {model}")
    if verdict:
        print("--- judge verdict (raw) ---")
        print(verdict[:1500])
        print("--- unblinding ---")
        for lab, truth in sorted(packet["unblinding"].items()):
            print(f"  {lab} = {truth}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
