#!/usr/bin/env bash
# offload cloud worker: one Claude Code cloud session = one render machine (compute/backends/claude_cloud.py starts it).
# Usage (the session's only command):  OFFLOAD_KEY=<job key> bash offload/cloud_worker.sh <job id> <machine>
# Runs this machine's pieces with runner.py and pushes ONLY the encrypted result as a parentless commit to
# refs/heads/offload-out/<job>-m<machine>. Nothing else in this repo is committed or pushed.
set -u
JOB="$1"; M="$2"; REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
URL="https://github.com/Alma806-mvk/render-farm/releases/download/offload-store/jobc-${JOB}.ofl"   # locked with the per-job key
python3 -m pip install -q --disable-pip-version-check cryptography >/dev/null 2>&1
python3 "$REPO_DIR/offload/runner.py" --manifest-url "$URL" --machine "$M" --root /tmp/offload_w --out /tmp/offload_out --encrypt-result
code=$?
cd "$REPO_DIR"
BLOB=$(git hash-object -w /tmp/offload_out/result.ofl) || { echo "no result file"; exit 1; }
TREE=$(printf '100644 blob %s\tresult.ofl\n' "$BLOB" | git mktree)
COMMIT=$(git -c user.name=offload -c user.email=offload@users.noreply.github.com commit-tree "$TREE" -m "offload result ${JOB} m${M}")
for k in 1 2 3 4 5; do git push -q origin "$COMMIT:refs/heads/offload-out/${JOB}-m${M}" && break; sleep $((k * 5)); done
echo "OFFLOAD WORKER DONE job=${JOB} machine=${M} runner_exit=${code}"
