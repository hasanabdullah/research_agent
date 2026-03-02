"""Deepshika web dashboard — topic management UI."""

import atexit
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests as http_requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
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
from llm import get_client, resolve_model

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


class BucketNotionPage(BaseModel):
    notion_page_id: str


class NotionConfig(BaseModel):
    token: str = ""
    root_page_id: str = ""


class LlmConfig(BaseModel):
    provider: str = ""
    api_key: str = ""


class TopicIdentityUpdate(BaseModel):
    purpose: str = ""


class TopicRename(BaseModel):
    new_name: str


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
        "agent_parameters": base / "agent_parameters.md",
        "identity": base / "identity.json",
        "data_dir": base / "data",
        "research_dir": base / "data" / "research",
        "costs_file": base / "data" / "costs.json",
        "cycles_file": base / "data" / "cycles.jsonl",
        "patches_dir": base / "data" / "pending_patches",
    }


def _research_files(research_dir: Path) -> list[Path]:
    """Glob both *.md and *.txt research files, deduped and sorted."""
    files = list(research_dir.glob("*.md")) + list(research_dir.glob("*.txt"))
    return sorted(set(files), key=lambda f: f.name)


def _load_agent_budget(name: str) -> dict | None:
    """Load per-agent budget from agent_config.yaml, or None."""
    cfg_path = ROOT / "topics" / name / "agent_config.yaml"
    if cfg_path.exists():
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        return cfg.get("budget")
    return None


def _load_buckets(name: str) -> list[dict]:
    """Load research_buckets from agent_config.yaml, or empty list."""
    cfg_path = ROOT / "topics" / name / "agent_config.yaml"
    if cfg_path.exists():
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        return cfg.get("research_buckets", [])
    return []


