"""AUTO-RESEARCHER — Part 2 of the research loop.

Reads the engine's experiment history (trial ledger + newest gate verdicts) and
asks Groq (llama-3.3-70b-versatile) to draft 1-2 NEW experiment proposals — each
with a stated mechanism and a pre-registered kill criterion — into the
``apex_research_proposals`` Supabase table. The site's Progress tab renders the
queue via ``/api/progress``.

DISCIPLINE (absolute): proposals are DRAFTS for human review (status='draft').
Nothing in this script runs an experiment, changes a config, or trades. The only
side effect is INSERT/UPSERT of draft rows; a human promotes or kills them.

Usage:
    python scripts/run_auto_researcher.py            # draft + upsert into Supabase
    python scripts/run_auto_researcher.py --dry-run  # print proposals, write nothing

Weekly cadence via .github/workflows/auto-researcher.yml. Requires GROQ_API_KEY
(engine/.env locally; GitHub secret in CI). Writes need SUPABASE_SERVICE_KEY
(anon is SELECT-only since the 2026-07-17 RLS lockdown).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Add engine directory to sys.path so we can import apex_quant
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Load .env file from engine/ directory
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from apex_quant.ai.client import extract_json  # noqa: E402
from apex_quant.storage._keys import service_or_anon_key  # noqa: E402

ENGINE_DIR = Path(__file__).resolve().parent.parent
VALIDATION_DIR = ENGINE_DIR / "data_store" / "validation"
LEDGER_PATH = VALIDATION_DIR / "trial_ledger.json"

SUPABASE_URL = os.environ.get(
    "SUPABASE_URL", "https://cuvchjhaojhmxfgczndy.supabase.co"
).rstrip("/")
TABLE_ENDPOINT = f"{SUPABASE_URL}/rest/v1/apex_research_proposals"

GROQ_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = "llama-3.3-70b-versatile"  # synthesis model; 8b-instant is for bulk jobs
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

N_GATE_FILES = 8       # newest gate JSONs fed to the researcher
MAX_PROPOSALS = 2      # hard cap — the queue is for review, not volume
MAX_CONFIGS = 2        # suggested_configs per proposal

# ── Dead ends (2026-07-28) ────────────────────────────────────────────────────
# Closed leads the researcher must NOT re-propose. Fed into the prompt verbatim
# AND enforced mechanically by the dedupe check below.
DEAD_ENDS = [
    "FX trend", "15m momentum", "4h crypto trend", "PEAD", "earnings blackout",
    "short-term reversal", "meta-labeling", "notional cap",
    "early partials (+0.75R/+0.5R)", "vol-adaptive partials", "runner exits",
    "vol-managed overlay", "FX carry", "put-write", "crypto XS momentum (incubating)",
    "universe expansion (independence ceiling found)", "defensive sukuk/gold sleeve",
    "CF-CVaR sizing", "order-invariant allocation (owner holds)",
    "Books I/J/K/L/R/S/T", "FOMC drift", "BTC dominance rotation",
    "low-vol anomaly", "sector rotation",
]

# Mechanical match phrases derived from the dead-end list (parentheticals and
# book enumerations expanded) plus the known alias for cross-sectional momentum.
_DEAD_END_PHRASES = [
    "fx trend", "15m momentum", "4h crypto trend", "pead", "post-earnings",
    "earnings blackout", "short-term reversal", "meta-label", "notional cap",
    "early partial", "0.75r", "0.5r", "vol-adaptive partial", "runner exit",
    "vol-managed", "vol managed", "fx carry", "put-write", "put write",
    "xs momentum", "cross-sectional momentum", "universe expansion",
    "sukuk", "gold sleeve", "defensive sleeve", "cf-cvar", "cvar sizing",
    "order-invariant", "book i", "book j", "book k", "book l", "book r",
    "book s", "book t", "fomc drift", "btc dominance", "low-vol anomaly",
    "low vol anomaly", "sector rotation",
]


# ── Experiment history loading ───────────────────────────────────────────────
def load_ledger_summary() -> dict:
    """Trial ledger: dedup registry {"<json of {factory, instrument, params,
    timeframe}>": null} — no timestamps. Returns count + kinds (factory counts)."""
    if not LEDGER_PATH.exists():
        print(f"  [WARN] trial ledger not found at {LEDGER_PATH}")
        return {"total": 0, "kinds": {}}
    ledger = json.loads(LEDGER_PATH.read_text())
    kinds: dict[str, int] = {}
    for key in ledger:
        try:
            entry = json.loads(key)
            kind = entry.get("factory") or "unknown"
        except (json.JSONDecodeError, AttributeError):
            kind = "unknown"
        kinds[kind] = kinds.get(kind, 0) + 1
    return {"total": len(ledger), "kinds": dict(sorted(kinds.items(), key=lambda kv: -kv[1]))}


