"""Deepshika — self-modifying agent with human-in-the-loop approval."""

import io
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Force UTF-8 stdout/stderr on Windows so click.echo and print never crash
# on non-ASCII characters (emojis, arrows, etc.) from web search results.
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import click
import yaml
from dotenv import load_dotenv

from costs import check_budget, get_summary, get_total_usd, record_call
from llm import get_client, resolve_model
from supervisor import review_proposal
from tools import TOOL_DEFINITIONS, dispatch_tool, reset_web_counters, set_paths, _resolve_file_path

load_dotenv()

ROOT = Path(__file__).parent


def load_config() -> dict:
    cfg_path = ROOT / "config.yaml"
    if cfg_path.exists():
        return yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    return {}


def save_config(config: dict):
    """Save config, preserving comments when only active_topic changes."""
    import re
    cfg_path = ROOT / "config.yaml"
    if cfg_path.exists():
        text = cfg_path.read_text(encoding="utf-8")
        # Try to surgically update active_topic if it exists
        if re.search(r'^active_topic:.*$', text, re.MULTILINE):
            text = re.sub(
                r'^active_topic:.*$',
                f"active_topic: {config.get('active_topic', '')}",
                text, flags=re.MULTILINE,
            )
            cfg_path.write_text(text, encoding="utf-8")
            return
        # Add active_topic at the end
        if "active_topic" in config:
            text = text.rstrip() + f"\n\nactive_topic: {config['active_topic']}\n"
            cfg_path.write_text(text, encoding="utf-8")
            return
    # Fallback: full rewrite
    cfg_path.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False), encoding="utf-8")


def load_agent_config(topic_name: str) -> dict:
    """Merge per-agent config over global config."""
    global_cfg = load_config()
    agent_cfg_path = ROOT / "topics" / topic_name / "agent_config.yaml"
    agent_cfg = yaml.safe_load(agent_cfg_path.read_text(encoding="utf-8")) if agent_cfg_path.exists() else {}
    merged = dict(global_cfg)
    merged["active_topic"] = topic_name
    if "budget" in agent_cfg:
        merged["budget"] = agent_cfg["budget"]
    if "research_buckets" in agent_cfg:
        merged["research_buckets"] = agent_cfg["research_buckets"]
    return merged


SCAFFOLD_META_PROMPT = """\
You are a scaffolding generator for a research agent called Deepshika. Given a topic name and description, produce a JSON object with exactly three keys:

1. "agent_parameters" — a research-mode agent parameters file (15-30 lines). Rules:
   - First line: "You are Deepshika, a research agent investigating: <topic>."
   - State RESEARCH ONLY mode — only create/edit files under data/research/.
   - Mandate web_search and web_fetch usage EVERY cycle — the agent must search for CURRENT data, not rely on training data.
   - List the numbered output files the agent should build.
   - Include a cycle-by-cycle research approach with specific search query suggestions.
   - Mandate append_to_file for growing docs, propose_edit only for new files.
   - Mandate 2000-char limit per tool call (multiple calls if needed).
   - Mandate honesty: name real companies, cite real incidents.
   - Every cycle must produce written output — no reflecting without writing.

2. "mission" — a detailed research mission document (40-80 lines of markdown). Rules:
   - H1 title with the topic name.
   - Framing paragraph (2-4 sentences) explaining what this research is about and why it matters.
   - ## Constraints — 4-6 bullet points scoping the research.
   - ## What To Research — 4-7 H3 subsections, each with 3-6 searchable research questions.
   - ## Output Files (create under data/research/) — list each file with its priority (HIGH/MEDIUM) and a 1-2 sentence description.
   - ## Research Standards — 4-6 bullet points about methodology (web search every cycle, name real companies, brutal honesty, depth over breadth).
   - Optional ## Cycle Guidance — priority order for building the output files.

3. "output_files" — an array of 3-5 objects, each with:
   - "filename": snake_case.md (e.g. "landscape_scan.md")
   - "question": the key question this file answers (1 sentence)
   Files should progress logically: landscape/overview → analysis → synthesis/recommendations.

Return ONLY valid JSON. No markdown fences, no explanation outside the JSON.
"""


def generate_agent_scaffold(name: str, description: str, config: dict, verbose: bool = True) -> dict | None:
    """Call LLM to generate topic-specific scaffold files.

    Returns {"agent_parameters": str, "mission": str, "output_files": [...], "_usage": {...}}
    on success, or None on any failure.
    """
    import re as _re

    # Use a fast/cheap model for scaffolding, not the expensive agent model
    scaffold_model = config.get("scaffold_model", "anthropic/claude-sonnet-4.6")

    try:
        client = get_client()

        user_msg = (
            f"Topic name: {name}\n"
            f"Description: {description}\n\n"
            f"Generate the scaffolding JSON now."
        )

        response = client.chat.completions.create(
            model=resolve_model(scaffold_model),
            max_tokens=4096,
            messages=[
                {"role": "system", "content": SCAFFOLD_META_PROMPT},
                {"role": "user", "content": user_msg},
            ],
        )

        raw = response.choices[0].message.content or ""
        raw = raw.strip()

        if not raw:
            if verbose:
                click.echo("  [scaffold] LLM returned empty response.")
            return None

        # Strip markdown code fences if present
        raw = _re.sub(r"^```(?:json)?\s*\n?", "", raw)
        raw = _re.sub(r"\n?```\s*$", "", raw)

        try:
            result = json.loads(raw)
        except json.JSONDecodeError as e:
            if verbose:
                click.echo(f"  [scaffold] JSON parse error: {e}")
                click.echo(f"  [scaffold] Raw response (first 200 chars): {raw[:200]}")
            return None

        # Validate required keys
        if not all(k in result for k in ("agent_parameters", "mission", "output_files")):
            if verbose:
                click.echo(f"  [scaffold] Missing keys. Got: {list(result.keys())}")
            return None
        if not isinstance(result["output_files"], list) or len(result["output_files"]) < 2:
            if verbose:
                click.echo(f"  [scaffold] output_files invalid (need >= 2, got {len(result.get('output_files', []))})")
            return None

        # Derive research buckets from output_files
        buckets = []
        for i, of in enumerate(result["output_files"], 1):
            fn = of["filename"]
            title = fn.replace("_", " ").replace(".md", "").replace(".txt", "").title()
            buckets.append({
                "name": f"Phase {i}: {title}",
                "files": [fn],
                "notion_page_id": "",
            })
        result["research_buckets"] = buckets

        # Attach usage info
        usage = response.usage
        result["_usage"] = {
            "input_tokens": usage.prompt_tokens,
            "output_tokens": usage.completion_tokens,
        }

        return result

    except Exception as e:
        if verbose:
            click.echo(f"  [scaffold] Error: {e}")
        return None


def _write_static_scaffold(topic_dir: Path, name: str, description: str):
    """Write the generic placeholder scaffold files (fallback when LLM fails)."""
    (topic_dir / "mission.md").write_text(
        f"# Mission: {name}\n\n{description}\n\n"
        f"## Current Mode: RESEARCH ONLY\n\n"
        f"You cannot modify code. Build research files under `data/research/`.\n\n"
        f"## What To Research\n\n(Define your research questions here)\n\n"
        f"## Output Files (create under data/research/)\n\n(Define your output files here)\n",
        encoding="utf-8",
    )
    (topic_dir / "agent_parameters.md").write_text(
        f"You are Deepshika, a research agent investigating: {name}.\n"
        f"You are in RESEARCH ONLY mode — only research files under data/research/.\n"
        f"You have web_search and web_fetch tools — USE THEM EVERY CYCLE.\n"
        f"Read 1-2 of your existing files to understand current state, then SEARCH the web, then WRITE.\n"
        f"EVERY CYCLE MUST PRODUCE WRITTEN OUTPUT — either append_to_file or propose_edit.\n"
        f"Do NOT just reflect. Reflecting without writing is wasting a cycle.\n"
        f"Use `append_to_file` to ADD sections to existing research files (preferred for growing documents).\n"
        f"Use `propose_edit` only when creating a new file.\n"
        f"IMPORTANT: Keep each tool call's content under 2000 characters. If you have more to write,\n"
        f"make multiple append_to_file calls in the same cycle. This prevents JSON formatting errors.\n"
        f"Be brutally honest. Name real companies, cite real incidents.\n",
        encoding="utf-8",
    )


