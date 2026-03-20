"""Tests for the autoresearch CLI."""

import json
import os
import shutil
import subprocess
import tempfile

import pytest

CLI = os.path.join(os.path.dirname(__file__), "..", "scripts", "cli.py")


def run_cli(*args, timeout=30):
    """Run cli.py with subcommand and args, return combined output."""
    r = subprocess.run(
        ["python3", CLI, *args], capture_output=True, text=True, timeout=timeout,
    )
    return (r.stdout + "\n" + r.stderr).strip()


@pytest.fixture
def test_dir():
    d = tempfile.mkdtemp(prefix="autoresearch-test-")
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.dev"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=d, check=True)
    os.makedirs(os.path.join(d, "src"), exist_ok=True)
    with open(os.path.join(d, "README.md"), "w") as f:
        f.write("# test\n")
    with open(os.path.join(d, "src", "main.sh"), "w") as f:
        f.write('echo "hello"\n')
    subprocess.run(["git", "add", "-A"], cwd=d, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=d, check=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


# --- Test 1: init ---

class TestInit:
    def test_creates_jsonl(self, test_dir):
        jp = os.path.join(test_dir, "autoresearch.jsonl")
        out = run_cli("init", "test speed", "duration", "s", "lower", jp)
        assert os.path.isfile(jp)
        assert "Initialized experiment" in out
        assert "duration" in out

    def test_valid_json(self, test_dir):
        jp = os.path.join(test_dir, "autoresearch.jsonl")
        run_cli("init", "test", "duration", "s", "lower", jp)
        data = json.loads(open(jp).readline())
        assert data["type"] == "config"
        assert data["metricName"] == "duration"
        assert isinstance(data["timestamp"], int)

    def test_reinit_appends(self, test_dir):
        jp = os.path.join(test_dir, "autoresearch.jsonl")
        run_cli("init", "test1", "d", "s", "lower", jp)
        out = run_cli("init", "test2", "size", "kb", "lower", jp)
        lines = [l for l in open(jp).readlines() if l.strip()]
        assert len(lines) == 2
        assert "Re-initialized" in out


# --- Test 2, 4, 11: run ---

class TestRun:
    def test_baseline(self, test_dir):
        bench = os.path.join(test_dir, "autoresearch.sh")
        with open(bench, "w") as f:
            f.write('#!/bin/bash\nset -euo pipefail\necho "Running..."\necho "METRIC duration=1.234"\necho "METRIC compile_ms=456"\n')
        os.chmod(bench, 0o755)
        out = run_cli("run", "./autoresearch.sh", "60", test_dir)
        assert "EXIT_CODE=0" in out
        assert "DURATION=" in out
        assert "TIMED_OUT=false" in out
        assert "METRIC duration=1.234" in out
        assert "METRIC compile_ms=456" in out
        assert "CHECKS_EXIT=skipped" in out

    def test_failing(self, test_dir):
        out = run_cli("run", "exit 1", "60", test_dir)
        assert "EXIT_CODE=1" in out

    def test_checks_pass(self, test_dir):
        with open(os.path.join(test_dir, "autoresearch.sh"), "w") as f:
            f.write('#!/bin/bash\nset -euo pipefail\necho "METRIC d=1.0"\n')
        os.chmod(os.path.join(test_dir, "autoresearch.sh"), 0o755)
        with open(os.path.join(test_dir, "autoresearch.checks.sh"), "w") as f:
            f.write('#!/bin/bash\necho "All passed"\nexit 0\n')
        os.chmod(os.path.join(test_dir, "autoresearch.checks.sh"), 0o755)
        out = run_cli("run", "./autoresearch.sh", "60", test_dir, "60")
        assert "CHECKS_EXIT=0" in out
        assert "All passed" in out

    def test_checks_fail(self, test_dir):
        with open(os.path.join(test_dir, "autoresearch.sh"), "w") as f:
            f.write('#!/bin/bash\nset -euo pipefail\necho "METRIC d=1.0"\n')
        os.chmod(os.path.join(test_dir, "autoresearch.sh"), 0o755)
        with open(os.path.join(test_dir, "autoresearch.checks.sh"), "w") as f:
            f.write('#!/bin/bash\necho "ERROR: type mismatch"\nexit 1\n')
        os.chmod(os.path.join(test_dir, "autoresearch.checks.sh"), 0o755)
        out = run_cli("run", "./autoresearch.sh", "60", test_dir, "60")
        assert "CHECKS_EXIT=1" in out
        assert "type mismatch" in out

    def test_checks_skipped_on_crash(self, test_dir):
        with open(os.path.join(test_dir, "autoresearch.checks.sh"), "w") as f:
            f.write('#!/bin/bash\nexit 0\n')
        os.chmod(os.path.join(test_dir, "autoresearch.checks.sh"), 0o755)
        out = run_cli("run", "exit 1", "60", test_dir, "60")
        assert "CHECKS_EXIT=skipped" in out

    def test_timeout(self, test_dir):
        with open(os.path.join(test_dir, "autoresearch.sh"), "w") as f:
            f.write('#!/bin/bash\nsleep 30\n')
        os.chmod(os.path.join(test_dir, "autoresearch.sh"), 0o755)
        out = run_cli("run", "./autoresearch.sh", "2", test_dir, timeout=10)
        assert "TIMED_OUT=true" in out
        assert "EXIT_CODE=-1" in out


# --- Test 3, 6, 10: log ---

class TestLog:
    def _setup_session(self, test_dir):
        jp = os.path.join(test_dir, "autoresearch.jsonl")
        run_cli("init", "test", "duration", "s", "lower", jp)
        with open(os.path.join(test_dir, "autoresearch.md"), "w") as f:
            f.write("# session\n")
        subprocess.run(["git", "add", "-A"], cwd=test_dir, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "setup"], cwd=test_dir, check=True)
        return jp

    def test_keep_commits(self, test_dir):
        jp = self._setup_session(test_dir)
        with open(os.path.join(test_dir, "src", "main.sh"), "a") as f:
            f.write("opt\n")
        commit = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"],
            cwd=test_dir, capture_output=True, text=True, check=True,
        ).stdout.strip()
        before = int(subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=test_dir, capture_output=True, text=True, check=True,
        ).stdout.strip())
        out = run_cli("log", jp, "1", commit, "1.0", "keep", "baseline", "0", test_dir, "{}")
        after = int(subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=test_dir, capture_output=True, text=True, check=True,
        ).stdout.strip())
        assert "Logged #1: keep" in out
        assert "Git: committed" in out
        assert after > before
        # Dashboard auto-printed
        assert "Autoresearch" in out

    def test_discard_reverts(self, test_dir):
        jp = self._setup_session(test_dir)
        # First keep
        with open(os.path.join(test_dir, "src", "main.sh"), "a") as f:
            f.write("opt\n")
        commit = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"],
            cwd=test_dir, capture_output=True, text=True, check=True,
        ).stdout.strip()
        run_cli("log", jp, "1", commit, "1.0", "keep", "baseline", "0", test_dir, "{}")
        # Discard
        with open(os.path.join(test_dir, "src", "main.sh"), "a") as f:
            f.write("bad change\n")
        out = run_cli("log", jp, "2", commit, "2.0", "discard", "bad", "0", test_dir, "{}")
        assert "Git: reverted" in out
        with open(os.path.join(test_dir, "src", "main.sh")) as f:
            assert "bad change" not in f.read()
        assert os.path.isfile(jp)
        assert os.path.isfile(os.path.join(test_dir, "autoresearch.md"))

    def test_strategy_tagged(self, test_dir):
        jp = self._setup_session(test_dir)
        with open(os.path.join(test_dir, "src", "main.sh"), "a") as f:
            f.write("c\n")
        commit = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"],
            cwd=test_dir, capture_output=True, text=True, check=True,
        ).stdout.strip()
        run_cli("log", jp, "1", commit, "1.0", "keep", "cache", "0", test_dir, "{}", "caching")
        line = [l for l in open(jp).readlines() if '"run":1' in l][0]
        assert '"strategy":"caching"' in line

    def test_strategy_empty_not_stored(self, test_dir):
        jp = self._setup_session(test_dir)
        with open(os.path.join(test_dir, "src", "main.sh"), "a") as f:
            f.write("c\n")
        commit = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"],
            cwd=test_dir, capture_output=True, text=True, check=True,
        ).stdout.strip()
        run_cli("log", jp, "1", commit, "0.9", "keep", "remove", "0", test_dir, "{}")
        line = [l for l in open(jp).readlines() if '"run":1' in l][0]
        assert '"strategy"' not in line

    def test_special_chars(self, test_dir):
        jp = os.path.join(test_dir, "autoresearch.jsonl")
        out = run_cli("init", "test's \"speed\" & more", "d", "s", "lower", jp)
        assert "Initialized" in out
        data = json.loads(open(jp).readline())
        assert "test's" in data["name"]