def _date_from_name(name: str) -> str | None:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", name)
    return m.group(1) if m else None


def _num(x) -> float | None:
    try:
        v = float(x)
        return v if v == v and abs(v) != float("inf") else None  # NaN/inf guard
    except (TypeError, ValueError):
        return None


def gate_blocks_from_file(name: str, d: dict) -> list[dict]:
    """Mirror of api/progress.js gateEntriesFromFile: per-book verdict blocks from
    `verdicts` (preferred), `books[*].gate`, `grid_results[*].gate`,
    `run1.grid_results[*].gate`, or `adoption` (trend-ensemble shape)."""
    if not isinstance(d, dict):
        return []
    kind = d.get("kind") if isinstance(d.get("kind"), str) else None
    is_measurement = bool(re.search(r"measurement", kind or "", re.I)
                          or re.search(r"measurement", name, re.I))
    date = str(d.get("generated_at") or d.get("timestamp") or "")[:10] or _date_from_name(name)

    blocks: list[tuple[str, dict]] = []
    if isinstance(d.get("verdicts"), dict):
        blocks.extend((b, v) for b, v in d["verdicts"].items() if isinstance(v, dict))
    else:
        for container in (d.get("books"), d.get("grid_results"),
                          (d.get("run1") or {}).get("grid_results")):
            if isinstance(container, dict):
                blocks.extend((b, v["gate"]) for b, v in container.items()
                              if isinstance(v, dict) and isinstance(v.get("gate"), dict))
            if blocks:
                break
    if not blocks and isinstance(d.get("adoption"), dict):
        for book, v in d["adoption"].items():
            if isinstance(v, dict) and isinstance(v.get("adopted"), bool):
                blocks.append((book, {"passed": v["adopted"], "dsr": {"dsr": _num(v.get("dsr"))},
                                      "reasons": [f"cpcv_paths_won_vs_control={v.get('cpcv_paths_won_vs_control')}"]}))

    out = []
    for book, v in blocks:
        verdict = None
        if isinstance(v.get("passed"), bool):
            verdict = "pass" if v["passed"] else "reject"
        elif isinstance(v.get("adopt_eligible"), bool):
            verdict = "pass" if v["adopt_eligible"] else "reject"
        if is_measurement and verdict:
            verdict = "measurement"
        if not verdict:
            continue
        dsr = v.get("dsr") if isinstance(v.get("dsr"), dict) else {}
        pbo = v.get("pbo") if isinstance(v.get("pbo"), dict) else {}
        reasons = v.get("reasons") if isinstance(v.get("reasons"), list) else []
        out.append({
            "file": name, "date": date, "book": book, "verdict": verdict,
            "dsr": _num(dsr.get("dsr")),
            "sharpe": _num(dsr.get("observed_sharpe_ann")),
            "pbo": _num(pbo.get("pbo")) or _num((d.get("pbo") or {}).get("pbo")),
            "takeaway": " · ".join(str(r) for r in reasons)[:300] or None,
        })
    return out


