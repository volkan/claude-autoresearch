---
name: autoresearch
description: Set up and run an autonomous experiment loop for any optimization target. Gathers what to optimize, writes benchmark scripts, then loops autonomously — edit code, run benchmark, keep improvements, discard regressions, never stop. Use when asked to "run autoresearch", "optimize X in a loop", "set up autoresearch", "start experiments", or "benchmark optimization".
allowed-tools: Bash(*), Read, Write, Edit, Glob, Grep
argument-hint: "[optimization goal]"
---

# Autoresearch

Autonomous experiment loop: try ideas, keep what works, discard what doesn't, never stop.

This skill uses bundled shell scripts at `${CLAUDE_SKILL_DIR}/scripts/` to handle experiment timing, logging, git operations, and state tracking. All state is persisted in `autoresearch.jsonl` (append-only JSONL).

## Setup

### 1. Gather Requirements

Ask the user (or infer from context):
- **Goal**: What are we optimizing? (e.g. "test speed", "bundle size", "training loss")
- **Command**: What runs the benchmark? (e.g. `pnpm test`, `./build.sh`)
- **Metric**: Primary metric name, unit, and direction (lower/higher is better)
- **Files in scope**: Which files may be modified
- **Constraints**: Hard rules (tests must pass, no new deps, etc.)

If the user passes `$ARGUMENTS`, use it as the goal and infer the rest from context.

### 2. Create Branch

```bash
git checkout -b autoresearch/<goal-slug>-$(date +%Y-%m-%d)
```

### 3. Read Source Files

Understand the workload deeply before writing anything. Read every file in scope.

### 4. Create Session Files

**autoresearch.md** — The heart of the session. See [templates](references/templates.md) for the full template. A fresh agent with no context should be able to read this file and run the loop.

**autoresearch.sh** — Bash benchmark script (`set -euo pipefail`) that outputs `METRIC name=number` lines. Keep it fast — every second is multiplied by hundreds of runs.

**autoresearch.checks.sh** (optional) — Only create when constraints require correctness validation (tests, types, lint). Runs after every passing benchmark. Failures block `keep`.

### 5. Initialize Experiment

```bash
bash ${CLAUDE_SKILL_DIR}/scripts/init_experiment.sh \
  "<session name>" "<metric_name>" "<metric_unit>" "<lower|higher>" \
  "$(pwd)/autoresearch.jsonl"
```

### 6. Commit Setup & Run Baseline

```bash
git add autoresearch.md autoresearch.sh autoresearch.checks.sh autoresearch.config.json 2>/dev/null; git add autoresearch.jsonl
git commit -m "autoresearch: setup session files"
```

Then immediately run the baseline and start looping.

## The Experiment Loop

### Step 1: Make a Change

Edit files in scope. Focus on one idea at a time.

### Step 2: Run Experiment

```bash
bash ${CLAUDE_SKILL_DIR}/scripts/run_experiment.sh \
  "./autoresearch.sh" 600 "$(pwd)"
```

Parse the output for:
- `EXIT_CODE=N` — 0 means passed, non-zero means crashed
- `DURATION=N.NNN` — wall-clock seconds
- `METRIC name=value` — extract the primary metric value
- `TIMED_OUT=true/false`
- `CHECKS_EXIT=N` — 0 means passed, non-zero means checks failed, `skipped` means no checks file

### Step 3: Determine Status

| Condition | Status |
|-----------|--------|
| EXIT_CODE != 0 or TIMED_OUT | `crash` |
| CHECKS_EXIT != 0 and != skipped | `checks_failed` |
| Primary metric improved | `keep` |
| Primary metric worse or equal | `discard` |

"Improved" means: lower value when direction=lower, higher value when direction=higher. Compare against the baseline (first run in current segment).

### Step 4: Log Result

```bash
COMMIT=$(git rev-parse --short=7 HEAD)
bash ${CLAUDE_SKILL_DIR}/scripts/log_experiment.sh \
  "$(pwd)/autoresearch.jsonl" <run_number> "$COMMIT" <metric_value> \
  "<status>" "<description>" 0 "$(pwd)" '{"secondary_metric": value}'
```

This automatically:
- Appends the result to `autoresearch.jsonl`
- On `keep`: runs `git add -A && git commit`
- On `discard`/`crash`/`checks_failed`: reverts changes but preserves autoresearch files

### Step 5: Show Dashboard

```bash
bash ${CLAUDE_SKILL_DIR}/scripts/show_dashboard.sh "$(pwd)/autoresearch.jsonl"
```

### Step 6: Repeat

Go back to Step 1. **NEVER STOP.**

## Loop Rules

**LOOP FOREVER.** Never ask "should I continue?" — the user expects autonomous work.

- **Primary metric is king.** Improved → `keep`. Worse/equal → `discard`. Secondary metrics rarely affect this decision.
- **Simpler is better.** Removing code for equal perf = keep. Ugly complexity for tiny gain = probably discard.
- **Don't thrash.** Repeatedly reverting the same idea? Try something structurally different.
- **Crashes:** fix if trivial, otherwise log and move on. Don't over-invest.
- **Think longer when stuck.** Re-read source files, study profiling data, reason about what the CPU is actually doing. The best ideas come from deep understanding, not random variations.
- **Be careful not to overfit to the benchmarks and do not cheat on the benchmarks.**

**NEVER STOP.** The user may be away for hours. Keep going until interrupted.

## Resume Protocol

If `autoresearch.jsonl` already exists when invoked:

1. Reconstruct state:
   ```bash
   bash ${CLAUDE_SKILL_DIR}/scripts/reconstruct_state.sh "$(pwd)/autoresearch.jsonl"
   ```
2. Read `autoresearch.md` for full context on what has been tried
3. Check `autoresearch.ideas.md` if it exists — prune stale entries, experiment with promising ones
4. Run `git log --oneline -20` for recent commit history
5. Show dashboard, then continue the loop from where it left off

## Ideas Backlog

When you discover complex but promising optimizations you won't pursue right now, append them as bullets to `autoresearch.ideas.md`. Don't let good ideas get lost.

On resume, check the ideas file — prune stale/tried entries, experiment with the rest. When all paths are exhausted, delete the file and write a final summary.

## Updating autoresearch.md

Update `autoresearch.md` periodically — especially the "What's Been Tried" section — so resuming agents have full context. Include key wins, dead ends, and architectural insights.

## User Messages During Experiments

If the user sends a message while an experiment is running, finish the current run + log cycle first, then incorporate their feedback in the next iteration.