# --- Test 5, 8: state ---

class TestState:
    JSONL = """\
{"type":"config","name":"test speed","metricName":"duration","metricUnit":"s","bestDirection":"lower"}
{"run":1,"commit":"a1b2c3d","metric":12.3,"metrics":{},"status":"keep","description":"baseline","timestamp":1710000000000,"segment":0}
{"run":2,"commit":"e4f5g6h","metric":11.1,"metrics":{},"status":"keep","description":"optimize","timestamp":1710000060000,"segment":0}
{"run":3,"commit":"rev","metric":14.0,"metrics":{},"status":"discard","description":"bad","timestamp":1710000120000,"segment":0}
{"run":4,"commit":"i7j8k9l","metric":10.5,"metrics":{},"status":"keep","description":"cache","timestamp":1710000180000,"segment":0}
{"run":5,"commit":"rev","metric":10.8,"metrics":{},"status":"checks_failed","description":"unsafe","timestamp":1710000240000,"segment":0}
"""

    def test_state_fields(self, test_dir):
        jp = os.path.join(test_dir, "autoresearch.jsonl")
        with open(jp, "w") as f:
            f.write(self.JSONL)
        state = json.loads(run_cli("state", jp))
        assert state["name"] == "test speed"
        assert state["totalRuns"] == 5
        assert state["kept"] == 3
        assert state["best"] == 10.5
        assert state["exists"] is True

    def test_missing_file(self):
        out = run_cli("state", "/nonexistent/file.jsonl")
        assert '"exists":false' in out

    def test_consecutive_discards(self, test_dir):
        jp = os.path.join(test_dir, "autoresearch.jsonl")
        with open(jp, "w") as f:
            f.write("""\
{"type":"config","name":"t","metricName":"m","metricUnit":"","bestDirection":"lower"}
{"run":1,"commit":"a","metric":10,"status":"keep","description":"b","segment":0}
{"run":2,"commit":"b","metric":9,"status":"keep","description":"b","segment":0}
{"run":3,"commit":"r","metric":11,"status":"discard","description":"b","segment":0}
{"run":4,"commit":"r","metric":12,"status":"crash","description":"b","segment":0}
{"run":5,"commit":"r","metric":11,"status":"discard","description":"b","segment":0}
{"run":6,"commit":"r","metric":13,"status":"checks_failed","description":"b","segment":0}
""")
        state = json.loads(run_cli("state", jp))
        assert state["consecutiveDiscards"] == 4
        assert state["lastKeepMetric"] == 9

    def test_keep_resets_discards(self, test_dir):
        jp = os.path.join(test_dir, "autoresearch.jsonl")
        with open(jp, "w") as f:
            f.write("""\
{"type":"config","name":"t","metricName":"m","metricUnit":"","bestDirection":"lower"}
{"run":1,"commit":"a","metric":10,"status":"discard","description":"d","segment":0}
{"run":2,"commit":"b","metric":9,"status":"discard","description":"d","segment":0}
{"run":3,"commit":"c","metric":8,"status":"keep","description":"w","segment":0}
""")
        state = json.loads(run_cli("state", jp))
        assert state["consecutiveDiscards"] == 0


