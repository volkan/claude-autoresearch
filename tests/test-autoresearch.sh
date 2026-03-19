#!/bin/bash
set -euo pipefail

# test-autoresearch.sh — Test suite for the autoresearch Claude Code skill
# Validates all 5 bundled scripts work correctly

SCRIPTS_DIR="$(cd "$(dirname "$0")/../scripts" && pwd)"
TESTS_PASSED=0
TESTS_FAILED=0
TEST_DIR=""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

cleanup() {
  [[ -n "${TEST_DIR:-}" ]] && rm -rf "$TEST_DIR" 2>/dev/null || true
}
trap cleanup EXIT

setup_test_dir() {
  TEST_DIR=$(mktemp -d)
  cd "$TEST_DIR"
  git init -q
  git config user.email "test@autoresearch.dev"
  git config user.name "Autoresearch Test"
  echo "# test project" > README.md
  mkdir -p src
  echo 'echo "hello world"' > src/main.sh
  git add -A && git commit -q -m "initial commit"
}

pass() {
  TESTS_PASSED=$((TESTS_PASSED + 1))
  echo -e "    ${GREEN}✓${NC} $1"
}

fail() {
  TESTS_FAILED=$((TESTS_FAILED + 1))
  echo -e "    ${RED}✗${NC} $1"
}

assert_file_exists() {
  if [[ -f "$1" ]]; then
    pass "File exists: $(basename "$1")"
  else
    fail "File missing: $1"
  fi
}

assert_file_contains() {
  if grep -q "$2" "$1" 2>/dev/null; then
    pass "File contains: '$2'"
  else
    fail "File '$1' does not contain: '$2'"
  fi
}

assert_output_contains() {
  if echo "$1" | grep -q "$2" 2>/dev/null; then
    pass "Output contains: '$2'"
  else
    fail "Output missing: '$2'"
  fi
}

assert_equals() {
  if [[ "$1" == "$2" ]]; then
    pass "$3"
  else
    fail "$3 (expected '$2', got '$1')"
  fi
}

assert_json_field() {
  local json="$1" field="$2" expected="$3" label="$4"
  local actual
  actual=$(echo "$json" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('$field',''))" 2>/dev/null || echo "PARSE_ERROR")
  if [[ "$actual" == "$expected" ]]; then
    pass "$label"
  else
    fail "$label (expected '$expected', got '$actual')"
  fi
}

# =====================================================================
# TEST 1: Setup / init_experiment.sh
# =====================================================================
test_1_setup() {
  echo -e "\n${BOLD}Test 1: Setup — init_experiment.sh creates valid JSONL${NC}"
  setup_test_dir

  # Run init
  OUTPUT=$(bash "$SCRIPTS_DIR/init_experiment.sh" \
    "test speed optimization" "duration" "s" "lower" \
    "$TEST_DIR/autoresearch.jsonl" 2>&1)

  assert_file_exists "$TEST_DIR/autoresearch.jsonl"
  assert_output_contains "$OUTPUT" "Initialized experiment"
  assert_output_contains "$OUTPUT" "duration"
  assert_output_contains "$OUTPUT" "lower is better"

  # Validate JSONL format
  FIRST_LINE=$(head -1 "$TEST_DIR/autoresearch.jsonl")
  assert_output_contains "$FIRST_LINE" '"type":"config"'
  assert_output_contains "$FIRST_LINE" '"metricName":"duration"'
  assert_output_contains "$FIRST_LINE" '"bestDirection":"lower"'

  # Verify valid JSON
  if python3 -c "import json; json.loads('$FIRST_LINE')" 2>/dev/null; then
    pass "Config line is valid JSON"
  else
    fail "Config line is not valid JSON"
  fi

  # Test re-init (append)
  OUTPUT2=$(bash "$SCRIPTS_DIR/init_experiment.sh" \
    "bundle size" "size" "kb" "lower" \
    "$TEST_DIR/autoresearch.jsonl" 2>&1)
  LINE_COUNT=$(wc -l < "$TEST_DIR/autoresearch.jsonl" | tr -d ' ')
  assert_equals "$LINE_COUNT" "2" "Re-init appends second config line"
  assert_output_contains "$OUTPUT2" "Re-initialized"

  cleanup
}

