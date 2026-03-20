#!/usr/bin/env python3
"""autoresearch CLI — single entry point for all experiment operations.

Usage:
  cli.py init      <name> <metric_name> <metric_unit> <direction> <jsonl_path>
  cli.py run       <command> [timeout] [work_dir] [checks_timeout]
  cli.py baseline  <jsonl_path> <command> <work_dir> [runs] [timeout] [metric_name]
  cli.py log       <jsonl_path> <run_num> <commit> <metric> <status> <description> <segment> <work_dir> [metrics_json] [strategy]
  cli.py state     <jsonl_path>
  cli.py dashboard <jsonl_path>
  cli.py analyze   <jsonl_path>
  cli.py history   <jsonl_path>
"""

import json
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import time

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROTECTED_FILES = [
    "autoresearch.jsonl",
    "autoresearch.md",
    "autoresearch.ideas.md",
    "autoresearch.sh",
    "autoresearch.checks.sh",
    "autoresearch.config.json",
]

STATUS_KEY_MAP = {
    "keep": "kept",
    "discard": "discarded",
    "crash": "crashed",
    "checks_failed": "checks_failed",
}

COMMON_STRATEGIES = [
    "algorithm", "caching", "parallelism", "io",
    "removal", "restructure", "batching",
]

# ---------------------------------------------------------------------------
# Resilience helpers
# ---------------------------------------------------------------------------

def _retry(fn, retries=2, delay=0.5, label="operation"):
    """Retry a function on failure with exponential backoff."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(delay * (2 ** attempt))
    print(f"WARNING: {label} failed after {retries + 1} attempts: {last_err}", file=sys.stderr)
    raise last_err


def _atomic_write(path, content):
    """Write content to file atomically (temp + rename). Prevents corruption."""
    dirn = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=dirn, prefix=".autoresearch-", suffix=".tmp")
    try:
        os.write(fd, content.encode("utf-8"))
        os.fsync(fd)
        os.close(fd)
        os.replace(tmp, path)
    except Exception:
        os.close(fd) if not os.get_inheritable(fd) else None
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _safe_append(path, line):
    """Append a line to file with fsync for durability."""
    with open(path, "a") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def _kill_process_tree(proc):
    """Kill process and all children. Handles zombie processes."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.kill()
    except (ProcessLookupError, OSError):
        pass


# ---------------------------------------------------------------------------
# JSONL
# ---------------------------------------------------------------------------

def read_jsonl(path):
    """Parse autoresearch.jsonl → (config, results).

    Resilient: silently skips corrupt lines, handles truncated files.
    """
    config, results, segment = {}, [], 0
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("type") == "config":
                        config = entry
                        if results:
                            segment += 1
                        continue
                    entry.setdefault("segment", segment)
                    results.append(entry)
                except json.JSONDecodeError:
                    continue
    except (IOError, OSError):
        pass
    return config, results


def append_jsonl(path, entry):
    """Append a JSON line to file with fsync for durability."""
    line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
    _safe_append(path, line)


def write_jsonl(path, entry):
    """Write a JSON line to a new file atomically."""
    line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
    _atomic_write(path, line)


# ---------------------------------------------------------------------------
# State computation
# ---------------------------------------------------------------------------

def current_segment_results(results):
    """Get current segment number and its results."""
    if not results:
        return 0, []
    seg = max(r.get("segment", 0) for r in results)
    return seg, [r for r in results if r.get("segment", 0) == seg]


def find_best(results, direction):
    """Find best kept metric → (value, run_number)."""
    best, best_run = None, 0
    for i, r in enumerate(results):
        if r["status"] == "keep" and isinstance(r.get("metric"), (int, float)):
            if best is None or (
                (direction == "lower" and r["metric"] < best)
                or (direction == "higher" and r["metric"] > best)
            ):
                best, best_run = r["metric"], i + 1
    return best, best_run


def count_consecutive_discards(results):
    """Count non-keep streak at end."""
    n = 0
    for r in reversed(results):
        if r["status"] in ("discard", "crash", "checks_failed"):
            n += 1
        else:
            break
    return n


def compute_strategies(results):
    """Per-strategy stats with win rates."""
    strategies = {}
    for r in results:
        s = r.get("strategy", "untagged")
        if s not in strategies:
            strategies[s] = {"kept": 0, "discarded": 0, "crashed": 0, "checks_failed": 0, "total": 0}
        key = STATUS_KEY_MAP.get(r["status"], r["status"])
        strategies[s][key] = strategies[s].get(key, 0) + 1
        strategies[s]["total"] += 1
    for s in strategies:
        t = strategies[s]["total"]
        strategies[s]["win_rate"] = round(strategies[s]["kept"] / t * 100, 1) if t > 0 else 0
    return strategies


