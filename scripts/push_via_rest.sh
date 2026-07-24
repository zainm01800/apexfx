#!/usr/bin/env bash
# Push local commits one-by-one via the GitHub REST API (blobs -> tree -> commit
# -> PATCH ref). Works around this network corrupting big git packs ("remote
# unpack failed: index-pack failed"). After all commits are up, fetch + reset
# local main to origin/main (contents are byte-identical).
# Usage: scripts/push_via_rest.sh [START_SHA]   # pushes origin/main..HEAD (or START_SHA..HEAD)
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
TOKEN="$(gh auth token)"
REPO="zainm01800/apexfx"
START="${1:-$(git rev-parse origin/main)}"

api() {  # api METHOD PATH JSON  — retries on empty/non-JSON responses (flaky network)
  local attempt resp
  for attempt in 1 2 3 4 5; do
    resp=$(curl -s -X "$1" -H "Authorization: token $TOKEN" -H "Accept: application/vnd.github+json" \
      "https://api.github.com/repos/$REPO/$2" ${3:+-d "$3"})
    if [ -n "$resp" ] && printf '%s' "$resp" | python3 -c "import sys,json;json.load(sys.stdin)" 2>/dev/null; then
      printf '%s' "$resp"; return 0
    fi
    sleep $((attempt * 2))
  done
  echo "API call failed after retries: $1 $2" >&2; return 1
}

REMOTE_PARENT="$(git rev-parse origin/main)"
for COMMIT in $(git rev-list --reverse "$START"..HEAD); do
  PARENT="$REMOTE_PARENT"   # local parent shas do not exist on the remote; chain remote ones
  echo "== $COMMIT ($(git log -1 --format=%s "$COMMIT" | head -c 60)...)"
  ENTRIES="["
  FIRST=1
  while IFS= read -r path; do
    B64="$(git show "$COMMIT:$path" | base64 | tr -d '\n')"
    BSHA="$(api POST git/blobs "{\"content\":\"$B64\",\"encoding\":\"base64\"}" | python3 -c "import sys,json;print(json.load(sys.stdin)['sha'])")"
    [ $FIRST -eq 0 ] && ENTRIES+=","
    ENTRIES+="{\"path\":\"$path\",\"mode\":\"100644\",\"type\":\"blob\",\"sha\":\"$BSHA\"}"
    FIRST=0
    echo "   blob $path $BSHA"
  done < <(git diff-tree --no-commit-id --name-only -r "$COMMIT")
  ENTRIES+="]"
  TSHA="$(api POST git/trees "{\"base_tree\":\"$PARENT\",\"tree\":$ENTRIES}" | python3 -c "import sys,json;print(json.load(sys.stdin)['sha'])")"
  MSG="$(git log -1 --format=%B "$COMMIT" | python3 -c "import sys,json;print(json.dumps(sys.stdin.read()))")"
  CSHA="$(api POST git/commits "{\"message\":$MSG,\"tree\":\"$TSHA\",\"parents\":[\"$PARENT\"]}" | python3 -c "import sys,json;print(json.load(sys.stdin)['sha'])")"
  echo "   commit $CSHA"
  api PATCH git/refs/heads/main "{\"sha\":\"$CSHA\",\"force\":false}" | python3 -c "import sys,json;d=json.load(sys.stdin);print('   ref ->',d['object']['sha'])"
  REMOTE_PARENT="$CSHA"
done
git fetch origin -q
if [ -n "$(git diff HEAD origin/main --stat)" ]; then
  echo "WARNING: content differs from origin/main after REST push"; git diff HEAD origin/main --stat | tail -5; exit 1
fi
# Pointer-only alignment: local and remote commits are siblings with identical
# trees, so neither reset --hard (destroys uncommitted tracked files — it once
# reverted the working-tree trial ledger) nor merge --ff-only (refuses sibling
# branches) can be used. update-ref moves the branch ref and touches nothing else.
git update-ref refs/heads/main origin/main
echo "OK: local main now at $(git rev-parse --short HEAD) == origin/main"