# =====================================================================
# TEST 2: Baseline Run — run_experiment.sh
# =====================================================================
test_2_baseline_run() {
  echo -e "\n${BOLD}Test 2: Baseline Run — run_experiment.sh times and captures output${NC}"
  setup_test_dir

  # Create a simple benchmark script
  cat > "$TEST_DIR/autoresearch.sh" << 'BENCH'
#!/bin/bash
set -euo pipefail
sleep 0.1
echo "Running benchmark..."
echo "METRIC duration=1.234"
echo "METRIC compile_ms=456"
BENCH
  chmod +x "$TEST_DIR/autoresearch.sh"

  # Run experiment
  OUTPUT=$(bash "$SCRIPTS_DIR/run_experiment.sh" \
    "./autoresearch.sh" 60 "$TEST_DIR" 2>&1)

  assert_output_contains "$OUTPUT" "EXIT_CODE=0"
  assert_output_contains "$OUTPUT" "DURATION="
  assert_output_contains "$OUTPUT" "TIMED_OUT=false"
  assert_output_contains "$OUTPUT" "METRIC duration=1.234"
  assert_output_contains "$OUTPUT" "METRIC compile_ms=456"
  assert_output_contains "$OUTPUT" "Running benchmark"

  # Verify CHECKS_EXIT=skipped (no checks file)
  assert_output_contains "$OUTPUT" "CHECKS_EXIT=skipped"

  # Test with failing command
  OUTPUT_FAIL=$(bash "$SCRIPTS_DIR/run_experiment.sh" \
    "exit 1" 60 "$TEST_DIR" 2>&1)
  assert_output_contains "$OUTPUT_FAIL" "EXIT_CODE=1"

  cleanup
}

# =====================================================================
# TEST 3: Keep/Discard — log_experiment.sh git integration
# =====================================================================
test_3_keep_discard() {
  echo -e "\n${BOLD}Test 3: Keep/Discard — log_experiment.sh git commit/revert${NC}"
  setup_test_dir

  # Init JSONL
  bash "$SCRIPTS_DIR/init_experiment.sh" \
    "test" "duration" "s" "lower" "$TEST_DIR/autoresearch.jsonl" > /dev/null 2>&1

  # Create autoresearch.md (protected file)
  echo "# Test session" > "$TEST_DIR/autoresearch.md"
  git add autoresearch.md autoresearch.jsonl && git commit -q -m "setup"

  INITIAL_COMMITS=$(git rev-list --count HEAD)

  # --- Test KEEP ---
  echo "optimization 1" >> "$TEST_DIR/src/main.sh"
  COMMIT=$(git rev-parse --short=7 HEAD)

  OUTPUT_KEEP=$(bash "$SCRIPTS_DIR/log_experiment.sh" \
    "$TEST_DIR/autoresearch.jsonl" 1 "$COMMIT" 1.0 "keep" \
    "baseline run" 0 "$TEST_DIR" '{}' 2>&1)

  assert_output_contains "$OUTPUT_KEEP" "Logged #1: keep"
  assert_output_contains "$OUTPUT_KEEP" "Git: committed"

  AFTER_KEEP=$(git rev-list --count HEAD)
  if [[ $AFTER_KEEP -gt $INITIAL_COMMITS ]]; then
    pass "Keep created a new git commit"
  else
    fail "Keep did not create a git commit"
  fi

  # Verify JSONL has the result
  RESULT_LINE=$(grep '"run":1' "$TEST_DIR/autoresearch.jsonl" || echo "")
  assert_output_contains "$RESULT_LINE" '"status":"keep"'

  # --- Test DISCARD ---
  echo "bad change that should be reverted" >> "$TEST_DIR/src/main.sh"

  OUTPUT_DISC=$(bash "$SCRIPTS_DIR/log_experiment.sh" \
    "$TEST_DIR/autoresearch.jsonl" 2 "$COMMIT" 2.0 "discard" \
    "bad optimization" 0 "$TEST_DIR" '{}' 2>&1)

  assert_output_contains "$OUTPUT_DISC" "Logged #2: discard"
  assert_output_contains "$OUTPUT_DISC" "Git: reverted"

  # Verify src/main.sh was reverted (no "bad change")
  if ! grep -q "bad change that should be reverted" "$TEST_DIR/src/main.sh" 2>/dev/null; then
    pass "Discard reverted source file changes"
  else
    fail "Discard did NOT revert source file changes"
  fi

  # Verify autoresearch.jsonl still exists (protected)
  assert_file_exists "$TEST_DIR/autoresearch.jsonl"
  assert_file_exists "$TEST_DIR/autoresearch.md"

  # Verify JSONL has both results
  JSONL_RESULTS=$(grep -c '"run":' "$TEST_DIR/autoresearch.jsonl")
  assert_equals "$JSONL_RESULTS" "2" "JSONL has 2 result entries"

  cleanup
}