def compute_confidence(results, direction):
    """Confidence that best improvement is real, not noise.

    Uses baseline MAD (first 3 runs = identical code = pure noise).
    Falls back to sliding-window MAD (last 10) if baseline MAD is 0.
    Returns (score, noise_floor, method) or (None, None, None).
    """
    metrics = [r["metric"] for r in results if isinstance(r.get("metric"), (int, float))]
    if len(metrics) < 3:
        return None, None, None

    baseline_val = metrics[0]
    best, _ = find_best(results, direction)
    if best is None or best == baseline_val:
        return None, None, None

    def median(xs):
        s = sorted(xs)
        n = len(s)
        return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2

    def mad_noise(values):
        med = median(values)
        return median([abs(x - med) for x in values]) * 1.4826

    # Try baseline MAD first (runs 1-3, identical code)
    bl = metrics[:3]
    nf = mad_noise(bl)
    method = "baseline_mad"

    # Fallback: sliding window MAD (last 10)
    if nf < 1e-10:
        window = metrics[-min(10, len(metrics)):]
        nf = mad_noise(window)
        method = "window_mad"

    # Deterministic
    if nf < 1e-10:
        return float("inf"), 0.0, "deterministic"

    score = round(abs(best - baseline_val) / nf, 1)
    return score, round(nf, 6), method


def confidence_label(score):
    """Map confidence score to human label."""
    if score is None:
        return None
    if score == float("inf"):
        return "deterministic"
    if score >= 2.0:
        return "strong"
    if score >= 1.0:
        return "moderate"
    return "weak"


def compute_full_state(config, results):
    """Compute complete experiment state."""
    seg, cur = current_segment_results(results)
    direction = config.get("bestDirection", "lower")
    baseline = cur[0]["metric"] if cur else None
    best, best_run = find_best(cur, direction)
    last_keep = None
    for r in cur:
        if r["status"] == "keep" and isinstance(r.get("metric"), (int, float)):
            last_keep = r["metric"]
    conf, nf, method = compute_confidence(cur, direction)
    return {
        "exists": True,
        "name": config.get("name", ""),
        "metricName": config.get("metricName", "metric"),
        "metricUnit": config.get("metricUnit", ""),
        "bestDirection": direction,
        "currentSegment": seg,
        "totalRuns": len(cur),
        "allRuns": len(results),
        "kept": sum(1 for r in cur if r["status"] == "keep"),
        "discarded": sum(1 for r in cur if r["status"] == "discard"),
        "crashed": sum(1 for r in cur if r["status"] == "crash"),
        "checksFailed": sum(1 for r in cur if r["status"] == "checks_failed"),
        "baseline": baseline,
        "best": best,
        "bestRun": best_run,
        "lastKeepMetric": last_keep,
        "consecutiveDiscards": count_consecutive_discards(cur),
        "nextRunNumber": len(cur) + 1,
        "strategies": compute_strategies(cur),
        "lastDescription": cur[-1].get("description", "") if cur else "",
        "lastStatus": cur[-1].get("status", "") if cur else "",
        "confidence": conf,
        "noiseFloor": nf,
        "confidenceMethod": method,
        "confidenceLabel": confidence_label(conf),
    }


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def fmt(value, unit):
    if value is None:
        return "\u2014"
    try:
        if isinstance(value, (int, float)) and value == int(value) and abs(value) < 1e15:
            return f"{int(value)}{unit}"
        return f"{value:.3f}{unit}"
    except (ValueError, TypeError, OverflowError):
        return f"{value}{unit}"


def delta_pct(value, baseline):
    if baseline is None or baseline == 0 or value is None:
        return "\u2014"
    pct = ((value - baseline) / baseline) * 100
    return f"{'+' if pct > 0 else ''}{pct:.1f}%"


# ---------------------------------------------------------------------------
# Git operations
# ---------------------------------------------------------------------------

