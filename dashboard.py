"""Deepshika web dashboard — topic management UI."""

import atexit
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from openai import OpenAI
from pydantic import BaseModel

import yaml
from dotenv import load_dotenv

load_dotenv()

from agent import (
    ROOT,
    _write_static_scaffold,
    generate_agent_scaffold,
    get_cycle_count,
    get_recent_cycles,
    load_config,
    load_identity,
    load_mission,
    resolve_topic_paths,
    save_config,
)
from costs import _load_ledger, get_summary

app = FastAPI(title="Deepshika Dashboard")

STATIC_DIR = ROOT / "static"

# --- Running agent registry ---
_running_agents: dict[str, dict] = {}


# --- Models ---

class TopicCreate(BaseModel):
    name: str
    description: str = ""
    max_total_usd: Optional[float] = None
    max_per_day_usd: Optional[float] = None


class BudgetUpdate(BaseModel):
    max_total_usd: float
    max_per_day_usd: float


class TextBody(BaseModel):
    content: str


# --- Helpers ---

def _topic_dir(name: str) -> Path:
    d = ROOT / "topics" / name
    if not d.exists():
        raise HTTPException(404, f"Topic '{name}' not found")
    return d


def _paths_for(name: str) -> dict:
    """Build resolve_topic_paths-style dict for an arbitrary topic."""
    base = ROOT / "topics" / name
    if not base.exists():
        raise HTTPException(404, f"Topic '{name}' not found")
    return {
        "base": base,
        "mission": base / "mission.md",
        "system_prompt": base / "system_prompt.md",
        "identity": base / "identity.json",
        "data_dir": base / "data",
        "research_dir": base / "data" / "research",
        "costs_file": base / "data" / "costs.json",
        "cycles_file": base / "data" / "cycles.jsonl",
        "patches_dir": base / "data" / "pending_patches",
    }


def _load_agent_budget(name: str) -> dict | None:
    """Load per-agent budget from agent_config.yaml, or None."""
    cfg_path = ROOT / "topics" / name / "agent_config.yaml"
    if cfg_path.exists():
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
        return cfg.get("budget")
    return None


def _topic_summary(name: str, active_topic: str) -> dict:
    paths = _paths_for(name)
    costs_file = paths["costs_file"]
    cost = _load_ledger(costs_file)["total_usd"] if costs_file.exists() else 0.0
    agent_budget = _load_agent_budget(name)
    entry = _running_agents.get(name)
    running = False
    if entry:
        # Check if process is still alive
        if entry["process"].poll() is None:
            running = True
        else:
            # Process ended — clean up
            del _running_agents[name]
    return {
        "name": name,
        "cycles": get_cycle_count(paths),
        "cost": round(cost, 4),
        "active": name == active_topic,
        "running": running,
        "budget": agent_budget,
    }


# --- Endpoints ---

@app.get("/", response_class=HTMLResponse)
def index():
    html = STATIC_DIR / "index.html"
    if not html.exists():
        raise HTTPException(404, "static/index.html not found")
    return FileResponse(html)


@app.get("/logo.png")
def logo():
    logo_path = ROOT / "deepshika.png"
    if not logo_path.exists():
        raise HTTPException(404, "logo not found")
    return FileResponse(logo_path, media_type="image/png")


@app.get("/api/config")
def api_config():
    config = load_config()
    budget = config.get("budget", {})
    return {
        "active_topic": config.get("active_topic", ""),
        "budget": budget,
    }


@app.get("/api/topics")
def api_topics():
    config = load_config()
    active = config.get("active_topic", "")
    topics_dir = ROOT / "topics"
    if not topics_dir.exists():
        return []
    dirs = sorted(d.name for d in topics_dir.iterdir() if d.is_dir())
    return [_topic_summary(name, active) for name in dirs]


@app.get("/api/topics/{name}")
def api_topic_detail(name: str):
    paths = _paths_for(name)
    config = load_config()
    identity = load_identity(paths)
    cost_info = get_summary(paths["costs_file"])
    mission_text = load_mission(paths)
    research_dir = paths["research_dir"]
    research_files = []
    if research_dir.exists():
        research_files = [
            {"name": f.name, "size": f.stat().st_size}
            for f in sorted(research_dir.glob("*.md"))
        ]
    return {
        "name": name,
        "active": name == config.get("active_topic", ""),
        "identity": identity,
        "cycles": get_cycle_count(paths),
        "cost": cost_info,
        "mission_preview": mission_text[:500] if mission_text else "",
        "research_files": research_files,
    }