# --- Test 5, 9: dashboard ---

class TestDashboard:
    def test_renders(self, test_dir):
        jp = os.path.join(test_dir, "autoresearch.jsonl")
        with open(jp, "w") as f:
            f.write(TestState.JSONL)
        out = run_cli("dashboard", jp)
        assert "Autoresearch: test speed" in out
        assert "Runs: 5" in out
        assert "3 kept" in out

    def test_anti_stop(self, test_dir):
        jp = os.path.join(test_dir, "autoresearch.jsonl")
        with open(jp, "w") as f:
            f.write("""\
{"type":"config","name":"t","metricName":"d","metricUnit":"s","bestDirection":"lower"}
{"run":1,"commit":"a","metric":10,"status":"keep","description":"b","segment":0}
{"run":2,"commit":"a","metric":9.5,"status":"keep","description":"b","segment":0}
{"run":3,"commit":"r","metric":10.1,"status":"discard","description":"b","segment":0}
""")
        out = run_cli("dashboard", jp)
        assert "Run #4" in out
        assert "Do NOT stop" in out
        assert "NEVER ask" in out

    def test_escalation(self, test_dir):
        jp = os.path.join(test_dir, "autoresearch.jsonl")
        lines = ['{"type":"config","name":"t","metricName":"m","metricUnit":"","bestDirection":"lower"}\n']
        lines.append('{"run":1,"commit":"a","metric":10,"status":"keep","description":"b","segment":0}\n')
        for i in range(2, 9):
            lines.append(f'{{"run":{i},"commit":"r","metric":{10+i},"status":"discard","description":"b","segment":0}}\n')
        with open(jp, "w") as f:
            f.writelines(lines)
        out = run_cli("dashboard", jp)
        assert "ESCALATE" in out