def _save_buckets(name: str, buckets: list[dict]):
    """Write research_buckets back to agent_config.yaml, preserving other keys."""
    cfg_path = ROOT / "topics" / name / "agent_config.yaml"
    if cfg_path.exists():
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    else:
        cfg = {}
    cfg["research_buckets"] = buckets
    cfg_path.write_text(
        yaml.dump(cfg, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


def _topic_summary(name: str, active_topic: str) -> dict:
    paths = _paths_for(name)
    costs_file = paths["costs_file"]
    cost = _load_ledger(costs_file).get("total_usd", 0.0) if costs_file.exists() else 0.0
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
    # Checkpoint state
    cp_file = ROOT / "topics" / name / "data" / "checkpoints.json"
    checkpoint = None
    if cp_file.exists():
        cp_data = json.loads(cp_file.read_text(encoding="utf-8"))
        cp_hit = cp_data.get("checkpoints_hit", [])
        if cp_hit and not running:
            last_cp = max(cp_hit)
            checkpoint = {
                "threshold": last_cp,
                "label": f"{int(last_cp * 100)}%",
                "checkpoints_hit": cp_hit,
            }

    return {
        "name": name,
        "cycles": get_cycle_count(paths),
        "cost": round(cost, 4),
        "active": name == active_topic,
        "running": running,
        "budget": agent_budget,
        "checkpoint": checkpoint,
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
            for f in _research_files(research_dir)
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
        (topic_dir / "mission.md").write_text(scaffold["mission"], encoding="utf-8")
        (topic_dir / "agent_parameters.md").write_text(scaffold["agent_parameters"], encoding="utf-8")

        # Pre-create output files
        research_dir = topic_dir / "data" / "research"
        for of in scaffold["output_files"]:
            fn = of["filename"]
            question = of.get("question", "")
            title = fn.replace("_", " ").replace(".md", "").title()
            (research_dir / fn).write_text(
                f"# {title}\n\n<!-- Key question: {question} -->\n\n",
                encoding="utf-8",
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
    }, indent=2), encoding="utf-8")

    # Write per-agent config (budget + research buckets)
    cfg_path = topic_dir / "agent_config.yaml"
    agent_cfg = {}
    if cfg_path.exists():
        agent_cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    if body.max_total_usd is not None or body.max_per_day_usd is not None:
        agent_cfg.setdefault("budget", {})
        if body.max_total_usd is not None:
            agent_cfg["budget"]["max_total_usd"] = body.max_total_usd
        if body.max_per_day_usd is not None:
            agent_cfg["budget"]["max_per_day_usd"] = body.max_per_day_usd

    if scaffold and scaffold.get("research_buckets"):
        agent_cfg["research_buckets"] = scaffold["research_buckets"]

    if agent_cfg:
        cfg_path.write_text(
            yaml.dump(agent_cfg, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )

    return {"created": name, "scaffolded": scaffolded, "output_files": output_file_names}


@app.delete("/api/topics/{name}")
def api_delete_topic(name: str):
    """Delete a topic and all its data. Stops the agent if running."""
    topic_dir = _topic_dir(name)  # validates existence

    # Stop agent if running
    if name in _running_agents:
        entry = _running_agents[name]
        proc = entry["process"]
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        if "log_file" in entry:
            entry["log_file"].close()
        del _running_agents[name]

    # Remove the directory tree
    import shutil
    shutil.rmtree(topic_dir)

    # If this was the active topic, clear it
    config = load_config()
    if config.get("active_topic") == name:
        config["active_topic"] = ""
        save_config(config)

    return {"deleted": name}


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
        for f in _research_files(research_dir)
    ]


@app.get("/api/topics/{name}/research/{file}")
def api_research_file(name: str, file: str):
    paths = _paths_for(name)
    f = paths["research_dir"] / file
    if not f.exists():
        raise HTTPException(404, f"Research file '{file}' not found")
    return {"name": file, "content": f.read_text(encoding="utf-8")}


# --- Research Buckets ---

@app.get("/api/topics/{name}/buckets")
def api_get_buckets(name: str):
    """Return enriched bucket list with file sizes and publish status, plus unbucketed files."""
    paths = _paths_for(name)
    research_dir = paths["research_dir"]
    buckets = _load_buckets(name)

    all_files = {}
    if research_dir.exists():
        for f in _research_files(research_dir):
            all_files[f.name] = {"name": f.name, "size": f.stat().st_size}

    if not buckets:
        return {
            "has_buckets": False,
            "buckets": [],
            "other_files": list(all_files.values()),
        }

    # Load publish state for status info
    publish_state_path = paths["data_dir"] / "notion_publish.json"
    publish_state = {}
    if publish_state_path.exists():
        publish_state = json.loads(publish_state_path.read_text(encoding="utf-8"))

    bucketed_names = set()
    enriched = []
    for i, b in enumerate(buckets):
        bucket_files = []
        total_size = 0
        for fn in b.get("files", []):
            if fn in all_files:
                bucket_files.append(all_files[fn])
                total_size += all_files[fn]["size"]
                bucketed_names.add(fn)
            else:
                bucket_files.append({"name": fn, "size": 0})
                bucketed_names.add(fn)

        # Check publish status for this bucket
        pub_info = publish_state.get(f"_bucket_{i}")
        published_at = pub_info.get("published_at") if isinstance(pub_info, dict) else None

        enriched.append({
            "index": i,
            "name": b.get("name", f"Bucket {i+1}"),
            "files": bucket_files,
            "total_size": total_size,
            "notion_page_id": b.get("notion_page_id", ""),
            "published_at": published_at,
        })

    other_files = [all_files[fn] for fn in sorted(all_files.keys()) if fn not in bucketed_names]

    return {
        "has_buckets": True,
        "buckets": enriched,
        "other_files": other_files,
    }


@app.put("/api/topics/{name}/buckets/{index}/notion-page")
def api_update_bucket_notion_page(name: str, index: int, body: BucketNotionPage):
    """Update one bucket's notion_page_id."""
    _topic_dir(name)
    buckets = _load_buckets(name)
    if index < 0 or index >= len(buckets):
        raise HTTPException(404, f"Bucket index {index} out of range")
    buckets[index]["notion_page_id"] = body.notion_page_id
    _save_buckets(name, buckets)
    return {"saved": True, "index": index, "notion_page_id": body.notion_page_id}


@app.post("/api/topics/{name}/buckets/{index}/publish")
def api_publish_bucket(name: str, index: int):
    """Publish only one bucket's files to its notion_page_id."""
    paths = _paths_for(name)
    research_dir = paths["research_dir"]
    buckets = _load_buckets(name)
    if index < 0 or index >= len(buckets):
        raise HTTPException(404, f"Bucket index {index} out of range")

    bucket = buckets[index]
    page_id = bucket.get("notion_page_id", "").strip()
    if not page_id:
        raise HTTPException(400, "No Notion page ID set for this bucket")

    notion_cfg = _load_notion_config()
    if not notion_cfg["token"]:
        raise HTTPException(500, "Notion token not configured")

    headers = _notion_headers(notion_cfg["token"])

    # Gather content from bucket files
    parts = []
    for fn in bucket.get("files", []):
        fpath = research_dir / fn
        if fpath.exists():
            content = fpath.read_text(encoding="utf-8").strip()
            if content:
                parts.append(f"## {fn}\n\n{content}")

    if not parts:
        raise HTTPException(404, "No content in bucket files to publish")

    combined = "\n\n---\n\n".join(parts)
    blocks = _md_to_notion_blocks(combined)
    title = bucket.get("name", f"Bucket {index + 1}")

    # Load publish state
    publish_state_path = paths["data_dir"] / "notion_publish.json"
    publish_state = {}
    if publish_state_path.exists():
        publish_state = json.loads(publish_state_path.read_text(encoding="utf-8"))

    now = datetime.now(timezone.utc).isoformat()
    bucket_key = f"_bucket_{index}"
    existing = publish_state.get(bucket_key)

    try:
        if existing and isinstance(existing, dict) and existing.get("page_id"):
            try:
                _notion_update_page(existing["page_id"], title, blocks, headers)
                result_page_id = existing["page_id"]
                url = existing.get("url", f"https://notion.so/{result_page_id.replace('-', '')}")
            except http_requests.HTTPError:
                page = _notion_create_page(page_id, title, blocks, headers)
                result_page_id = page["id"]
                url = page.get("url", f"https://notion.so/{result_page_id.replace('-', '')}")
        else:
            page = _notion_create_page(page_id, title, blocks, headers)
            result_page_id = page["id"]
            url = page.get("url", f"https://notion.so/{result_page_id.replace('-', '')}")

        publish_state[bucket_key] = {
            "page_id": result_page_id,
            "url": url,
            "published_at": now,
            "bucket_index": index,
        }
        publish_state_path.write_text(json.dumps(publish_state, indent=2), encoding="utf-8")

        return {
            "published_at": now,
            "url": url,
            "bucket_index": index,
            "bucket_name": title,
            "status": "ok",
        }
    except http_requests.HTTPError as e:
        raise HTTPException(502, f"Notion API error: {e}")


@app.get("/api/topics/{name}/mission")
def api_get_mission(name: str):
    paths = _paths_for(name)
    return {"content": load_mission(paths)}


@app.put("/api/topics/{name}/mission")
def api_put_mission(name: str, body: TextBody):
    paths = _paths_for(name)
    paths["mission"].write_text(body.content, encoding="utf-8")
    return {"saved": True}


@app.get("/api/topics/{name}/prompt")
def api_get_prompt(name: str):
    paths = _paths_for(name)
    sp = paths["agent_parameters"]
    content = sp.read_text(encoding="utf-8") if sp and sp.exists() else ""
    return {"content": content}


@app.put("/api/topics/{name}/prompt")
def api_put_prompt(name: str, body: TextBody):
    paths = _paths_for(name)
    paths["agent_parameters"].write_text(body.content, encoding="utf-8")
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


# --- Error detection ---

_ERROR_PATTERNS = [
    (re.compile(r"Traceback \(most recent call last\)"), True),   # start of traceback
    (re.compile(r"^\w*Error:?\s+(.+)", re.MULTILINE), False),    # ErrorType: message
    (re.compile(r"^\w*Exception:?\s+(.+)", re.MULTILINE), False),
    (re.compile(r"Budget exceeded", re.IGNORECASE), False),
    (re.compile(r"FAILED", re.IGNORECASE), False),
]


def _summarize_error(lines: list[str]) -> str | None:
    """Extract a human-readable error summary from log tail lines."""
    text = "\n".join(lines)

    # Look for the last Python exception (ErrorType: message)
    exc_match = None
    for m in re.finditer(r"^(\w+(?:Error|Exception)):\s*(.+)", text, re.MULTILINE):
        exc_match = m
    if exc_match:
        err_type = exc_match.group(1)
        err_msg = exc_match.group(2).strip()[:200]
        # Make it more readable
        spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", err_type).lower()
        return f"{err_type}: {err_msg}"

    # Budget exceeded
    for line in reversed(lines):
        if re.search(r"budget exceeded", line, re.IGNORECASE):
            return "Budget exceeded — agent stopped because spending limit was reached"

    # Generic FAILED
    for line in reversed(lines):
        if re.search(r"FAILED", line, re.IGNORECASE):
            clean = line.strip()[:200]
            return f"Agent failure: {clean}"

    return None


@app.get("/api/topics/{name}/errors")
def api_topic_errors(name: str):
    """Check agent.log for errors and detect unexpected process exit."""
    _topic_dir(name)  # validates existence

    log_path = ROOT / "topics" / name / "data" / "agent.log"
    has_error = False
    message = ""
    timestamp = ""

    # Check for unexpected process exit
    entry = _running_agents.get(name)
    unexpected_exit = False
    if entry:
        proc = entry["process"]
        rc = proc.poll()
        if rc is not None and rc != 0:
            unexpected_exit = True
            message = f"Agent process exited unexpectedly (exit code {rc})"

    # Read log tail for error patterns
    if log_path.exists():
        try:
            raw = log_path.read_bytes()
            text = raw.decode("utf-8", errors="replace")
            all_lines = text.splitlines()
            tail = all_lines[-50:] if len(all_lines) > 50 else all_lines

            # Check for error patterns
            tail_text = "\n".join(tail)
            found_error = False
            for pat, _ in _ERROR_PATTERNS:
                if pat.search(tail_text):
                    found_error = True
                    break

            if found_error:
                summary = _summarize_error(tail)
                if summary:
                    has_error = True
                    message = summary

            # Get timestamp from last log line
            if tail:
                # Try to extract timestamp from log line
                ts_match = re.match(r"(\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2})", tail[-1])
                if ts_match:
                    timestamp = ts_match.group(1)
                else:
                    timestamp = datetime.now(timezone.utc).isoformat()
        except Exception:
            pass

    if unexpected_exit and not has_error:
        has_error = True
        if not timestamp:
            timestamp = datetime.now(timezone.utc).isoformat()

    return {"has_error": has_error, "message": message, "timestamp": timestamp}


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
        cost = _load_ledger(costs_file).get("total_usd", 0.0) if costs_file.exists() else 0.0
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

        # Checkpoint state
        cp_file = d / "data" / "checkpoints.json"
        checkpoint = None
        if cp_file.exists():
            cp_data = json.loads(cp_file.read_text(encoding="utf-8"))
            cp_hit = cp_data.get("checkpoints_hit", [])
            if cp_hit and not running:
                last_cp = max(cp_hit)
                checkpoint = {
                    "threshold": last_cp,
                    "label": f"{int(last_cp * 100)}%",
                    "checkpoints_hit": cp_hit,
                }

        result.append({
            "name": name,
            "running": running,
            "pid": pid,
            "started_at": started_at,
            "cost": round(cost, 4),
            "budget": agent_budget,
            "checkpoint": checkpoint,
        })

    return result


@app.put("/api/topics/{name}/budget")
def api_update_budget(name: str, body: BudgetUpdate):
    """Update per-agent budget in agent_config.yaml."""
    _topic_dir(name)  # validates existence
    cfg_path = ROOT / "topics" / name / "agent_config.yaml"
    if cfg_path.exists():
        agent_cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    else:
        agent_cfg = {}

    agent_cfg["budget"] = {
        "max_total_usd": body.max_total_usd,
        "max_per_day_usd": body.max_per_day_usd,
    }
    cfg_path.write_text(
        yaml.dump(agent_cfg, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    return {"saved": True, "budget": agent_cfg["budget"]}


@app.put("/api/topics/{name}/identity")
def api_update_identity(name: str, body: TopicIdentityUpdate):
    """Update identity.json fields (purpose/description)."""
    paths = _paths_for(name)
    id_path = paths["identity"]
    if id_path.exists():
        identity = json.loads(id_path.read_text(encoding="utf-8"))
    else:
        identity = {}
    identity["purpose"] = body.purpose
    id_path.write_text(json.dumps(identity, indent=2), encoding="utf-8")
    return {"saved": True}


@app.post("/api/topics/{name}/rename")
def api_rename_topic(name: str, body: TopicRename):
    """Rename a topic directory."""
    new_name = body.new_name.strip()
    if not new_name:
        raise HTTPException(400, "New name is required")
    if re.search(r'[\\/:*?"<>|\s]', new_name):
        raise HTTPException(400, "Name cannot contain spaces or special characters")
    old_dir = _topic_dir(name)
    new_dir = ROOT / "topics" / new_name
    if new_dir.exists():
        raise HTTPException(409, f"Topic '{new_name}' already exists")
    # Refuse if agent is running
    if name in _running_agents and _running_agents[name]["process"].poll() is None:
        raise HTTPException(409, "Cannot rename a running agent — stop it first")
    # Move directory
    import shutil
    shutil.move(str(old_dir), str(new_dir))
    # Update active_topic in config.yaml if needed
    config = load_config()
    if config.get("active_topic") == name:
        config["active_topic"] = new_name
        save_config(config)
    return {"saved": True, "new_name": new_name}


# --- Summarize ---

@app.post("/api/topics/{name}/summarize")
def api_summarize(name: str):
    """Summarize all research files for a topic using the LLM."""
    paths = _paths_for(name)
    research_dir = paths["research_dir"]
    if not research_dir.exists():
        raise HTTPException(404, "No research directory found")

    files = _research_files(research_dir)
    if not files:
        raise HTTPException(404, "No research files to summarize")

    # Gather all research content
    research_parts = []
    for f in files:
        content = f.read_text(encoding="utf-8").strip()
        if content:
            research_parts.append(f"## {f.name}\n\n{content}")

    if not research_parts:
        raise HTTPException(404, "Research files are empty")

    combined = "\n\n---\n\n".join(research_parts)

    config = load_config()
    model = config.get("scaffold_model", "anthropic/claude-sonnet-4.6")

    client = get_client()

    response = client.chat.completions.create(
        model=resolve_model(model),
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
    }, indent=2), encoding="utf-8")

    return {"summary": summary, "generated_at": generated_at}


@app.get("/api/topics/{name}/summary")
def api_get_summary_file(name: str):
    """Return the saved summary if it exists."""
    paths = _paths_for(name)
    summary_path = paths["base"] / "data" / "summary.json"
    if not summary_path.exists():
        return {"summary": None, "generated_at": None}
    data = json.loads(summary_path.read_text(encoding="utf-8"))
    return {"summary": data.get("summary"), "generated_at": data.get("generated_at")}


# --- Notion publishing ---

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def _load_notion_config() -> dict:
    """Load Notion config from config.yaml, falling back to env vars."""
    cfg = load_config()
    notion = cfg.get("notion", {}) or {}
    token = notion.get("token", "") or os.environ.get("NOTION_TOKEN", "")
    root_page_id = notion.get("root_page_id", "") or os.environ.get("NOTION_PAGE_ID", "")
    hub_database_id = notion.get("hub_database_id", "")
    return {
        "token": token,
        "root_page_id": root_page_id,
        "hub_database_id": hub_database_id,
    }


def _save_notion_config(notion_cfg: dict):
    """Upsert the notion: block in config.yaml, preserving other content."""
    cfg_path = ROOT / "config.yaml"
    text = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""

    # Build the YAML snippet for the notion block
    lines = ["notion:"]
    for key in ("token", "root_page_id", "hub_database_id"):
        val = notion_cfg.get(key, "")
        lines.append(f'  {key}: "{val}"')
    snippet = "\n".join(lines)

    # Replace existing notion: block or append
    if re.search(r"^notion:\s*$", text, re.MULTILINE):
        # Remove the old notion block (notion: line + indented lines following it)
        text = re.sub(
            r"^notion:\s*\n(?:[ \t]+\S.*\n?)*",
            snippet + "\n",
            text,
            flags=re.MULTILINE,
        )
    else:
        text = text.rstrip() + "\n\n" + snippet + "\n"

    cfg_path.write_text(text, encoding="utf-8")


def _notion_headers(token: str = None) -> dict:
    if not token:
        token = _load_notion_config()["token"]
    if not token:
        raise HTTPException(500, "Notion token not configured")
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _md_to_notion_blocks(md_text: str) -> list[dict]:
    """Convert markdown text to Notion block objects.

    Handles headings, unordered/ordered lists, dividers, and paragraphs.
    Content is chunked to respect Notion's 2000-char limit per rich text span.
    """
    blocks: list[dict] = []
    lines = md_text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]

        # Divider
        if re.match(r"^-{3,}$", line.strip()):
            blocks.append({"object": "block", "type": "divider", "divider": {}})
            i += 1
            continue

        # Headings
        h3 = re.match(r"^### (.+)$", line)
        if h3:
            blocks.append(_heading_block(3, h3.group(1)))
            i += 1
            continue
        h2 = re.match(r"^## (.+)$", line)
        if h2:
            blocks.append(_heading_block(2, h2.group(1)))
            i += 1
            continue
        h1 = re.match(r"^# (.+)$", line)
        if h1:
            blocks.append(_heading_block(1, h1.group(1)))
            i += 1
            continue

        # Unordered list item
        ul = re.match(r"^[-*] (.+)$", line)
        if ul:
            blocks.append({
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": _rich_text(ul.group(1))},
            })
            i += 1
            continue

        # Ordered list item
        ol = re.match(r"^\d+\. (.+)$", line)
        if ol:
            blocks.append({
                "object": "block",
                "type": "numbered_list_item",
                "numbered_list_item": {"rich_text": _rich_text(ol.group(1))},
            })
            i += 1
            continue

        # Empty line — skip
        if not line.strip():
            i += 1
            continue

        # Paragraph (collect consecutive non-empty, non-special lines)
        para_lines = [line]
        i += 1
        while i < len(lines):
            nxt = lines[i]
            if not nxt.strip():
                break
            if re.match(r"^(#{1,3} |[-*] |\d+\. |-{3,}$)", nxt):
                break
            para_lines.append(nxt)
            i += 1
        text = " ".join(para_lines)
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": _rich_text(text)},
        })

    return blocks


