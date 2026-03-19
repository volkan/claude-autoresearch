---
name: autoresearch
description: Set up and run an autonomous experiment loop for any optimization target. Gathers what to optimize, writes benchmark scripts, then loops autonomously — edit code, run benchmark, keep improvements, discard regressions, never stop. Use when asked to "run autoresearch", "optimize X in a loop", "set up autoresearch", "start experiments", or "benchmark optimization".
allowed-tools: Bash(*), Read, Write, Edit, Glob, Grep
argument-hint: "[optimization goal]"
---

# Autoresearch

Autonomous experiment loop: try ideas, keep what works, discard what doesn't, never stop.

## Tools

All operations go through a single CLI at `${CLAUDE_SKILL_DIR}/scripts/cli.py`:

- **`init`** — configure session (name, metric, unit, direction). Call again to re-initialize with a new baseline.
- **`run`** — runs command, times it, captures output and METRIC lines. Runs `autoresearch.checks.sh` automatically if present.
- **`log`** — records result to JSONL. `keep` auto-commits. `discard`/`crash`/`checks_failed` auto-reverts (autoresearch files preserved). **Auto-prints dashboard after every log.**
- **`state`** — reconstructs current experiment state as JSON.
- **`dashboard`** — prints ASCII dashboard (also shown automatically after `log`).
- **`analyze`** — strategy effectiveness analysis with recommendations.

## Setup

1. Ask (or infer from `$ARGUMENTS`): **Goal**, **Command**, **Metric** (+ direction), **Files in scope**, **Constraints** (hard vs soft).
2. `git checkout -b autoresearch/<goal>-$(date +%Y-%m-%d)`
3. Read every source file in scope deeply before writing anything.
4. Write `autoresearch.md` (session doc — see [template](references/templates.md)) and `autoresearch.sh` (benchmark script).
5. Optionally write `autoresearch.checks.sh` for correctness validation (tests, types, lint).
6. **Wait for user confirmation** before proceeding.
7. Initialize and run baseline:
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/cli.py init \
     "<name>" "<metric_name>" "<unit>" "<lower|higher>" "$(pwd)/autoresearch.jsonl"
   # Run 3 baselines to measure variance:
   python3 ${CLAUDE_SKILL_DIR}/scripts/cli.py run "./autoresearch.sh" 600 "$(pwd)"
   ```
8. Compute variance = (max - min) / median. Significance threshold = 2× variance. Present to user.
9. **Wait for user confirmation** to enter autonomous loop.
10. Commit setup files, then start looping.

## The Experiment Loop

After setup, execute this loop **forever** until the user interrupts:

### 1. Make a Change

Edit files in scope. One idea at a time. Tag your strategy: `algorithm`, `caching`, `parallelism`, `io`, `removal`, `restructure`, `batching`.

**Strategy playbook:**
- **Unoptimized codebase:** start bold — algorithm replacement, removing unnecessary work, caching, batching. 10× levers before 10%.
- **Already optimized:** profile first, target the actual bottleneck.
- Avoid dependency upgrades early — breaking-change risk.

### 2. Run Experiment

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/cli.py run "./autoresearch.sh" 600 "$(pwd)"
```

Parse output: `EXIT_CODE`, `DURATION`, `METRIC name=value`, `TIMED_OUT`, `CHECKS_EXIT`.

### 3. Determine Status

| Condition | Status |
|-----------|--------|
| EXIT_CODE != 0 or TIMED_OUT | `crash` |
| CHECKS_EXIT != 0 and != skipped | `checks_failed` |
| Metric improved beyond significance threshold | `keep` |
| Worse, equal, or within noise | `discard` |

Compare against **best kept value** (or baseline). Variance-aware: changes within 2× baseline variance are noise → `discard`.

**Confidence score (advisory):** The dashboard shows statistical confidence
(improvement / noise floor). ≥2.0× = likely real. <1.0× = within noise,
re-run to confirm. This is advisory — does not change keep/discard.

### 4. Log Result

```bash
COMMIT=$(git rev-parse --short=7 HEAD)
python3 ${CLAUDE_SKILL_DIR}/scripts/cli.py log \
  "$(pwd)/autoresearch.jsonl" <run_number> "$COMMIT" <metric> \
  "<status>" "<description>" 0 "$(pwd)" '{"secondary": value}' "<strategy>"
```

This auto-commits or auto-reverts, then **prints the dashboard**.

### 5. Repeat — go to Step 1. **NEVER STOP.**

## Loop Rules

**LOOP FOREVER.** Never ask "should I continue?" — the user expects autonomous work.

- Primary metric is king. Simpler is better. Don't thrash.
- **Be careful not to overfit to the benchmarks and do not cheat.**
- **NEVER produce a summary.** That is a stop signal. Re-read `autoresearch.md` instead.
- Crashes: fix if trivial, otherwise log and move on.

### Escalation (consecutive discards)

- High-variance (≥5%): escalate after **7** consecutive discards.
- Low-variance (<5%): escalate after **4** consecutive discards.

When escalating:
1. Run `python3 ${CLAUDE_SKILL_DIR}/scripts/cli.py analyze "$(pwd)/autoresearch.jsonl"` — check win rates, dead strategies.
2. Re-read `autoresearch.md` and source files from scratch.
3. Stop dead strategies (0 wins after 3+ attempts). Try untried categories.
4. Try a **structurally different** approach.

### Context Management

- **Re-read `autoresearch.md` every 5 runs** and run `analyze`. Update "What's Been Tried" section.
- Keep outputs minimal. Don't accumulate explanations. No recaps.
- Never produce a final summary — that's the context compression talking.

## Resume Protocol

If `autoresearch.jsonl` already exists:
```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/cli.py state "$(pwd)/autoresearch.jsonl"
python3 ${CLAUDE_SKILL_DIR}/scripts/cli.py analyze "$(pwd)/autoresearch.jsonl"
```
Read `autoresearch.md`, check `autoresearch.ideas.md`, `git log --oneline -20`, then continue looping.

## Ideas Backlog

Append promising ideas to `autoresearch.ideas.md`. On resume, prune stale entries and experiment with the rest.
