#!/usr/bin/env python3
"""Replay local commits onto GitHub via the Git Data REST API.

Fallback for when `git push` dies with "remote unpack failed" (the network
corrupts large packs AND tls streams). Replays each local commit in
origin/main..HEAD: upload changed blobs (base64) -> create tree (base_tree
overlay) -> create commit with original author/committer -> fast-forward
refs/heads/main at the end.

Transport is curl (urllib's TLS stack was eating bad_record_mac alerts on
this network). Resumable via a state file: re-run after a failure and it
picks up at the commit after the last one it created remotely.

Stdlib only. Token from env GH_TOKEN (`gh auth token`).
"""
from __future__ import annotations

import base64
import json
import os
import random
import subprocess
import sys
import tempfile
import time

REPO = "zainm01800/apexfx"
API = f"https://api.github.com/repos/{REPO}"
TOKEN = os.environ["GH_TOKEN"]
STATE_FILE = os.path.join(os.path.dirname(__file__), "push_via_rest.state.json")
MAX_TRIES = 14


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def curl_api(method: str, path: str, payload: dict | None) -> dict:
    """GitHub API call via curl with manual retry on transport errors / 5xx."""
    last = "?"
    for attempt in range(1, MAX_TRIES + 1):
        body_file = None
        cmd = ["curl", "-sS", "--http1.1", "--connect-timeout", "20",
               "--max-time", "180", "-X", method,
               "-H", f"Authorization: Bearer {TOKEN}",
               "-H", "Accept: application/vnd.github+json",
               "-H", "X-GitHub-Api-Version: 2022-11-28",
               "-w", "\n%{http_code}", f"{API}{path}"]
        if payload is not None:
            body_file = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
            json.dump(payload, body_file)
            body_file.close()
            cmd += ["-H", "Content-Type: application/json", "--data-binary",
                    f"@{body_file.name}"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=200)
        except subprocess.TimeoutExpired:
            r = None
            code, out, err = 0, "", "curl timeout"
        finally:
            if body_file:
                os.unlink(body_file.name)
        if r is not None:
            out, _, code_s = r.stdout.rpartition("\n")
            code = int(code_s) if code_s.isdigit() else 0
            err = r.stderr.strip()
        if code and 200 <= code < 300:
            return json.loads(out) if out else {}
        last = f"HTTP {code} {out[:250]} {err[:150]}"
        if code in (400, 401, 403, 404, 410, 451):
            raise RuntimeError(f"{method} {path} -> {last} (fatal)")
        wait = min(2 ** attempt, 45) * (0.5 + random.random())
        print(f"    retry {attempt}/{MAX_TRIES} {method} {path}: {last[:160]} "
              f"(wait {wait:.0f}s)", flush=True)
        time.sleep(wait)
    raise RuntimeError(f"{method} {path} failed after {MAX_TRIES} tries: {last}")


def upload_blob(sha: str) -> None:
    raw = subprocess.run(["git", "cat-file", "blob", sha], capture_output=True,
                         check=True).stdout
    b64 = base64.b64encode(raw).decode()
    for attempt in range(1, 6):
        res = curl_api("POST", "/git/blobs", {"content": b64, "encoding": "base64"})
        if res.get("sha") == sha:
            return
        print(f"    blob sha mismatch ({res.get('sha')} != {sha}), re-uploading", flush=True)
        time.sleep(3)
    raise RuntimeError(f"blob {sha} ({len(raw)} bytes) would not upload intact")


def commit_diff_entries(c: str) -> tuple[list[str], list[dict]]:
    out = git("diff-tree", "-r", "--no-commit-id", c)
    blobs, entries = [], []
    for line in out.splitlines():
        meta, path = line.split("\t", 1)
        parts = meta.split()
        old_mode, new_mode, old_sha, new_sha, status = parts[0], parts[1], parts[2], parts[3], parts[4]
        typ = "commit" if "160000" in (old_mode, new_mode) else "blob"
        if status.startswith("D"):
            entries.append({"path": path, "mode": old_mode, "type": typ, "sha": None})
        else:
            blobs.append(new_sha)
            entries.append({"path": path, "mode": new_mode, "type": typ, "sha": new_sha})
    return blobs, entries


def load_state() -> dict:
    try:
        return json.load(open(STATE_FILE))
    except Exception:
        return {"done": {}}  # local commit sha -> remote commit sha


def save_state(st: dict) -> None:
    with open(STATE_FILE, "w") as fh:
        json.dump(st, fh, indent=1)


def main() -> int:
    ref = curl_api("GET", "/git/refs/heads/main", None)
    remote_sha = ref["object"]["sha"]
    base = git("rev-parse", "origin/main")
    st = load_state()
    done: dict = st["done"]

    commits = git("rev-list", "--reverse", "origin/main..HEAD").split()
    # resume: the remote tip must be origin/main or the remote sha of a done commit
    idx = 0
    if remote_sha != base:
        matches = [i for i, c in enumerate(commits) if done.get(c) == remote_sha]
        if not matches:
            raise SystemExit(f"remote main moved to unknown sha {remote_sha} — refusing")
        idx = matches[0] + 1
        print(f"resuming after commit {idx}/{len(commits)}", flush=True)
    parent = remote_sha
    print(f"replaying commits {idx + 1}..{len(commits)} of {len(commits)} onto "
          f"remote main ({remote_sha[:8]})", flush=True)

    for i in range(idx, len(commits)):
        c = commits[i]
        subject = git("log", "-1", "--format=%s", c)
        local_tree = git("rev-parse", f"{c}^{{tree}}")
        blobs, entries = commit_diff_entries(c)
        for j, b in enumerate(sorted(set(blobs)), 1):
            print(f"    blob {j}/{len(set(blobs))} {b[:8]}", flush=True)
            upload_blob(b)
        base_tree = git("rev-parse", f"{c}^1^{{tree}}")
        tree = curl_api("POST", "/git/trees", {"base_tree": base_tree, "tree": entries})
        if tree["sha"] != local_tree:
            raise RuntimeError(
                f"tree mismatch for {c[:8]}: remote {tree['sha']} != local {local_tree}. "
                "Refusing to continue — history would diverge.")
        an, ae, ad = git("log", "-1", "--format=%an|%ae|%aI", c).split("|")
        cn, ce, cd = git("log", "-1", "--format=%cn|%ce|%cI", c).split("|")
        new_commit = curl_api("POST", "/git/commits", {
            "message": git("log", "-1", "--format=%B", c),
            "tree": tree["sha"],
            "parents": [parent],
            "author": {"name": an, "email": ae, "date": ad},
            "committer": {"name": cn, "email": ce, "date": cd},
        })
        parent = new_commit["sha"]
        done[c] = parent
        save_state(st)
        print(f"  [{i + 1}/{len(commits)}] {c[:8]} -> {parent[:8]}  {subject[:70]}", flush=True)

    curl_api("PATCH", "/git/refs/heads/main", {"sha": parent, "force": False})
    print(f"DONE: remote main is now {parent}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
