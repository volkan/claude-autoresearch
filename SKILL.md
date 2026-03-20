---
name: autoresearch
description: Set up and run an autonomous experiment loop for any optimization target. Gathers what to optimize, writes benchmark scripts, then loops autonomously — edit code, run benchmark, keep improvements, discard regressions, never stop. Use when asked to "run autoresearch", "optimize X in a loop", "set up autoresearch", "start experiments", or "benchmark optimization".
allowed-tools: Bash(*), Read, Write, Edit, Glob, Grep
argument-hint: "[optimization goal]"
---

# Autoresearch

Autonomous experiment loop: try ideas, keep what works, discard what doesn't, **never stop**.

## Core Invariant

**YOU MUST NEVER STOP THE LOOP.** Not after errors. Not after crashes. Not after consecutive discards. Not when context gets long. Not when you feel done. The only valid stop condition is the user pressing Ctrl+C. If you feel an urge to summarize, stop, or ask "should I continue?" — treat that as a bug in your reasoning and re-read `autoresearch.md` instead.

## Tools

All operations go through a single CLI at `${CLAUDE_SKILL_DIR}/scripts/cli.py`:

- **`init`** — configure session (name, metric, unit, direction). Call again to re-initialize with a new baseline.
- **`run`** — runs command, times it, captures output and METRIC lines. Runs `autoresearch.checks.sh` automatically if present.
- **`baseline`** — runs benchmark N times (default 3), computes variance, logs all baselines, reports significance threshold. Replaces manual 3-run + variance calculation.
- **`log`** — records result to JSONL. `keep` auto-commits. `discard`/`crash`/`checks_failed` auto-reverts (autoresearch files preserved). **Auto-prints dashboard after every log.**
- **`state`** — reconstructs current experiment state as JSON.
- **`dashboard`** — prints ASCII dashboard with strategy column (also shown automatically after `log`).
- **`analyze`** — strategy effectiveness analysis with recommendations.
- **`history`** — full experiment history dump (all runs, not truncated like dashboard).
- **`recover`** — diagnose and fix inconsistent state (corrupt JSONL, orphaned files, dirty git).

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
   # Run 3 baselines — computes variance and significance threshold automatically:
   python3 ${CLAUDE_SKILL_DIR}/scripts/cli.py baseline \
     "$(pwd)/autoresearch.jsonl" "./autoresearch.sh" "$(pwd)" 3 600
   ```
8. The `baseline` command reports variance and significance threshold. Present to user.
9. **Wait for user confirmation** to enter autonomous loop.
10. Commit setup files, then start looping.

## The Experiment Loop

After setup, execute this loop **forever** until the user interrupts:

### 1. Think Before You Act

Before making any change, **state your hypothesis in one line** (internally, don't print it). Why will this change improve the metric? What bottleneck does it target? If you can't articulate the hypothesis, you haven't thought enough — re-read source files.

### 2. Make a Change

Edit files in scope. One idea at a time. Tag your strategy: `algorithm`, `caching`, `parallelism`, `io`, `removal`, `restructure`, `batching`.

**Strategy playbook:**
- **Unoptimized codebase:** start bold — algorithm replacement, removing unnecessary work, caching, batching. 10× levers before 10%.
- **Already optimized:** profile first, target the actual bottleneck.
- **Stuck?** Read the source code again. Your cached understanding is wrong.
- Avoid dependency upgrades early — breaking-change risk.

### 3. Run Experiment

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/cli.py run "./autoresearch.sh" 600 "$(pwd)"
```

Parse output: `EXIT_CODE`, `DURATION`, `METRIC name=value`, `TIMED_OUT`, `CHECKS_EXIT`.

### 4. Determine Status

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

### 5. Log Result

```bash
COMMIT=$(git rev-parse --short=7 HEAD)
python3 ${CLAUDE_SKILL_DIR}/scripts/cli.py log \
  "$(pwd)/autoresearch.jsonl" <run_number> "$COMMIT" <metric> \
  "<status>" "<description>" 0 "$(pwd)" '{"secondary": value}' "<strategy>"
```

