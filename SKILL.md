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

Setup has three phases. **Do not skip the confirmation checkpoints.**

### Phase 1: Discovery (read-only, no codebase changes)

#### 1. Parse the Goal

If the user passes `$ARGUMENTS`, use it as the goal. Otherwise ask.

#### 2. Research the Codebase

Understand the workload deeply before proposing anything:
- Read files in scope and related files
- Find existing test/build/benchmark commands (grep for scripts, package.json, Makefile, etc.)
- Run the candidate benchmark command once to see what happens (output format, timing, pass/fail)
- Check for existing CI scripts, test configs, or performance baselines

Be thorough — wrong setup wastes every future loop iteration.

#### 3. Challenge Constraints

Classify every constraint as **hard** (tests must pass, correctness required, API contracts) or **soft** (no dependency changes, don't touch file X, keep current architecture).

If there are non-trivial soft constraints, ask the user which can be relaxed — often the biggest optimization lever is removing an unnecessary constraint. If constraints are obvious and minimal (e.g., "output must be identical"), just state them and move on.

#### 4. Present Setup Plan

Show the user a summary of what you found and what you propose:

```
## Autoresearch Setup
- **Goal**: <what we're optimizing>
- **Benchmark command**: `<exact command>`
- **Primary metric**: <name> (<unit>, <lower|higher> is better)
- **Files in scope**: <directories/files that may be modified>
- **Hard constraints**: <real rules — tests must pass, etc.>
- **Soft constraints**: <self-imposed — can be relaxed if needed>
- **Checks command**: `<validation command, if any>`

### Constraint Challenge
- Constraint X is real because: <reason>
- Constraint Y could be relaxed: <what that unlocks>

Does this look right? I'll do 3 baseline runs to measure variance before entering the loop.
```

#### 5. Wait for User Confirmation

**Stop and wait.** Do not proceed until the user confirms the setup plan. If they request changes, adjust and re-present.

### Phase 2: Validation (create files, baseline measurement)

#### 6. Create Branch

```bash
git checkout -b autoresearch/<goal-slug>-$(date +%Y-%m-%d)
```

#### 7. Read Source Files

Read every file in scope to build deep understanding before writing session files.

#### 8. Create Session Files

**autoresearch.md** — The heart of the session. See [templates](references/templates.md) for the full template. A fresh agent with no context should be able to read this file and run the loop.

**autoresearch.sh** — Bash benchmark script (`set -euo pipefail`) that outputs `METRIC name=number` lines. Keep it fast — every second is multiplied by hundreds of runs.

**autoresearch.checks.sh** (optional) — Only create when constraints require correctness validation (tests, types, lint). Runs after every passing benchmark. Failures block `keep`.

#### 9. Initialize Experiment

```bash
bash ${CLAUDE_SKILL_DIR}/scripts/init_experiment.sh \
  "<session name>" "<metric_name>" "<metric_unit>" "<lower|higher>" \
  "$(pwd)/autoresearch.jsonl"
```

#### 10. Measure Noise Floor (3 baseline runs)

Run the benchmark **3 times** with no code changes to establish variance:

```bash
bash ${CLAUDE_SKILL_DIR}/scripts/run_experiment.sh \
  "./autoresearch.sh" 600 "$(pwd)"
# Run 3 times, record all 3 metric values
```

Compute:
- **Baseline** = median of the 3 values
- **Variance** = (max - min) / median as a percentage
- **Significance threshold** = 2x variance (changes must exceed this to be meaningful)

If variance > 20%, the benchmark is too noisy — fix the benchmark script (pin randomness, increase iterations, warm up caches) and re-measure.

If variance < 1%, the metric is effectively deterministic — note this and skip the significance threshold during the loop (any improvement counts).

#### 11. Show Baseline Results

Present the noise-floor analysis to the user:

```
## Baseline Measurement (3 runs)
- Run 1: <value>, Run 2: <value>, Run 3: <value>
- Baseline (median): <value> <unit>
- Variance: <X>%
- Significance threshold: changes must exceed <2X>% to count
- Checks: ✓/✗/skipped

Ready to start the autonomous loop? I'll keep optimizing until you stop me.
```

If any run failed (crash, checks fail, unexpected output), diagnose and fix the setup files before re-running. Do not ask the user to confirm a broken setup.

#### 12. Wait for User Confirmation

**Stop and wait.** Do not enter the autonomous loop until the user explicitly confirms.

### Phase 3: Enter Autonomous Loop (on confirmation)

#### 13. Commit Setup & Log Baseline

```bash
git add autoresearch.md autoresearch.sh autoresearch.checks.sh autoresearch.config.json 2>/dev/null; git add autoresearch.jsonl
git commit -m "autoresearch: setup session files"
```

Log the 3 baseline runs (runs #1-3). Record the median as the baseline and the variance percentage in `autoresearch.md`.

#### 14. Start Looping

Enter the experiment loop below. From this point: **NEVER STOP.**

## Context Management (CRITICAL — prevents premature stopping)

Long sessions exhaust the context window, causing the loop to stop. Follow these rules strictly:

1. **Re-read `autoresearch.md` every 5 runs.** When the current run number is a multiple of 5 (5, 10, 15, 20...), re-read `autoresearch.md` before making the next change. This refreshes your understanding and reinforces the loop contract.

2. **Keep outputs minimal.** Don't print full file contents or long analysis between runs. Focus on: what you're trying, the result, and what's next.

3. **Don't accumulate explanations.** After logging a result and showing the dashboard, immediately proceed to the next change. No summaries, no "so far we've done X" recaps.

4. **Never produce a final summary.** If you feel the urge to summarize the session and stop — that is the context compression talking. Re-read `autoresearch.md` and continue.

5. **Update `autoresearch.md` every 5 runs** (same cadence as re-reading). This ensures a resuming agent has full context even if this session ends.

## The Experiment Loop

### Step 1: Make a Change

Edit files in scope. Focus on one idea at a time. If run number is a multiple of 5, first re-read `autoresearch.md` and update its "What's Been Tried" section.

**Idea ordering:** Before your first change, assess the optimization landscape:
- **If the codebase is unoptimized or you spot clear structural waste:** start bold — parallel execution, algorithm replacement, removing unnecessary work, caching, batching. Go for 10x levers before 10% levers.
- **If the codebase is already well-optimized:** structural headroom may not exist. Start with profiling to find the actual bottleneck, then target it directly — even if the fix is incremental.

In either case, avoid dependency upgrades as an early move — they introduce breaking-change risk and rabbit holes. Try them after you've exhausted changes within the current dependency set.

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
| Primary metric improved beyond significance threshold | `keep` |
| Primary metric worse, equal, or within noise | `discard` |

"Improved" means: lower value when direction=lower, higher value when direction=higher. Compare against the **best kept value** (or baseline if no keeps yet).

**Variance-aware gating:** If the baseline variance was non-trivial (≥1%), a change only counts as an improvement if the delta exceeds the significance threshold (2x baseline variance, established in Phase 2). Changes within the noise floor are `discard` — don't let random variance pollute your kept results. If your baseline variance was 3%, you need >6% improvement to `keep`. For deterministic metrics (variance <1%), any improvement counts.

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

**LOOP FOREVER.** Never ask "should I continue?" — the user expects autonomous work. (This applies only after the Phase 2 confirmation checkpoint. During setup, always wait for explicit user approval before entering the loop.)

- **Primary metric is king.** Improved beyond significance threshold → `keep`. Within noise or worse → `discard`. Secondary metrics rarely affect this decision.
- **Simpler is better.** Removing code for equal perf = keep. Ugly complexity for tiny gain = probably discard.
- **Don't thrash.** Repeatedly reverting the same idea? Try something structurally different.
- **Crashes:** fix if trivial, otherwise log and move on. Don't over-invest.
- **Think longer when stuck.** Re-read source files, study profiling data, reason about what the CPU is actually doing. The best ideas come from deep understanding, not random variations.
- **Be careful not to overfit to the benchmarks and do not cheat on the benchmarks.**
- **NEVER produce a "session summary" or "results summary".** That is a stop signal. If you feel compelled to summarize, re-read `autoresearch.md` and start the next experiment instead.
- **NEVER say "here's what we achieved" or "remaining opportunities".** Those phrases mean you're about to stop. Don't.

### Escalation Protocol (pivot after repeated discards)

Track consecutive discards. When you accumulate too many in a row, force a change in approach:
- **High-variance metrics (≥5%):** escalate after **7** consecutive discards (some may be noise)
- **Low-variance metrics (<5%):** escalate after **4** consecutive discards (failures are real signal)

When escalating:
1. Re-read the goal and `autoresearch.md` from scratch.
2. Re-read source files and profile if possible — the bottleneck may not be where you think.
3. If soft constraints exist, consider whether relaxing one unlocks a new approach.
4. **Revisit deferred high-impact ideas.** Check `autoresearch.ideas.md` and the "What's Been Tried" section for ideas you ruled out or deferred. The constraint that blocked them may be softer than you assumed.
5. Try a **structurally different** approach — not a variation of what you've been doing.
6. If you've genuinely exhausted structural ideas, shift to targeted micro-optimizations informed by profiling, not random tweaks.
7. Reset the consecutive discard counter after ANY keep.

**NEVER STOP.** Once the loop has begun, do NOT pause to ask the user if you should continue. Do NOT ask "should I keep going?" or "is this a good stopping point?". Do NOT write a results summary or session recap — those are stop signals disguised as useful work. The user might be asleep or away from the computer and expects you to continue working *indefinitely* until manually interrupted. If each experiment takes ~3 minutes, you can run ~20/hour, ~100 overnight. The user wakes up to a dashboard full of results. That is the point.

If you run out of ideas, think harder — re-read source files for new angles, study profiling output, combine previous near-misses, try radical architectural changes. The loop runs until the user interrupts you, period. After showing the dashboard, IMMEDIATELY proceed to Step 1.

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
