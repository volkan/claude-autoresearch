# autoresearch

Autonomous experiment loop skill for [Claude Code](https://docs.anthropic.com/en/docs/claude-code). Try ideas, keep what works, discard what doesn't, never stop.

Inspired by [karpathy/autoresearch](https://github.com/karpathy/autoresearch) and [davebcn87/pi-autoresearch](https://github.com/davebcn87/pi-autoresearch).

Give it an optimization target (test speed, bundle size, training loss, etc.) and it will:
1. Write benchmark scripts
2. Loop autonomously — edit code, run benchmark, measure
3. Keep improvements, revert regressions
4. Track everything in append-only JSONL

## Installation

```bash
git clone https://github.com/anthropics/claude-autoresearch.git ~/.claude/skills/autoresearch
```

Or clone elsewhere and symlink:

```bash
git clone https://github.com/anthropics/claude-autoresearch.git ~/projects/claude-autoresearch
ln -sf ~/projects/claude-autoresearch ~/.claude/skills/autoresearch
```

## Usage

In Claude Code, invoke the skill:

```
/autoresearch optimize test suite speed
```

Or use natural language:

```
set up autoresearch for bundle size reduction
run experiments to find the fastest build configuration
```

The skill will ask clarifying questions (or infer from context), create benchmark scripts, initialize tracking, and start looping.

## Scripts

All scripts live in `scripts/` and are invoked by the skill automatically.

| Script | Purpose |
|--------|---------|
| `init_experiment.sh` | Initialize a new experiment session (writes config to JSONL) |
| `run_experiment.sh` | Run benchmark with timing, timeout, and optional checks |
| `log_experiment.sh` | Record result and handle git commit (keep) or revert (discard) |
| `reconstruct_state.sh` | Parse JSONL and output current experiment state as JSON |
| `show_dashboard.sh` | Print ASCII dashboard of all experiment results |

## Running Tests

```bash
bash tests/test-autoresearch.sh
```

Runs 5 test suites covering initialization, benchmark execution, git keep/discard, checks integration, and state reconstruction + dashboard rendering.

## Requirements

- bash
- python3
- git