def _heading_block(level: int, text: str) -> dict:
    key = f"heading_{level}"
    return {"object": "block", "type": key, key: {"rich_text": _rich_text(text)}}


def _rich_text(text: str) -> list[dict]:
    """Split text into <=2000-char rich_text spans (Notion limit)."""
    spans = []
    for start in range(0, len(text), 2000):
        spans.append({"type": "text", "text": {"content": text[start : start + 2000]}})
    return spans or [{"type": "text", "text": {"content": ""}}]


def _notion_create_page(parent_id: str, title: str, blocks: list[dict], headers: dict) -> dict:
    """Create a child page under parent_id and append blocks."""
    body = {
        "parent": {"page_id": parent_id},
        "properties": {"title": [{"text": {"content": title}}]},
    }
    r = http_requests.post(f"{NOTION_API}/pages", headers=headers, json=body, timeout=30)
    r.raise_for_status()
    page = r.json()

    # Append blocks in batches of 100 (Notion limit)
    page_id = page["id"]
    for start in range(0, len(blocks), 100):
        chunk = blocks[start : start + 100]
        r2 = http_requests.patch(
            f"{NOTION_API}/blocks/{page_id}/children",
            headers=headers,
            json={"children": chunk},
            timeout=30,
        )
        r2.raise_for_status()

    return page