# --- Test 7: analyze ---

class TestAnalyze:
    def test_analysis(self, test_dir):
        jp = os.path.join(test_dir, "autoresearch.jsonl")
        with open(jp, "w") as f:
            f.write("""\
{"type":"config","name":"t","metricName":"d","metricUnit":"s","bestDirection":"lower"}
{"run":1,"commit":"a","metric":10.0,"status":"keep","description":"b","segment":0,"strategy":"algorithm"}
{"run":2,"commit":"b","metric":9.0,"status":"keep","description":"b","segment":0,"strategy":"algorithm"}
{"run":3,"commit":"r","metric":11.0,"status":"discard","description":"b","segment":0,"strategy":"caching"}
{"run":4,"commit":"r","metric":10.5,"status":"discard","description":"b","segment":0,"strategy":"caching"}
{"run":5,"commit":"r","metric":10.8,"status":"discard","description":"b","segment":0,"strategy":"caching"}
""")
        analysis = json.loads(run_cli("analyze", jp))
        assert analysis["hasData"] is True
        assert analysis["totalRuns"] == 5
        assert "caching" in analysis["deadStrategies"]
        assert analysis["strategies"]["algorithm"]["kept"] == 2

    def test_missing(self):
        out = json.loads(run_cli("analyze", "/nonexistent/file.jsonl"))
        assert out["hasData"] is False


# --- find_best: zero and negative metrics ---