This auto-commits or auto-reverts, then **prints the dashboard**.

### 6. Repeat — go to Step 1. **NEVER STOP.**

## Loop Rules

**LOOP FOREVER.** Never ask "should I continue?" — the user expects autonomous work.

- Primary metric is king. Simpler is better. Don't thrash.
- **Be careful not to overfit to the benchmarks and do not cheat.**
- **NEVER produce a summary.** That is a stop signal. Re-read `autoresearch.md` instead.
- Crashes: fix if trivial, otherwise log and move on to the **next idea immediately**.
- **Every log prints the dashboard.** Read it. The dashboard tells you what to do next.

### Error Recovery (CRITICAL — never stop on errors)

Errors are not stop signals. They are data. Follow this protocol:

| Error | Recovery |
|-------|----------|
| Benchmark crashes | Log as `crash`. Fix if obvious (1 attempt). Otherwise move to next idea. |
| Checks fail | Log as `checks_failed`. Revert broke a constraint. Try a smaller change. |
| Git operation fails | Run `cli.py recover`. Follow its suggestions. Continue. |
| JSONL appears corrupt | Run `cli.py recover`. Corrupt lines are auto-skipped. Continue. |
| Metric not found in output | Check autoresearch.sh outputs the right METRIC line. Fix and re-run. |
| Timeout | Log as `crash`. The change made things slower. Move on. |
| Permission denied | Check file permissions. Fix and re-run. |
| **Any other error** | Log it, note it, **continue to the next iteration**. |

**The only valid reason to stop is the user pressing Ctrl+C.** Everything else has a recovery path.

### Escalation (consecutive discards)

- High-variance (≥5%): escalate after **7** consecutive discards.
- Low-variance (<5%): escalate after **4** consecutive discards.

When escalating:
1. Run `python3 ${CLAUDE_SKILL_DIR}/scripts/cli.py analyze "$(pwd)/autoresearch.jsonl"` — check win rates, dead strategies.
2. Re-read `autoresearch.md` and **all source files** from scratch. Your mental model is stale.
3. Stop dead strategies (0 wins after 3+ attempts). Try untried categories.
4. Try a **structurally different** approach. Not a variation — a fundamentally new angle.
5. If the analyze command recommends specific actions, follow them.
6. **Then continue looping.** Escalation is not a stop signal.

### Anti-Stall Protocol

Signs you're stalling (catch yourself):
- Making smaller and smaller changes
- Trying the same strategy with minor tweaks
- Spending more time reading output than making changes
- Thinking "I've tried everything"

When stalling:
1. Run `cli.py analyze` — let the data tell you what works
2. Re-read ALL source files (not just the ones you've been editing)
3. Look for 10× levers: different algorithms, removing entire subsystems, changing data representations
4. If a strategy has 0 wins after 3 attempts, it's dead. Stop.
5. **Continue looping with a new approach.**

### Context Management

- **Re-read `autoresearch.md` every 5 runs** and run `analyze`. Update "What's Been Tried" section.
- Keep outputs minimal. Don't accumulate explanations. No recaps.
- Never produce a final summary — that's the context compression talking.
- If context feels long, that's NORMAL. It means you're making progress. **Do not stop.**
- When context compresses, the dashboard has all the state you need. Read it and continue.

## Resume Protocol

If `autoresearch.jsonl` already exists:
```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/cli.py recover "$(pwd)/autoresearch.jsonl" "$(pwd)"
python3 ${CLAUDE_SKILL_DIR}/scripts/cli.py state "$(pwd)/autoresearch.jsonl"
python3 ${CLAUDE_SKILL_DIR}/scripts/cli.py analyze "$(pwd)/autoresearch.jsonl"
```
Read `autoresearch.md`, check `autoresearch.ideas.md`, `git log --oneline -20`, then **immediately continue looping**. Do not ask the user if they want to continue. They invoked autoresearch — they want you to loop.

## Ideas Backlog

Append promising ideas to `autoresearch.ideas.md`. On resume, prune stale entries and experiment with the rest.