def resolve_topic_paths(config: dict) -> dict:
    """Return a dict of all paths for the active topic."""
    topic = config.get("active_topic")
    if topic:
        base = ROOT / "topics" / topic
        return {
            "base": base,
            "mission": base / "mission.md",
            "agent_parameters": base / "agent_parameters.md",
            "identity": base / "identity.json",
            "data_dir": base / "data",
            "research_dir": base / "data" / "research",
            "costs_file": base / "data" / "costs.json",
            "cycles_file": base / "data" / "cycles.jsonl",
            "patches_dir": base / "data" / "pending_patches",
        }
    # Legacy fallback — no active_topic set
    return {
        "base": ROOT,
        "mission": ROOT / "MISSION.md",
        "agent_parameters": None,
        "identity": ROOT / "data" / "identity.json",
        "data_dir": ROOT / "data",
        "research_dir": ROOT / "data" / "research",
        "costs_file": ROOT / "data" / "costs.json",
        "cycles_file": ROOT / "data" / "cycles.jsonl",
        "patches_dir": ROOT / "data" / "pending_patches",
    }


def load_agent_parameters(paths: dict) -> str:
    """Load agent parameters from topic file, or return a generic fallback."""
    sp_file = paths.get("agent_parameters")
    if sp_file and sp_file.exists():
        return sp_file.read_text(encoding="utf-8").strip()
    return "You are a research agent. Follow your mission file."


def load_identity(paths: dict = None) -> dict:
    identity_file = paths["identity"] if paths else ROOT / "data" / "identity.json"
    if identity_file.exists():
        return json.loads(identity_file.read_text(encoding="utf-8"))
    return {"name": "Deepshika", "version": "0.0.0"}


def save_identity(identity: dict, paths: dict = None):
    identity_file = paths["identity"] if paths else ROOT / "data" / "identity.json"
    identity_file.parent.mkdir(parents=True, exist_ok=True)
    identity_file.write_text(json.dumps(identity, indent=2), encoding="utf-8")


def bump_version(identity: dict) -> str:
    """Bump patch version: 0.1.0 -> 0.1.1."""
    parts = identity.get("version", "0.1.0").split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    new_version = ".".join(parts)
    identity["version"] = new_version
    return new_version


def log_cycle(cycle_data: dict, paths: dict = None):
    """Append a cycle record to the JSONL log."""
    data_dir = paths["data_dir"] if paths else ROOT / "data"
    cycles_file = paths["cycles_file"] if paths else ROOT / "data" / "cycles.jsonl"
    data_dir.mkdir(parents=True, exist_ok=True)
    with open(cycles_file, "a") as f:
        f.write(json.dumps(cycle_data) + "\n")


def get_cycle_count(paths: dict = None) -> int:
    cycles_file = paths["cycles_file"] if paths else ROOT / "data" / "cycles.jsonl"
    if not cycles_file.exists():
        return 0
    return sum(1 for _ in open(cycles_file))


def get_recent_cycles(n: int = 5, paths: dict = None) -> list[dict]:
    cycles_file = paths["cycles_file"] if paths else ROOT / "data" / "cycles.jsonl"
    if not cycles_file.exists():
        return []
    lines = [l for l in cycles_file.read_text(encoding="utf-8").strip().split("\n") if l]
    return [json.loads(l) for l in lines[-n:]]


def git_run(*args, check=True) -> str:
    """Run a git command in the project root."""
    result = subprocess.run(
        ["git"] + list(args),
        capture_output=True, text=True, cwd=str(ROOT),
        check=False,
    )
    if check and result.returncode != 0:
        return f"git error: {result.stderr.strip()}"
    return result.stdout.strip()


def git_log_short(n: int = 5) -> str:
    return git_run("log", "--oneline", f"-{n}", check=False)


def file_listing() -> str:
    """List modifiable files with sizes."""
    config = load_config()
    lines = []
    for f in config.get("modifiable_files", []):
        path = ROOT / f
        if path.exists():
            size = path.stat().st_size
            lines.append(f"  {f} ({size} bytes)")
        else:
            lines.append(f"  {f} (not found)")
    return "\n".join(lines)


# --- Core cycle ---


def load_mission(paths: dict = None) -> str:
    """Load the mission file."""
    mission_path = paths["mission"] if paths else ROOT / "MISSION.md"
    if mission_path.exists():
        return mission_path.read_text(encoding="utf-8")
    return ""


def list_research_files(paths: dict = None) -> str:
    """List existing research files with sizes."""
    research_dir = paths["research_dir"] if paths else ROOT / "data" / "research"
    if not research_dir.exists():
        return "  (no research files yet — create them under data/research/)"
    files = sorted(research_dir.glob("*.md"))
    if not files:
        return "  (no research files yet — create them under data/research/)"
    lines = []
    for f in files:
        size = f.stat().st_size
        lines.append(f"  data/research/{f.name} ({size} bytes)")
    return "\n".join(lines)


