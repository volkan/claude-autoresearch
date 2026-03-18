#!/bin/bash
set -euo pipefail

# reconstruct_state.sh — Parse autoresearch.jsonl and output state summary
# Usage: reconstruct_state.sh <jsonl_path>
# Output: JSON summary of experiment state

JSONL_PATH="${1:?Usage: reconstruct_state.sh <jsonl_path>}"

if [[ ! -f "$JSONL_PATH" ]]; then
  echo '{"error":"autoresearch.jsonl not found","exists":false}'
  exit 0
fi

AR_JSONL_PATH="$JSONL_PATH" python3 << 'PYEOF'
import json, os

jsonl_path = os.environ["AR_JSONL_PATH"]

config = {}
results = []
segment = 0

with open(jsonl_path) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            if entry.get("type") == "config":
                config = entry
                if results:
                    segment += 1
                continue
            entry.setdefault("segment", segment)
            results.append(entry)
        except json.JSONDecodeError:
            continue

current_segment = max((r.get("segment", 0) for r in results), default=0) if results else 0
cur = [r for r in results if r.get("segment", 0) == current_segment]

direction = config.get("bestDirection", "lower")
baseline = cur[0]["metric"] if cur else None
best = None
best_run = 0

for i, r in enumerate(cur):
    if r["status"] == "keep" and r["metric"] > 0:
        if best is None:
            best = r["metric"]
            best_run = i + 1
        elif (direction == "lower" and r["metric"] < best) or \
             (direction == "higher" and r["metric"] > best):
            best = r["metric"]
            best_run = i + 1

state = {
    "exists": True,
    "name": config.get("name", ""),
    "metricName": config.get("metricName", "metric"),
    "metricUnit": config.get("metricUnit", ""),
    "bestDirection": direction,
    "currentSegment": current_segment,
    "totalRuns": len(cur),
    "allRuns": len(results),
    "kept": sum(1 for r in cur if r["status"] == "keep"),
    "discarded": sum(1 for r in cur if r["status"] == "discard"),
    "crashed": sum(1 for r in cur if r["status"] == "crash"),
    "checksFailed": sum(1 for r in cur if r["status"] == "checks_failed"),
    "baseline": baseline,
    "best": best,
    "bestRun": best_run,
    "lastDescription": cur[-1].get("description", "") if cur else "",
    "lastStatus": cur[-1].get("status", "") if cur else ""
}

print(json.dumps(state, indent=2))
PYEOF