class TestFindBestEdgeCases:
    def test_zero_metric_kept(self, test_dir):
        """Metrics of 0 should not be excluded (e.g., error_count=0 is perfect)."""
        jp = os.path.join(test_dir, "autoresearch.jsonl")
        with open(jp, "w") as f:
            f.write("""\
{"type":"config","name":"t","metricName":"errors","metricUnit":"","bestDirection":"lower"}
{"run":1,"commit":"a","metric":5,"status":"keep","description":"baseline","segment":0}
{"run":2,"commit":"b","metric":0,"status":"keep","description":"fixed all","segment":0}
""")
        state = json.loads(run_cli("state", jp))
        assert state["best"] == 0
        assert state["bestRun"] == 2

    def test_negative_metric_kept(self, test_dir):
        """Negative metrics should work (e.g., log-loss)."""
        jp = os.path.join(test_dir, "autoresearch.jsonl")
        with open(jp, "w") as f:
            f.write("""\
{"type":"config","name":"t","metricName":"log_loss","metricUnit":"","bestDirection":"lower"}
{"run":1,"commit":"a","metric":-1.5,"status":"keep","description":"baseline","segment":0}
{"run":2,"commit":"b","metric":-2.0,"status":"keep","description":"worse","segment":0}
{"run":3,"commit":"c","metric":-0.5,"status":"keep","description":"better","segment":0}
""")
        state = json.loads(run_cli("state", jp))
        assert state["best"] == -2.0
        assert state["bestRun"] == 2

    def test_higher_direction(self, test_dir):
        """'higher is better' direction works correctly."""
        jp = os.path.join(test_dir, "autoresearch.jsonl")
        with open(jp, "w") as f:
            f.write("""\
{"type":"config","name":"t","metricName":"score","metricUnit":"","bestDirection":"higher"}
{"run":1,"commit":"a","metric":50,"status":"keep","description":"baseline","segment":0}
{"run":2,"commit":"b","metric":75,"status":"keep","description":"better","segment":0}
{"run":3,"commit":"c","metric":60,"status":"discard","description":"worse","segment":0}
""")
        state = json.loads(run_cli("state", jp))
        assert state["best"] == 75
        assert state["bestRun"] == 2
        assert state["bestDirection"] == "higher"


# --- Multiple segments ---

class TestMultiSegment:
    def test_reinit_creates_new_segment(self, test_dir):
        jp = os.path.join(test_dir, "autoresearch.jsonl")
        with open(jp, "w") as f:
            f.write("""\
{"type":"config","name":"seg1","metricName":"d","metricUnit":"s","bestDirection":"lower"}
{"run":1,"commit":"a","metric":10,"status":"keep","description":"b","segment":0}
{"run":2,"commit":"b","metric":9,"status":"keep","description":"b","segment":0}
{"type":"config","name":"seg2","metricName":"d","metricUnit":"s","bestDirection":"lower"}
{"run":1,"commit":"c","metric":20,"status":"keep","description":"new baseline","segment":1}
{"run":2,"commit":"d","metric":18,"status":"keep","description":"improved","segment":1}
""")
        state = json.loads(run_cli("state", jp))
        assert state["currentSegment"] == 1
        assert state["totalRuns"] == 2
        assert state["allRuns"] == 4
        assert state["baseline"] == 20
        assert state["best"] == 18


# --- Dashboard: strategy column ---

class TestDashboardStrategy:
    def test_strategy_shown(self, test_dir):
        jp = os.path.join(test_dir, "autoresearch.jsonl")
        with open(jp, "w") as f:
            f.write("""\
{"type":"config","name":"t","metricName":"d","metricUnit":"s","bestDirection":"lower"}
{"run":1,"commit":"a","metric":10,"status":"keep","description":"baseline","segment":0}
{"run":2,"commit":"b","metric":9,"status":"keep","description":"opt","segment":0,"strategy":"algorithm"}
{"run":3,"commit":"r","metric":11,"status":"discard","description":"bad","segment":0,"strategy":"caching"}
""")
        out = run_cli("dashboard", jp)
        assert "strategy" in out.lower()
        assert "algorithm" in out
        assert "caching" in out


# --- Baseline subcommand ---