def load_recent_gates(n: int = N_GATE_FILES) -> list[dict]:
    """Newest n gate JSONs (date-in-filename sort — mtime is useless in CI
    checkouts) parsed into compact verdict blocks for the prompt."""
    is_gate = re.compile(r"(_gate[_.]|measurement)", re.I)

    def sort_key(p: Path):
        d = _date_from_name(p.name)
        return (d or "", p.name)

    files = sorted(
        (p for p in VALIDATION_DIR.glob("*.json") if is_gate.search(p.name)),
        key=sort_key, reverse=True,
    )[:n]

    gates = []
    for p in files:
        try:
            d = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"  [WARN] skipping unreadable gate file {p.name}: {e}")
            continue
        blocks = gate_blocks_from_file(p.name, d)
        if not blocks:
            # Measurement / scalar-only files still say something useful.
            blocks = [{
                "file": p.name, "date": _date_from_name(p.name), "book": None,
                "verdict": "measurement",
                "dsr": None, "sharpe": None,
                "pbo": _num((d.get("pbo") or {}).get("pbo")) if isinstance(d.get("pbo"), dict) else None,
                "takeaway": str(d.get("verdict_rule") or d.get("kind") or "")[:300] or None,
            }]
        gates.extend(blocks)
    return gates


# ── Prior proposals (dedupe source) ──────────────────────────────────────────
def fetch_prior_proposals(headers: dict) -> tuple[list[dict], bool]:
    """(rows, table_exists). Tolerates the table being absent — the SQL migration
    may not have been pasted yet."""
    try:
        r = httpx.get(f"{TABLE_ENDPOINT}?select=id,title&limit=100",
                      headers=headers, timeout=30)
        if r.status_code != 200:
            print(f"  [WARN] apex_research_proposals read returned HTTP {r.status_code} "
                  f"— treating as empty (run supabase/apex_research_proposals.sql if missing).")
            return [], False
        rows = r.json()
        return (rows if isinstance(rows, list) else []), True
    except Exception as e:
        print(f"  [WARN] prior-proposal fetch failed: {type(e).__name__}: {e}")
        return [], False


# ── Prompt ────────────────────────────────────────────────────────────────────
def build_prompt(ledger: dict, gates: list[dict], prior_titles: list[str]) -> tuple[str, str]:
    kinds_text = "\n".join(f"  - {k}: {n} trials" for k, n in ledger["kinds"].items())
    gate_lines = []
    for g in gates:
        bits = [f"[{g['verdict'].upper()}]", g["file"]]
        if g.get("book"):
            bits.append(f"book={g['book']}")
        metrics = []
        if g.get("sharpe") is not None:
            metrics.append(f"Sharpe {g['sharpe']:.3f}")
        if g.get("dsr") is not None:
            metrics.append(f"DSR {g['dsr']:.3f}")
        if g.get("pbo") is not None:
            metrics.append(f"PBO {g['pbo']:.3f}")
        if metrics:
            bits.append("(" + ", ".join(metrics) + ")")
        if g.get("takeaway"):
            bits.append("— " + g["takeaway"])
        gate_lines.append("  " + " ".join(bits))
    gates_text = "\n".join(gate_lines) or "  (none parsed)"
    prior_text = "\n".join(f"  - {t}" for t in prior_titles) or "  (queue empty)"
    dead_text = "\n".join(f"  - {d}" for d in DEAD_ENDS)

    system = (
        "You are the research assistant for APEX, a validated multi-asset trend-following "
        "engine (daily bars; ~42 instruments: US mega-cap equities, sector ETFs, sukuk/gold "
        "UCITS ETFs, crypto majors, FX majors). Its validation bar is strict: DSR > 0.95 "
        "deflated against the full trial ledger, PBO < 0.5, CPCV majority-positive paths, "
        "single-shot pre-registered gates. Rejections are the norm; weak ideas waste a "
        "validation slot. Reply ONLY with valid JSON."
    )
    prompt = f"""EXPERIMENT HISTORY (trial ledger — {ledger['total']} deduplicated trials by kind):
{kinds_text}

NEWEST GATE VERDICTS (what was just tried, and how it ended):
{gates_text}

DEAD ENDS — already tested and CLOSED, do NOT re-propose any of these or a minor variant:
{dead_text}

ALREADY IN THE REVIEW QUEUE (do not re-propose):
{prior_text}

Draft EXACTLY 1-2 NEW experiment proposals. Every proposal MUST:
(a) NOT duplicate anything in the ledger kinds, the dead ends, or the queue — a different mechanism, not a re-parameterisation of a closed lead;
(b) state a MECHANISM — the economic or behavioural reason the edge should exist, and why it should survive retail costs;
(c) be testable with daily bars at retail costs (equity 2bps + 1bps/side commission, crypto 1.25bps/side) on the existing universe and data — no options chains, no intraday feeds, no short-sale borrow data, no fundamentals databases;
(d) be halal-compatible — no riba: no interest-rate/carry mechanisms, no conventional bond or lending yield;
(e) carry a pre-registered KILL CRITERION — the single measurement that, if it fails, closes the idea after one gate run.

Respond with ONE JSON object:
{{"proposals": [
  {{"title": "...", "summary": "2-3 sentences: what to test and on which instruments",
    "mechanism": "why the edge should exist",
    "suggested_configs": ["<=2 concrete configs, e.g. lookback/holding choices"],
    "kill_criterion": "the falsification rule",
    "evidence_links": []}}
]}}
No markdown, no prose outside the JSON."""
    return prompt, system


