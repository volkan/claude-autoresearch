#!/bin/bash
set -euo pipefail

# init_experiment.sh — Initialize autoresearch experiment session
# Usage: init_experiment.sh <name> <metric_name> <metric_unit> <direction> <jsonl_path>
# - name: Human-readable session name
# - metric_name: Primary metric display name (e.g. "duration", "bundle_kb")
# - metric_unit: Unit string (e.g. "s", "ms", "kb", "")
# - direction: "lower" or "higher" (which is better)
# - jsonl_path: Path to autoresearch.jsonl

NAME="${1:?Usage: init_experiment.sh <name> <metric_name> <metric_unit> <direction> <jsonl_path>}"
METRIC_NAME="${2:?Missing metric_name}"
METRIC_UNIT="${3:-}"
DIRECTION="${4:-lower}"
JSONL_PATH="${5:?Missing jsonl_path}"

# Validate direction
if [[ "$DIRECTION" != "lower" && "$DIRECTION" != "higher" ]]; then
  echo "ERROR: direction must be 'lower' or 'higher', got '$DIRECTION'" >&2
  exit 1
fi

# Build config header JSON
CONFIG_LINE=$(python3 -c "
import json
print(json.dumps({
    'type': 'config',
    'name': '$NAME',
    'metricName': '$METRIC_NAME',
    'metricUnit': '$METRIC_UNIT',
    'bestDirection': '$DIRECTION'
}, ensure_ascii=False, separators=(',',':')))
")

# If JSONL already exists with content, append (re-init = new segment)
if [[ -f "$JSONL_PATH" ]] && [[ -s "$JSONL_PATH" ]]; then
  echo "$CONFIG_LINE" >> "$JSONL_PATH"
  echo "Re-initialized experiment: \"$NAME\" (new segment appended)"
  echo "Metric: $METRIC_NAME ($METRIC_UNIT, $DIRECTION is better)"
  echo "Previous results archived. Run baseline for new segment."
else
  echo "$CONFIG_LINE" > "$JSONL_PATH"
  echo "Initialized experiment: \"$NAME\""
  echo "Metric: $METRIC_NAME ($METRIC_UNIT, $DIRECTION is better)"
  echo "Config written to $JSONL_PATH. Now run the baseline."
fi
