"""Quality Engineer (QE) agent — monitors the research agent for quality issues."""

import json
import os
import sys
import time
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

from costs import get_total_usd, record_call
from llm import get_client, resolve_model, completions_with_retry

ROOT = Path(__file__).parent


def _qe_model() -> str:
    """Return the model name from config.yaml (same model the research agent uses)."""
    cfg_path = ROOT / "config.yaml"
    if cfg_path.exists():
        try:
            import yaml
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            return cfg.get("model", "gemini-2.5-flash")
        except Exception:
            pass
    return "gemini-2.5-flash"


def _qe_pricing() -> dict:
    """Return pricing from config.yaml."""
    cfg_path = ROOT / "config.yaml"
    if cfg_path.exists():
        try:
            import yaml
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            p = cfg.get("pricing", {})
            if p:
                return p
        except Exception:
            pass
    return {"input_per_mtok": 1.25, "output_per_mtok": 10.00}


# ---------------------------------------------------------------------------
# Deterministic checks (no LLM calls)
# ---------------------------------------------------------------------------

def detect_stalls(cycles: list[dict], threshold: int = 3) -> list[dict]:
    """Detect 3+ consecutive reflect cycles with no applied output."""
    issues = []
    if len(cycles) < threshold:
        return issues
    tail = cycles[-threshold:]
    if all(c.get("action") == "reflect" and not c.get("applied") for c in tail):
        issues.append({
            "type": "qe_stall",
            "severity": "warning" if threshold <= 3 else "critical",
            "message": f"{threshold} consecutive reflect cycles with no written output — agent may be stuck",
            "suggestion": "Force a web_search + append_to_file action next cycle",
        })
    return issues


def detect_budget_waste(cycles: list[dict], threshold_usd: float = 0.30) -> list[dict]:
    """High cost with no applied output in last 3 cycles."""
    issues = []
    if len(cycles) < 3:
        return issues
    window = cycles[-3:]
    any_applied = any(c.get("applied") for c in window)
    if not any_applied:
        cost_start = window[0].get("cost_after", 0)
        cost_end = window[-1].get("cost_after", 0)
        burn = cost_end - cost_start
        if burn >= threshold_usd:
            issues.append({
                "type": "qe_budget_waste",
                "severity": "critical",
                "message": f"${burn:.2f} spent over last 3 cycles with 0 applied patches",
                "suggestion": "Produce written output immediately — append findings to the current phase file",
            })
    return issues


def detect_duplicates(research_dir: Path, similarity_threshold: float = 0.85) -> list[dict]:
    """Find near-duplicate paragraphs across research files."""
    issues = []
    if not research_dir.exists():
        return issues

    paragraphs: list[tuple[str, str]] = []  # (filename, text)
    for f in research_dir.glob("*.md"):
        try:
            content = f.read_text(encoding="utf-8")
        except Exception:
            continue
        for para in content.split("\n\n"):
            stripped = para.strip()
            if len(stripped) > 100:
                paragraphs.append((f.name, stripped))

    seen = set()
    for i in range(len(paragraphs)):
        for j in range(i + 1, len(paragraphs)):
            if paragraphs[i][0] == paragraphs[j][0] and i == j - 1:
                continue  # skip adjacent paragraphs in same file
            key = (min(i, j), max(i, j))
            if key in seen:
                continue
            ratio = SequenceMatcher(None, paragraphs[i][1], paragraphs[j][1]).ratio()
            if ratio >= similarity_threshold:
                seen.add(key)
                issues.append({
                    "type": "qe_duplicate",
                    "severity": "warning",
                    "message": (
                        f"Near-duplicate paragraphs: {paragraphs[i][0]} ↔ {paragraphs[j][0]} "
                        f"(similarity {ratio:.0%})"
                    ),
                    "suggestion": "Remove or merge the duplicate content",
                })
                if len(issues) >= 5:
                    return issues
    return issues