class TestBaseline:
    def test_baseline_runs(self, test_dir):
        jp = os.path.join(test_dir, "autoresearch.jsonl")
        run_cli("init", "test", "duration", "s", "lower", jp)
        bench = os.path.join(test_dir, "autoresearch.sh")
        with open(bench, "w") as f:
            f.write('#!/bin/bash\nset -euo pipefail\necho "METRIC duration=1.5"\n')
        os.chmod(bench, 0o755)
        out = run_cli("baseline", jp, "./autoresearch.sh", test_dir, "3", "10")
        assert "Baseline Results" in out
        assert "Median" in out
        assert "Variance" in out
        assert "baselineRuns" in out
        # Check JSONL has baseline entries
        lines = [l for l in open(jp).readlines() if l.strip() and '"run":' in l]
        assert len(lines) == 3

    def test_baseline_missing_init(self, test_dir):
        jp = os.path.join(test_dir, "nonexistent.jsonl")
        out = run_cli("baseline", jp, "echo hi", test_dir)
        assert "not found" in out.lower() or "ERROR" in out

    def test_baseline_crash_handling(self, test_dir):
        jp = os.path.join(test_dir, "autoresearch.jsonl")
        run_cli("init", "test", "duration", "s", "lower", jp)
        out = run_cli("baseline", jp, "exit 1", test_dir, "3", "5")
        assert "need at least 2" in out.lower() or "ERROR" in out


# --- History subcommand ---

class TestHistory:
    def test_history_shows_all(self, test_dir):
        jp = os.path.join(test_dir, "autoresearch.jsonl")
        with open(jp, "w") as f:
            f.write("""\
{"type":"config","name":"test speed","metricName":"duration","metricUnit":"s","bestDirection":"lower"}
{"run":1,"commit":"a1b2c3d","metric":12.3,"status":"keep","description":"baseline","segment":0}
{"run":2,"commit":"e4f5g6h","metric":11.1,"status":"keep","description":"optimize","segment":0}
{"run":3,"commit":"rev","metric":14.0,"status":"discard","description":"bad","segment":0}
""")
        out = run_cli("history", jp)
        assert "Full History" in out
        assert "3 runs" in out
        assert "baseline" in out
        assert "optimize" in out
        assert "bad" in out

    def test_history_missing_file(self):
        out = run_cli("history", "/nonexistent/file.jsonl")
        assert "not found" in out.lower() or "No experiments" in out

    def test_history_empty(self, test_dir):
        jp = os.path.join(test_dir, "autoresearch.jsonl")
        with open(jp, "w") as f:
            f.write('{"type":"config","name":"t","metricName":"d","metricUnit":"s","bestDirection":"lower"}\n')
        out = run_cli("history", jp)
        assert "No experiments" in out


# --- Corrupt JSONL resilience ---

class TestCorruptData:
    def test_corrupt_lines_skipped(self, test_dir):
        jp = os.path.join(test_dir, "autoresearch.jsonl")
        with open(jp, "w") as f:
            f.write("""\
{"type":"config","name":"t","metricName":"d","metricUnit":"s","bestDirection":"lower"}
THIS IS NOT JSON
{"run":1,"commit":"a","metric":10,"status":"keep","description":"b","segment":0}
another corrupt line {{{
{"run":2,"commit":"b","metric":9,"status":"keep","description":"b","segment":0}
""")
        state = json.loads(run_cli("state", jp))
        assert state["totalRuns"] == 2
        assert state["best"] == 9

    def test_empty_file(self, test_dir):
        jp = os.path.join(test_dir, "autoresearch.jsonl")
        with open(jp, "w") as f:
            f.write("")
        out = run_cli("dashboard", jp)
        assert "No experiments" in out


# --- Recover subcommand ---

