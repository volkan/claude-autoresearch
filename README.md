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

## Architecture

Single CLI at `scripts/cli.py` with subcommands — inspired by [pi-autoresearch](https://github.com/davebcn87/pi-autoresearch)'s single-file approach:

| Command | Purpose |
|---------|---------|
| `cli.py init` | Initialize experiment session (writes config to JSONL) |
| `cli.py run` | Run benchmark with timing, timeout, and optional checks |
| `cli.py baseline` | Run N baselines, compute variance and significance threshold |
| `cli.py log` | Record result, git commit/revert, auto-print dashboard |
| `cli.py state` | Reconstruct experiment state as JSON |
| `cli.py dashboard` | Print ASCII dashboard with strategy column |
| `cli.py analyze` | Strategy effectiveness analysis with recommendations |
| `cli.py history` | Full experiment history (all runs, not truncated) |
| `cli.py recover` | Diagnose and fix inconsistent state (corrupt JSONL, dirty git) |

## Running Tests

```bash
python3 -m pytest tests/ -v
```

## Requirements

- python3 (3.8+)
- git