def _build_phase_guidance(config: dict, paths: dict) -> str:
    """Build dynamic phase budget tracker that tells the agent which phase to work on.

    Uses weighted allocation: later phases get proportionally more budget because
    they read all prior research files (growing context = higher per-cycle cost)
    and produce the highest-value deliverables.
    """
    buckets = config.get("research_buckets", [])
    if not buckets:
        return ""

    max_budget = config.get("budget", {}).get("max_total_usd", 100.0)
    costs_file = paths["costs_file"] if paths else None
    cost_info = get_summary(costs_file)
    spent = cost_info["total_usd"]
    remaining = max(0, max_budget - spent)
    research_dir = paths["research_dir"] if paths else ROOT / "data" / "research"

    # Classify each phase as complete or incomplete
    phases = []
    for bucket in buckets:
        name = bucket["name"]
        files = bucket.get("files", [])
        weight = bucket.get("weight", 1.0)
        min_budget = bucket.get("min_budget_usd", 0.0)
        min_complete_bytes = bucket.get("min_complete_bytes", 2048)
        completion_marker = bucket.get("completion_marker", "")
        min_marker_count = bucket.get("min_marker_count", 0)
        md_files = [f for f in files if f.endswith(".md")]
        target_file = md_files[0] if md_files else (files[0] if files else None)

        # Check file size across all output files for this phase
        max_size = 0
        primary_content = ""
        for fn in files:
            fpath = research_dir / fn
            if fpath.exists():
                size = fpath.stat().st_size
                if size > max_size:
                    max_size = size
                # Read the largest .md file for marker counting
                if fn.endswith(".md") and size > 0:
                    try:
                        primary_content = fpath.read_text(encoding="utf-8")
                    except Exception:
                        pass

        # Completion requires BOTH: byte threshold AND marker count (if configured)
        bytes_ok = max_size >= min_complete_bytes
        markers_ok = True
        marker_count = 0
        if completion_marker and min_marker_count > 0:
            marker_count = primary_content.count(completion_marker)
            markers_ok = marker_count >= min_marker_count

        phases.append({
            "name": name,
            "target_file": target_file,
            "max_size": max_size,
            "complete": bytes_ok and markers_ok,
            "weight": weight,
            "min_budget": min_budget,
            "marker_count": marker_count,
            "min_marker_count": min_marker_count,
            "completion_marker": completion_marker,
        })

    # Find current phase (first incomplete)
    current_idx = None
    for i, p in enumerate(phases):
        if not p["complete"]:
            current_idx = i
            break

    # --- Weighted budget allocation ---
    # Compute target for ALL phases based on full budget (for display)
    total_weight = sum(p["weight"] for p in phases)
    for p in phases:
        p["target"] = (p["weight"] / total_weight) * max_budget if total_weight > 0 else 0

    # For incomplete phases, allocate the *remaining* budget by weight
    incomplete = [p for p in phases if not p["complete"]]
    if incomplete:
        inc_total_weight = sum(p["weight"] for p in incomplete)

        # First pass: proportional allocation
        for p in incomplete:
            p["allocation"] = (p["weight"] / inc_total_weight) * remaining if inc_total_weight > 0 else 0

        # Second pass: enforce minimums — if budget is tight, scale minimums proportionally
        total_min = sum(p["min_budget"] for p in incomplete)
        if total_min > remaining and total_min > 0:
            # Not enough budget to meet all minimums — scale them down
            scale = remaining / total_min
            for p in incomplete:
                p["allocation"] = p["min_budget"] * scale
        else:
            # Enough budget — clamp up to minimums and redistribute surplus
            clamped = []
            unclamped = []
            surplus = 0.0
            for p in incomplete:
                if p["allocation"] < p["min_budget"]:
                    surplus += p["min_budget"] - p["allocation"]
                    p["allocation"] = p["min_budget"]
                    clamped.append(p)
                else:
                    unclamped.append(p)
            # Take surplus from unclamped phases proportionally
            if surplus > 0 and unclamped:
                unc_weight = sum(p["weight"] for p in unclamped)
                for p in unclamped:
                    share = (p["weight"] / unc_weight) * surplus if unc_weight > 0 else 0
                    p["allocation"] = max(p["min_budget"], p["allocation"] - share)
    else:
        for p in phases:
            p["allocation"] = 0.0

    # For complete phases, allocation is 0 (already spent)
    for p in phases:
        if p["complete"]:
            p["allocation"] = 0.0

    # Build the tracker text
    lines = [
        "## Phase Budget Tracker",
        f"Total budget: ${max_budget:.2f} | Spent: ${spent:.2f} | Remaining: ${remaining:.2f}",
        "",
    ]

    for i, p in enumerate(phases):
        progress = ""
        if p["completion_marker"] and p["min_marker_count"] > 0:
            progress = f" [{p['marker_count']}/{p['min_marker_count']} {p['completion_marker']}s]"

        if p["complete"]:
            lines.append(f"{p['name']} — COMPLETE ({p['max_size']} bytes{progress}, target ${p['target']:.2f})")
        elif i == current_idx:
            lines.append(f"{p['name']} — CURRENT | Target: ${p['target']:.2f} | Remaining: ${p['allocation']:.2f}{progress}")
            lines.append(f"  Target file: {p['target_file']}")
        else:
            lines.append(f"{p['name']} — QUEUED | Target: ${p['target']:.2f}")

    # Add directive
    lines.append("")
    if current_idx is not None:
        current = phases[current_idx]
        completed_phases = [p for p in phases if p["complete"]]

        # Check for phase_filter.json to restrict which ideas to write playbooks for
        phase_filter_file = research_dir / "phase_filter.json"
        phase_filter = None
        if phase_filter_file.exists():
            try:
                phase_filter = json.loads(phase_filter_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        if phase_filter and phase_filter.get("selected_ideas"):
            selected = phase_filter["selected_ideas"]
            idea_names = ", ".join(
                f"{idea['title']} (#{idea['rank']})" for idea in selected
            )
            lines.append(f">>> CURRENT TASK: Work on {current['name']}.")
            lines.append(f">>> Write to: {current['target_file']}")
            lines.append(f">>> Write playbooks ONLY for these selected ideas: {idea_names}")
            lines.append(f">>> Skip all other ideas.")
        elif completed_phases and current_idx > 0:
            prev = phases[current_idx - 1]
            lines.append(f">>> ACTION REQUIRED: Start {current['name']} NOW.")
            lines.append(f">>> Write to: {current['target_file']}")
            lines.append(f">>> Do NOT continue writing to {prev['target_file']}.")
        else:
            lines.append(f">>> CURRENT TASK: Work on {current['name']}.")
            lines.append(f">>> Write to: {current['target_file']}")
        lines.append(f">>> Budget remaining for this phase: ${current['allocation']:.2f}")
    else:
        lines.append(">>> All phases complete!")

    return "\n".join(lines)


def _extract_ideas(research_dir: Path) -> list[dict]:
    """Parse top_ideas.md for ## IDEA #N: Title headers with descriptions.

    Returns a list of dicts with id, rank, title, score, industry,
    what_it_is, and problem fields.
    """
    import re
    ideas_file = research_dir / "top_ideas.md"
    if not ideas_file.exists():
        return []

    content = ideas_file.read_text(encoding="utf-8")
    lines = content.splitlines()

    # Match headers like: ## IDEA #1: QuoteFlow AI — Commercial Insurance Quoting (8/8)
    header_pattern = re.compile(
        r'^## IDEA\s*#(\d+)\s*:\s*(.+?)(?:\s*\((\d+)/(\d+)\))?\s*$',
    )
    # Match metadata line: **Rank: #4 | Score: 8/8 | Industry: Accounting**
    meta_pattern = re.compile(
        r'^\*\*Rank:.*?Score:\s*(\d+/\d+).*?Industry:\s*(.+?)\*\*\s*$',
    )

    # Find all idea header line numbers
    idea_starts = []
    for i, line in enumerate(lines):
        m = header_pattern.match(line)
        if m:
            idea_starts.append((i, m))

    ideas = []
    for idx, (start_line, m) in enumerate(idea_starts):
        rank = int(m.group(1))
        title = m.group(2).strip()
        score_num = m.group(3)
        score_den = m.group(4)
        score = f"{score_num}/{score_den}" if score_num and score_den else ""

        # Determine the block of text for this idea (until next ## IDEA or EOF)
        end_line = idea_starts[idx + 1][0] if idx + 1 < len(idea_starts) else len(lines)
        block = lines[start_line:end_line]

        # Extract industry and score from metadata line
        industry = ""
        for bl in block[:3]:
            mm = meta_pattern.match(bl)
            if mm:
                score = score or mm.group(1)
                industry = mm.group(2).strip()
                break

        # Extract ### sections
        what_it_is = _extract_section(block, "What It Is")
        problem = _extract_section(block, "Problem Solved")

        ideas.append({
            "id": f"idea_{rank}",
            "rank": rank,
            "title": title,
            "score": score,
            "industry": industry,
            "what_it_is": what_it_is,
            "problem": problem,
        })
    ideas.sort(key=lambda x: x["rank"])
    return ideas


def _extract_section(block: list[str], heading_prefix: str) -> str:
    """Extract the first paragraph after a ### heading that starts with heading_prefix."""
    capturing = False
    paragraphs = []
    for line in block:
        if line.startswith("### ") and heading_prefix in line:
            capturing = True
            continue
        if capturing:
            if line.startswith("### ") or line.startswith("## "):
                break
            stripped = line.strip()
            if stripped:
                paragraphs.append(stripped)
            elif paragraphs:
                break  # stop at first blank line after content
    return " ".join(paragraphs)[:300]


def _extract_phase_directive(phase_guidance: str) -> str:
    """Extract >>> directive lines from phase guidance for end-of-context emphasis."""
    directives = [ln.lstrip("> ") for ln in phase_guidance.splitlines() if ln.startswith(">>>")]
    if not directives:
        return ""
    return "## PHASE DIRECTIVE (HIGHEST PRIORITY)\n" + "\n".join(directives) + "\n"


def build_context(identity: dict, config: dict, paths: dict = None) -> str:
    """Build the context string the agent sees at the start of each cycle."""
    cycle_num = get_cycle_count(paths) + 1
    recent = get_recent_cycles(3, paths)
    recent_text = ""
    if recent:
        for c in recent:
            action = c.get("action", "none")
            summary = c.get("summary", "")
            recent_text += f"  Cycle {c.get('cycle', '?')}: {action} — {summary}\n"
    else:
        recent_text = "  (no previous cycles)\n"

    git_history = git_log_short(5)
    files = file_listing()
    research_files = list_research_files(paths)
    costs_file = paths["costs_file"] if paths else None
    cost_info = get_summary(costs_file)
    mission = load_mission(paths)
    phase_guidance = _build_phase_guidance(config, paths) if paths else ""

    return (
        f"You are {identity['name']} v{identity['version']}, a research agent.\n"
        f"Cycle: {cycle_num}\n"
        f"Budget: ${cost_info['total_usd']:.4f} spent of ${config.get('budget', {}).get('max_total_usd', 100.0):.2f} total\n\n"
        + (f"{phase_guidance}\n\n" if phase_guidance else "")
        + f"## Mission\n{mission}\n\n"
        f"## Your source files\n{files}\n\n"
        f"## Your research files\n{research_files}\n\n"
        f"## Recent git history\n{git_history}\n\n"
        f"## Recent cycles\n{recent_text}\n"
        f"## Instructions\n"
        f"You may read your own source files and research files, then either:\n"
        f"1. Call `propose_edit` to CREATE a new research file under data/research/*.md\n"
        f"2. Call `append_to_file` to ADD content to an existing research file (preferred for growing docs)\n"
        f"3. Call `reflect` to synthesize observations before your next move\n\n"
        f"IMPORTANT: Use `append_to_file` for adding to existing files — it preserves all existing content.\n"
        f"Only use `propose_edit` when creating a brand new file.\n"
        f"Focus on depth over breadth. One well-analyzed insight is worth ten bullet points.\n"
        f"You cannot modify protected files: {', '.join(config.get('protected_files', []))}\n\n"
        + (_extract_phase_directive(phase_guidance) if phase_guidance else "")
    )


def _repair_tool_args(name: str, raw: str) -> dict | None:
    """Try to extract tool arguments from malformed JSON.

    Common failure: Opus puts unescaped newlines/quotes inside string values.
    Strategy: find field boundaries by looking for known key patterns.
    """
    import re

    try:
        if name in ("propose_edit", "append_to_file"):
            content_key = "new_content" if name == "propose_edit" else "content"

            # Extract path (always short and clean)
            path_match = re.search(r'"path"\s*:\s*"([^"]+)"', raw)
            if not path_match:
                return None
            path = path_match.group(1)

            # Find all three field start positions
            field_starts = {}
            for key in ["path", "reasoning", content_key]:
                pattern = rf'"{key}"\s*:\s*"'
                m = re.search(pattern, raw)
                if m:
                    field_starts[key] = m.end()  # position after opening quote

            if content_key not in field_starts:
                return None

            # Sort fields by position to know the order
            ordered = sorted(field_starts.items(), key=lambda x: x[1])

            # For each field, its value runs from its start to the next field's key
            extracted = {}
            for i, (key, start_pos) in enumerate(ordered):
                if key == "path":
                    extracted[key] = path
                    continue

                if i + 1 < len(ordered):
                    # Value ends somewhere before the next field key
                    next_key = ordered[i + 1][0]
                    # Find where the next key's pattern starts
                    next_pattern = rf'",\s*"{next_key}"'
                    remaining = raw[start_pos:]
                    m = re.search(next_pattern, remaining)
                    if m:
                        value = remaining[:m.start()]
                    else:
                        value = remaining
                else:
                    # Last field — value ends at closing brace
                    remaining = raw[start_pos:]
                    # Strip trailing "} or "}
                    value = re.sub(r'"\s*\}\s*$', '', remaining)

                value = value.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\").replace("\\t", "\t")
                extracted[key] = value

            reasoning = extracted.get("reasoning", "auto-repaired")
            content = extracted.get(content_key, "")

            if not content:
                return None

            if name == "propose_edit":
                return {"path": path, "new_content": content, "reasoning": reasoning}
            else:
                return {"path": path, "content": content, "reasoning": reasoning}

        elif name == "read_file":
            path_match = re.search(r'"path"\s*:\s*"([^"]+)"', raw)
            if path_match:
                return {"path": path_match.group(1)}
            return None

        elif name == "reflect":
            obs_match = re.search(r'"observation"\s*:\s*"', raw)
            if obs_match:
                content = raw[obs_match.end():]
                content = re.sub(r'"\s*\}\s*$', '', content)
                content = content.replace("\\n", "\n").replace('\\"', '"')
                return {"observation": content}
            return None

    except Exception:
        return None

    return None


def run_cycle(config: dict) -> dict:
    """Execute one Wake -> Reflect -> Plan -> Act -> Sleep cycle."""
    paths = resolve_topic_paths(config)
    costs_file = paths["costs_file"]
    patches_dir = paths["patches_dir"]

    # Configure tools module paths for this topic
    set_paths(paths["data_dir"], patches_dir, paths["research_dir"])

    reset_web_counters()
    identity = load_identity(paths)
    cycle_num = get_cycle_count(paths) + 1
    ts = datetime.now(timezone.utc).isoformat()

    # Budget check
    ok, msg = check_budget(config, costs_file)
    if not ok:
        click.echo(f"Budget exceeded: {msg}")
        return {"cycle": cycle_num, "action": "budget_stop", "summary": msg, "timestamp": ts}

    topic_name = config.get("active_topic", "(legacy)")
    click.echo(f"\n{'='*60}")
    click.echo(f"Cycle {cycle_num} — {identity['name']} v{identity['version']} [{topic_name}]")
    click.echo(f"{'='*60}\n")

    # Wake: gather context
    context = build_context(identity, config, paths)
    pricing = config.get("pricing", {"input_per_mtok": 1.0, "output_per_mtok": 5.0})

    # Load agent parameters from topic file
    agent_parameters = load_agent_parameters(paths)

    # Reflect + Plan: LLM call with tools
    click.echo("Thinking...")
    client = get_client()
    messages = [
        {"role": "system", "content": agent_parameters},
        {"role": "user", "content": context},
    ]

    # Multi-turn tool use loop
    action = "reflect"
    summary = ""
    patch_file = None

    for turn in range(10):  # max 10 tool-use turns per cycle
        response = client.chat.completions.create(
            model=resolve_model(config.get("model", "anthropic/claude-opus-4.6")),
            max_tokens=4096,
            tools=TOOL_DEFINITIONS,
            messages=messages,
        )

        record_call(
            response.usage.prompt_tokens,
            response.usage.completion_tokens,
            pricing,
            label=f"cycle_{cycle_num}_turn_{turn}",
            costs_file=costs_file,
        )

        # Mid-cycle budget check
        ok, _ = check_budget(config, costs_file)
        if not ok:
            click.echo("Budget limit reached mid-cycle — stopping.")
            break

        # Process response
        msg = response.choices[0].message
        has_tool_calls = bool(msg.tool_calls)

        if msg.content:
            click.echo(f"\nAgent: {msg.content[:500]}")

        if has_tool_calls:
            # Append the assistant message (with tool_calls) to history
            messages.append(msg)

            for tool_call in msg.tool_calls:
                name = tool_call.function.name
                try:
                    args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    args = _repair_tool_args(name, tool_call.function.arguments)
                    if args is None:
                        click.echo(f"\n  Tool: {name} — JSON parse error (unrepairable)")
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": "ERROR: Could not parse tool arguments. Use shorter content with no special characters.",
                        })
                        continue
                    click.echo(f"\n  Tool: {name} — repaired malformed JSON")

                display_args = {k: v[:80] if isinstance(v, str) and len(v) > 80 else v for k, v in args.items()}
                click.echo(f"\n  Tool: {name}({json.dumps(display_args, indent=None)})")
                result = dispatch_tool(name, args)
                click.echo(f"  Result: {result[:200]}{'...' if len(result) > 200 else ''}")

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                })

                # Track what the agent did
                if name == "propose_edit":
                    action = "propose_edit"
                    summary = f"Proposed edit to {args['path']}: {args.get('reasoning', '')[:100]}"
                    patches = sorted(patches_dir.glob("*.patch")) if patches_dir.exists() else []
                    if patches:
                        patch_file = patches[-1]
                elif name == "reflect":
                    action = "reflect"
                    summary = args.get("observation", "")[:200]

        # Nudge agent to write if it hasn't by turn 7
        if turn == 7 and action == "reflect":
            messages.append({
                "role": "user",
                "content": "IMPORTANT: You have used most of your turns without writing any output. You MUST call append_to_file or propose_edit NOW to write your findings to a research file. Do not search or reflect further — write what you have.",
            })

        # If no tool calls, we're done
        finish = response.choices[0].finish_reason
        if finish == "stop" or not has_tool_calls:
            break

    # Process all pending patches
    applied = False
    mode = config.get("mode", "full")
    research_dir = config.get("research_dir", "data/research")
    pending_patches = sorted(patches_dir.glob("*.patch")) if patches_dir.exists() else []

    for patch_file in pending_patches:
        patch_data = json.loads(patch_file.read_text(encoding="utf-8"))
        if patch_data.get("status") != "pending":
            continue

        is_research = patch_data["path"].startswith(research_dir + "/")

        # Supervisor review
        click.echo(f"\nSupervisor reviewing: {patch_data['path']}...")
        verdict = review_proposal(patch_data, costs_file=costs_file)
        click.echo(f"Supervisor verdict: {verdict['verdict'].upper()}")
        click.echo(f"  {verdict['reasoning'][:300]}")

        # Determine approval
        if mode == "research" and is_research:
            # Auto-approve research files (unless supervisor rejects)
            if verdict["verdict"] == "reject":
                click.echo(f"Supervisor REJECTED research edit — skipping.")
                patch_data["status"] = "rejected"
                patch_file.write_text(json.dumps(patch_data, indent=2), encoding="utf-8")
                action = "rejected"
                continue

            click.echo(f"Auto-approving research file: {patch_data['path']}")
            choice = "a"
        elif mode == "research" and not is_research:
            click.echo(f"Research mode — skipping code edit: {patch_data['path']}")
            action = "skipped"
            continue
        else:
            # Full mode — human approval gate
            click.echo(f"\n{'—'*60}")
            click.echo(f"PROPOSED CHANGE: {patch_data['path']}")
            click.echo(f"Reasoning: {patch_data['reasoning']}")
            click.echo(f"{'—'*60}")
            click.echo(patch_data["diff"])
            click.echo(f"{'—'*60}")
            click.echo(f"Supervisor says: {verdict['verdict'].upper()}")

            choice = click.prompt(
                "\n[A]pprove / [R]eject / [S]kip / [Q]uit",
                type=click.Choice(["a", "r", "s", "q"], case_sensitive=False),
            )

        if choice == "a":
            target = _resolve_file_path(patch_data["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(patch_data["new_content"], encoding="utf-8")
            click.echo(f"Applied: {target}")

            # Git commit
            git_run("add", str(target.relative_to(ROOT)))
            git_run("commit", "-m", f"deepshika cycle {cycle_num}: {patch_data['reasoning'][:60]}")
            click.echo(f"Committed.")
            action = "applied"
            applied = True
            summary = f"Applied {patch_data['path']}: {patch_data['reasoning'][:100]}"

            patch_file.unlink(missing_ok=True)

        elif choice == "r":
            click.echo("Change rejected.")
            patch_data["status"] = "rejected"
            patch_file.write_text(json.dumps(patch_data, indent=2), encoding="utf-8")
            action = "rejected"
        elif choice == "s":
            click.echo("Skipped for now.")
            action = "skipped"
        elif choice == "q":
            click.echo("Quitting.")
            action = "quit"
            break

    # Sleep: log cycle
    cost_info = get_summary(costs_file)
    cycle_record = {
        "cycle": cycle_num,
        "action": action,
        "summary": summary[:300],
        "applied": applied,
        "timestamp": ts,
        "cost_after": cost_info["total_usd"],
    }
    log_cycle(cycle_record, paths)

    click.echo(f"\nCycle {cycle_num} complete. Action: {action}")
    click.echo(f"Total cost: ${cost_info['total_usd']:.4f}")

    return cycle_record


# --- CLI ---


@click.group()
def cli():
    """Deepshika — self-modifying agent experiment."""
    pass


@cli.command()
def init():
    """Initialize the Deepshika project: data dirs, git repo, identity."""
    config = load_config()
    paths = resolve_topic_paths(config)

    paths["data_dir"].mkdir(parents=True, exist_ok=True)
    paths["patches_dir"].mkdir(parents=True, exist_ok=True)

    # Initialize identity if missing
    if not paths["identity"].exists():
        save_identity({
            "name": "Deepshika",
            "version": "0.1.0",
            "born": datetime.now(timezone.utc).isoformat(),
            "traits": ["curious", "cautious", "transparent"],
            "purpose": "A self-modifying agent that improves its own source code through careful, human-approved iterations.",
            "modification_history": [],
        }, paths)
        click.echo(f"Created {paths['identity']}")

    # Git init
    git_dir = ROOT / ".git"
    if not git_dir.exists():
        git_run("init", check=False)
        click.echo("Initialized git repository")

    # Initial commit if no commits yet
    log = git_run("log", "--oneline", "-1", check=False)
    if "error" in log.lower() or not log.strip():
        git_run("add", "-A", check=False)
        git_run("commit", "-m", "deepshika: initial commit", check=False)
        click.echo("Created initial commit")
    else:
        click.echo("Git repository already has commits")

    identity = load_identity(paths)
    click.echo(f"\nDeepshika initialized!")
    click.echo(f"  Name: {identity['name']}")
    click.echo(f"  Version: {identity['version']}")
    click.echo(f"  Ready to run: deepshika run")


@cli.command()
@click.option("--cycles", default=1, help="Number of cycles to run")
def run(cycles):
    """Run one or more agent cycles."""
    config = load_config()
    for i in range(cycles):
        result = run_cycle(config)
        if result.get("action") in ("budget_stop", "quit"):
            break
        if cycles > 1 and i < cycles - 1:
            click.echo(f"\n{'#'*60}")
            click.echo(f"Completed cycle {i+1}/{cycles}. Continuing...")
            click.echo(f"{'#'*60}")


@cli.command()
def status():
    """Show agent identity, budget, and cycle count."""
    config = load_config()
    paths = resolve_topic_paths(config)
    identity = load_identity(paths)
    costs_file = paths["costs_file"]
    cost_info = get_summary(costs_file)
    budget = config.get("budget", {})

    topic_name = config.get("active_topic", "(no topic set — legacy mode)")
    click.echo(f"\n{identity['name']} v{identity['version']}")
    click.echo(f"Topic: {topic_name}")
    click.echo(f"{'='*40}")
    click.echo(f"Cycles completed: {get_cycle_count(paths)}")
    click.echo(f"Cost: ${cost_info['total_usd']:.4f} / ${budget.get('max_total_usd', 5.0):.2f} total")
    click.echo(f"Today: ${cost_info['today_usd']:.4f} / ${budget.get('max_per_day_usd', 1.0):.2f} daily")
    click.echo(f"API calls: {cost_info['total_calls']}")
    click.echo(f"Tokens: {cost_info['total_input_tokens']} in / {cost_info['total_output_tokens']} out")

    # Pending patches
    patches_dir = paths["patches_dir"]
    pending = list(patches_dir.glob("*.patch")) if patches_dir.exists() else []
    pending_count = sum(1 for p in pending if json.loads(p.read_text(encoding="utf-8")).get("status") == "pending")
    if pending_count:
        click.echo(f"Pending patches: {pending_count}")

    # Traits
    if identity.get("traits"):
        click.echo(f"Traits: {', '.join(identity['traits'])}")
    click.echo()


@cli.command()
def review():
    """Interactively review pending patches."""
    config = load_config()
    paths = resolve_topic_paths(config)
    patches_dir = paths["patches_dir"]

    if not patches_dir.exists():
        click.echo("No patches directory found.")
        return

    patches = sorted(patches_dir.glob("*.patch"))
    pending = [p for p in patches if json.loads(p.read_text(encoding="utf-8")).get("status") == "pending"]

    if not pending:
        click.echo("No pending patches to review.")
        return

    for patch_path in pending:
        patch_data = json.loads(patch_path.read_text(encoding="utf-8"))
        click.echo(f"\n{'='*60}")
        click.echo(f"File: {patch_data['path']}")
        click.echo(f"Reasoning: {patch_data['reasoning']}")
        click.echo(f"Created: {patch_data['created_at']}")
        click.echo(f"{'='*60}")
        click.echo(patch_data["diff"])
        click.echo(f"{'='*60}")

        choice = click.prompt(
            "[A]pprove / [R]eject / [S]kip / [Q]uit",
            type=click.Choice(["a", "r", "s", "q"], case_sensitive=False),
        )

        if choice == "a":
            target = ROOT / patch_data["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(patch_data["new_content"], encoding="utf-8")
            git_run("add", patch_data["path"])
            identity = load_identity(paths)
            new_version = bump_version(identity)
            save_identity(identity, paths)
            git_run("add", str(paths["identity"].relative_to(ROOT)))
            git_run("commit", "-m", f"deepshika review: {patch_data['reasoning'][:60]}")
            click.echo(f"Applied and committed. Version: {new_version}")
            patch_path.unlink()
        elif choice == "r":
            patch_data["status"] = "rejected"
            patch_path.write_text(json.dumps(patch_data, indent=2), encoding="utf-8")
            click.echo("Rejected.")
        elif choice == "s":
            click.echo("Skipped.")
        elif choice == "q":
            break


@cli.command()
@click.option("-n", default=10, help="Number of recent entries")
def log(n):
    """Show recent cycle log entries."""
    config = load_config()
    paths = resolve_topic_paths(config)
    cycles = get_recent_cycles(n, paths)
    if not cycles:
        click.echo("No cycles recorded yet.")
        return

    click.echo(f"\nRecent cycles (last {len(cycles)}):")
    click.echo(f"{'='*60}")
    for c in cycles:
        applied = " [APPLIED]" if c.get("applied") else ""
        click.echo(
            f"  Cycle {c['cycle']}: {c['action']}{applied} — "
            f"{c.get('summary', '')[:80]} "
            f"(${c.get('cost_after', 0):.4f})"
        )
    click.echo()


@cli.command()
def costs():
    """Show detailed cost breakdown."""
    config = load_config()
    paths = resolve_topic_paths(config)
    costs_file = paths["costs_file"]
    info = get_summary(costs_file)

    click.echo(f"\nCost Report")
    click.echo(f"{'='*40}")
    click.echo(f"Total: ${info['total_usd']:.4f}")
    click.echo(f"Today: ${info['today_usd']:.4f}")
    click.echo(f"API calls: {info['total_calls']}")
    click.echo(f"Input tokens: {info['total_input_tokens']:,}")
    click.echo(f"Output tokens: {info['total_output_tokens']:,}")

    budget = config.get("budget", {})
    remaining = budget.get("max_total_usd", 5.0) - info["total_usd"]
    click.echo(f"Remaining budget: ${remaining:.4f}")

    if info["total_calls"] > 0:
        avg = info["total_usd"] / info["total_calls"]
        click.echo(f"Avg cost/call: ${avg:.6f}")
        est_remaining = int(remaining / avg) if avg > 0 else "unlimited"
        click.echo(f"Estimated calls remaining: {est_remaining}")
    click.echo()


@cli.command()
@click.option("-n", default=5, help="Number of recent cycles to show")
@click.option("--interval", default=5, help="Refresh interval in seconds")
def watch(n, interval):
    """Live dashboard — refreshes every few seconds. Ctrl+C to exit."""
    import time

    try:
        while True:
            # Clear screen
            click.clear()

            config = load_config()
            paths = resolve_topic_paths(config)
            identity = load_identity(paths)
            costs_file = paths["costs_file"]
            cost_info = get_summary(costs_file)
            budget = config.get("budget", {})
            total_budget = budget.get("max_total_usd", 100.0)
            remaining = total_budget - cost_info["total_usd"]
            pct = (cost_info["total_usd"] / total_budget * 100) if total_budget else 0
            topic_name = config.get("active_topic", "(legacy)")

            # Header
            click.echo(f"  DEEPSHIKHA WATCH — {identity['name']} v{identity['version']} [{topic_name}]")
            click.echo(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
            click.echo(f"{'='*60}")

            # Budget bar
            bar_width = 40
            filled = int(bar_width * pct / 100)
            bar = "#" * filled + "-" * (bar_width - filled)
            click.echo(f"  Budget: [{bar}] {pct:.1f}%")
            click.echo(f"  ${cost_info['total_usd']:.4f} spent / ${remaining:.4f} remaining / ${total_budget:.2f} cap")
            click.echo(f"  API calls: {cost_info['total_calls']}  |  Tokens: {cost_info['total_input_tokens']:,} in / {cost_info['total_output_tokens']:,} out")
            click.echo()

            # Cycles
            cycles_done = get_cycle_count(paths)
            click.echo(f"  Cycles: {cycles_done}")
            if cost_info["total_calls"] > 0:
                avg = cost_info["total_usd"] / cost_info["total_calls"]
                est = int(remaining / avg) if avg > 0 else 0
                click.echo(f"  Avg cost/call: ${avg:.5f}  |  Est. calls remaining: {est}")
            click.echo()

            # Research files
            research_dir = paths["research_dir"]
            click.echo(f"  Research Files")
            click.echo(f"  {'-'*56}")
            if research_dir.exists():
                files = sorted(research_dir.glob("*.md"))
                if files:
                    for f in files:
                        size = f.stat().st_size
                        lines = f.read_text(encoding="utf-8").count("\n")
                        click.echo(f"    {f.name:<35} {size:>6} bytes  {lines:>4} lines")
                else:
                    click.echo(f"    (none yet)")
            else:
                click.echo(f"    (research dir not created)")
            click.echo()

            # Recent cycles
            recent = get_recent_cycles(n, paths)
            if recent:
                click.echo(f"  Recent Cycles")
                click.echo(f"  {'-'*56}")
                for c in recent:
                    applied_marker = " +" if c.get("applied") else "  "
                    click.echo(
                        f"  {applied_marker} Cycle {c['cycle']:>3}: {c['action']:<15} "
                        f"${c.get('cost_after', 0):.4f}  "
                        f"{c.get('summary', '')[:40]}"
                    )
            click.echo()
            click.echo(f"  Refreshing every {interval}s — Ctrl+C to exit")

            time.sleep(interval)
    except KeyboardInterrupt:
        click.echo("\nWatch stopped.")


@cli.command()
@click.option("--to", "cycle_num", required=True, type=int, help="Revert to state at this cycle number")
def revert(cycle_num):
    """Revert the project to its state at a given cycle via git."""
    # Find the commit for the given cycle
    log_output = git_run("log", "--oneline", "--all", check=False)
    if not log_output:
        click.echo("No git history found.")
        return

    target_hash = None
    for line in log_output.split("\n"):
        if f"deepshika cycle {cycle_num}:" in line or (cycle_num == 0 and "initial commit" in line):
            target_hash = line.split()[0]
            break

    if not target_hash:
        click.echo(f"Could not find commit for cycle {cycle_num}.")
        click.echo("Available commits:")
        click.echo(log_output)
        return

    click.echo(f"Reverting to cycle {cycle_num} (commit {target_hash})...")
    confirm = click.confirm("This will discard all changes after that cycle. Continue?")
    if confirm:
        git_run("checkout", target_hash, "--", ".", check=False)
        git_run("commit", "-m", f"deepshika revert: rolled back to cycle {cycle_num}", check=False)
        click.echo(f"Reverted to cycle {cycle_num}.")
    else:
        click.echo("Revert cancelled.")


# --- Topic management ---


@cli.group()
def topic():
    """Manage research topics."""
    pass


@topic.command("create")
@click.argument("name")
def topic_create(name):
    """Create a new topic directory with scaffold files."""
    topic_dir = ROOT / "topics" / name
    if topic_dir.exists():
        click.echo(f"Topic '{name}' already exists at {topic_dir}")
        return

    description = click.prompt("One-line description for this topic", default=f"Research topic: {name}")

    # Create directory structure
    (topic_dir / "data" / "research").mkdir(parents=True)
    (topic_dir / "data" / "pending_patches").mkdir(parents=True)

    # Attempt LLM-generated scaffold
    config = load_config()
    click.echo("Generating topic scaffolding via LLM...")
    scaffold = generate_agent_scaffold(name, description, config)

    if scaffold:
        # Write LLM-generated files
        (topic_dir / "mission.md").write_text(scaffold["mission"], encoding="utf-8")
        (topic_dir / "agent_parameters.md").write_text(scaffold["agent_parameters"], encoding="utf-8")

        # Pre-create output files in data/research/
        research_dir = topic_dir / "data" / "research"
        for of in scaffold["output_files"]:
            fn = of["filename"]
            question = of.get("question", "")
            title = fn.replace("_", " ").replace(".md", "").title()
            (research_dir / fn).write_text(
                f"# {title}\n\n<!-- Key question: {question} -->\n\n",
                encoding="utf-8",
            )

        output_names = [of["filename"] for of in scaffold["output_files"]]
        click.echo(f"  LLM scaffold generated successfully.")
        click.echo(f"  Output files: {', '.join(output_names)}")

        # Record LLM cost to topic's costs.json
        if scaffold.get("_usage"):
            pricing = config.get("pricing", {"input_per_mtok": 1.0, "output_per_mtok": 5.0})
            record_call(
                scaffold["_usage"]["input_tokens"],
                scaffold["_usage"]["output_tokens"],
                pricing,
                label="scaffold_generation",
                costs_file=topic_dir / "data" / "costs.json",
            )
    else:
        click.echo("  LLM scaffold failed — using static templates.")
        _write_static_scaffold(topic_dir, name, description)

    # Scaffold identity.json
    (topic_dir / "identity.json").write_text(json.dumps({
        "name": "Deepshika",
        "version": "0.1.0",
        "born": datetime.now(timezone.utc).isoformat(),
        "traits": ["curious", "systematic", "depth-over-breadth"],
        "purpose": description,
        "modification_history": [],
    }, indent=2), encoding="utf-8")

    # Switch to the new topic
    config = load_config()
    config["active_topic"] = name
    save_config(config)

    click.echo(f"\nCreated topic: {name}")
    click.echo(f"  Directory: topics/{name}/")
    click.echo(f"  mission.md — edit to define your research questions")
    click.echo(f"  agent_parameters.md — edit to tune agent behavior")
    click.echo(f"  identity.json — agent persona for this topic")
    click.echo(f"\nSwitched active_topic to '{name}'")
    click.echo(f"Run 'deepshika run' to start researching.")


@topic.command("list")
def topic_list():
    """List all topics with active indicator, cycle count, and cost."""
    config = load_config()
    active = config.get("active_topic", "")
    topics_dir = ROOT / "topics"

    if not topics_dir.exists():
        click.echo("No topics directory found. Run 'deepshika migrate' or 'deepshika topic create <name>'.")
        return

    topic_dirs = sorted([d for d in topics_dir.iterdir() if d.is_dir()])
    if not topic_dirs:
        click.echo("No topics found. Run 'deepshika topic create <name>' to create one.")
        return

    click.echo(f"\n{'Name':<30} {'Cycles':>7} {'Cost':>10} {'Active':>7}")
    click.echo(f"{'-'*30} {'-'*7} {'-'*10} {'-'*7}")

    for d in topic_dirs:
        name = d.name
        is_active = "*" if name == active else ""

        # Cycle count
        cycles_file = d / "data" / "cycles.jsonl"
        cycle_count = sum(1 for _ in open(cycles_file)) if cycles_file.exists() else 0

        # Cost
        costs_file = d / "data" / "costs.json"
        if costs_file.exists():
            ledger = json.loads(costs_file.read_text(encoding="utf-8"))
            total_cost = ledger.get("total_usd", 0.0)
        else:
            total_cost = 0.0

        click.echo(f"  {name:<28} {cycle_count:>7} ${total_cost:>8.4f} {is_active:>7}")

    click.echo()


@topic.command("switch")
@click.argument("name")
def topic_switch(name):
    """Switch to a different topic."""
    topic_dir = ROOT / "topics" / name
    if not topic_dir.exists():
        click.echo(f"Topic '{name}' not found. Available topics:")
        topics_dir = ROOT / "topics"
        if topics_dir.exists():
            for d in sorted(topics_dir.iterdir()):
                if d.is_dir():
                    click.echo(f"  {d.name}")
        return

    config = load_config()
    config["active_topic"] = name
    save_config(config)
    click.echo(f"Switched to topic: {name}")


@topic.command("status")
def topic_status():
    """Show detailed info for the active topic."""
    config = load_config()
    active = config.get("active_topic")
    if not active:
        click.echo("No active topic set. Run 'deepshika migrate' or 'deepshika topic create <name>'.")
        return

    paths = resolve_topic_paths(config)
    if not paths["base"].exists():
        click.echo(f"Topic directory not found: {paths['base']}")
        return

    identity = load_identity(paths)
    cost_info = get_summary(paths["costs_file"])
    cycle_count = get_cycle_count(paths)

    click.echo(f"\nTopic: {active}")
    click.echo(f"{'='*50}")
    click.echo(f"Agent: {identity['name']} v{identity['version']}")
    click.echo(f"Cycles: {cycle_count}")
    click.echo(f"Cost: ${cost_info['total_usd']:.4f}")
    click.echo(f"API calls: {cost_info['total_calls']}")

    # Mission preview
    mission = load_mission(paths)
    if mission:
        first_line = mission.strip().split("\n")[0]
        click.echo(f"Mission: {first_line}")

    # Research files
    research_dir = paths["research_dir"]
    if research_dir.exists():
        files = sorted(research_dir.glob("*.md"))
        if files:
            click.echo(f"\nResearch files:")
            for f in files:
                size = f.stat().st_size
                click.echo(f"  {f.name} ({size} bytes)")

    # Traits
    if identity.get("traits"):
        click.echo(f"\nTraits: {', '.join(identity['traits'])}")
    click.echo()


# --- Migration ---


@cli.command()
def migrate():
    """Migrate existing data into topics/data-play-acquisition/."""
    topic_name = "data-play-acquisition"
    topic_dir = ROOT / "topics" / topic_name

    if topic_dir.exists():
        click.echo(f"Topic '{topic_name}' already exists. Migration may have already run.")
        return

    click.echo(f"Migrating existing data to topics/{topic_name}/...")

    # Create directory structure
    (topic_dir / "data" / "research").mkdir(parents=True)
    (topic_dir / "data" / "pending_patches").mkdir(parents=True)

    # 1. Copy MISSION.md -> topic/mission.md
    mission_src = ROOT / "MISSION.md"
    if mission_src.exists():
        shutil.copy2(mission_src, topic_dir / "mission.md")
        click.echo(f"  Copied MISSION.md -> topics/{topic_name}/mission.md")

    # 2. Extract agent parameters from this file's hardcoded string -> agent_parameters.md
    agent_parameters = (
        "You are Deepshika, a market researcher evaluating a data-play acquisition opportunity in consumer AI agent security. "
        "You are in RESEARCH ONLY mode — only research files under data/research/. "
        "Your mission: Determine if there's a viable startup opportunity building a 'Credit Karma for AI agent security' — "
        "a consumer/prosumer tool that monitors AI personal assistants (ChatGPT, Claude, Gemini, open-source agents) "
        "and scores their security posture, where the real value is the proprietary dataset of cross-agent behavioral telemetry. "
        "The Basepaws model: product generates data -> data has strategic value to acquirer -> acquisition at data-asset multiples. "
        "You have web_search and web_fetch tools — USE THEM EVERY CYCLE. "
        "Search for: AI agent security incidents, consumer AI agent permissions, MCP server security risks, "
        "AI assistant data breaches, consumer security startups, AI agent monitoring tools. "
        "Do NOT rely solely on your training data — search for CURRENT incidents, products, and market data. "
        "Read 1-2 of your existing files to understand current state, then SEARCH the web, then WRITE. "
        "EVERY CYCLE MUST PRODUCE WRITTEN OUTPUT — either append_to_file or propose_edit. "
        "Do NOT just reflect. Reflecting without writing is wasting a cycle. "
        "Five output files: (1) agent_security_landscape.md — the security surface for consumer AI agents, "
        "(2) product_thesis.md — what we'd build (form factor, features, scoring, GTM), "
        "(3) data_asset_analysis.md — the Basepaws-style dataset and why it compounds, "
        "(4) acquirer_and_competitive.md — named acquirers with M&A history + competitors, "
        "(5) viability_assessment.md — honest stress-test and scoring. "
        "Use `append_to_file` to ADD sections to existing research files (preferred for growing documents). "
        "Use `propose_edit` only when creating a new file. "
        "IMPORTANT: Keep each tool call's content under 2000 characters. If you have more to write, "
        "make multiple append_to_file calls in the same cycle. This prevents JSON formatting errors. "
        "Be brutally honest. If this thesis doesn't work, say so. Name real companies, cite real incidents. "
        "The question is: can you build a product consumers actually use, that generates a proprietary dataset "
        "large incumbents would acquire at premium multiples?"
    )
    (topic_dir / "agent_parameters.md").write_text(agent_parameters, encoding="utf-8")
    click.echo(f"  Extracted agent parameters -> topics/{topic_name}/agent_parameters.md")

    # 3. Copy data/identity.json -> topic/identity.json
    identity_src = ROOT / "data" / "identity.json"
    if identity_src.exists():
        shutil.copy2(identity_src, topic_dir / "identity.json")
        click.echo(f"  Copied data/identity.json -> topics/{topic_name}/identity.json")

    # 4. Copy research files
    research_src = ROOT / "data" / "research"
    if research_src.exists():
        research_dst = topic_dir / "data" / "research"
        for f in research_src.glob("*"):
            shutil.copy2(f, research_dst / f.name)
        click.echo(f"  Copied data/research/* -> topics/{topic_name}/data/research/")

    # 5. Copy costs.json
    costs_src = ROOT / "data" / "costs.json"
    if costs_src.exists():
        shutil.copy2(costs_src, topic_dir / "data" / "costs.json")
        click.echo(f"  Copied data/costs.json -> topics/{topic_name}/data/costs.json")

    # 6. Copy cycles.jsonl
    cycles_src = ROOT / "data" / "cycles.jsonl"
    if cycles_src.exists():
        shutil.copy2(cycles_src, topic_dir / "data" / "cycles.jsonl")
        click.echo(f"  Copied data/cycles.jsonl -> topics/{topic_name}/data/cycles.jsonl")

    # 7. Copy pending patches
    patches_src = ROOT / "data" / "pending_patches"
    if patches_src.exists():
        patches_dst = topic_dir / "data" / "pending_patches"
        for f in patches_src.glob("*"):
            shutil.copy2(f, patches_dst / f.name)
        click.echo(f"  Copied pending patches -> topics/{topic_name}/data/pending_patches/")

    # 8. Set active_topic in config.yaml
    config = load_config()
    config["active_topic"] = topic_name
    save_config(config)
    click.echo(f"  Set active_topic: {topic_name} in config.yaml")

    click.echo(f"\nMigration complete! Topic '{topic_name}' is now active.")
    click.echo(f"Run 'deepshika topic list' to see all topics.")
    click.echo(f"Run 'deepshika status' to verify.")


@cli.command()
@click.option("--port", default=8000, help="Port to serve the dashboard on")
def dashboard(port):
    """Launch the web dashboard for topic management."""
    import uvicorn
    from dashboard import app

    click.echo(f"Starting Deepshika dashboard on http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port)


def _load_checkpoints(topic_name: str) -> set:
    """Load already-crossed checkpoint thresholds from disk."""
    cp_file = ROOT / "topics" / topic_name / "data" / "checkpoints.json"
    if cp_file.exists():
        data = json.loads(cp_file.read_text(encoding="utf-8"))
        return set(data.get("checkpoints_hit", []))
    return set()


def _save_checkpoints(topic_name: str, checkpoints_hit: set):
    """Persist crossed checkpoint thresholds to disk."""
    cp_file = ROOT / "topics" / topic_name / "data" / "checkpoints.json"
    cp_file.parent.mkdir(parents=True, exist_ok=True)
    cp_file.write_text(json.dumps({"checkpoints_hit": sorted(checkpoints_hit)}), encoding="utf-8")


def _get_current_phase_idx(config: dict, paths: dict) -> int | None:
    """Return the index of the first incomplete phase, or None if all complete."""
    buckets = config.get("research_buckets", [])
    research_dir = paths["research_dir"] if paths else ROOT / "data" / "research"
    for i, bucket in enumerate(buckets):
        files = bucket.get("files", [])
        min_complete_bytes = bucket.get("min_complete_bytes", 2048)
        completion_marker = bucket.get("completion_marker", "")
        min_marker_count = bucket.get("min_marker_count", 0)

        max_size = 0
        primary_content = ""
        for fn in files:
            fpath = research_dir / fn
            if fpath.exists():
                size = fpath.stat().st_size
                if size > max_size:
                    max_size = size
                if fn.endswith(".md") and size > 0:
                    try:
                        primary_content = fpath.read_text(encoding="utf-8")
                    except Exception:
                        pass

        bytes_ok = max_size >= min_complete_bytes
        markers_ok = True
        if completion_marker and min_marker_count > 0:
            markers_ok = primary_content.count(completion_marker) >= min_marker_count

        if not (bytes_ok and markers_ok):
            return i
    return None


@cli.command("run-topic")
@click.argument("topic_name")
@click.option("--cycles", default=0, help="0 = run until budget exhausted")
@click.option("--delay", default=10, type=int, help="Seconds between cycles")
def run_topic(topic_name, cycles, delay):
    """Run cycles for a specific topic (used by dashboard)."""
    topic_dir = ROOT / "topics" / topic_name
    if not topic_dir.exists():
        click.echo(f"Topic '{topic_name}' not found.")
        return

    config = load_agent_config(topic_name)
    paths = resolve_topic_paths(config)

    # Checkpoint setup
    review_thresholds = config.get("review_checkpoints", [0.33, 0.66])
    checkpoints_hit = _load_checkpoints(topic_name)

    cycle_count = 0
    prev_phase_idx = _get_current_phase_idx(config, paths)

    while True:
        result = run_cycle(config)
        cycle_count += 1
        if result.get("action") in ("budget_stop", "quit"):
            break
        if cycles > 0 and cycle_count >= cycles:
            break

        # Selection checkpoint: detect phase transition on phases with selection_checkpoint
        cur_phase_idx = _get_current_phase_idx(config, paths)
        buckets = config.get("research_buckets", [])
        if (prev_phase_idx is not None
                and cur_phase_idx != prev_phase_idx
                and prev_phase_idx < len(buckets)):
            completed_bucket = buckets[prev_phase_idx]
            if completed_bucket.get("selection_checkpoint"):
                research_dir = paths["research_dir"]
                ideas = _extract_ideas(research_dir)
                if ideas:
                    # Save to checkpoints.json
                    cp_file = paths["data_dir"] / "checkpoints.json"
                    cp_data = {}
                    if cp_file.exists():
                        try:
                            cp_data = json.loads(cp_file.read_text(encoding="utf-8"))
                        except Exception:
                            pass
                    cp_data["selection_checkpoint"] = {
                        "phase": completed_bucket["name"],
                        "ideas": ideas,
                        "confirmed": False,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    cp_file.write_text(json.dumps(cp_data, indent=2), encoding="utf-8")

                    # Log to cycles.jsonl
                    log_cycle({
                        "cycle": result.get("cycle", cycle_count),
                        "action": "selection_checkpoint",
                        "summary": f"Selection checkpoint after {completed_bucket['name']}: {len(ideas)} ideas extracted",
                        "applied": False,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "cost_after": get_total_usd(paths["costs_file"]),
                    }, paths)

                    click.echo(f"\n{'='*60}")
                    click.echo(f"SELECTION CHECKPOINT: {completed_bucket['name']} complete")
                    click.echo(f"  {len(ideas)} ideas extracted. Select which ideas to write playbooks for.")
                    click.echo(f"  Use the dashboard to pick ideas, then restart.")
                    click.echo(f"{'='*60}\n")
                    break

        prev_phase_idx = cur_phase_idx

        # Budget checkpoint check
        if review_thresholds:
            total_spent = get_total_usd(paths["costs_file"])
            max_total = config.get("budget", {}).get("max_total_usd", 10.0)
            pct = total_spent / max_total if max_total > 0 else 0

            stop = False
            for threshold in sorted(review_thresholds):
                if pct >= threshold and threshold not in checkpoints_hit:
                    checkpoints_hit.add(threshold)
                    _save_checkpoints(topic_name, checkpoints_hit)

                    click.echo(f"\n{'='*60}")
                    click.echo(f"CHECKPOINT: {int(threshold*100)}% of budget used (${total_spent:.2f} / ${max_total:.2f})")
                    click.echo(f"Review research output, then restart to continue.")
                    click.echo(f"{'='*60}\n")

                    # Log checkpoint in cycles.jsonl
                    checkpoint_record = {
                        "cycle": result.get("cycle", cycle_count),
                        "action": "checkpoint_review",
                        "summary": f"Budget checkpoint at {int(threshold*100)}%: ${total_spent:.2f} / ${max_total:.2f} spent",
                        "applied": [],
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "cost_after": total_spent,
                    }
                    log_cycle(checkpoint_record, paths)

                    stop = True
                    break

            if stop:
                break

        time.sleep(delay)


if __name__ == "__main__":
    cli()
