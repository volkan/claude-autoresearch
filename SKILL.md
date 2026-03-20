---
name: autoresearch
description: Set up and run an autonomous experiment loop for any optimization target. Gathers what to optimize, writes benchmark scripts, then loops autonomously — edit code, run benchmark, keep improvements, discard regressions, never stop. Use when asked to "run autoresearch", "optimize X in a loop", "set up autoresearch", "start experiments", or "benchmark optimization".
allowed-tools: Bash(*), Read, Write, Edit, Glob, Grep
argument-hint: "[optimization goal]"
---

# autoresearch

This is an experiment loop. You are an autonomous researcher. You try ideas, measure them, keep what works, discard what doesn't, and never stop.

## Setup

To set up a new experiment, work with the user to:

1. **Agree on the goal**: What are we optimizing? What's the metric, and which direction is better (lower/higher)? What command runs the benchmark? What files are in scope? What are the constraints?
2. **Create the branch**: `git checkout -b autoresearch/<goal>-$(date +%Y-%m-%d)` from current main.
3. **Read the in-scope files**: Read every file you're allowed to modify, deeply, before writing anything. Understand the codebase.
4. **Write the session doc**: Create `autoresearch.md` (see [template](references/templates.md)) — a fresh agent with no context should be able to read this file and continue the loop. Also create `autoresearch.sh` (benchmark script that outputs `METRIC name=value` lines).
5. **Optionally write checks**: If correctness constraints exist (tests must pass, types must check), create `autoresearch.checks.sh`.
6. **Confirm and go**: Present the setup to the user and get confirmation.

Once you get confirmation, initialize tracking and establish the baseline:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/cli.py init \
  "<name>" "<metric_name>" "<unit>" "<lower|higher>" "$(pwd)/autoresearch.jsonl"
python3 ${CLAUDE_SKILL_DIR}/scripts/cli.py baseline \
  "$(pwd)/autoresearch.jsonl" "./autoresearch.sh" "$(pwd)" 3 600
```

The baseline command runs the benchmark 3 times, computes variance, and reports the significance threshold. Present results to user, get confirmation, commit setup files, then start looping.

## The experiment loop

LOOP FOREVER:

1. Edit files in scope with an experimental idea. One idea at a time.
2. Run the experiment:
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/cli.py run "./autoresearch.sh" 600 "$(pwd)"
   ```
3. Parse the output: `EXIT_CODE`, `DURATION`, `METRIC name=value`, `TIMED_OUT`, `CHECKS_EXIT`.
4. Determine the status:
   - If EXIT_CODE != 0 or TIMED_OUT → `crash`
   - If CHECKS_EXIT != 0 and != skipped → `checks_failed`
   - If metric improved beyond the significance threshold → `keep`
   - Otherwise (worse, equal, or within noise) → `discard`
5. Log the result:
   ```bash
   COMMIT=$(git rev-parse --short=7 HEAD)
   python3 ${CLAUDE_SKILL_DIR}/scripts/cli.py log \
     "$(pwd)/autoresearch.jsonl" <run_number> "$COMMIT" <metric> \
     "<status>" "<description>" 0 "$(pwd)" '{"secondary": value}' "<strategy>"
   ```
   This auto-commits on keep, auto-reverts on discard/crash/checks_failed (preserving autoresearch files), and prints the dashboard.
6. Read the dashboard. It tells you the current state and what to do next. Then go to step 1.

Tag each experiment with a strategy: `algorithm`, `caching`, `parallelism`, `io`, `removal`, `restructure`, `batching`.

## Tools

All operations go through `${CLAUDE_SKILL_DIR}/scripts/cli.py`:

- `init` — configure session
- `run` — run benchmark with timeout, auto-run checks if present
- `baseline` — run N baselines, compute variance and significance threshold
- `log` — record result, auto-commit/revert, print dashboard
- `state` — current experiment state as JSON
- `dashboard` — ASCII dashboard
- `analyze` — strategy effectiveness analysis with recommendations
- `history` — full run history
- `recover` — diagnose and fix inconsistent state

## What makes a good experiment

**The goal is simple: improve the primary metric.** Everything is fair game within the files in scope.

**Simplicity criterion**: All else being equal, simpler is better. A small improvement that adds ugly complexity is not worth it. Conversely, removing something and getting equal or better results is a great outcome — that's a simplification win. When evaluating whether to keep a change, weigh the complexity cost against the improvement magnitude.

**Do not cheat.** The workload must remain functionally identical before and after your change. If the benchmark does less work, that's not optimization — it's removal. If `autoresearch.checks.sh` exists, it enforces this automatically.

**Strategy**: Start bold. Algorithm replacement, removing unnecessary work, caching, batching — 10x levers before 10%. If the codebase is already optimized, profile first and target the actual bottleneck. Avoid dependency upgrades early (breaking-change risk).

**Crashes**: Use your judgment. If it's something dumb and easy to fix (a typo, a missing import), fix it and re-run. If the idea itself is fundamentally broken, just log it as a crash and move on.

## NEVER STOP

Once the experiment loop has begun (after the initial setup), do NOT pause to ask the human if you should continue. Do NOT ask "should I keep going?" or "is this a good stopping point?". Do NOT produce a summary — that is a stop signal. The human might be asleep, or gone from a computer and expects you to continue working *indefinitely* until you are manually stopped. You are autonomous.

If you run out of ideas, think harder — re-read the source files, re-read `autoresearch.md`, run `cli.py analyze` to see what strategies work, try combining previous near-misses, try more radical changes. The loop runs until the human interrupts you, period.

Every 5 runs, re-read `autoresearch.md` and run `analyze`. Update the "What's Been Tried" section so a future agent can pick up where you left off.

If you hit 4+ consecutive non-keeps (7+ for high-variance metrics), escalate: run `analyze`, re-read all source files from scratch (your mental model is stale), stop dead strategies (0 wins after 3+ attempts), and try something structurally different. Then continue looping.

## Resume protocol

If `autoresearch.jsonl` already exists, you're resuming a previous session:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/cli.py recover "$(pwd)/autoresearch.jsonl" "$(pwd)"
python3 ${CLAUDE_SKILL_DIR}/scripts/cli.py state "$(pwd)/autoresearch.jsonl"
python3 ${CLAUDE_SKILL_DIR}/scripts/cli.py analyze "$(pwd)/autoresearch.jsonl"
```

Read `autoresearch.md`, check `autoresearch.ideas.md`, `git log --oneline -20`, then immediately continue looping. Do not ask the user if they want to continue — they invoked autoresearch, they want you to loop.

## Ideas backlog

Append promising ideas to `autoresearch.ideas.md`. On resume, prune stale entries and experiment with the rest.