# ── Groq ──────────────────────────────────────────────────────────────────────
def groq_propose(prompt: str, system: str, retries: int = 3) -> str | None:
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 2000,
        "temperature": 0.4,
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
    for attempt in range(retries):
        try:
            r = httpx.post(GROQ_URL, json=payload, headers=headers, timeout=90)
            if r.status_code == 429:
                wait = 20 * (attempt + 1)
                print(f"  [Rate Limit] waiting {wait}s (attempt {attempt + 1}/{retries})...")
                time.sleep(wait)
                continue
            if r.status_code != 200:
                print(f"  [Groq Error] HTTP {r.status_code}: {r.text[:300]}")
                return None
            content = r.json()["choices"][0]["message"]["content"]
            if content and "<think>" in content:
                if "</think>" in content:
                    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
                else:
                    content = content.split("<think>")[0].strip()
            return content
        except Exception as e:
            print(f"  [Groq Exception] {type(e).__name__}: {e}")
            return None
    return None


# ── Validation & dedupe ──────────────────────────────────────────────────────
def slugify(text: str, max_len: int = 60) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:max_len].strip("-") or "proposal"


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(phrase.lower()) + r"(?!\w)", text) is not None


def validate_proposals(raw, ledger: dict, prior: list[dict]) -> list[dict]:
    """Coerce the LLM JSON into clean proposal dicts; drop malformed, duplicate,
    and dead-end entries. Hard-capped at MAX_PROPOSALS."""
    if isinstance(raw, dict):
        items = raw.get("proposals") or raw.get("items") or []
    elif isinstance(raw, list):
        items = raw
    else:
        items = []
    if isinstance(items, dict):
        items = [items]

    forbidden = list(_DEAD_END_PHRASES)
    for kind in ledger["kinds"]:
        forbidden.append(kind.replace("_", " ").lower())
    prior_slugs = {slugify(str(p.get("title") or "")) for p in prior}
    prior_slugs |= {str(p.get("id") or "") for p in prior}

    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "").strip()
        summary = str(it.get("summary") or "").strip()
        mechanism = str(it.get("mechanism") or "").strip()
        kill = str(it.get("kill_criterion") or "").strip()
        if not (title and summary and mechanism and kill):
            print(f"  [DROP] malformed proposal (missing title/summary/mechanism/kill_criterion): {title!r}")
            continue

        configs = it.get("suggested_configs")
        if not isinstance(configs, list):
            configs = [configs] if configs else []
        configs = configs[:MAX_CONFIGS]
        links = it.get("evidence_links")
        if not isinstance(links, list):
            links = [links] if links else []

        haystack = f"{title} {summary} {mechanism}".lower()
        dupe = next((ph for ph in forbidden if len(ph) >= 3 and _contains_phrase(haystack, ph)), None)
        if dupe:
            print(f"  [DROP] duplicates dead-end/ledger kind ({dupe!r}): {title!r}")
            continue
        if slugify(title) in prior_slugs:
            print(f"  [DROP] already in review queue: {title!r}")
            continue

        out.append({
            "title": title, "summary": summary, "mechanism": mechanism,
            "suggested_configs": configs, "kill_criterion": kill,
            "evidence_links": links,
        })
        if len(out) >= MAX_PROPOSALS:
            break
    return out


