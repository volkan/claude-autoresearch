#!/bin/bash
set -euo pipefail

# run_experiment.sh — Run a benchmark command with timing and output capture
# Usage: run_experiment.sh <command> [timeout_seconds] [work_dir] [checks_timeout]
# Outputs structured results for Claude to parse

COMMAND="${1:?Usage: run_experiment.sh <command> [timeout_seconds] [work_dir] [checks_timeout]}"
TIMEOUT="${2:-600}"
WORK_DIR="${3:-.}"
CHECKS_TIMEOUT="${4:-300}"

cd "$WORK_DIR"

# Run benchmark with timing using python3 (macOS compatible)
RESULT_FILE=$(mktemp)
trap "rm -f '$RESULT_FILE'" EXIT

python3 -c "
import subprocess, time, sys, os

cmd = '''$COMMAND'''
timeout = $TIMEOUT
work_dir = '$WORK_DIR'

start = time.time()
try:
    result = subprocess.run(
        ['bash', '-c', cmd],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=work_dir if work_dir != '.' else None
    )
    duration = time.time() - start
    output = (result.stdout + '\n' + result.stderr).strip()
    tail = '\n'.join(output.split('\n')[-80:])

    with open('$RESULT_FILE', 'w') as f:
        f.write(f'EXIT_CODE={result.returncode}\n')
        f.write(f'DURATION={duration:.3f}\n')
        f.write(f'TIMED_OUT=false\n')
        f.write(f'---OUTPUT_START---\n')
        f.write(tail + '\n')
        f.write(f'---OUTPUT_END---\n')

        # Extract METRIC lines
        metrics = [l for l in output.split('\n') if l.startswith('METRIC ')]
        for m in metrics:
            f.write(m + '\n')

except subprocess.TimeoutExpired:
    duration = time.time() - start
    with open('$RESULT_FILE', 'w') as f:
        f.write(f'EXIT_CODE=-1\n')
        f.write(f'DURATION={duration:.3f}\n')
        f.write(f'TIMED_OUT=true\n')
        f.write(f'---OUTPUT_START---\n')
        f.write(f'TIMEOUT after {timeout}s\n')
        f.write(f'---OUTPUT_END---\n')
" 2>&1

cat "$RESULT_FILE"

# Parse exit code from result
EXIT_CODE=$(grep '^EXIT_CODE=' "$RESULT_FILE" | head -1 | cut -d= -f2)
TIMED_OUT=$(grep '^TIMED_OUT=' "$RESULT_FILE" | head -1 | cut -d= -f2)

# Run checks if benchmark passed and autoresearch.checks.sh exists
CHECKS_FILE="$WORK_DIR/autoresearch.checks.sh"
if [[ "$EXIT_CODE" == "0" && "$TIMED_OUT" == "false" && -f "$CHECKS_FILE" ]]; then
  echo ""
  echo "--- RUNNING CHECKS ---"

  CHECKS_RESULT_FILE=$(mktemp)
  trap "rm -f '$RESULT_FILE' '$CHECKS_RESULT_FILE'" EXIT

  python3 -c "
import subprocess, time

timeout = $CHECKS_TIMEOUT
work_dir = '$WORK_DIR'
checks_file = '$CHECKS_FILE'

start = time.time()
try:
    result = subprocess.run(
        ['bash', checks_file],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=work_dir if work_dir != '.' else None
    )
    duration = time.time() - start
    output = (result.stdout + '\n' + result.stderr).strip()
    tail = '\n'.join(output.split('\n')[-80:])

    with open('$CHECKS_RESULT_FILE', 'w') as f:
        f.write(f'CHECKS_EXIT={result.returncode}\n')
        f.write(f'CHECKS_DURATION={duration:.3f}\n')
        f.write(f'CHECKS_TIMED_OUT=false\n')
        f.write(f'---CHECKS_OUTPUT_START---\n')
        f.write(tail + '\n')
        f.write(f'---CHECKS_OUTPUT_END---\n')

except subprocess.TimeoutExpired:
    duration = time.time() - start
    with open('$CHECKS_RESULT_FILE', 'w') as f:
        f.write(f'CHECKS_EXIT=-1\n')
        f.write(f'CHECKS_DURATION={duration:.3f}\n')
        f.write(f'CHECKS_TIMED_OUT=true\n')
        f.write(f'---CHECKS_OUTPUT_START---\n')
        f.write(f'CHECKS TIMEOUT after {timeout}s\n')
        f.write(f'---CHECKS_OUTPUT_END---\n')
" 2>&1

  cat "$CHECKS_RESULT_FILE"
else
  echo ""
  echo "CHECKS_EXIT=skipped"
fi
