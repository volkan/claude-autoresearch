#!/bin/bash
set -euo pipefail

# log_experiment.sh — Record experiment result, handle git commit/revert
# Usage: log_experiment.sh <jsonl_path> <run_num> <commit> <metric> <status> <description> <segment> <work_dir> [metrics_json]
# - status: keep | discard | crash | checks_failed
# - metrics_json: optional JSON object of secondary metrics e.g. '{"compile_ms":420}'

JSONL_PATH="${1:?Usage: log_experiment.sh <jsonl_path> <run> <commit> <metric> <status> <desc> <segment> <work_dir> [metrics_json]}"
RUN_NUM="${2:?Missing run_num}"
COMMIT="${3:?Missing commit}"
METRIC="${4:?Missing metric}"
STATUS="${5:?Missing status}"
DESCRIPTION="${6:?Missing description}"
SEGMENT="${7:-0}"
WORK_DIR="${8:-.}"
METRICS_JSON="${9:-\{\}}"

# Validate status
if [[ "$STATUS" != "keep" && "$STATUS" != "discard" && "$STATUS" != "crash" && "$STATUS" != "checks_failed" ]]; then
  echo "ERROR: status must be keep|discard|crash|checks_failed, got '$STATUS'" >&2
  exit 1
fi

# Append result to JSONL (pass description via env to avoid shell quoting issues)
AR_DESCRIPTION="$DESCRIPTION" AR_METRICS_JSON="$METRICS_JSON" python3 -c "
import json, os, time

description = os.environ['AR_DESCRIPTION']
metrics_json = os.environ.get('AR_METRICS_JSON', '{}')
try:
    metrics = json.loads(metrics_json)
except:
    metrics = {}

result = {
    'run': $RUN_NUM,
    'commit': '$COMMIT',
    'metric': $METRIC,
    'metrics': metrics,
    'status': '$STATUS',
    'description': description,
    'timestamp': int(time.time() * 1000),
    'segment': $SEGMENT
}
with open('$JSONL_PATH', 'a') as f:
    f.write(json.dumps(result, ensure_ascii=False, separators=(',',':')) + '\n')
"

echo "Logged #$RUN_NUM: $STATUS — $DESCRIPTION (metric=$METRIC)"

# Protected autoresearch files (never reverted)
PROTECTED_FILES=(
  "autoresearch.jsonl"
  "autoresearch.md"
  "autoresearch.ideas.md"
  "autoresearch.sh"
  "autoresearch.checks.sh"
  "autoresearch.config.json"
)

cd "$WORK_DIR"

if [[ "$STATUS" == "keep" ]]; then
  # Auto-commit on keep
  git add -A 2>/dev/null || true
  if ! git diff --cached --quiet 2>/dev/null; then
    git commit -m "$DESCRIPTION

Result: {\"status\":\"keep\",\"metric\":$METRIC}" 2>/dev/null || echo "WARNING: git commit failed"
    NEW_SHA=$(git rev-parse --short=7 HEAD 2>/dev/null || echo "$COMMIT")
    echo "Git: committed ($NEW_SHA)"
  else
    echo "Git: nothing to commit (working tree clean)"
  fi
else
  # Auto-revert on discard/crash/checks_failed — preserve autoresearch files
  BACKUP_DIR=$(mktemp -d)
  for f in "${PROTECTED_FILES[@]}"; do
    [[ -f "$f" ]] && cp "$f" "$BACKUP_DIR/$f" 2>/dev/null || true
  done

  git checkout -- . 2>/dev/null || true
  git clean -fd 2>/dev/null || true

  for f in "${PROTECTED_FILES[@]}"; do
    [[ -f "$BACKUP_DIR/$f" ]] && mv "$BACKUP_DIR/$f" "$f" 2>/dev/null || true
  done
  rm -rf "$BACKUP_DIR"

  echo "Git: reverted changes ($STATUS) — autoresearch files preserved"
fi