# =====================================================================
# TEST 4: Checks Integration — run_experiment.sh with checks
# =====================================================================
test_4_checks() {
  echo -e "\n${BOLD}Test 4: Checks Integration — passing and failing checks${NC}"
  setup_test_dir

  # Create benchmark
  cat > "$TEST_DIR/autoresearch.sh" << 'BENCH'
#!/bin/bash
set -euo pipefail
echo "METRIC duration=1.0"
BENCH
  chmod +x "$TEST_DIR/autoresearch.sh"

  # Create PASSING checks
  cat > "$TEST_DIR/autoresearch.checks.sh" << 'CHECKS'
#!/bin/bash
set -euo pipefail
echo "All tests passed"
exit 0
CHECKS
  chmod +x "$TEST_DIR/autoresearch.checks.sh"

  # Run — should show checks passing
  OUTPUT_PASS=$(bash "$SCRIPTS_DIR/run_experiment.sh" \
    "./autoresearch.sh" 60 "$TEST_DIR" 60 2>&1)

  assert_output_contains "$OUTPUT_PASS" "EXIT_CODE=0"
  assert_output_contains "$OUTPUT_PASS" "RUNNING CHECKS"
  assert_output_contains "$OUTPUT_PASS" "CHECKS_EXIT=0"
  assert_output_contains "$OUTPUT_PASS" "All tests passed"

  # Create FAILING checks
  cat > "$TEST_DIR/autoresearch.checks.sh" << 'CHECKS'
#!/bin/bash
set -euo pipefail
echo "ERROR: type mismatch on line 42"
exit 1
CHECKS
  chmod +x "$TEST_DIR/autoresearch.checks.sh"

  # Run — should show checks failing
  OUTPUT_FAIL=$(bash "$SCRIPTS_DIR/run_experiment.sh" \
    "./autoresearch.sh" 60 "$TEST_DIR" 60 2>&1)

  assert_output_contains "$OUTPUT_FAIL" "EXIT_CODE=0"
  assert_output_contains "$OUTPUT_FAIL" "CHECKS_EXIT=1"
  assert_output_contains "$OUTPUT_FAIL" "type mismatch"

  # Run with crashing benchmark — checks should NOT run
  OUTPUT_CRASH=$(bash "$SCRIPTS_DIR/run_experiment.sh" \
    "exit 1" 60 "$TEST_DIR" 60 2>&1)

  assert_output_contains "$OUTPUT_CRASH" "EXIT_CODE=1"
  assert_output_contains "$OUTPUT_CRASH" "CHECKS_EXIT=skipped"

  cleanup
}