# ── Supabase write ────────────────────────────────────────────────────────────
def upsert_proposals(rows: list[dict], headers: dict, table_exists: bool) -> bool:
    if not table_exists:
        print("  [WARN] apex_research_proposals table not found — skipping write.")
        print("         Paste supabase/apex_research_proposals.sql into the Supabase "
              "SQL editor, then re-run.")
        return False
    h = dict(headers)
    h["Prefer"] = "resolution=merge-duplicates,return=representation"
    try:
        r = httpx.post(TABLE_ENDPOINT, json=rows, headers=h, timeout=30)
        if r.status_code in (200, 201):
            print(f"  [OK] upserted {len(rows)} proposal(s) into apex_research_proposals.")
            return True
        if r.status_code == 404 or "42P01" in r.text:
            print("  [WARN] table missing (404/42P01) — paste "
                  "supabase/apex_research_proposals.sql into the Supabase SQL editor, then re-run.")
            return False
        print(f"  [ERROR] upsert failed: HTTP {r.status_code}: {r.text[:300]}")
        return False
    except Exception as e:
        print(f"  [ERROR] upsert exception: {type(e).__name__}: {e}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="Draft new experiment proposals into the review queue.")
    ap.add_argument("--dry-run", action="store_true",
                    help="print proposals without writing to Supabase")
    args = ap.parse_args()

    print("=" * 72)
    print(f"AUTO-RESEARCHER (Part 2 — proposal drafter)  {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")
    print("DRAFTS ONLY — nothing here runs experiments or trades.")
    print("=" * 72)

    if not GROQ_KEY:
        print("[ERROR] GROQ_API_KEY not set (engine/.env locally, GitHub secret in CI).")
        return 1

    ledger = load_ledger_summary()
    print(f"[1/4] Ledger: {ledger['total']} trials across {len(ledger['kinds'])} kinds "
          f"(top: {', '.join(list(ledger['kinds'])[:5])})")

    gates = load_recent_gates()
    print(f"[2/4] Gates: {len(gates)} verdict blocks from the newest gate files")

    key = service_or_anon_key()
    headers = {"apikey": key, "Authorization": f"Bearer {key}",
               "Content-Type": "application/json"}
    prior, table_exists = fetch_prior_proposals(headers)
    print(f"[3/4] Review queue: {len(prior)} existing proposal(s)"
          + ("" if table_exists else " (table absent — tolerated)"))

    prompt, system = build_prompt(ledger, gates, [str(p.get("title") or "") for p in prior])
    print(f"[4/4] Calling Groq ({GROQ_MODEL}) — prompt {len(prompt)} chars...")
    resp = groq_propose(prompt, system)
    if not resp:
        print("[ERROR] Groq call failed — no proposals drafted this run.")
        return 1

    raw = extract_json(resp)
    proposals = validate_proposals(raw, ledger, prior)
    if not proposals:
        print("[WARN] 0 usable proposals after validation/dedupe (all dropped or malformed).")
        return 0

    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = [{
        "id": f"{slugify(p['title'])}-{run_date}",
        "title": p["title"],
        "summary": p["summary"],
        "mechanism": p["mechanism"],
        "suggested_configs": p["suggested_configs"],
        "kill_criterion": p["kill_criterion"],
        "evidence_links": p["evidence_links"],
        "status": "draft",
        "source": "auto-researcher",
    } for p in proposals]

    print("\n--- DRAFT PROPOSALS (human review required — nothing auto-runs) ---")
    print(json.dumps(rows, indent=2))

    if args.dry_run:
        print(f"\n[DRY-RUN] {len(rows)} proposal(s) drafted — nothing written to Supabase.")
        return 0

    ok = upsert_proposals(rows, headers, table_exists)
    if not ok and table_exists:
        return 1
    print("Done. Proposals are DRAFTS — review them on the site's Progress tab.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