def git_commit(work_dir, description, metric):
    """Stage and commit with retry. Returns SHA or None."""
    def _do_commit():
        subprocess.run(["git", "add", "-A"], cwd=work_dir, capture_output=True, timeout=30)
        if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=work_dir, capture_output=True, timeout=10).returncode == 0:
            print("Git: nothing to commit (working tree clean)")
            return None
        msg = f"{description}\n\nResult: {{\"status\":\"keep\",\"metric\":{metric}}}"
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
        try:
            tmp.write(msg)
            tmp.close()
            r = subprocess.run(["git", "commit", "-F", tmp.name], cwd=work_dir, capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                err_msg = (r.stderr or "").strip()
                print(f"WARNING: git commit failed: {err_msg}", file=sys.stderr)
                return None
        finally:
            os.unlink(tmp.name)
        sha = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"],
            cwd=work_dir, capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        print(f"Git: committed ({sha})")
        return sha

    try:
        return _retry(_do_commit, retries=2, delay=0.5, label="git commit")
    except Exception as e:
        print(f"ERROR: git commit failed permanently: {e}. Logging continues.", file=sys.stderr)
        return None


def git_revert(work_dir):
    """Revert all changes, preserving protected files. Retry-safe."""
    def _do_revert():
        backup = tempfile.mkdtemp(prefix="autoresearch-")
        try:
            for f in PROTECTED_FILES:
                src = os.path.join(work_dir, f)
                if os.path.isfile(src):
                    shutil.copy2(src, os.path.join(backup, f))
            subprocess.run(["git", "checkout", "--", "."], cwd=work_dir, capture_output=True, timeout=30)
            subprocess.run(["git", "clean", "-fd"], cwd=work_dir, capture_output=True, timeout=30)
            for f in PROTECTED_FILES:
                bak = os.path.join(backup, f)
                if os.path.isfile(bak):
                    shutil.move(bak, os.path.join(work_dir, f))
        finally:
            shutil.rmtree(backup, ignore_errors=True)

    try:
        _retry(_do_revert, retries=2, delay=0.5, label="git revert")
    except Exception as e:
        print(f"ERROR: git revert failed: {e}. Manual cleanup may be needed.", file=sys.stderr)


# ---------------------------------------------------------------------------
# Dashboard renderer
# ---------------------------------------------------------------------------

def render_dashboard(config, results, scripts_dir="."):
    """Print ASCII dashboard. Called automatically after log."""
    if not results:
        print("No experiments logged yet.")
        return

    _, cur = current_segment_results(results)
    name = config.get("name", "autoresearch")
    unit = config.get("metricUnit", "")
    direction = config.get("bestDirection", "lower")

    total = len(cur)
    kept = sum(1 for r in cur if r["status"] == "keep")
    discarded = sum(1 for r in cur if r["status"] == "discard")
    crashed = sum(1 for r in cur if r["status"] == "crash")
    chk_fail = sum(1 for r in cur if r["status"] == "checks_failed")
    baseline = cur[0]["metric"] if cur else None
    best, best_run = find_best(cur, direction)
    metric_name = config.get("metricName", "metric")

    # Header
    print(f"\n{'=' * 72}")
    print(f"  Autoresearch: {name}")
    print(f"{'=' * 72}")

    parts = [f"Runs: {total}", f"{kept} kept"]
    if discarded: parts.append(f"{discarded} discarded")
    if crashed: parts.append(f"{crashed} crashed")
    if chk_fail: parts.append(f"{chk_fail} checks_failed")
    print(f"  {' | '.join(parts)}")
    print(f"  Baseline: {fmt(baseline, unit)} | Best: {fmt(best, unit)} #{best_run} ({delta_pct(best, baseline)})")

    # Confidence
    conf, nf, method = compute_confidence(cur, direction)
    if conf is not None:
        label = confidence_label(conf)
        if conf == float("inf"):
            print(f"  Confidence: deterministic ({method}) | Noise floor: 0")
        else:
            print(f"  Confidence: {conf}\u00d7 {label} ({method}) | Noise floor: {fmt(nf, unit)}")
        if label == "weak":
            print(f"  \u26a0 Low confidence \u2014 improvement may be noise. Re-run to confirm.")
    print()

    # Table
    print(f"  {'#':>3}  {'commit':<9} {'* ' + metric_name:<14} {'delta':>8}  {'status':<15} {'strategy':<12} {'description'}")
    print(f"  {'\u2500' * 80}")

    if len(cur) > 10:
        display = [cur[0]] + cur[-9:]
        offset = True
    else:
        display, offset = cur, False

    for j, r in enumerate(display):
        idx = 1 if (offset and j == 0) else (len(cur) - 9 + j if offset else j + 1)
        if offset and j == 1:
            print(f"  {'':>3}  {'...':^9} {'':14} {'':>8}  {'':15} {'':12} (runs 2-{len(cur) - 9} omitted)")

        commit = r.get("commit", "?")[:7]
        metric = fmt(r["metric"], unit)
        d = delta_pct(r["metric"], baseline) if idx > 1 else "baseline"
        st = {
            "keep": "\u2713 keep", "discard": "\u2013 discard",
            "crash": "\u2717 crash", "checks_failed": "\u26a0 chk_fail",
        }.get(r["status"], r["status"])
        strat = r.get("strategy", "")[:11]
        desc = r.get("description", "")[:24]
        print(f"  {idx:>3}  {commit:<9} {metric:<14} {d:>8}  {st:<15} {strat:<12} {desc}")

    print(f"  {'\u2500' * 80}")
    if total > 10:
        print(f"  (showing last 10 of {total} runs)")
    print()

    # Escalation warnings
    consec = count_consecutive_discards(cur)
    if consec >= 7:
        print(f"  \u26a0\u26a0\u26a0 ESCALATE: 7+ consecutive non-keeps. MUST try structurally different approach.")
        print()
    elif consec >= 4:
        print(f"  \u26a0 {consec} consecutive non-keeps. Consider pivoting strategy.")
        print()

    # Target
    target = best if best is not None else baseline
    beat = "below" if direction == "lower" else "above"
    print(f"  Target: next run must be {beat} {fmt(target, unit)} to keep")
    print()

    # Stall pattern detection
    if total >= 3:
        last3 = [r.get("strategy", "") for r in cur[-3:]]
        last3_status = [r["status"] for r in cur[-3:]]
        if len(set(last3)) == 1 and last3[0] != "" and all(s != "keep" for s in last3_status):
            print(f"  \u26a0 STALL: same strategy '{last3[0]}' failed 3x in a row. Switch strategies NOW.")
            print()
        if all(s == "crash" for s in last3_status):
            print(f"  \u26a0 CRASH LOOP: 3 consecutive crashes. Fix the root cause or skip this approach.")
            print()

    # Anti-stop box
    next_run = total + 1
    W = 76
    box = lambda t: f"  \u2551 {t:<{W}} \u2551"
    print(f"  \u2554{'\u2550' * (W + 2)}\u2557")
    print(box(f"NEXT: Run #{next_run}. Make a change, then run the experiment."))
    if next_run % 5 == 0:
        print(box("\u2192 RE-READ autoresearch.md FIRST (multiple-of-5 run)"))
    print(box(""))
    print(box("MANDATORY: Do NOT stop. Do NOT summarize. Do NOT recap."))
    print(box("MANDATORY: NEVER ask 'should I continue?' — JUST CONTINUE."))
    print(box("MANDATORY: If you feel like stopping, re-read autoresearch.md instead."))
    print(f"  \u255a{'\u2550' * (W + 2)}\u255d")
    print()


# ---------------------------------------------------------------------------
# Strategy analysis
# ---------------------------------------------------------------------------

def render_analysis(config, results):
    """Print strategy analysis JSON."""
    if not results:
        print(json.dumps({"hasData": False, "recommendation": "No experiments yet. Start with bold structural changes."}))
        return

    direction = config.get("bestDirection", "lower")
    _, cur = current_segment_results(results)
    consec = count_consecutive_discards(cur)
    strategies = compute_strategies(cur)
    baseline = cur[0]["metric"] if cur else None
    best, best_run = find_best(cur, direction)
    last_keep = None
    for r in cur:
        if r["status"] == "keep" and isinstance(r.get("metric"), (int, float)):
            last_keep = r["metric"]

    # Velocity
    windows = []
    for i in range(0, len(cur), 5):
        windows.append(sum(1 for r in cur[i:i+5] if r["status"] == "keep"))
    velocity = "stable"
    if len(windows) >= 2:
        recent, earlier = windows[-1], sum(windows[:-1]) / len(windows[:-1])
        if recent < earlier * 0.5: velocity = "slowing"
        elif recent > earlier * 1.5: velocity = "accelerating"

    # Dead strategies
    dead = [s for s, st in strategies.items() if st["total"] >= 3 and st["kept"] == 0]

    # Recommendations
    rec = []
    if consec >= 7:
        rec.append("ESCALATE NOW: 7+ consecutive non-keeps. Try a structurally different approach.")
        rec.append("Re-read source files. Profile the workload. The bottleneck is not where you think.")
        rec.append("Consider: rewrite the hot path, change data structures, eliminate entire subsystems.")
    elif consec >= 4:
        rec.append("WARNING: 4+ consecutive non-keeps. Consider pivoting strategy.")
        rec.append("Read the source code again. Your mental model may be stale.")
    if dead:
        rec.append(f"STOP trying: {', '.join(dead)} \u2014 zero wins after 3+ attempts each.")
    if velocity == "slowing":
        rec.append("Improvement velocity is slowing. Try a fundamentally different angle.")

    # Detect crash loops
    crash_count = sum(1 for r in cur[-5:] if r["status"] == "crash") if len(cur) >= 5 else 0
    if crash_count >= 3:
        rec.append("CRASH LOOP: 3+ crashes in last 5 runs. Fix stability before optimizing.")

    # Detect checks_failed loops
    chk_fail_count = sum(1 for r in cur[-5:] if r["status"] == "checks_failed") if len(cur) >= 5 else 0
    if chk_fail_count >= 3:
        rec.append("CHECKS FAILING: 3+ check failures in last 5 runs. Review constraints and test expectations.")

    best_strat, best_wr = None, 0
    for s, st in strategies.items():
        if st["total"] >= 2 and st["win_rate"] > best_wr:
            best_strat, best_wr = s, st["win_rate"]
    if best_strat and best_wr > 0:
        rec.append(f"Best strategy: '{best_strat}' ({best_wr}% win rate). Try more variations.")
    untried = [s for s in COMMON_STRATEGIES if s not in strategies]
    if untried and consec >= 3:
        rec.append(f"Untried categories: {', '.join(untried)}")
    if not rec:
        rec.append("Keep going. Current approach is productive.")

    # Always end with continuation directive
    rec.append("CONTINUE LOOPING. Do not stop. Do not summarize.")

    # Confidence
    conf, nf, method = compute_confidence(cur, direction)
    label = confidence_label(conf)
    if label == "weak" and sum(1 for r in cur if r["status"] == "discard") > 0:
        rec.append("Results may be noise. Re-run or try bolder changes.")
    if label == "deterministic":
        rec.append("Metric is deterministic. Any change is real signal.")

    print(json.dumps({
        "hasData": True, "totalRuns": len(cur),
        "kept": sum(1 for r in cur if r["status"] == "keep"),
        "consecutiveDiscards": consec, "baseline": baseline,
        "best": best, "bestRun": best_run, "lastKeepMetric": last_keep,
        "nextRunNumber": len(cur) + 1, "velocityTrend": velocity,
        "strategies": strategies, "deadStrategies": dead,
        "recommendation": rec,
        "confidence": conf, "noiseFloor": nf,
        "confidenceMethod": method, "confidenceLabel": label,
    }, indent=2))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_init(args):
    if len(args) < 5:
        print("Usage: cli.py init <name> <metric_name> <metric_unit> <direction> <jsonl_path>", file=sys.stderr)
        sys.exit(1)
    name, metric_name, metric_unit, direction, jsonl_path = args[0], args[1], args[2], args[3], args[4]
    if direction not in ("lower", "higher"):
        print(f"ERROR: direction must be 'lower' or 'higher', got '{direction}'", file=sys.stderr)
        sys.exit(1)
    entry = {
        "type": "config", "name": name, "metricName": metric_name,
        "metricUnit": metric_unit, "bestDirection": direction,
        "timestamp": int(time.time() * 1000),
    }
    if os.path.isfile(jsonl_path) and os.path.getsize(jsonl_path) > 0:
        append_jsonl(jsonl_path, entry)
        print(f'Re-initialized experiment: "{name}" (new segment appended)')
    else:
        write_jsonl(jsonl_path, entry)
        print(f'Initialized experiment: "{name}"')
    print(f"Metric: {metric_name} ({metric_unit}, {direction} is better)")


def _run_subprocess(command, timeout, cwd, label="benchmark"):
    """Run a subprocess with proper process group cleanup on timeout."""
    start = time.time()
    proc = None
    try:
        proc = subprocess.Popen(
            ["bash", "-c", command],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=cwd,
            preexec_fn=os.setsid,  # New process group for clean cleanup
        )
        stdout, stderr = proc.communicate(timeout=timeout)
        duration = time.time() - start
        output = (stdout + "\n" + stderr).strip()
        return proc.returncode, duration, output, False
    except subprocess.TimeoutExpired:
        duration = time.time() - start
        if proc:
            _kill_process_tree(proc)
            try:
                proc.communicate(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                pass
        return -1, duration, f"TIMEOUT after {timeout}s ({label})", True
    except OSError as e:
        duration = time.time() - start
        return -1, duration, f"OS error running {label}: {e}", False


def cmd_run(args):
    if len(args) < 1:
        print("Usage: cli.py run <command> [timeout] [work_dir] [checks_timeout]", file=sys.stderr)
        sys.exit(1)
    command = args[0]
    timeout = int(args[1]) if len(args) > 1 else 600
    work_dir = args[2] if len(args) > 2 else "."
    checks_timeout = int(args[3]) if len(args) > 3 else 300
    cwd = work_dir if work_dir != "." else None

    # Run benchmark
    exit_code, duration, output, timed_out = _run_subprocess(command, timeout, cwd, "benchmark")
    tail = "\n".join(output.split("\n")[-10:])
    metrics = [l for l in output.split("\n") if l.startswith("METRIC ")]

    print(f"EXIT_CODE={exit_code}")
    print(f"DURATION={duration:.3f}")
    print(f"TIMED_OUT={'true' if timed_out else 'false'}")
    print("---OUTPUT_START---")
    print(tail)
    print("---OUTPUT_END---")
    for m in metrics:
        print(m)

    # Checks
    checks_file = os.path.join(work_dir, "autoresearch.checks.sh")
    if exit_code == 0 and not timed_out and os.path.isfile(checks_file):
        print("\n--- RUNNING CHECKS ---")
        chk_code, chk_dur, chk_out, chk_timeout = _run_subprocess(
            f"bash {checks_file}", checks_timeout, cwd, "checks"
        )
        tail_c = "\n".join(chk_out.split("\n")[-10:])
        print(f"CHECKS_EXIT={chk_code}")
        print(f"CHECKS_DURATION={chk_dur:.3f}")
        print(f"CHECKS_TIMED_OUT={'true' if chk_timeout else 'false'}")
        print("---CHECKS_OUTPUT_START---")
        print(tail_c)
        print("---CHECKS_OUTPUT_END---")
    else:
        print("\nCHECKS_EXIT=skipped")


def cmd_log(args):
    if len(args) < 6:
        print("Usage: cli.py log <jsonl_path> <run> <commit> <metric> <status> <desc> [segment] [work_dir] [metrics_json] [strategy]", file=sys.stderr)
        sys.exit(1)
    jsonl_path, run_num = args[0], int(args[1])
    commit, metric = args[2], float(args[3])
    status, description = args[4], args[5]
    segment = int(args[6]) if len(args) > 6 else 0
    work_dir = args[7] if len(args) > 7 else "."
    metrics_json = args[8] if len(args) > 8 else "{}"
    strategy = args[9] if len(args) > 9 else ""

    valid = ("keep", "discard", "crash", "checks_failed")
    if status not in valid:
        print(f"ERROR: status must be {'|'.join(valid)}, got '{status}'", file=sys.stderr)
        sys.exit(1)

    try:
        metrics = json.loads(metrics_json)
    except (json.JSONDecodeError, ValueError):
        metrics = {}

    entry = {
        "run": run_num, "commit": commit, "metric": metric,
        "metrics": metrics, "status": status, "description": description,
        "timestamp": int(time.time() * 1000), "segment": segment,
    }
    if strategy:
        entry["strategy"] = strategy

    append_jsonl(jsonl_path, entry)
    print(f"Logged #{run_num}: {status} \u2014 {description} (metric={metric})")

    # Git
    if status == "keep":
        git_commit(work_dir, description, metric)
    else:
        git_revert(work_dir)
        print(f"Git: reverted changes ({status}) \u2014 autoresearch files preserved")

    # Auto-show dashboard
    if os.path.isfile(jsonl_path):
        config, results = read_jsonl(jsonl_path)
        render_dashboard(config, results)


def cmd_state(args):
    if len(args) < 1:
        print("Usage: cli.py state <jsonl_path>", file=sys.stderr)
        sys.exit(1)
    jsonl_path = args[0]
    if not os.path.isfile(jsonl_path):
        print('{"error":"autoresearch.jsonl not found","exists":false}')
        sys.exit(0)
    config, results = read_jsonl(jsonl_path)
    print(json.dumps(compute_full_state(config, results), indent=2))


def cmd_dashboard(args):
    if len(args) < 1:
        print("Usage: cli.py dashboard <jsonl_path>", file=sys.stderr)
        sys.exit(1)
    jsonl_path = args[0]
    if not os.path.isfile(jsonl_path):
        print("No experiments yet (autoresearch.jsonl not found)")
        sys.exit(0)
    config, results = read_jsonl(jsonl_path)
    render_dashboard(config, results)


def cmd_analyze(args):
    if len(args) < 1:
        print("Usage: cli.py analyze <jsonl_path>", file=sys.stderr)
        sys.exit(1)
    jsonl_path = args[0]
    if not os.path.isfile(jsonl_path):
        print('{"error":"autoresearch.jsonl not found","hasData":false}')
        sys.exit(0)
    config, results = read_jsonl(jsonl_path)
    render_analysis(config, results)


def cmd_baseline(args):
    """Run benchmark N times, compute variance, log baselines, report threshold."""
    if len(args) < 3:
        print("Usage: cli.py baseline <jsonl_path> <command> <work_dir> [runs] [timeout] [metric_name]", file=sys.stderr)
        sys.exit(1)
    jsonl_path = args[0]
    command = args[1]
    work_dir = args[2]
    runs = int(args[3]) if len(args) > 3 else 3
    timeout = int(args[4]) if len(args) > 4 else 600
    metric_name = args[5] if len(args) > 5 else None

    if not os.path.isfile(jsonl_path):
        print(f"ERROR: {jsonl_path} not found. Run 'init' first.", file=sys.stderr)
        sys.exit(1)

    config, _ = read_jsonl(jsonl_path)
    if not metric_name:
        metric_name = config.get("metricName", "metric")
    direction = config.get("bestDirection", "lower")
    unit = config.get("metricUnit", "")

    print(f"Running {runs} baseline measurements...")
    print(f"Metric: {metric_name} ({unit}, {direction} is better)")
    print()

    values = []
    for i in range(1, runs + 1):
        print(f"--- Baseline run {i}/{runs} ---")
        cwd = work_dir if work_dir != "." else None
        exit_code, duration, output, timed_out = _run_subprocess(command, timeout, cwd, f"baseline {i}")

        if timed_out:
            print(f"  TIMEOUT after {timeout}s")
            continue
        if exit_code != 0:
            tail = "\n".join(output.split("\n")[-5:])
            print(f"  CRASH (exit {exit_code}): {tail}")
            continue

        metrics = [l for l in output.split("\n") if l.startswith("METRIC ")]
        value = None
        for m in metrics:
            parts = m.split("=", 1)
            if len(parts) == 2:
                name = parts[0].replace("METRIC ", "").strip()
                if name == metric_name:
                    try:
                        value = float(parts[1].strip())
                    except ValueError:
                        pass
        if value is not None:
            values.append(value)
            print(f"  {metric_name} = {fmt(value, unit)} ({duration:.1f}s)")
        else:
            print(f"  WARNING: no METRIC {metric_name}=... found in output")

    if len(values) < 2:
        print(f"\nERROR: need at least 2 successful runs, got {len(values)}", file=sys.stderr)
        sys.exit(1)

    # Compute variance
    values_sorted = sorted(values)
    n = len(values_sorted)
    median_val = values_sorted[n // 2] if n % 2 else (values_sorted[n // 2 - 1] + values_sorted[n // 2]) / 2
    spread = values_sorted[-1] - values_sorted[0]
    variance_pct = (spread / median_val * 100) if median_val != 0 else 0
    threshold_pct = variance_pct * 2
    threshold_val = spread * 2

    # Log baseline runs
    for i, v in enumerate(values):
        commit = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"],
            cwd=work_dir, capture_output=True, text=True,
        ).stdout.strip()
        entry = {
            "run": i + 1, "commit": commit, "metric": v,
            "metrics": {}, "status": "keep", "description": f"baseline {i+1}/{len(values)}",
            "timestamp": int(time.time() * 1000), "segment": 0,
        }
        append_jsonl(jsonl_path, entry)

    # Report
    print(f"\n{'=' * 50}")
    print(f"  Baseline Results ({len(values)} runs)")
    print(f"{'=' * 50}")
    print(f"  Values: {', '.join(fmt(v, unit) for v in values)}")
    print(f"  Median: {fmt(median_val, unit)}")
    print(f"  Range:  {fmt(values_sorted[0], unit)} — {fmt(values_sorted[-1], unit)}")
    print(f"  Variance: {variance_pct:.2f}%")
    print(f"  Significance threshold: {threshold_pct:.2f}% ({fmt(threshold_val, unit)})")
    if variance_pct >= 5:
        print(f"  High variance — escalation after 7 consecutive discards")
    else:
        print(f"  Low variance — escalation after 4 consecutive discards")
    print(f"  Next run: #{len(values) + 1}")
    print()

    # Output JSON summary for easy parsing
    print(json.dumps({
        "baselineRuns": len(values),
        "values": values,
        "median": median_val,
        "min": values_sorted[0],
        "max": values_sorted[-1],
        "variancePct": round(variance_pct, 2),
        "thresholdPct": round(threshold_pct, 2),
        "thresholdAbsolute": round(threshold_val, 6),
        "nextRunNumber": len(values) + 1,
        "highVariance": variance_pct >= 5,
    }, indent=2))


def cmd_recover(args):
    """Diagnose and fix inconsistent experiment state."""
    if len(args) < 1:
        print("Usage: cli.py recover <jsonl_path> [work_dir]", file=sys.stderr)
        sys.exit(1)
    jsonl_path = args[0]
    work_dir = args[1] if len(args) > 1 else "."
    issues = []
    fixes = []

    # Check JSONL exists and is readable
    if not os.path.isfile(jsonl_path):
        print('{"status":"no_file","message":"No JSONL file found. Run init to start."}')
        return

    # Check for truncated/corrupt JSONL
    good_lines = 0
    bad_lines = 0
    with open(jsonl_path) as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                json.loads(line)
                good_lines += 1
            except json.JSONDecodeError:
                bad_lines += 1
                issues.append(f"Corrupt line {i}")

    if bad_lines > 0:
        issues.append(f"{bad_lines} corrupt lines (will be skipped automatically)")

    # Check git state
    try:
        git_status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=work_dir,
            capture_output=True, text=True, timeout=10,
        )
        dirty_files = [l for l in git_status.stdout.strip().split("\n") if l.strip()]
        if dirty_files:
            # Only flag files that aren't autoresearch-managed
            protected_basenames = set(PROTECTED_FILES)
            non_protected = []
            for f in dirty_files:
                # git status format: "XY filename" — extract just the filename
                fname = f[3:].strip().strip('"')
                basename = os.path.basename(fname)
                if basename not in protected_basenames:
                    non_protected.append(f)
            if non_protected:
                issues.append(f"{len(non_protected)} uncommitted non-autoresearch files (leftover from failed revert?)")
                fixes.append("Run: git checkout -- . && git clean -fd (will preserve autoresearch files)")
    except (subprocess.TimeoutExpired, OSError):
        issues.append("Could not check git status")

    # Check JSONL/state consistency
    config, results = read_jsonl(jsonl_path)
    if not config:
        issues.append("No config entry found — JSONL may be corrupt")
        fixes.append("Run: cli.py init to re-initialize")

    # Check for orphaned backup files
    for f in os.listdir(work_dir):
        if f.startswith(".autoresearch-") and f.endswith(".tmp"):
            issues.append(f"Orphaned temp file: {f}")
            fixes.append(f"Remove: {os.path.join(work_dir, f)}")

    status = "healthy" if not issues else "issues_found"
    result = {
        "status": status,
        "goodLines": good_lines,
        "badLines": bad_lines,
        "issues": issues,
        "fixes": fixes,
        "message": "No issues found. Experiment is healthy." if not issues else f"{len(issues)} issue(s) found. See fixes.",
    }
    if config:
        result["nextRunNumber"] = compute_full_state(config, results).get("nextRunNumber", 1)

    print(json.dumps(result, indent=2))


def cmd_history(args):
    """Dump all experiment results in a concise format."""
    if len(args) < 1:
        print("Usage: cli.py history <jsonl_path>", file=sys.stderr)
        sys.exit(1)
    jsonl_path = args[0]
    if not os.path.isfile(jsonl_path):
        print("No experiments yet (autoresearch.jsonl not found)")
        sys.exit(0)

    config, results = read_jsonl(jsonl_path)
    unit = config.get("metricUnit", "")
    metric_name = config.get("metricName", "metric")
    direction = config.get("bestDirection", "lower")

    if not results:
        print("No experiments logged yet.")
        return

    _, cur = current_segment_results(results)
    baseline = cur[0]["metric"] if cur else None

    print(f"\n{'=' * 88}")
    print(f"  Full History: {config.get('name', 'autoresearch')} ({len(cur)} runs)")
    print(f"{'=' * 88}")
    print(f"  {'#':>3}  {'commit':<9} {'* ' + metric_name:<14} {'delta':>8}  {'status':<15} {'strategy':<12} {'description'}")
    print(f"  {'\u2500' * 84}")

    for i, r in enumerate(cur):
        idx = i + 1
        commit = r.get("commit", "?")[:7]
        metric = fmt(r["metric"], unit)
        d = delta_pct(r["metric"], baseline) if idx > 1 else "baseline"
        st = {
            "keep": "\u2713 keep", "discard": "\u2013 discard",
            "crash": "\u2717 crash", "checks_failed": "\u26a0 chk_fail",
        }.get(r["status"], r["status"])
        strat = r.get("strategy", "")[:11]
        desc = r.get("description", "")[:30]
        print(f"  {idx:>3}  {commit:<9} {metric:<14} {d:>8}  {st:<15} {strat:<12} {desc}")

    print(f"  {'\u2500' * 84}")
    best, best_run = find_best(cur, direction)
    print(f"  Baseline: {fmt(baseline, unit)} | Best: {fmt(best, unit)} #{best_run} ({delta_pct(best, baseline)})")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

COMMANDS = {
    "init": cmd_init,
    "run": cmd_run,
    "log": cmd_log,
    "state": cmd_state,
    "dashboard": cmd_dashboard,
    "analyze": cmd_analyze,
    "baseline": cmd_baseline,
    "history": cmd_history,
    "recover": cmd_recover,
}

def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"Usage: cli.py <{'|'.join(COMMANDS)}> [args...]", file=sys.stderr)
        sys.exit(1)
    COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