def detect_shrinkage(research_dir: Path, snapshot_path: Path) -> list[dict]:
    """Detect files that got significantly smaller (content overwritten)."""
    issues = []
    if not snapshot_path.exists():
        return issues
    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except Exception:
        return issues

    for f in research_dir.glob("*.md"):
        current_size = f.stat().st_size
        prev_size = snapshot.get(f.name, 0)
        if prev_size > 500 and current_size < prev_size * 0.7:
            issues.append({
                "type": "qe_shrinkage",
                "severity": "critical",
                "message": (
                    f"{f.name} shrank from {prev_size:,} to {current_size:,} bytes "
                    f"({(1 - current_size / prev_size):.0%} loss)"
                ),
                "suggestion": "Check git log — content may have been overwritten. Restore and use append_to_file",
            })
    return issues


def check_phase_alignment(cycles: list[dict], config: dict) -> list[dict]:
    """Check if recent cycles are writing to files that belong to the current phase."""
    issues = []
    buckets = config.get("research_buckets", [])
    if not buckets:
        return issues

    # Determine current phase by finding the first incomplete bucket
    # (simplified: check which bucket files recent cycles reference)
    recent = cycles[-3:] if len(cycles) >= 3 else cycles
    mentioned_files = set()
    for c in recent:
        summary = (c.get("summary", "") + " " + c.get("action", "")).lower()
        for b in buckets:
            for fname in b.get("files", []):
                if fname.lower().replace(".md", "") in summary:
                    mentioned_files.add(fname)

    if not mentioned_files:
        return issues

    # Check if any mentioned file doesn't belong to the current phase
    # Current phase = first bucket with incomplete markers
    current_bucket_files = set()
    for b in buckets:
        current_bucket_files = set(b.get("files", []))
        break  # Just use first bucket as approximation; real check needs marker counts

    off_phase = mentioned_files - current_bucket_files
    if off_phase and current_bucket_files:
        issues.append({
            "type": "qe_phase_misalign",
            "severity": "warning",
            "message": f"Recent cycles reference files outside current phase: {', '.join(off_phase)}",
            "suggestion": "Focus on the current phase's target files before moving on",
        })
    return issues


def check_citation_density(research_dir: Path, min_ratio: float = 0.05) -> list[dict]:
    """Flag research files with very few citations/source attributions."""
    issues = []
    if not research_dir.exists():
        return issues

    url_indicators = ["http://", "https://", "source:", "according to", "cited from", "reference:"]

    for f in research_dir.glob("*.md"):
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        if len(lines) < 20:
            continue
        content_lines = [l for l in lines if l.strip() and not l.startswith("#")]
        if not content_lines:
            continue
        cited = sum(1 for l in content_lines if any(ind in l.lower() for ind in url_indicators))
        ratio = cited / len(content_lines) if content_lines else 0
        if ratio < min_ratio:
            issues.append({
                "type": "qe_citation",
                "severity": "warning",
                "message": (
                    f"{f.name}: only {cited}/{len(content_lines)} lines have citations "
                    f"({ratio:.0%}) — below {min_ratio:.0%} threshold"
                ),
                "suggestion": "Add source URLs or attribution for key claims",
            })
    return issues


def check_marker_progression(state: dict, config: dict, research_dir: Path) -> list[dict]:
    """Check if completion markers haven't increased across QE runs."""
    issues = []
    buckets = config.get("research_buckets", [])
    if not buckets:
        return issues

    prev_markers = state.get("last_markers", {})
    current_markers = {}
    stall_counts = state.get("marker_stall_counts", {})

    for b in buckets:
        marker = b.get("completion_marker", "")
        if not marker:
            continue
        count = 0
        for fname in b.get("files", []):
            fpath = research_dir / fname
            if fpath.exists():
                try:
                    count += fpath.read_text(encoding="utf-8").count(marker)
                except Exception:
                    pass
        current_markers[marker] = count

        prev = prev_markers.get(marker, 0)
        if count > 0 and count == prev:
            stall_counts[marker] = stall_counts.get(marker, 0) + 1
        else:
            stall_counts[marker] = 0

        if stall_counts.get(marker, 0) >= 3:
            issues.append({
                "type": "qe_marker_stall",
                "severity": "warning",
                "message": (
                    f"Marker '{marker}' stuck at {count} for 3+ QE runs — "
                    f"phase '{b.get('name', '?')}' not progressing"
                ),
                "suggestion": "Research agent should add more sections with the completion marker",
            })

    state["last_markers"] = current_markers
    state["marker_stall_counts"] = stall_counts
    return issues