def _notion_update_page(page_id: str, title: str, blocks: list[dict], headers: dict) -> dict:
    """Update an existing page: clear old blocks, set title, append new blocks."""
    # Update title
    http_requests.patch(
        f"{NOTION_API}/pages/{page_id}",
        headers=headers,
        json={"properties": {"title": [{"text": {"content": title}}]}},
        timeout=30,
    ).raise_for_status()

    # Delete existing children
    children_r = http_requests.get(
        f"{NOTION_API}/blocks/{page_id}/children?page_size=100",
        headers=headers,
        timeout=30,
    )
    children_r.raise_for_status()
    for child in children_r.json().get("results", []):
        http_requests.delete(
            f"{NOTION_API}/blocks/{child['id']}",
            headers=headers,
            timeout=30,
        )

    # Append new blocks in batches
    for start in range(0, len(blocks), 100):
        chunk = blocks[start : start + 100]
        http_requests.patch(
            f"{NOTION_API}/blocks/{page_id}/children",
            headers=headers,
            json={"children": chunk},
            timeout=30,
        ).raise_for_status()

    return {"id": page_id}


# --- Notion database hub ---

NOTION_DB_SCHEMA = {
    "Name": {"title": {}},
    "Status": {
        "select": {
            "options": [
                {"name": "Not Started", "color": "default"},
                {"name": "In Progress", "color": "blue"},
                {"name": "Checkpoint", "color": "yellow"},
                {"name": "Completed", "color": "green"},
            ]
        }
    },
    "Budget Spent": {"number": {"format": "dollar"}},
    "Budget Total": {"number": {"format": "dollar"}},
    "Cycles": {"number": {"format": "number"}},
    "Last Published": {"date": {}},
}