class TestRecover:
    def test_healthy_state(self, test_dir):
        jp = os.path.join(test_dir, "autoresearch.jsonl")
        run_cli("init", "test", "duration", "s", "lower", jp)
        out = json.loads(run_cli("recover", jp, test_dir))
        assert out["status"] == "healthy"
        assert out["badLines"] == 0

    def test_corrupt_lines_detected(self, test_dir):
        jp = os.path.join(test_dir, "autoresearch.jsonl")
        with open(jp, "w") as f:
            f.write('{"type":"config","name":"t","metricName":"d","metricUnit":"s","bestDirection":"lower"}\n')
            f.write("CORRUPT LINE\n")
            f.write('{"run":1,"commit":"a","metric":10,"status":"keep","description":"b","segment":0}\n')
        out = json.loads(run_cli("recover", jp, test_dir))
        assert out["status"] == "issues_found"
        assert out["badLines"] == 1
        assert any("corrupt" in i.lower() or "Corrupt" in i for i in out["issues"])

    def test_missing_file(self, test_dir):
        jp = os.path.join(test_dir, "nonexistent.jsonl")
        out = json.loads(run_cli("recover", jp, test_dir))
        assert out["status"] == "no_file"

    def test_next_run_number(self, test_dir):
        jp = os.path.join(test_dir, "autoresearch.jsonl")
        with open(jp, "w") as f:
            f.write('{"type":"config","name":"t","metricName":"d","metricUnit":"s","bestDirection":"lower"}\n')
            f.write('{"run":1,"commit":"a","metric":10,"status":"keep","description":"b","segment":0}\n')
            f.write('{"run":2,"commit":"b","metric":9,"status":"keep","description":"b","segment":0}\n')
        out = json.loads(run_cli("recover", jp, test_dir))
        assert out["nextRunNumber"] == 3


# --- Stall detection in dashboard ---

class TestStallDetection:
    def test_same_strategy_stall(self, test_dir):
        jp = os.path.join(test_dir, "autoresearch.jsonl")
        with open(jp, "w") as f:
            f.write('{"type":"config","name":"t","metricName":"d","metricUnit":"s","bestDirection":"lower"}\n')
            f.write('{"run":1,"commit":"a","metric":10,"status":"keep","description":"b","segment":0}\n')
            f.write('{"run":2,"commit":"b","metric":11,"status":"discard","description":"b","segment":0,"strategy":"caching"}\n')
            f.write('{"run":3,"commit":"c","metric":12,"status":"discard","description":"b","segment":0,"strategy":"caching"}\n')
            f.write('{"run":4,"commit":"d","metric":11,"status":"discard","description":"b","segment":0,"strategy":"caching"}\n')
        out = run_cli("dashboard", jp)
        assert "STALL" in out

    def test_crash_loop_warning(self, test_dir):
        jp = os.path.join(test_dir, "autoresearch.jsonl")
        with open(jp, "w") as f:
            f.write('{"type":"config","name":"t","metricName":"d","metricUnit":"s","bestDirection":"lower"}\n')
            f.write('{"run":1,"commit":"a","metric":10,"status":"keep","description":"b","segment":0}\n')
            f.write('{"run":2,"commit":"b","metric":0,"status":"crash","description":"b","segment":0}\n')
            f.write('{"run":3,"commit":"c","metric":0,"status":"crash","description":"b","segment":0}\n')
            f.write('{"run":4,"commit":"d","metric":0,"status":"crash","description":"b","segment":0}\n')
        out = run_cli("dashboard", jp)
        assert "CRASH LOOP" in out


# --- Enhanced anti-stop in dashboard ---

class TestAntiStopEnhanced:
    def test_mandatory_continue_messages(self, test_dir):
        jp = os.path.join(test_dir, "autoresearch.jsonl")
        with open(jp, "w") as f:
            f.write('{"type":"config","name":"t","metricName":"d","metricUnit":"s","bestDirection":"lower"}\n')
            f.write('{"run":1,"commit":"a","metric":10,"status":"keep","description":"b","segment":0}\n')
        out = run_cli("dashboard", jp)
        assert "MANDATORY" in out
        assert "NEVER ask" in out

    def test_analyze_continue_directive(self, test_dir):
        jp = os.path.join(test_dir, "autoresearch.jsonl")
        with open(jp, "w") as f:
            f.write('{"type":"config","name":"t","metricName":"d","metricUnit":"s","bestDirection":"lower"}\n')
            f.write('{"run":1,"commit":"a","metric":10,"status":"keep","description":"b","segment":0,"strategy":"algorithm"}\n')
            f.write('{"run":2,"commit":"b","metric":9,"status":"keep","description":"b","segment":0,"strategy":"algorithm"}\n')
        analysis = json.loads(run_cli("analyze", jp))
        assert any("CONTINUE" in r or "continue" in r.lower() for r in analysis["recommendation"])
