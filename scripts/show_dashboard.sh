#!/bin/bash
set -euo pipefail

# show_dashboard.sh — Print ASCII experiment dashboard from autoresearch.jsonl
# Usage: show_dashboard.sh <jsonl_path>

JSONL_PATH="${1:?Usage: show_dashboard.sh <jsonl_path>}"

if [[ ! -f "$JSONL_PATH" ]]; then
  echo "No experiments yet (autoresearch.jsonl not found)"
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
            entry["segment"] = entry.get("segment", segment)
            results.append(entry)
        except json.JSONDecodeError:
            continue

if not results:
    print("No experiments logged yet.")
    raise SystemExit(0)

name = config.get("name", "autoresearch")
metric_name = config.get("metricName", "metric")
metric_unit = config.get("metricUnit", "")
direction = config.get("bestDirection", "lower")

# Current segment results
current_segment = max(r.get("segment", 0) for r in results)
cur = [r for r in results if r.get("segment", 0) == current_segment]

total = len(cur)
kept = sum(1 for r in cur if r["status"] == "keep")
discarded = sum(1 for r in cur if r["status"] == "discard")
crashed = sum(1 for r in cur if r["status"] == "crash")
checks_failed = sum(1 for r in cur if r["status"] == "checks_failed")

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

def fmt(v):
    if v is None:
        return "—"
    if v == int(v):
        return f"{int(v)}{metric_unit}"
    return f"{v:.3f}{metric_unit}"

def delta_pct(val, base):
    if base is None or base == 0 or val is None:
        return "—"
    pct = ((val - base) / base) * 100
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.1f}%"

# Header
print(f"\n{'=' * 72}")
print(f"  Autoresearch: {name}")
print(f"{'=' * 72}")

# Summary
parts = [f"Runs: {total}", f"{kept} kept"]
if discarded: parts.append(f"{discarded} discarded")
if crashed: parts.append(f"{crashed} crashed")
if checks_failed: parts.append(f"{checks_failed} checks_failed")
print(f"  {' | '.join(parts)}")
print(f"  Baseline: {fmt(baseline)} | Best: {fmt(best)} #{best_run} ({delta_pct(best, baseline)})")
print()

# Table
hdr = f"  {'#':>3}  {'commit':<9} {'* ' + metric_name:<14} {'delta':>8}  {'status':<15} {'description'}"
print(hdr)
print(f"  {'─' * 68}")

# Truncate to last 10 runs for context savings (keep first run as baseline reference)
if len(cur) > 10:
    display = [cur[0]] + cur[-9:]
    display_offset = True
else:
    display = cur
    display_offset = False

for j, r in enumerate(display):
    if display_offset:
        idx = 1 if j == 0 else (len(cur) - 9 + j)
    else:
        idx = j + 1

    # Print separator after baseline row when truncated
    if display_offset and j == 1:
        print(f"  {'':>3}  {'...':^9} {'':14} {'':>8}  {'':15} (runs 2-{len(cur)-9} omitted)")

    commit = r.get("commit", "?")[:7]
    metric = fmt(r["metric"])
    d = delta_pct(r["metric"], baseline) if idx > 1 else "baseline"
    status = r["status"]

    # Status indicators
    if status == "keep":
        st = "✓ keep"
    elif status == "discard":
        st = "– discard"
    elif status == "crash":
        st = "✗ crash"
    elif status == "checks_failed":
        st = "⚠ chk_fail"
    else:
        st = status

    desc = r.get("description", "")[:30]
    print(f"  {idx:>3}  {commit:<9} {metric:<14} {d:>8}  {st:<15} {desc}")

print(f"  {'─' * 68}")

# Context management: only show last 10 runs in detail when table is large
if total > 10:
    print(f"  (showing last 10 of {total} runs — older runs omitted to save context)")

print()

# Loop continuation reminder — survives context compression
print("  ╔══════════════════════════════════════════════════════════════════╗")
print("  ║  CONTINUE: Go to Step 1. Make another change. Do NOT stop.     ║")
print("  ║  Re-read autoresearch.md if run# is a multiple of 5.           ║")
print("  ╚══════════════════════════════════════════════════════════════════╝")
print()
PYEOF