def _notion_ensure_hub_database(headers: dict, notion_cfg: dict) -> str:
    """Return the hub database ID, creating it if needed."""
    db_id = notion_cfg.get("hub_database_id", "")

    # Verify existing database still exists
    if db_id:
        try:
            r = http_requests.get(
                f"{NOTION_API}/databases/{db_id}",
                headers=headers,
                timeout=15,
            )
            if r.status_code == 200 and not r.json().get("archived"):
                return db_id
        except Exception:
            pass
        # Database gone — will recreate
        db_id = ""

    # Create new database under root_page_id
    root_page_id = notion_cfg.get("root_page_id", "")
    if not root_page_id:
        raise HTTPException(500, "Notion root_page_id not configured")

    body = {
        "parent": {"page_id": root_page_id},
        "is_inline": True,
        "title": [{"type": "text", "text": {"content": "Deepshika Research Hub"}}],
        "properties": NOTION_DB_SCHEMA,
    }
    r = http_requests.post(
        f"{NOTION_API}/databases",
        headers=headers,
        json=body,
        timeout=30,
    )
    r.raise_for_status()
    db = r.json()
    db_id = db["id"]

    # Save back to config
    notion_cfg["hub_database_id"] = db_id
    _save_notion_config(notion_cfg)

    return db_id