@app.post("/api/topics")
def api_create_topic(body: TopicCreate):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Name required")
    topic_dir = ROOT / "topics" / name
    if topic_dir.exists():
        raise HTTPException(409, f"Topic '{name}' already exists")

    desc = body.description or f"Research topic: {name}"

    # Create directory structure
    (topic_dir / "data" / "research").mkdir(parents=True)
    (topic_dir / "data" / "pending_patches").mkdir(parents=True)

    # Attempt LLM-generated scaffold
    config = load_config()
    scaffold = generate_agent_scaffold(name, desc, config)
    scaffolded = False
    output_file_names = []

    if scaffold:
        (topic_dir / "mission.md").write_text(scaffold["mission"])
        (topic_dir / "system_prompt.md").write_text(scaffold["system_prompt"])

        # Pre-create output files
        research_dir = topic_dir / "data" / "research"
        for of in scaffold["output_files"]:
            fn = of["filename"]
            question = of.get("question", "")
            title = fn.replace("_", " ").replace(".md", "").title()
            (research_dir / fn).write_text(
                f"# {title}\n\n<!-- Key question: {question} -->\n\n"
            )
            output_file_names.append(fn)

        # Record LLM cost
        if scaffold.get("_usage"):
            from costs import record_call
            pricing = config.get("pricing", {"input_per_mtok": 1.0, "output_per_mtok": 5.0})
            record_call(
                scaffold["_usage"]["input_tokens"],
                scaffold["_usage"]["output_tokens"],
                pricing,
                label="scaffold_generation",
                costs_file=topic_dir / "data" / "costs.json",
            )

        scaffolded = True
    else:
        _write_static_scaffold(topic_dir, name, desc)

    (topic_dir / "identity.json").write_text(json.dumps({
        "name": "Deepshika",
        "version": "0.1.0",
        "born": datetime.now(timezone.utc).isoformat(),
        "traits": ["curious", "systematic", "depth-over-breadth"],
        "purpose": desc,
        "modification_history": [],
    }, indent=2))

    # Write per-agent budget config if provided
    if body.max_total_usd is not None or body.max_per_day_usd is not None:
        agent_cfg = {"budget": {}}
        if body.max_total_usd is not None:
            agent_cfg["budget"]["max_total_usd"] = body.max_total_usd
        if body.max_per_day_usd is not None:
            agent_cfg["budget"]["max_per_day_usd"] = body.max_per_day_usd
        (topic_dir / "agent_config.yaml").write_text(
            yaml.dump(agent_cfg, default_flow_style=False, sort_keys=False)
        )

    return {"created": name, "scaffolded": scaffolded, "output_files": output_file_names}


@app.post("/api/topics/{name}/switch")
def api_switch_topic(name: str):
    _topic_dir(name)  # validates existence
    config = load_config()
    config["active_topic"] = name
    save_config(config)
    return {"active_topic": name}


@app.get("/api/topics/{name}/research")
def api_research_list(name: str):
    paths = _paths_for(name)
    research_dir = paths["research_dir"]
    if not research_dir.exists():
        return []
    return [
        {"name": f.name, "size": f.stat().st_size}
        for f in sorted(research_dir.glob("*.md"))
    ]


@app.get("/api/topics/{name}/research/{file}")
def api_research_file(name: str, file: str):
    paths = _paths_for(name)
    f = paths["research_dir"] / file
    if not f.exists():
        raise HTTPException(404, f"Research file '{file}' not found")
    return {"name": file, "content": f.read_text()}


@app.get("/api/topics/{name}/mission")
def api_get_mission(name: str):
    paths = _paths_for(name)
    return {"content": load_mission(paths)}


@app.put("/api/topics/{name}/mission")
def api_put_mission(name: str, body: TextBody):
    paths = _paths_for(name)
    paths["mission"].write_text(body.content)
    return {"saved": True}


@app.get("/api/topics/{name}/prompt")
def api_get_prompt(name: str):
    paths = _paths_for(name)
    sp = paths["system_prompt"]
    content = sp.read_text() if sp and sp.exists() else ""
    return {"content": content}


@app.put("/api/topics/{name}/prompt")
def api_put_prompt(name: str, body: TextBody):
    paths = _paths_for(name)
    paths["system_prompt"].write_text(body.content)
    return {"saved": True}


@app.get("/api/topics/{name}/cycles")
def api_cycles(name: str, n: int = 20):
    paths = _paths_for(name)
    return get_recent_cycles(n, paths)


@app.get("/api/topics/{name}/costs")
def api_costs(name: str):
    paths = _paths_for(name)
    summary = get_summary(paths["costs_file"])
    ledger = _load_ledger(paths["costs_file"])
    return {
        "summary": summary,
        "calls": ledger.get("calls", [])[-50:],  # last 50 calls
    }


# --- Agent process management ---

@app.post("/api/agents/{name}/start")
def api_start_agent(name: str, delay: int = 10):
    """Spawn a subprocess running deepshika run-topic for this topic."""
    _topic_dir(name)  # validates existence
    if name in _running_agents:
        proc = _running_agents[name]["process"]
        if proc.poll() is None:
            raise HTTPException(409, f"Agent '{name}' is already running (pid {proc.pid})")
        # Process ended — clean up stale entry
        del _running_agents[name]

    log_path = ROOT / "topics" / name / "data" / "agent.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "a")

    proc = subprocess.Popen(
        [sys.executable, "-m", "agent", "run-topic", name, "--cycles", "0", "--delay", str(delay)],
        cwd=str(ROOT),
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )

    _running_agents[name] = {
        "process": proc,
        "pid": proc.pid,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "log_file": log_file,
    }

    return {"started": name, "pid": proc.pid}