# ---------------------------------------------------------------------------
# LLM-based check
# ---------------------------------------------------------------------------

def check_mission_alignment(research_dir: Path, mission_path: Path, costs_file: Path,
                            run_number: int) -> list[dict]:
    """Use a cheap LLM call to check if content addresses the mission's key questions."""
    issues = []
    if not mission_path.exists() or not research_dir.exists():
        return issues

    try:
        mission = mission_path.read_text(encoding="utf-8").strip()
    except Exception:
        return issues

    # Gather research content (truncated for cost)
    research_snippets = []
    for f in sorted(research_dir.glob("*.md")):
        try:
            text = f.read_text(encoding="utf-8")
            # Take first 500 chars of each file
            research_snippets.append(f"### {f.name}\n{text[:500]}")
        except Exception:
            continue

    if not research_snippets:
        return issues

    research_sample = "\n\n".join(research_snippets)[:3000]

    prompt = (
        "You are a quality reviewer. Compare the research content against the mission.\n"
        "If the research is significantly off-topic or missing key questions from the mission, "
        "respond with a single sentence describing the gap. "
        "If the research is on-track, respond with just: ON_TRACK\n\n"
        f"## Mission\n{mission[:1000]}\n\n"
        f"## Research Sample\n{research_sample}"
    )

    try:
        client = get_client()
        model = resolve_model(_qe_model())
        resp = completions_with_retry(
            client,
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
            temperature=0.1,
        )
        answer = resp.choices[0].message.content.strip()

        # Record cost
        usage = resp.usage
        if usage:
            record_call(
                input_tokens=usage.prompt_tokens or 0,
                output_tokens=usage.completion_tokens or 0,
                pricing=_qe_pricing(),
                label=f"qe_check_{run_number}",
                costs_file=costs_file,
            )

        if "ON_TRACK" not in answer.upper():
            issues.append({
                "type": "qe_mission",
                "severity": "warning",
                "message": f"Mission alignment concern: {answer[:200]}",
                "suggestion": "Re-read mission.md and refocus research on the key questions",
            })
    except Exception as e:
        print(f"  [QE] Mission alignment check failed: {e}")

    return issues


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_quality_check(topic_name: str, state: dict) -> dict:
    """Run all QE checks and return a structured report."""
    from agent import load_agent_config, resolve_topic_paths

    config = load_agent_config(topic_name)
    paths = resolve_topic_paths(config)
    research_dir = paths["research_dir"]
    qe_dir = paths["data_dir"] / "qe"
    qe_dir.mkdir(parents=True, exist_ok=True)

    # Load cycles
    cycles_file = paths["cycles_file"]
    cycles: list[dict] = []
    if cycles_file.exists():
        try:
            lines = [l for l in cycles_file.read_text(encoding="utf-8").strip().split("\n") if l]
            cycles = [json.loads(l) for l in lines]
        except Exception:
            pass

    run_number = state.get("run_count", 0) + 1
    snapshot_path = qe_dir / "file_sizes.json"

    all_issues: list[dict] = []

    # Deterministic checks
    all_issues.extend(detect_stalls(cycles))
    all_issues.extend(detect_budget_waste(cycles))
    all_issues.extend(detect_duplicates(research_dir))
    all_issues.extend(detect_shrinkage(research_dir, snapshot_path))
    all_issues.extend(check_phase_alignment(cycles, config))
    all_issues.extend(check_citation_density(research_dir))
    all_issues.extend(check_marker_progression(state, config, research_dir))

    # LLM check (only every 3rd run to save budget)
    if run_number % 3 == 1:
        all_issues.extend(check_mission_alignment(
            research_dir, paths["mission"], paths["costs_file"], run_number
        ))

    # Build report
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "qe_run": run_number,
        "topic": topic_name,
        "cycle_count": len(cycles),
        "issues": all_issues,
        "issue_count": len(all_issues),
    }

    state["run_count"] = run_number
    state["last_cycle_checked"] = len(cycles)

    return report


