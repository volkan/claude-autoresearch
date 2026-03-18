# Autoresearch Templates

## autoresearch.md Template

Create this file during setup. A fresh agent with no context should be able to read this file and run the loop effectively.

```markdown
# Autoresearch: <goal>

## Objective
<Specific description of what we're optimizing and the workload.>

## Metrics
- **Primary**: <name> (<unit>, lower/higher is better)
- **Secondary**: <name>, <name>, ...

## How to Run
`./autoresearch.sh` — outputs `METRIC name=number` lines.

## Files in Scope
<Every file the agent may modify, with a brief note on what it does.>

## Off Limits
<What must NOT be touched.>

## Constraints
<Hard rules: tests must pass, no new deps, etc.>

## What's Been Tried
<Update this section as experiments accumulate. Note key wins, dead ends,
and architectural insights so the agent doesn't repeat failed approaches.>
```

## autoresearch.sh Template

Bash benchmark script that outputs `METRIC name=number` lines.

```bash
#!/bin/bash
set -euo pipefail

# Pre-checks (fast, < 1 second)
# e.g. syntax check, file existence

# Run the workload
# <your benchmark command here>

# Output metrics — one METRIC line per metric
# The primary metric MUST match the metric_name from init_experiment
echo "METRIC <metric_name>=<number>"

# Optional secondary metrics
# echo "METRIC compile_ms=420"
# echo "METRIC render_ms=980"
```

## autoresearch.checks.sh Template (Optional)

Only create when user constraints require correctness validation.

```bash
#!/bin/bash
set -euo pipefail

# Run correctness checks — tests, types, lint
# Keep output minimal. Only errors matter.
# Only the last 80 lines are fed back to the agent on failure.

# Example:
# pnpm test --run --reporter=dot 2>&1 | tail -50
# pnpm typecheck 2>&1 | grep -i error || true
```

## autoresearch.config.json Template (Optional)

```json
{
  "maxIterations": 50,
  "workingDir": "/path/to/project"
}
```

- `maxIterations`: Stop after N experiments
- `workingDir`: Override directory for all operations (absolute or relative)