# =====================================================================
# TEST 5: State Reconstruction + Dashboard
# =====================================================================
test_5_state_reconstruction() {
  echo -e "\n${BOLD}Test 5: State Reconstruction + Dashboard${NC}"
  setup_test_dir

  # Write a realistic multi-result JSONL
  cat > "$TEST_DIR/autoresearch.jsonl" << 'EOF'
{"type":"config","name":"test speed","metricName":"duration","metricUnit":"s","bestDirection":"lower"}
{"run":1,"commit":"a1b2c3d","metric":12.3,"metrics":{},"status":"keep","description":"baseline","timestamp":1710000000000,"segment":0}
{"run":2,"commit":"e4f5g6h","metric":11.1,"metrics":{},"status":"keep","description":"optimize inner loop","timestamp":1710000060000,"segment":0}
{"run":3,"commit":"reverted","metric":14.0,"metrics":{},"status":"discard","description":"aggressive inlining","timestamp":1710000120000,"segment":0}
{"run":4,"commit":"i7j8k9l","metric":10.5,"metrics":{},"status":"keep","description":"cache hot path","timestamp":1710000180000,"segment":0}
{"run":5,"commit":"reverted","metric":10.8,"metrics":{},"status":"checks_failed","description":"unsafe optimization","timestamp":1710000240000,"segment":0}
EOF

  # Test reconstruct_state.sh
  STATE=$(bash "$SCRIPTS_DIR/reconstruct_state.sh" "$TEST_DIR/autoresearch.jsonl" 2>&1)

  assert_json_field "$STATE" "name" "test speed" "State name is 'test speed'"
  assert_json_field "$STATE" "metricName" "duration" "Metric name is 'duration'"
  assert_json_field "$STATE" "bestDirection" "lower" "Direction is 'lower'"
  assert_json_field "$STATE" "totalRuns" "5" "Total runs is 5"
  assert_json_field "$STATE" "kept" "3" "Kept count is 3"
  assert_json_field "$STATE" "discarded" "1" "Discarded count is 1"
  assert_json_field "$STATE" "checksFailed" "1" "Checks failed count is 1"
  assert_json_field "$STATE" "baseline" "12.3" "Baseline is 12.3"
  assert_json_field "$STATE" "best" "10.5" "Best is 10.5"
  assert_json_field "$STATE" "exists" "True" "State exists is True"

  # Test show_dashboard.sh
  DASHBOARD=$(bash "$SCRIPTS_DIR/show_dashboard.sh" "$TEST_DIR/autoresearch.jsonl" 2>&1)

  assert_output_contains "$DASHBOARD" "Autoresearch: test speed"
  assert_output_contains "$DASHBOARD" "Runs: 5"
  assert_output_contains "$DASHBOARD" "3 kept"
  assert_output_contains "$DASHBOARD" "1 discarded"
  assert_output_contains "$DASHBOARD" "1 checks_failed"
  assert_output_contains "$DASHBOARD" "baseline"
  assert_output_contains "$DASHBOARD" "optimize inner loop"
  assert_output_contains "$DASHBOARD" "cache hot path"
  assert_output_contains "$DASHBOARD" "unsafe optimization"

  # Test with nonexistent file
  EMPTY_STATE=$(bash "$SCRIPTS_DIR/reconstruct_state.sh" "/nonexistent/file.jsonl" 2>&1)
  assert_output_contains "$EMPTY_STATE" '"exists":false'

  cleanup
}