def synthesize_feedback(report: dict, config: dict, costs_file: Path) -> str:
    """Single LLM call to produce actionable feedback for the research agent."""
    issues = report.get("issues", [])
    if not issues:
        return ""

    issue_text = "\n".join(
        f"- [{i['severity'].upper()}] {i['type']}: {i['message']}"
        for i in issues
    )

    prompt = (
        "You are a quality engineer reviewing an autonomous research agent.\n"
        "Given these quality issues, write a numbered action list (max 300 words) "
        "the research agent should follow in its next cycles to fix these problems.\n"
        "Be direct and specific. Do not repeat the issues — give actionable fixes.\n\n"
        f"Issues found:\n{issue_text}"
    )

    try:
        client = get_client()
        model = resolve_model(_qe_model())
        resp = completions_with_retry(
            client,
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
            temperature=0.2,
        )
        feedback = resp.choices[0].message.content.strip()

        usage = resp.usage
        if usage:
            record_call(
                input_tokens=usage.prompt_tokens or 0,
                output_tokens=usage.completion_tokens or 0,
                pricing=_qe_pricing(),
                label=f"qe_feedback_{report['qe_run']}",
                costs_file=costs_file,
            )

        return feedback
    except Exception as e:
        print(f"  [QE] Feedback synthesis failed: {e}")
        # Fallback: just list suggestions
        return "\n".join(
            f"{idx+1}. {i.get('suggestion', i['message'])}"
            for idx, i in enumerate(issues)
        )