def _notion_find_topic_row(db_id: str, topic_name: str, headers: dict) -> str | None:
    """Find an existing non-archived row by title. Returns page_id or None."""
    body = {
        "filter": {
            "property": "Name",
            "title": {"equals": topic_name},
        }
    }
    r = http_requests.post(
        f"{NOTION_API}/databases/{db_id}/query",
        headers=headers,
        json=body,
        timeout=15,
    )
    r.raise_for_status()
    for row in r.json().get("results", []):
        if not row.get("archived"):
            return row["id"]
    return None


def _notion_upsert_topic_row(
    db_id: str,
    topic_name: str,
    status: str,
    budget_spent: float,
    budget_total: float,
    cycles: int,
    headers: dict,
    existing_row_id: str = None,
) -> dict:
    """Create or update a database row with topic metadata."""
    now_iso = datetime.now(timezone.utc).isoformat()
    properties = {
        "Name": {"title": [{"text": {"content": topic_name}}]},
        "Status": {"select": {"name": status}},
        "Budget Spent": {"number": round(budget_spent, 4)},
        "Budget Total": {"number": round(budget_total, 2)},
        "Cycles": {"number": cycles},
        "Last Published": {"date": {"start": now_iso}},
    }

    if existing_row_id:
        r = http_requests.patch(
            f"{NOTION_API}/pages/{existing_row_id}",
            headers=headers,
            json={"properties": properties},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()
    else:
        body = {
            "parent": {"database_id": db_id},
            "properties": properties,
        }
        r = http_requests.post(
            f"{NOTION_API}/pages",
            headers=headers,
            json=body,
            timeout=30,
        )
        r.raise_for_status()
        return r.json()


def _compute_topic_status(name: str, paths: dict) -> str:
    """Determine the current status of a topic."""
    # Running?
    if name in _running_agents:
        entry = _running_agents[name]
        if entry["process"].poll() is None:
            return "In Progress"

    # Checkpoint?
    cp_file = paths["data_dir"] / "checkpoints.json"
    if cp_file.exists():
        cp_data = json.loads(cp_file.read_text(encoding="utf-8"))
        if cp_data.get("checkpoints_hit"):
            return "Checkpoint"

    # Any cycles run?
    if get_cycle_count(paths) == 0:
        return "Not Started"

    return "Completed"


# --- Notion config API endpoints ---

@app.get("/api/notion/config")
def api_get_notion_config():
    """Return Notion config with masked token."""
    cfg = _load_notion_config()
    token = cfg.get("token", "")
    masked = ""
    if token:
        masked = token[:8] + "..." + token[-4:] if len(token) > 12 else "***"
    return {
        "token_masked": masked,
        "token_set": bool(token),
        "root_page_id": cfg.get("root_page_id", ""),
        "hub_database_id": cfg.get("hub_database_id", ""),
    }


@app.put("/api/notion/config")
def api_put_notion_config(body: NotionConfig):
    """Save Notion token and root_page_id to config.yaml."""
    existing = _load_notion_config()
    notion_cfg = {
        "token": body.token if body.token else existing.get("token", ""),
        "root_page_id": body.root_page_id if body.root_page_id else existing.get("root_page_id", ""),
        "hub_database_id": existing.get("hub_database_id", ""),
    }
    _save_notion_config(notion_cfg)
    return {"status": "saved"}


# --- LLM config helpers & endpoints ---

def _load_llm_config() -> dict:
    """Load LLM config from config.yaml, falling back to env vars."""
    cfg = load_config()
    llm = cfg.get("llm", {}) or {}
    provider = llm.get("provider", "") or os.environ.get("LLM_PROVIDER", "")
    api_key = llm.get("api_key", "") or os.environ.get("LLM_API_KEY", "") or os.environ.get("OPENROUTER_API_KEY", "")
    return {"provider": provider, "api_key": api_key}


def _save_llm_config(llm_cfg: dict):
    """Upsert the llm: block in config.yaml, preserving other content."""
    cfg_path = ROOT / "config.yaml"
    text = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""

    lines = ["llm:"]
    for key in ("provider", "api_key"):
        val = llm_cfg.get(key, "")
        lines.append(f'  {key}: "{val}"')
    snippet = "\n".join(lines)

    if re.search(r"^llm:\s*$", text, re.MULTILINE):
        text = re.sub(
            r"^llm:\s*\n(?:[ \t]+\S.*\n?)*",
            snippet + "\n",
            text,
            flags=re.MULTILINE,
        )
    else:
        text = text.rstrip() + "\n\n" + snippet + "\n"

    cfg_path.write_text(text, encoding="utf-8")


@app.get("/api/llm/config")
def api_get_llm_config():
    """Return LLM config with masked api_key."""
    cfg = _load_llm_config()
    api_key = cfg.get("api_key", "")
    masked = ""
    if api_key:
        masked = api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else "***"
    return {
        "provider": cfg.get("provider", ""),
        "api_key_masked": masked,
        "api_key_set": bool(api_key),
    }


@app.put("/api/llm/config")
def api_put_llm_config(body: LlmConfig):
    """Save LLM provider and api_key to config.yaml."""
    existing = _load_llm_config()
    llm_cfg = {
        "provider": body.provider if body.provider else existing.get("provider", ""),
        "api_key": body.api_key if body.api_key else existing.get("api_key", ""),
    }
    _save_llm_config(llm_cfg)
    return {"status": "saved"}


@app.post("/api/topics/{name}/publish-to-notion")
def api_publish_to_notion(name: str):
    """Publish research files to Notion, organizing under a database hub row."""
    paths = _paths_for(name)
    research_dir = paths["research_dir"]
    if not research_dir.exists():
        raise HTTPException(404, "No research directory found")

    files = _research_files(research_dir)
    if not files:
        raise HTTPException(404, "No research files to publish")

    notion_cfg = _load_notion_config()
    if not notion_cfg["token"]:
        raise HTTPException(500, "Notion token not configured")
    if not notion_cfg["root_page_id"]:
        raise HTTPException(500, "Notion root_page_id not configured")

    headers = _notion_headers(notion_cfg["token"])

    # Ensure hub database exists
    db_id = _notion_ensure_hub_database(headers, notion_cfg)

    # Gather topic metadata
    costs_file = paths["data_dir"] / "costs.json"
    ledger = _load_ledger(costs_file) if costs_file.exists() else {"total_usd": 0.0}
    budget_spent = ledger.get("total_usd", 0.0)
    agent_budget = _load_agent_budget(name)
    budget_total = (agent_budget or {}).get("max_total_usd", 0) or load_config().get("budget", {}).get("max_total_usd", 10)
    cycles = get_cycle_count(paths)
    status = _compute_topic_status(name, paths)

    # Find or create the topic database row
    existing_row_id = _notion_find_topic_row(db_id, name, headers)
    row = _notion_upsert_topic_row(
        db_id, name, status, budget_spent, budget_total, cycles,
        headers, existing_row_id=existing_row_id,
    )
    topic_row_id = row["id"]
    topic_row_url = row.get("url", f"https://notion.so/{topic_row_id.replace('-', '')}")

    # Load publish state
    publish_state_path = paths["data_dir"] / "notion_publish.json"
    publish_state: dict = {}
    if publish_state_path.exists():
        publish_state = json.loads(publish_state_path.read_text(encoding="utf-8"))

    now = datetime.now(timezone.utc).isoformat()

    results = []

    for f in files:
        content = f.read_text(encoding="utf-8").strip()
        if not content:
            continue

        title = f.stem.replace("_", " ").replace("-", " ").title()
        blocks = _md_to_notion_blocks(content)
        file_key = f.name
        existing = publish_state.get(file_key)

        try:
            if existing and isinstance(existing, dict) and existing.get("page_id"):
                try:
                    _notion_update_page(existing["page_id"], title, blocks, headers)
                    page_id = existing["page_id"]
                    url = existing.get("url", f"https://notion.so/{page_id.replace('-', '')}")
                except http_requests.HTTPError:
                    # Page deleted or inaccessible — create new under row
                    page = _notion_create_page(topic_row_id, title, blocks, headers)
                    page_id = page["id"]
                    url = page.get("url", f"https://notion.so/{page_id.replace('-', '')}")
            else:
                page = _notion_create_page(topic_row_id, title, blocks, headers)
                page_id = page["id"]
                url = page.get("url", f"https://notion.so/{page_id.replace('-', '')}")

            publish_state[file_key] = {
                "page_id": page_id,
                "url": url,
                "published_at": now,
            }
            results.append({"file": file_key, "url": url, "status": "ok"})
        except http_requests.HTTPError as e:
            results.append({"file": file_key, "status": "error", "error": str(e)})

    # Save publish state with hub metadata
    publish_state["_topic_row_id"] = topic_row_id
    publish_state["_topic_row_url"] = topic_row_url
    publish_state["_hub_database_id"] = db_id
    publish_state_path.write_text(json.dumps(publish_state, indent=2), encoding="utf-8")

    # Build database URL
    database_url = f"https://notion.so/{db_id.replace('-', '')}"

    return {
        "published_at": now,
        "topic_url": topic_row_url,
        "database_url": database_url,
        "results": results,
    }


@app.get("/api/topics/{name}/notion-status")
def api_notion_status(name: str):
    """Return the last publish state for a topic."""
    paths = _paths_for(name)
    publish_state_path = paths["data_dir"] / "notion_publish.json"
    if not publish_state_path.exists():
        return {"published": False, "published_at": None, "topic_url": None, "database_url": None, "pages": []}
    pub = json.loads(publish_state_path.read_text(encoding="utf-8"))
    timestamps = [
        v.get("published_at", "")
        for k, v in pub.items()
        if isinstance(v, dict) and not k.startswith("_")
    ]
    latest = max(timestamps) if timestamps else None
    pages = [
        {"file": k, "url": v.get("url", ""), "published_at": v.get("published_at", "")}
        for k, v in pub.items()
        if isinstance(v, dict) and not k.startswith("_")
    ]
    # Prefer new row URL, fall back to legacy page URL
    topic_url = pub.get("_topic_row_url") or pub.get("_topic_page_url")
    hub_db_id = pub.get("_hub_database_id", "")
    database_url = f"https://notion.so/{hub_db_id.replace('-', '')}" if hub_db_id else None
    return {
        "published": True,
        "published_at": latest,
        "topic_url": topic_url,
        "database_url": database_url,
        "pages": pages,
    }


@app.post("/api/notion/publish-all")
def api_publish_all_topics():
    """Sync all topics to the hub database (metadata only, no page content)."""
    notion_cfg = _load_notion_config()
    if not notion_cfg["token"]:
        raise HTTPException(500, "Notion token not configured")
    if not notion_cfg["root_page_id"]:
        raise HTTPException(500, "Notion root_page_id not configured")

    headers = _notion_headers(notion_cfg["token"])
    db_id = _notion_ensure_hub_database(headers, notion_cfg)

    topics_dir = ROOT / "topics"
    if not topics_dir.exists():
        return {"synced": 0, "topics": []}

    results = []
    for d in sorted(topics_dir.iterdir()):
        if not d.is_dir():
            continue
        name = d.name
        try:
            paths = _paths_for(name)
        except HTTPException:
            continue

        costs_file = paths["data_dir"] / "costs.json"
        ledger = _load_ledger(costs_file) if costs_file.exists() else {"total_usd": 0.0}
        budget_spent = ledger.get("total_usd", 0.0)
        agent_budget = _load_agent_budget(name)
        budget_total = (agent_budget or {}).get("max_total_usd", 0) or load_config().get("budget", {}).get("max_total_usd", 10)
        cycles = get_cycle_count(paths)
        status = _compute_topic_status(name, paths)

        existing_row_id = _notion_find_topic_row(db_id, name, headers)
        row = _notion_upsert_topic_row(
            db_id, name, status, budget_spent, budget_total, cycles,
            headers, existing_row_id=existing_row_id,
        )
        results.append({"topic": name, "status": status, "row_url": row.get("url", "")})
        time.sleep(0.35)  # Respect Notion rate limits

    database_url = f"https://notion.so/{db_id.replace('-', '')}"
    return {"synced": len(results), "database_url": database_url, "topics": results}


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