# =====================================================================
# TEST 6: Statistical Confidence Layer
# =====================================================================
test_6_cli_confidence() {
  echo -e "\n${BOLD}Test 6: Statistical Confidence Layer${NC}"
  setup_test_dir

  CLI="$SCRIPTS_DIR/cli.py"

  # --- Sub-test 1: Clear improvement → strong confidence ---
  cat > "$TEST_DIR/test1.jsonl" << 'EOF'
{"type":"config","name":"test","metricName":"duration","metricUnit":"s","bestDirection":"lower"}
{"run":1,"commit":"aaa","metric":100,"metrics":{},"status":"keep","description":"baseline1","timestamp":1,"segment":0}
{"run":2,"commit":"bbb","metric":102,"metrics":{},"status":"keep","description":"baseline2","timestamp":2,"segment":0}
{"run":3,"commit":"ccc","metric":98,"metrics":{},"status":"keep","description":"baseline3","timestamp":3,"segment":0}
{"run":4,"commit":"ddd","metric":80,"metrics":{},"status":"keep","description":"real improvement","timestamp":4,"segment":0}
EOF

  STATE1=$(python3 "$CLI" state "$TEST_DIR/test1.jsonl" 2>&1)
  CONF1=$(echo "$STATE1" | python3 -c "import json,sys; print(json.load(sys.stdin).get('confidence',''))" 2>/dev/null)
  LABEL1=$(echo "$STATE1" | python3 -c "import json,sys; print(json.load(sys.stdin).get('confidenceLabel',''))" 2>/dev/null)
  if [[ "$LABEL1" == "strong" ]]; then
    pass "Clear improvement: label is 'strong' (conf=$CONF1)"
  else
    fail "Clear improvement: expected 'strong', got '$LABEL1' (conf=$CONF1)"
  fi

  # --- Sub-test 2: Marginal in noisy data → weak ---
  cat > "$TEST_DIR/test2.jsonl" << 'EOF'
{"type":"config","name":"test","metricName":"duration","metricUnit":"s","bestDirection":"lower"}
{"run":1,"commit":"aaa","metric":100,"metrics":{},"status":"keep","description":"baseline1","timestamp":1,"segment":0}
{"run":2,"commit":"bbb","metric":110,"metrics":{},"status":"keep","description":"baseline2","timestamp":2,"segment":0}
{"run":3,"commit":"ccc","metric":90,"metrics":{},"status":"keep","description":"baseline3","timestamp":3,"segment":0}
{"run":4,"commit":"ddd","metric":95,"metrics":{},"status":"keep","description":"marginal","timestamp":4,"segment":0}
EOF

  STATE2=$(python3 "$CLI" state "$TEST_DIR/test2.jsonl" 2>&1)
  LABEL2=$(echo "$STATE2" | python3 -c "import json,sys; print(json.load(sys.stdin).get('confidenceLabel',''))" 2>/dev/null)
  if [[ "$LABEL2" == "weak" ]]; then
    pass "Marginal in noisy data: label is 'weak'"
  else
    fail "Marginal in noisy data: expected 'weak', got '$LABEL2'"
  fi

  # --- Sub-test 3: Deterministic baseline → deterministic ---
  cat > "$TEST_DIR/test3.jsonl" << 'EOF'
{"type":"config","name":"test","metricName":"duration","metricUnit":"s","bestDirection":"lower"}
{"run":1,"commit":"aaa","metric":100,"metrics":{},"status":"keep","description":"baseline1","timestamp":1,"segment":0}
{"run":2,"commit":"bbb","metric":100,"metrics":{},"status":"keep","description":"baseline2","timestamp":2,"segment":0}
{"run":3,"commit":"ccc","metric":100,"metrics":{},"status":"keep","description":"baseline3","timestamp":3,"segment":0}
{"run":4,"commit":"ddd","metric":95,"metrics":{},"status":"keep","description":"improved","timestamp":4,"segment":0}
EOF

  STATE3=$(python3 "$CLI" state "$TEST_DIR/test3.jsonl" 2>&1)
  LABEL3=$(echo "$STATE3" | python3 -c "import json,sys; print(json.load(sys.stdin).get('confidenceLabel',''))" 2>/dev/null)
  METHOD3=$(echo "$STATE3" | python3 -c "import json,sys; print(json.load(sys.stdin).get('confidenceMethod',''))" 2>/dev/null)
  if [[ "$LABEL3" == "deterministic" ]]; then
    pass "Deterministic baseline: label is 'deterministic'"
  else
    fail "Deterministic baseline: expected 'deterministic', got '$LABEL3' (method=$METHOD3)"
  fi

  # --- Sub-test 4: Window fallback (baseline MAD=0 but later variance) ---
  cat > "$TEST_DIR/test4.jsonl" << 'EOF'
{"type":"config","name":"test","metricName":"duration","metricUnit":"s","bestDirection":"lower"}
{"run":1,"commit":"aaa","metric":100,"metrics":{},"status":"keep","description":"baseline1","timestamp":1,"segment":0}
{"run":2,"commit":"bbb","metric":100,"metrics":{},"status":"keep","description":"baseline2","timestamp":2,"segment":0}
{"run":3,"commit":"ccc","metric":100,"metrics":{},"status":"keep","description":"baseline3","timestamp":3,"segment":0}
{"run":4,"commit":"ddd","metric":90,"metrics":{},"status":"keep","description":"noise1","timestamp":4,"segment":0}
{"run":5,"commit":"eee","metric":110,"metrics":{},"status":"keep","description":"noise2","timestamp":5,"segment":0}
{"run":6,"commit":"fff","metric":80,"metrics":{},"status":"keep","description":"real improvement","timestamp":6,"segment":0}
EOF

  STATE4=$(python3 "$CLI" state "$TEST_DIR/test4.jsonl" 2>&1)
  METHOD4=$(echo "$STATE4" | python3 -c "import json,sys; print(json.load(sys.stdin).get('confidenceMethod',''))" 2>/dev/null)
  if [[ "$METHOD4" == "window_mad" ]]; then
    pass "Window fallback: method is 'window_mad'"
  else
    fail "Window fallback: expected 'window_mad', got '$METHOD4'"
  fi

  # --- Sub-test 5: Insufficient data (<3 runs) → null ---
  cat > "$TEST_DIR/test5.jsonl" << 'EOF'
{"type":"config","name":"test","metricName":"duration","metricUnit":"s","bestDirection":"lower"}
{"run":1,"commit":"aaa","metric":100,"metrics":{},"status":"keep","description":"baseline1","timestamp":1,"segment":0}
{"run":2,"commit":"bbb","metric":90,"metrics":{},"status":"keep","description":"baseline2","timestamp":2,"segment":0}
EOF

  STATE5=$(python3 "$CLI" state "$TEST_DIR/test5.jsonl" 2>&1)
  CONF5=$(echo "$STATE5" | python3 -c "import json,sys; print(json.load(sys.stdin).get('confidence',''))" 2>/dev/null)
  if [[ "$CONF5" == "None" ]]; then
    pass "Insufficient data: confidence is null"
  else
    fail "Insufficient data: expected null, got '$CONF5'"
  fi

  # --- Sub-test 6: State command includes confidence fields ---
  FIELDS6=$(echo "$STATE1" | python3 -c "
import json, sys
d = json.load(sys.stdin)
fields = ['confidence', 'noiseFloor', 'confidenceMethod', 'confidenceLabel']
print('ok' if all(f in d for f in fields) else 'missing')
" 2>/dev/null)
  if [[ "$FIELDS6" == "ok" ]]; then
    pass "State command includes all confidence fields"
  else
    fail "State command missing confidence fields"
  fi

  # --- Sub-test 7: Analyze command includes confidence fields ---
  ANALYSIS=$(python3 "$CLI" analyze "$TEST_DIR/test1.jsonl" 2>&1)
  FIELDS7=$(echo "$ANALYSIS" | python3 -c "
import json, sys
d = json.load(sys.stdin)
fields = ['confidence', 'noiseFloor', 'confidenceMethod', 'confidenceLabel']
print('ok' if all(f in d for f in fields) else 'missing')
" 2>/dev/null)
  if [[ "$FIELDS7" == "ok" ]]; then
    pass "Analyze command includes all confidence fields"
  else
    fail "Analyze command missing confidence fields"
  fi

  cleanup
}

# =====================================================================
# MAIN
# =====================================================================
main() {
  echo -e "${BOLD}=========================================${NC}"
  echo -e "${BOLD}  Autoresearch Skill Test Suite${NC}"
  echo -e "${BOLD}=========================================${NC}"

  # Verify scripts exist
  for script in init_experiment.sh run_experiment.sh log_experiment.sh show_dashboard.sh reconstruct_state.sh; do
    if [[ ! -x "$SCRIPTS_DIR/$script" ]]; then
      echo -e "${RED}ERROR: $SCRIPTS_DIR/$script not found or not executable${NC}"
      exit 1
    fi
  done
  echo -e "${GREEN}All 5 scripts found and executable${NC}"

  test_1_setup
  test_2_baseline_run
  test_3_keep_discard
  test_4_checks
  test_5_state_reconstruction
  test_6_cli_confidence

  echo -e "\n${BOLD}=========================================${NC}"
  if [[ $TESTS_FAILED -eq 0 ]]; then
    echo -e "${GREEN}${BOLD}  ALL PASSED: $TESTS_PASSED passed, 0 failed${NC}"
  else
    echo -e "${RED}${BOLD}  RESULTS: $TESTS_PASSED passed, $TESTS_FAILED failed${NC}"
  fi
  echo -e "${BOLD}=========================================${NC}"

  [[ $TESTS_FAILED -eq 0 ]] && exit 0 || exit 1
}

main "$@"