def log_issues(qe_dir: Path, issues: list[dict], run_number: int, state: dict):
    """Append issues to issues.jsonl. Write resolution entries for cleared issues."""
    issues_file = qe_dir / "issues.jsonl"
    now = datetime.now(timezone.utc).isoformat()

    # Current issue types in this run
    current_types = {i["type"] for i in issues}

    # Previously open issue types
    prev_open = set(state.get("open_issue_types", []))

    entries = []

    # Write new/continuing issues
    for i in issues:
        entries.append({
            "timestamp": now,
            "qe_run": run_number,
            "type": i["type"],
            "severity": i["severity"],
            "message": i["message"],
            "status": "open",
        })

    # Write resolution entries for issues that cleared
    resolved = prev_open - current_types
    for t in resolved:
        entries.append({
            "timestamp": now,
            "qe_run": run_number,
            "type": t,
            "severity": "info",
            "message": f"PASS — {t.replace('qe_', '')} issue resolved",
            "status": "resolved",
        })

    # Append to file
    if entries:
        with open(issues_file, "a", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

    # Update state
    state["open_issue_types"] = list(current_types)


def _save_file_snapshot(research_dir: Path, snapshot_path: Path):
    """Save current file sizes for shrinkage detection."""
    sizes = {}
    if research_dir.exists():
        for f in research_dir.glob("*.md"):
            sizes[f.name] = f.stat().st_size
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(json.dumps(sizes, indent=2), encoding="utf-8")


def _load_state(qe_dir: Path) -> dict:
    state_path = qe_dir / "state.json"
    if state_path.exists():
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_cycle_checked": 0, "run_count": 0, "open_issue_types": [], "last_markers": {}, "marker_stall_counts": {}}


def _save_state(qe_dir: Path, state: dict):
    state_path = qe_dir / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def run_qe_loop(topic_name: str, interval: int = 90, cycle_threshold: int = 3):
    """Main QE loop — runs until research agent stops or QE budget exceeded."""
    from agent import load_agent_config, resolve_topic_paths

    print(f"[QE] Starting quality engineer for '{topic_name}' (interval={interval}s, threshold={cycle_threshold})")

    config = load_agent_config(topic_name)
    paths = resolve_topic_paths(config)
    qe_dir = paths["data_dir"] / "qe"
    qe_dir.mkdir(parents=True, exist_ok=True)

    # Load QE config
    agent_cfg_path = ROOT / "topics" / topic_name / "agent_config.yaml"
    agent_cfg = {}
    if agent_cfg_path.exists():
        try:
            import yaml
            agent_cfg = yaml.safe_load(agent_cfg_path.read_text(encoding="utf-8")) or {}
        except Exception:
            pass
    qe_cfg = agent_cfg.get("qe", {})
    max_budget = qe_cfg.get("max_budget_usd", 1.00)

    state = _load_state(qe_dir)

    while True:
        time.sleep(interval)

        # Check if cycles file has enough new cycles
        cycles_file = paths["cycles_file"]
        current_cycle_count = 0
        if cycles_file.exists():
            try:
                lines = [l for l in cycles_file.read_text(encoding="utf-8").strip().split("\n") if l]
                current_cycle_count = len(lines)
            except Exception:
                pass

        last_checked = state.get("last_cycle_checked", 0)
        if current_cycle_count - last_checked < cycle_threshold:
            # Not enough new cycles yet; check if agent is still running
            # (Look for agent.log being recently modified)
            log_path = paths["data_dir"] / "agent.log"
            if log_path.exists():
                age = time.time() - log_path.stat().st_mtime
                if age > interval * 5:
                    print(f"[QE] Agent log stale ({age:.0f}s old). Stopping QE.")
                    break
            continue

        print(f"[QE] Run {state.get('run_count', 0) + 1}: {current_cycle_count - last_checked} new cycles detected")

        # Check QE budget
        total_spent = get_total_usd(paths["costs_file"])
        qe_spent = _estimate_qe_cost(paths["costs_file"], state.get("run_count", 0))
        if qe_spent >= max_budget:
            print(f"[QE] QE budget exceeded (${qe_spent:.2f} >= ${max_budget:.2f}). Stopping.")
            break

        # Run checks
        report = run_quality_check(topic_name, state)

        # Log issues
        log_issues(qe_dir, report["issues"], report["qe_run"], state)

        # Save report
        report_path = qe_dir / f"report_{report['qe_run']:03d}.json"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

        # Synthesize feedback if there are issues
        feedback_path = paths["data_dir"] / "quality_feedback.md"
        if report["issues"]:
            feedback = synthesize_feedback(report, config, paths["costs_file"])
            if feedback:
                feedback_path.write_text(
                    f"*Auto-generated by QE (run {report['qe_run']}, "
                    f"{datetime.now(timezone.utc).strftime('%H:%M UTC')})*\n\n{feedback}",
                    encoding="utf-8",
                )
                print(f"  [QE] Wrote feedback ({len(report['issues'])} issues)")
        else:
            # Clear feedback if no issues
            if feedback_path.exists():
                feedback_path.write_text(
                    f"*QE run {report['qe_run']}: All checks passed.*",
                    encoding="utf-8",
                )
            print(f"  [QE] All checks passed")

        # Update file snapshot for next run
        _save_file_snapshot(paths["research_dir"], qe_dir / "file_sizes.json")

        # Save state
        _save_state(qe_dir, state)

    print("[QE] Quality engineer stopped.")


def _estimate_qe_cost(costs_file: Path, run_count: int) -> float:
    """Estimate total QE spend by summing qe_* labels in costs."""
    if not costs_file.exists():
        return 0.0
    try:
        ledger = json.loads(costs_file.read_text(encoding="utf-8"))
        return sum(
            c.get("usd", 0)
            for c in ledger.get("calls", [])
            if c.get("label", "").startswith("qe_")
        )
    except Exception:
        return 0.0