@app.post("/api/agents/{name}/stop")
def api_stop_agent(name: str):
    """Terminate a running agent subprocess."""
    if name not in _running_agents:
        raise HTTPException(404, f"Agent '{name}' is not running")

    entry = _running_agents[name]
    proc = entry["process"]

    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    # Close log file handle
    if "log_file" in entry:
        entry["log_file"].close()

    del _running_agents[name]
    return {"stopped": name}


@app.get("/api/agents/status")
def api_agents_status():
    """Return running/stopped status + budget + spend for all topics."""
    topics_dir = ROOT / "topics"
    if not topics_dir.exists():
        return []

    result = []
    for d in sorted(topics_dir.iterdir()):
        if not d.is_dir():
            continue
        name = d.name
        costs_file = d / "data" / "costs.json"
        cost = _load_ledger(costs_file)["total_usd"] if costs_file.exists() else 0.0
        agent_budget = _load_agent_budget(name)

        entry = _running_agents.get(name)
        running = False
        pid = None
        started_at = None
        if entry:
            if entry["process"].poll() is None:
                running = True
                pid = entry["pid"]
                started_at = entry["started_at"]
            else:
                if "log_file" in entry:
                    entry["log_file"].close()
                del _running_agents[name]

        result.append({
            "name": name,
            "running": running,
            "pid": pid,
            "started_at": started_at,
            "cost": round(cost, 4),
            "budget": agent_budget,
        })

    return result


@app.put("/api/topics/{name}/budget")
def api_update_budget(name: str, body: BudgetUpdate):
    """Update per-agent budget in agent_config.yaml."""
    _topic_dir(name)  # validates existence
    cfg_path = ROOT / "topics" / name / "agent_config.yaml"
    if cfg_path.exists():
        agent_cfg = yaml.safe_load(cfg_path.read_text()) or {}
    else:
        agent_cfg = {}

    agent_cfg["budget"] = {
        "max_total_usd": body.max_total_usd,
        "max_per_day_usd": body.max_per_day_usd,
    }
    cfg_path.write_text(
        yaml.dump(agent_cfg, default_flow_style=False, sort_keys=False)
    )
    return {"saved": True, "budget": agent_cfg["budget"]}


# --- Summarize ---

@app.post("/api/topics/{name}/summarize")
def api_summarize(name: str):
    """Summarize all research files for a topic using the LLM."""
    paths = _paths_for(name)
    research_dir = paths["research_dir"]
    if not research_dir.exists():
        raise HTTPException(404, "No research directory found")

    files = sorted(research_dir.glob("*.md"))
    if not files:
        raise HTTPException(404, "No research files to summarize")

    # Gather all research content
    research_parts = []
    for f in files:
        content = f.read_text().strip()
        if content:
            research_parts.append(f"## {f.name}\n\n{content}")

    if not research_parts:
        raise HTTPException(404, "Research files are empty")

    combined = "\n\n---\n\n".join(research_parts)

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise HTTPException(500, "OPENROUTER_API_KEY not set")

    config = load_config()
    model = config.get("scaffold_model", "anthropic/claude-sonnet-4.6")

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )

    response = client.chat.completions.create(
        model=model,
        max_tokens=4096,
        messages=[
            {"role": "system", "content": (
                "You are a research analyst. You will be given the full research output from an AI research agent. "
                "Produce a concise executive summary in markdown with:\n"
                "- A 2-3 sentence TL;DR at the top\n"
                "- Key findings (bulleted)\n"
                "- Top recommendations (numbered)\n"
                "- Open questions or gaps\n"
                "Be direct, specific, and reference real data points from the research."
            )},
            {"role": "user", "content": f"Summarize the following research:\n\n{combined}"},
        ],
    )

    summary = response.choices[0].message.content
    generated_at = datetime.now(timezone.utc).isoformat()

    # Save as JSON with timestamp
    summary_path = paths["base"] / "data" / "summary.json"
    summary_path.write_text(json.dumps({
        "summary": summary,
        "generated_at": generated_at,
    }, indent=2))

    return {"summary": summary, "generated_at": generated_at}


@app.get("/api/topics/{name}/summary")
def api_get_summary_file(name: str):
    """Return the saved summary if it exists."""
    paths = _paths_for(name)
    summary_path = paths["base"] / "data" / "summary.json"
    if not summary_path.exists():
        return {"summary": None, "generated_at": None}
    data = json.loads(summary_path.read_text())
    return {"summary": data.get("summary"), "generated_at": data.get("generated_at")}


# --- Shutdown hook ---

def _shutdown_agents():
    """Terminate all running agent subprocesses on exit."""
    for name, entry in list(_running_agents.items()):
        proc = entry["process"]
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        if "log_file" in entry:
            entry["log_file"].close()
    _running_agents.clear()


atexit.register(_shutdown_agents)
