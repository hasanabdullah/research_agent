"""Deepshika web dashboard — topic management UI."""

import atexit
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from llm import get_client, resolve_model, completions_with_retry
from templates import apply_template, list_templates

app = FastAPI(title="Deepshika Dashboard")

STATIC_DIR = ROOT / "static"

# --- Running agent registry ---
_running_agents: dict[str, dict] = {}
_crashed_agents: set[str] = set()  # tracks agents that crashed until restarted
_running_qe: dict[str, dict] = {}  # QE process registry


# --- Models ---

class TopicCreate(BaseModel):
    name: str
    description: str = ""
    template: Optional[str] = None
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
    model: str = ""
    api_key: str = ""
    vertex_project: str = ""
    vertex_location: str = ""


class TopicIdentityUpdate(BaseModel):
    purpose: str = ""


class TopicRename(BaseModel):
    new_name: str


class SelectionConfirm(BaseModel):
    selected_idea_ids: list[str]


class RedditConfig(BaseModel):
    client_id: str = ""
    client_secret: str = ""


class NewsApiConfig(BaseModel):
    api_key: str = ""


class AdzunaConfig(BaseModel):
    app_id: str = ""
    app_key: str = ""


class HunterConfig(BaseModel):
    api_key: str = ""


class TavilyConfig(BaseModel):
    api_key: str = ""



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


def _is_completed(name: str) -> bool:
    """Check if a topic is marked as completed in agent_config.yaml."""
    cfg_path = ROOT / "topics" / name / "agent_config.yaml"
    if cfg_path.exists():
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        return bool(cfg.get("completed", False))
    return False


def _load_buckets(name: str) -> list[dict]:
    """Load research_buckets from agent_config.yaml, or empty list."""
    cfg_path = ROOT / "topics" / name / "agent_config.yaml"
    if cfg_path.exists():
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        return cfg.get("research_buckets", [])
    return []


def _compute_phase_statuses(name: str) -> list[dict]:
    """Compute completion status for each research bucket/phase.

    Mirrors agent.py:_get_current_phase_idx logic.
    Returns list of {index, name, status} where status is
    'complete', 'current', 'queued', or 'empty'.
    """
    buckets = _load_buckets(name)
    if not buckets:
        return []

    paths = _paths_for(name)
    research_dir = paths["research_dir"]
    found_current = False
    results = []

    for i, bucket in enumerate(buckets):
        files = bucket.get("files", [])
        min_complete_bytes = bucket.get("min_complete_bytes", 2048)
        completion_marker = bucket.get("completion_marker", "")
        min_marker_count = bucket.get("min_marker_count", 0)

        max_size = 0
        primary_content = ""
        any_file_exists = False
        for fn in files:
            fpath = research_dir / fn
            if fpath.exists():
                size = fpath.stat().st_size
                any_file_exists = True
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

        if bytes_ok and markers_ok:
            status = "complete"
        elif not found_current and any_file_exists and max_size > 0:
            status = "current"
            found_current = True
        elif not found_current and not any_file_exists:
            # First phase with no files yet — it's current if all prior are complete
            status = "current" if all(
                r["status"] == "complete" for r in results
            ) else "queued"
            if status == "current":
                found_current = True
        else:
            status = "empty" if not any_file_exists else "queued"

        results.append({
            "index": i,
            "name": bucket.get("name", f"Phase {i+1}"),
            "status": status,
        })

    # If no current was found but not all complete, mark first non-complete as current
    if not found_current:
        for r in results:
            if r["status"] in ("queued", "empty"):
                r["status"] = "current"
                break

    return results


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
    selection_checkpoint = None
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
        # Selection checkpoint
        sc = cp_data.get("selection_checkpoint")
        if sc and not running:
            # Check if Phase 5 already ran by looking for execution_playbook cycles
            phase5_ran = False
            cycles_file = paths.get("cycles_file")
            if cycles_file and cycles_file.exists():
                try:
                    ctext = cycles_file.read_text(encoding="utf-8")
                    phase5_ran = "execution_playbook" in ctext
                except Exception:
                    pass
            selection_checkpoint = {
                "phase": sc.get("phase", ""),
                "idea_count": len(sc.get("ideas", [])),
                "timestamp": sc.get("timestamp", ""),
                "confirmed": bool(sc.get("confirmed")),
                "phase5_ran": phase5_ran,
            }

    return {
        "name": name,
        "cycles": get_cycle_count(paths),
        "cost": round(cost, 4),
        "active": name == active_topic,
        "running": running,
        "budget": agent_budget,
        "checkpoint": checkpoint,
        "selection_checkpoint": selection_checkpoint,
        "completed": _is_completed(name),
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


@app.get("/api/templates")
def api_list_templates():
    """Return available research templates for the UI picker."""
    return list_templates()


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

    # Use template if specified, otherwise fall through to LLM scaffold
    config = load_config()
    scaffold = None
    scaffolded = False

    if body.template:
        scaffold = apply_template(body.template, name, desc)
        if scaffold:
            scaffolded = True

    if scaffold is None:
        scaffold = generate_agent_scaffold(name, desc, config)
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


@app.post("/api/topics/{name}/clone")
def api_clone_topic(name: str):
    """Clone a topic: copies mission + agent parameters but not research output."""
    import shutil

    src_dir = _topic_dir(name)  # validates existence

    # Auto-generate clone name: foo -> foo_2, foo_3, ...
    base = re.sub(r"_(\d+)$", "", name)  # strip existing _N suffix
    counter = 2
    while True:
        clone_name = f"{base}_{counter}"
        clone_dir = ROOT / "topics" / clone_name
        if not clone_dir.exists():
            break
        counter += 1

    # Create directory structure
    (clone_dir / "data" / "research").mkdir(parents=True)
    (clone_dir / "data" / "pending_patches").mkdir(parents=True)

    # Copy mission.md
    src_mission = src_dir / "mission.md"
    if src_mission.exists():
        shutil.copy2(src_mission, clone_dir / "mission.md")

    # Copy agent_parameters.md
    src_params = src_dir / "agent_parameters.md"
    if src_params.exists():
        shutil.copy2(src_params, clone_dir / "agent_parameters.md")

    # Copy identity.json with reset version and born timestamp
    src_identity = src_dir / "identity.json"
    if src_identity.exists():
        identity = json.loads(src_identity.read_text(encoding="utf-8"))
        identity["version"] = "1.0"
        identity["born"] = datetime.now(timezone.utc).isoformat()
        identity["modification_history"] = []
        (clone_dir / "identity.json").write_text(
            json.dumps(identity, indent=2), encoding="utf-8"
        )

    # Copy agent_config.yaml and pre-create empty research files from buckets
    src_config = src_dir / "agent_config.yaml"
    if src_config.exists():
        agent_cfg = yaml.safe_load(src_config.read_text(encoding="utf-8")) or {}
        # Reset any runtime state but keep budget + research_buckets
        (clone_dir / "agent_config.yaml").write_text(
            yaml.dump(agent_cfg, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        # Pre-create empty research output files from research_buckets
        for bucket in agent_cfg.get("research_buckets", []):
            for f in bucket.get("files", []):
                fname = f if isinstance(f, str) else f.get("filename", "")
                if fname:
                    (clone_dir / "data" / "research" / fname).write_text(
                        "", encoding="utf-8"
                    )

    return {"cloned": clone_name}


@app.post("/api/topics/{name}/switch")
def api_switch_topic(name: str):
    _topic_dir(name)  # validates existence
    config = load_config()
    config["active_topic"] = name
    save_config(config)
    return {"active_topic": name}


@app.post("/api/topics/{name}/toggle-completed")
def api_toggle_completed(name: str):
    """Toggle the completed flag on a topic's agent_config.yaml."""
    _topic_dir(name)
    cfg_path = ROOT / "topics" / name / "agent_config.yaml"
    cfg = {}
    if cfg_path.exists():
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    cfg["completed"] = not cfg.get("completed", False)
    cfg_path.write_text(yaml.dump(cfg, default_flow_style=False), encoding="utf-8")
    return {"completed": cfg["completed"]}


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

    # Compute phase statuses
    phase_statuses = _compute_phase_statuses(name)
    status_map = {ps["index"]: ps["status"] for ps in phase_statuses}

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
            "status": status_map.get(i, "empty"),
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


class ConfigDocPublish(BaseModel):
    doc_type: str  # "mission" or "agent_parameters"


@app.post("/api/topics/{name}/publish-config-doc")
def api_publish_config_doc(name: str, body: ConfigDocPublish):
    """Publish mission.md or agent_parameters.md to Notion."""
    paths = _paths_for(name)

    if body.doc_type == "mission":
        file_path = paths["mission"]
        title = f"Research Objective — {name}"
        state_key = "_config_mission"
    elif body.doc_type == "agent_parameters":
        file_path = paths["agent_parameters"]
        title = f"Agent Parameters — {name}"
        state_key = "_config_agent_parameters"
    else:
        raise HTTPException(400, f"Unknown doc_type: {body.doc_type}")

    if not file_path or not file_path.exists():
        raise HTTPException(404, f"File not found: {body.doc_type}")

    content = file_path.read_text(encoding="utf-8").strip()
    if not content:
        raise HTTPException(404, f"No content in {body.doc_type} to publish")

    notion_cfg = _load_notion_config()
    if not notion_cfg["token"]:
        raise HTTPException(500, "Notion token not configured")
    if not notion_cfg.get("root_page_id", "").strip():
        raise HTTPException(500, "Notion root page ID not configured")

    headers = _notion_headers(notion_cfg["token"])
    blocks = _md_to_notion_blocks(content)

    parent_page_id = notion_cfg["root_page_id"].strip()

    publish_state_path = paths["data_dir"] / "notion_publish.json"
    publish_state = {}
    if publish_state_path.exists():
        publish_state = json.loads(publish_state_path.read_text(encoding="utf-8"))

    now = datetime.now(timezone.utc).isoformat()
    existing = publish_state.get(state_key)

    try:
        if existing and isinstance(existing, dict) and existing.get("page_id"):
            try:
                _notion_update_page(existing["page_id"], title, blocks, headers)
                result_page_id = existing["page_id"]
                url = existing.get("url", f"https://notion.so/{result_page_id.replace('-', '')}")
            except (http_requests.HTTPError, Exception):
                page = _notion_create_page(parent_page_id, title, blocks, headers)
                result_page_id = page["id"]
                url = page.get("url", f"https://notion.so/{result_page_id.replace('-', '')}")
        else:
            page = _notion_create_page(parent_page_id, title, blocks, headers)
            result_page_id = page["id"]
            url = page.get("url", f"https://notion.so/{result_page_id.replace('-', '')}")

        publish_state[state_key] = {
            "page_id": result_page_id,
            "url": url,
            "published_at": now,
        }
        publish_state_path.write_text(json.dumps(publish_state, indent=2), encoding="utf-8")

        return {
            "published_at": now,
            "url": url,
            "doc_type": body.doc_type,
            "status": "ok",
        }
    except http_requests.HTTPError as e:
        resp_text = ""
        if hasattr(e, "response") and e.response is not None:
            try:
                resp_text = e.response.text
            except Exception:
                pass
        raise HTTPException(502, f"Notion API error: {e} — {resp_text}")
    except Exception as e:
        raise HTTPException(502, f"Notion publish failed: {type(e).__name__}: {e}")


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


@app.get("/api/topics/{name}/spend-history")
def api_spend_history(name: str):
    """Return cumulative spend points from cycles.jsonl for charting, with phase info."""
    paths = _paths_for(name)
    cycles_file = paths["cycles_file"]
    points = []
    if cycles_file.exists():
        try:
            for line in cycles_file.read_text(encoding="utf-8").strip().split("\n"):
                if not line:
                    continue
                rec = json.loads(line)
                t = rec.get("timestamp", "")
                cost = rec.get("cost_after", 0)
                if t and cost is not None:
                    # Extract phase from summary (file being written to)
                    summary = rec.get("summary", "")
                    phase = ""
                    m = re.search(r"data/research/(\w+)\.md", summary)
                    if m:
                        phase = m.group(1).replace("_", " ").title()
                    elif rec.get("action") == "reflect":
                        phase = "Reflect"
                    elif rec.get("action") in ("checkpoint_review", "selection_checkpoint"):
                        phase = "Checkpoint"
                    elif rec.get("action") == "budget_stop":
                        phase = "Budget Stop"
                    points.append({"t": t, "cost": round(cost, 6), "phase": phase})
        except Exception:
            pass
    return {"points": points}


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


@app.get("/api/topics/{name}/health")
def api_health(name: str):
    """Full issue history: scans all cycles + logs for every issue that occurred."""
    _topic_dir(name)
    paths = _paths_for(name)
    issues: list[dict] = []

    # --- Load all cycles first (needed to correlate log errors to costs) ---
    cycles_file = paths["cycles_file"]
    all_cycles: list[dict] = []
    if cycles_file.exists():
        try:
            raw_lines = [l for l in cycles_file.read_text(encoding="utf-8").strip().split("\n") if l]
            all_cycles = [json.loads(l) for l in raw_lines]
        except Exception:
            pass

    def _nearest_cost(ts_str: str) -> float | None:
        """Find the cost_after of the cycle closest to a timestamp."""
        if not all_cycles or not ts_str:
            return None
        try:
            from datetime import datetime as _dt
            # Parse just the date+time portion
            ts_clean = ts_str.replace("T", " ")[:19]
            target = _dt.strptime(ts_clean, "%Y-%m-%d %H:%M:%S")
            best, best_diff = None, None
            for c in all_cycles:
                ct = c.get("timestamp", "")
                if not ct:
                    continue
                ct_clean = ct.replace("T", " ")[:19]
                try:
                    ct_dt = _dt.strptime(ct_clean, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                diff = abs((ct_dt - target).total_seconds())
                if best_diff is None or diff < best_diff:
                    best_diff = diff
                    best = c.get("cost_after")
            return best
        except Exception:
            return None

    # --- Scan full agent.log for all errors with timestamps ---
    log_path = ROOT / "topics" / name / "data" / "agent.log"
    if log_path.exists():
        try:
            raw = log_path.read_bytes()
            text = raw.decode("utf-8", errors="replace")
            all_lines = text.splitlines()
            seen_errors: set[str] = set()
            chunk_size = 50
            for start in range(0, len(all_lines), chunk_size):
                chunk = all_lines[start:start + chunk_size]
                chunk_text = "\n".join(chunk)
                for pat, _ in _ERROR_PATTERNS:
                    if pat.search(chunk_text):
                        summary = _summarize_error(chunk)
                        if summary and summary not in seen_errors:
                            seen_errors.add(summary)
                            ts = ""
                            for line in chunk:
                                ts_match = re.match(r"(\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2})", line)
                                if ts_match:
                                    ts = ts_match.group(1)
                            if not ts:
                                ts = datetime.now(timezone.utc).isoformat()
                            issues.append({
                                "type": "log_error",
                                "severity": "critical",
                                "message": summary,
                                "timestamp": ts,
                                "cost_at": _nearest_cost(ts),
                            })
                        break
        except Exception:
            pass

    # --- Process crash detection ---
    entry = _running_agents.get(name)
    if entry:
        proc = entry["process"]
        rc = proc.poll()
        if rc is not None and rc != 0:
            now = datetime.now(timezone.utc).isoformat()
            issues.append({
                "type": "process_crash",
                "severity": "critical",
                "message": f"Agent process exited with code {rc}",
                "timestamp": now,
                "cost_at": all_cycles[-1].get("cost_after") if all_cycles else None,
            })

    # --- Full cycle history scan ---
    if all_cycles:
        for i, c in enumerate(all_cycles):
            action = c.get("action", "")
            # budget_stop
            if action == "budget_stop":
                issues.append({
                    "type": "budget_stop",
                    "severity": "critical",
                    "message": f"Budget exceeded — {c.get('summary', 'agent stopped')}",
                    "timestamp": c.get("timestamp", ""),
                    "cost_at": c.get("cost_after"),
                })
            # checkpoint_review (informational)
            if action == "checkpoint_review":
                issues.append({
                    "type": "checkpoint",
                    "severity": "warning",
                    "message": c.get("summary", "Budget checkpoint reached"),
                    "timestamp": c.get("timestamp", ""),
                    "cost_at": c.get("cost_after"),
                })

        # Consecutive reflect cycles with no applied output (2+)
        i = 0
        while i < len(all_cycles):
            if all_cycles[i].get("action") == "reflect" and not all_cycles[i].get("applied"):
                run_start = i
                while i < len(all_cycles) and all_cycles[i].get("action") == "reflect" and not all_cycles[i].get("applied"):
                    i += 1
                run = i - run_start
                if run >= 2:
                    c = all_cycles[i - 1]
                    issues.append({
                        "type": "stuck_reflecting",
                        "severity": "warning" if run < 4 else "critical",
                        "message": f"{run} consecutive reflect cycles with no output — agent stuck",
                        "timestamp": c.get("timestamp", ""),
                        "cost_at": c.get("cost_after"),
                    })
            else:
                i += 1

        # Sliding window checks
        for i in range(4, len(all_cycles)):
            window = all_cycles[i - 4:i + 1]

            # Repeating summary: same summary 3+ times in 5-cycle window
            summaries = [c.get("summary", "").strip().lower() for c in window if c.get("summary")]
            if len(summaries) >= 3:
                counts = Counter(summaries)
                for s, count in counts.items():
                    if count >= 3 and s:
                        prev_window = all_cycles[i - 5:i] if i >= 5 else []
                        prev_sums = [c.get("summary", "").strip().lower() for c in prev_window if c.get("summary")]
                        prev_count = Counter(prev_sums).get(s, 0) if prev_sums else 0
                        if prev_count < 3:
                            issues.append({
                                "type": "repeating_summary",
                                "severity": "warning",
                                "message": f"Same summary repeated {count}x in 5 cycles — possible loop",
                                "timestamp": window[-1].get("timestamp", ""),
                                "cost_at": window[-1].get("cost_after"),
                            })
                        break

            # High burn no output: $0.50+ over 5 cycles with zero applied
            any_applied = any(c.get("applied") for c in window)
            if not any_applied:
                burn = (window[-1].get("cost_after", 0) - window[0].get("cost_after", 0))
                if burn >= 0.50:
                    prev_window = all_cycles[i - 5:i] if i >= 5 else []
                    prev_applied = any(c.get("applied") for c in prev_window) if prev_window else True
                    prev_burn = (prev_window[-1].get("cost_after", 0) - prev_window[0].get("cost_after", 0)) if len(prev_window) >= 2 else 0
                    if prev_applied or prev_burn < 0.50:
                        issues.append({
                            "type": "high_burn_no_output",
                            "severity": "critical",
                            "message": f"${burn:.2f} spent over 5 cycles with no applied output",
                            "timestamp": window[-1].get("timestamp", ""),
                            "cost_at": window[-1].get("cost_after"),
                        })

    # --- Merge QE issues from issues.jsonl ---
    qe_issues_file = ROOT / "topics" / name / "data" / "qe" / "issues.jsonl"
    if qe_issues_file.exists():
        try:
            for line in qe_issues_file.read_text(encoding="utf-8").strip().split("\n"):
                if not line:
                    continue
                entry = json.loads(line)
                # QE issues are not filtered by started_at — they span agent restarts
                issues.append({
                    "type": entry.get("type", "qe_check"),
                    "severity": entry.get("severity", "info") if entry.get("status") != "resolved" else "info",
                    "message": entry.get("message", ""),
                    "timestamp": entry.get("timestamp", ""),
                    "cost_at": _nearest_cost(entry.get("timestamp", "")),
                    "qe_run": entry.get("qe_run"),
                    "status": entry.get("status", "open"),
                })
        except Exception:
            pass

    # Sort by timestamp
    issues.sort(key=lambda x: x.get("timestamp", ""))
    return {"issues": issues, "has_issues": len(issues) > 0}


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
    log_file = open(log_path, "a", encoding="utf-8")

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    proc = subprocess.Popen(
        [sys.executable, "-m", "agent", "run-topic", name, "--cycles", "0", "--delay", str(delay)],
        cwd=str(ROOT),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )

    _running_agents[name] = {
        "process": proc,
        "pid": proc.pid,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "log_file": log_file,
    }
    _crashed_agents.discard(name)

    # Auto-start QE if enabled
    _maybe_start_qe(name)

    return {"started": name, "pid": proc.pid}


@app.post("/api/agents/{name}/stop")
def api_stop_agent(name: str):
    """Terminate a running agent subprocess (or acknowledge it already stopped)."""
    if name not in _running_agents:
        # Agent not tracked — already stopped or server was restarted.
        # Clean up crashed state and return success so the UI resets.
        _crashed_agents.discard(name)
        return {"stopped": name}

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
    _crashed_agents.discard(name)

    # Auto-stop QE
    _maybe_stop_qe(name)

    return {"stopped": name}


def _maybe_start_qe(name: str):
    """Start QE subprocess if enabled in agent_config."""
    if name in _running_qe:
        qe_proc = _running_qe[name].get("process")
        if qe_proc and qe_proc.poll() is None:
            return  # already running

    # Check if QE is enabled
    agent_cfg_path = ROOT / "topics" / name / "agent_config.yaml"
    qe_enabled = True  # default
    qe_interval = 90
    qe_threshold = 3
    if agent_cfg_path.exists():
        try:
            acfg = yaml.safe_load(agent_cfg_path.read_text(encoding="utf-8")) or {}
            qe_cfg = acfg.get("qe", {})
            qe_enabled = qe_cfg.get("enabled", True)
            qe_interval = qe_cfg.get("interval_seconds", 90)
            qe_threshold = qe_cfg.get("cycle_threshold", 3)
        except Exception:
            pass

    if not qe_enabled:
        return

    qe_log_dir = ROOT / "topics" / name / "data" / "qe"
    qe_log_dir.mkdir(parents=True, exist_ok=True)
    qe_log_path = qe_log_dir / "qe.log"
    qe_log_file = open(qe_log_path, "a", encoding="utf-8")

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    qe_proc = subprocess.Popen(
        [sys.executable, "-m", "agent", "run-qe", name,
         "--interval", str(qe_interval), "--cycle-threshold", str(qe_threshold)],
        cwd=str(ROOT),
        env=env,
        stdout=qe_log_file,
        stderr=subprocess.STDOUT,
    )
    _running_qe[name] = {"process": qe_proc, "pid": qe_proc.pid, "log_file": qe_log_file}


def _maybe_stop_qe(name: str):
    """Stop QE subprocess if running."""
    if name not in _running_qe:
        return
    entry = _running_qe[name]
    proc = entry.get("process")
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    if "log_file" in entry:
        try:
            entry["log_file"].close()
        except Exception:
            pass
    del _running_qe[name]


@app.get("/api/topics/{name}/qe-report")
def api_qe_report(name: str):
    """Return the latest QE report."""
    _topic_dir(name)
    qe_dir = ROOT / "topics" / name / "data" / "qe"
    if not qe_dir.exists():
        return {"report": None}
    # Find latest report file
    reports = sorted(qe_dir.glob("report_*.json"))
    if not reports:
        return {"report": None}
    try:
        return {"report": json.loads(reports[-1].read_text(encoding="utf-8"))}
    except Exception:
        return {"report": None}


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
        crashed = name in _crashed_agents
        pid = None
        started_at = None
        if entry:
            proc = entry["process"]
            rc = proc.poll()
            if rc is None:
                running = True
                pid = entry["pid"]
                started_at = entry["started_at"]
            else:
                if rc != 0:
                    _crashed_agents.add(name)
                    crashed = True
                if "log_file" in entry:
                    entry["log_file"].close()
                del _running_agents[name]

        # Checkpoint state
        cp_file = d / "data" / "checkpoints.json"
        checkpoint = None
        selection_checkpoint = None
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
            # Selection checkpoint
            sc = cp_data.get("selection_checkpoint")
            if sc and not running:
                phase5_ran = False
                cycles_f = d / "data" / "cycles.jsonl"
                if cycles_f.exists():
                    try:
                        phase5_ran = "execution_playbook" in cycles_f.read_text(encoding="utf-8")
                    except Exception:
                        pass
                selection_checkpoint = {
                    "phase": sc.get("phase", ""),
                    "idea_count": len(sc.get("ideas", [])),
                    "timestamp": sc.get("timestamp", ""),
                    "confirmed": bool(sc.get("confirmed")),
                    "phase5_ran": phase5_ran,
                }

        # QE status
        qe_running = False
        qe_issues = 0
        qe_entry = _running_qe.get(name)
        if qe_entry:
            qe_proc = qe_entry.get("process")
            qe_running = qe_proc is not None and qe_proc.poll() is None
        qe_reports = sorted((d / "data" / "qe").glob("report_*.json")) if (d / "data" / "qe").exists() else []
        if qe_reports:
            try:
                latest = json.loads(qe_reports[-1].read_text(encoding="utf-8"))
                qe_issues = latest.get("issue_count", 0)
            except Exception:
                pass

        result.append({
            "name": name,
            "running": running,
            "crashed": crashed,
            "pid": pid,
            "started_at": started_at,
            "cost": round(cost, 4),
            "budget": agent_budget,
            "checkpoint": checkpoint,
            "selection_checkpoint": selection_checkpoint,
            "qe_running": qe_running,
            "qe_issues": qe_issues,
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


@app.get("/api/topics/{name}/selection-checkpoint")
def api_get_selection_checkpoint(name: str):
    """Return selection checkpoint data if one is active (not yet confirmed)."""
    _topic_dir(name)
    cp_file = ROOT / "topics" / name / "data" / "checkpoints.json"
    if not cp_file.exists():
        return {"active": False}
    try:
        cp_data = json.loads(cp_file.read_text(encoding="utf-8"))
    except Exception:
        return {"active": False}
    sc = cp_data.get("selection_checkpoint")
    if not sc or sc.get("confirmed"):
        return {"active": False}
    return {
        "active": True,
        "phase": sc.get("phase", ""),
        "ideas": sc.get("ideas", []),
        "timestamp": sc.get("timestamp", ""),
    }


@app.post("/api/topics/{name}/selection-checkpoint/confirm")
def api_confirm_selection(name: str, body: SelectionConfirm):
    """Confirm idea selection: save to checkpoints.json and create phase_filter.json."""
    _topic_dir(name)
    cp_file = ROOT / "topics" / name / "data" / "checkpoints.json"
    if not cp_file.exists():
        raise HTTPException(404, "No checkpoint data found")

    cp_data = json.loads(cp_file.read_text(encoding="utf-8"))
    sc = cp_data.get("selection_checkpoint")
    if not sc:
        raise HTTPException(404, "No selection checkpoint active")

    all_ideas = sc.get("ideas", [])
    selected_ids = set(body.selected_idea_ids)
    selected_ideas = [idea for idea in all_ideas if idea["id"] in selected_ids]

    if not selected_ideas:
        raise HTTPException(400, "Must select at least one idea")

    # Mark checkpoint as confirmed
    sc["confirmed"] = True
    sc["selected_idea_ids"] = list(selected_ids)
    cp_file.write_text(json.dumps(cp_data, indent=2), encoding="utf-8")

    # Create phase_filter.json for the agent to read on resume
    research_dir = ROOT / "topics" / name / "data" / "research"
    phase_filter = {
        "selected_ideas": selected_ideas,
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
    }
    (research_dir / "phase_filter.json").write_text(
        json.dumps(phase_filter, indent=2), encoding="utf-8"
    )

    return {"saved": True, "selected_count": len(selected_ideas)}


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

_OUTLINE_SYSTEM_PROMPT = (
    "You are a research analyst. Given a research summary, produce a structured JSON outline "
    "organized by THEME (not by file or phase). Return ONLY valid JSON with this schema:\n"
    '{"label":"Research Topic","children":[{"label":"Theme Name","status":"complete|current|queued",'
    '"children":[{"label":"Key Finding","detail":"Supporting detail"}]}]}\n'
    "Rules:\n"
    "- Organize by semantic themes, not by source structure\n"
    "- Use status: 'complete' for well-established findings, 'current' for active areas, "
    "'queued' for identified gaps or open questions\n"
    "- Keep labels concise (under 80 chars)\n"
    "- Include 2-6 top-level themes\n"
    "- Each theme should have 1-5 key findings with supporting details\n"
    "- Return ONLY the JSON object, no markdown fences or extra text"
)


def _extract_json(raw: str):
    """Extract a JSON object from LLM output, handling markdown fences and preamble."""
    raw = raw.strip()
    # Strip markdown fences (```json ... ``` or ``` ... ```)
    if "```" in raw:
        # Find content between first ``` and last ```
        parts = raw.split("```")
        # parts[0] = before first fence, parts[1] = inside fence, ...
        if len(parts) >= 3:
            inner = parts[1]
            # Remove optional language tag on first line (e.g. "json\n")
            if inner.startswith("json"):
                inner = inner[4:]
            raw = inner.strip()
        elif len(parts) == 2:
            inner = parts[1]
            if inner.startswith("json"):
                inner = inner[4:]
            raw = inner.strip()
    # Fallback: find first { and last }
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        raw = raw[start:end + 1]
    return json.loads(raw)


def _generate_outline(client, model, summary_text):
    """Generate a structured outline JSON from summary text. Returns (dict, usage) or (None, None)."""
    try:
        response = completions_with_retry(
            client,
            model=resolve_model(model),
            max_tokens=4096,
            messages=[
                {"role": "system", "content": _OUTLINE_SYSTEM_PROMPT},
                {"role": "user", "content": f"Create a structured research outline from this summary:\n\n{summary_text}"},
            ],
        )
        raw = response.choices[0].message.content
        outline = _extract_json(raw)
        usage = getattr(response, "usage", None)
        return outline, usage
    except Exception:
        return None, None


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

    response = completions_with_retry(
        client,
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

    # Generate structured outline
    outline, outline_usage = _generate_outline(client, model, summary)

    # Record outline cost if available
    if outline_usage:
        from costs import record_call
        pricing = config.get("pricing", {"input_per_mtok": 1.0, "output_per_mtok": 5.0})
        record_call(
            getattr(outline_usage, "prompt_tokens", 0) or 0,
            getattr(outline_usage, "completion_tokens", 0) or 0,
            pricing, label="research_outline", costs_file=paths["costs_file"],
        )

    # Save as JSON with timestamp
    summary_path = paths["base"] / "data" / "summary.json"
    save_data = {
        "summary": summary,
        "generated_at": generated_at,
        "outline": outline,
    }
    summary_path.write_text(json.dumps(save_data, indent=2), encoding="utf-8")

    return save_data


@app.post("/api/topics/{name}/summarize-phases")
def api_summarize_phases(name: str):
    """Summarize research by phase: one LLM call per bucket + a global executive summary."""
    paths = _paths_for(name)
    research_dir = paths["research_dir"]
    buckets = _load_buckets(name)

    # Fall back to flat summarize if no buckets
    if not buckets:
        return api_summarize(name)

    if not research_dir.exists():
        raise HTTPException(404, "No research directory found")

    config = load_config()
    model = config.get("scaffold_model", "anthropic/claude-sonnet-4.6")

    # Collect content per bucket
    bucket_contents = []
    bucketed_file_names = set()
    for i, b in enumerate(buckets):
        parts = []
        for fn in b.get("files", []):
            fpath = research_dir / fn
            bucketed_file_names.add(fn)
            if fpath.exists():
                content = fpath.read_text(encoding="utf-8").strip()
                if content:
                    parts.append(f"### {fn}\n\n{content}")
        bucket_contents.append({
            "index": i,
            "name": b.get("name", f"Phase {i+1}"),
            "content": "\n\n---\n\n".join(parts) if parts else "",
        })

    # Collect unbucketed files for additional context
    unbucketed_parts = []
    if research_dir.exists():
        for f in _research_files(research_dir):
            if f.name not in bucketed_file_names:
                content = f.read_text(encoding="utf-8").strip()
                if content:
                    unbucketed_parts.append(f"### {f.name}\n\n{content}")

    # Parallel LLM calls for each phase with content
    # Accumulate usage from threads, record costs after (record_call is not thread-safe)
    usage_accumulator = []

    def summarize_phase(bc):
        if not bc["content"]:
            return {
                "bucket_index": bc["index"],
                "bucket_name": bc["name"],
                "summary": None,
                "generated_at": None,
            }
        client = get_client()
        response = completions_with_retry(
            client,
            model=resolve_model(model),
            max_tokens=2048,
            messages=[
                {"role": "system", "content": (
                    "You are a research analyst. Summarize this research phase concisely in markdown. "
                    "Produce:\n"
                    "- A 1-2 sentence overview of what this phase covers\n"
                    "- 3-5 key findings as bullet points\n"
                    "- A confidence level (High / Medium / Low) with brief justification\n"
                    "Be direct and reference specific data points from the research."
                )},
                {"role": "user", "content": (
                    f"Research phase: {bc['name']}\n\n{bc['content']}"
                )},
            ],
        )
        usage = getattr(response, "usage", None)
        if usage:
            usage_accumulator.append({
                "input": getattr(usage, "prompt_tokens", 0) or 0,
                "output": getattr(usage, "completion_tokens", 0) or 0,
                "label": "phase_summary",
            })
        summary_text = None
        if response.choices and response.choices[0].message:
            summary_text = response.choices[0].message.content
        return {
            "bucket_index": bc["index"],
            "bucket_name": bc["name"],
            "summary": summary_text,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    phase_summaries = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(summarize_phase, bc): bc["index"]
            for bc in bucket_contents
        }
        for future in as_completed(futures):
            phase_summaries.append(future.result())

    # Sort by bucket index
    phase_summaries.sort(key=lambda ps: ps["bucket_index"])

    # Build input for global executive summary
    global_parts = []
    for ps in phase_summaries:
        if ps["summary"]:
            global_parts.append(f"## {ps['bucket_name']}\n\n{ps['summary']}")
    if unbucketed_parts:
        global_parts.append("## Additional Research\n\n" + "\n\n---\n\n".join(unbucketed_parts))

    # Generate global executive summary
    global_summary = ""
    generated_at = datetime.now(timezone.utc).isoformat()
    if global_parts:
        client = get_client()
        response = completions_with_retry(
            client,
            model=resolve_model(model),
            max_tokens=4096,
            messages=[
                {"role": "system", "content": (
                    "You are a research analyst. You will be given phase-by-phase summaries from an AI research agent. "
                    "Produce a concise executive summary in markdown with:\n"
                    "- A 2-3 sentence TL;DR at the top\n"
                    "- Key findings across all phases (bulleted)\n"
                    "- Top recommendations (numbered)\n"
                    "- Open questions or gaps\n"
                    "Be direct, specific, and synthesize insights across phases."
                )},
                {"role": "user", "content": (
                    "Synthesize the following phase summaries into an executive summary:\n\n"
                    + "\n\n---\n\n".join(global_parts)
                )},
            ],
        )
        usage = getattr(response, "usage", None)
        if usage:
            usage_accumulator.append({
                "input": getattr(usage, "prompt_tokens", 0) or 0,
                "output": getattr(usage, "completion_tokens", 0) or 0,
                "label": "executive_summary",
            })
        if response.choices and response.choices[0].message:
            global_summary = response.choices[0].message.content or ""

    # Generate structured outline from all phase summaries + executive summary
    outline = None
    if global_summary:
        outline_input = global_summary + "\n\n---\n\n" + "\n\n".join(global_parts)
        client = get_client()
        outline, outline_usage = _generate_outline(client, model, outline_input)
        if outline_usage:
            usage_accumulator.append({
                "input": getattr(outline_usage, "prompt_tokens", 0) or 0,
                "output": getattr(outline_usage, "completion_tokens", 0) or 0,
                "label": "research_outline",
            })

    # Record all accumulated costs sequentially (thread-safe)
    if usage_accumulator:
        from costs import record_call
        pricing = config.get("pricing", {"input_per_mtok": 1.0, "output_per_mtok": 5.0})
        for u in usage_accumulator:
            record_call(
                u["input"], u["output"], pricing,
                label=u["label"], costs_file=paths["costs_file"],
            )

    # Save to summary.json
    summary_path = paths["base"] / "data" / "summary.json"
    save_data = {
        "summary": global_summary,
        "generated_at": generated_at,
        "phase_summaries": [
            {
                "bucket_index": ps["bucket_index"],
                "bucket_name": ps["bucket_name"],
                "summary": ps["summary"],
                "generated_at": ps["generated_at"],
            }
            for ps in phase_summaries
        ],
        "outline": outline,
    }
    summary_path.write_text(json.dumps(save_data, indent=2), encoding="utf-8")

    return save_data


@app.get("/api/topics/{name}/summary")
def api_get_summary_file(name: str):
    """Return the saved summary if it exists, including phase_summaries if available."""
    paths = _paths_for(name)
    summary_path = paths["base"] / "data" / "summary.json"
    if not summary_path.exists():
        return {"summary": None, "generated_at": None, "phase_summaries": None, "outline": None}
    data = json.loads(summary_path.read_text(encoding="utf-8"))
    return {
        "summary": data.get("summary"),
        "generated_at": data.get("generated_at"),
        "phase_summaries": data.get("phase_summaries"),
        "outline": data.get("outline"),
    }


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
    if re.search(r"^notion:\s*\r?$", text, re.MULTILINE):
        # Remove the old notion block (notion: line + indented lines following it)
        text = re.sub(
            r"^notion:\s*\r?\n(?:[ \t]+\S.*\r?\n?)*",
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
    """Create a child page under parent_id (page or database) and append blocks."""
    # Detect whether parent is a database or a page
    try:
        r_check = http_requests.get(
            f"{NOTION_API}/databases/{parent_id}",
            headers=headers, timeout=10,
        )
        is_database = r_check.status_code == 200
    except Exception:
        is_database = False

    if is_database:
        parent = {"database_id": parent_id}
        # Find the title property name from the database schema
        db_props = r_check.json().get("properties", {})
        title_prop = "Name"
        for prop_name, prop_val in db_props.items():
            if prop_val.get("type") == "title":
                title_prop = prop_name
                break
        properties = {title_prop: {"title": [{"text": {"content": title}}]}}
    else:
        parent = {"page_id": parent_id}
        properties = {"title": [{"text": {"content": title}}]}

    body = {"parent": parent, "properties": properties}
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
    model = cfg.get("model", "") or os.environ.get("LLM_MODEL", "")
    vertex = cfg.get("vertex", {}) or {}
    vertex_project = vertex.get("project", "") or os.environ.get("VERTEX_PROJECT", "")
    vertex_location = vertex.get("location", "") or os.environ.get("VERTEX_LOCATION", "us-central1")
    return {"provider": provider, "api_key": api_key, "model": model, "vertex_project": vertex_project, "vertex_location": vertex_location}


def _save_llm_config(llm_cfg: dict):
    """Upsert the llm:, model:, and vertex: blocks in config.yaml, preserving other content."""
    cfg_path = ROOT / "config.yaml"
    text = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""

    # Update top-level model: field
    model_val = llm_cfg.get("model", "")
    if model_val:
        if re.search(r"^model:\s*\S", text, re.MULTILINE):
            text = re.sub(
                r"^model:\s*\S.*$",
                f"model: {model_val}",
                text,
                flags=re.MULTILINE,
            )
        elif re.search(r"^model:\s*\r?$", text, re.MULTILINE):
            text = re.sub(
                r"^model:\s*\r?$",
                f"model: {model_val}",
                text,
                flags=re.MULTILINE,
            )
        else:
            text = f"model: {model_val}\n" + text

    # Update llm: block
    lines = ["llm:"]
    for key in ("provider", "api_key"):
        val = llm_cfg.get(key, "")
        lines.append(f'  {key}: "{val}"')
    snippet = "\n".join(lines)

    if re.search(r"^llm:\s*\r?$", text, re.MULTILINE):
        text = re.sub(
            r"^llm:\s*\r?\n(?:[ \t]+\S.*\r?\n?)*",
            snippet + "\n",
            text,
            flags=re.MULTILINE,
        )
    else:
        text = text.rstrip() + "\n\n" + snippet + "\n"

    # Update vertex: block
    vertex_project = llm_cfg.get("vertex_project", "")
    vertex_location = llm_cfg.get("vertex_location", "") or "us-central1"
    vlines = ["vertex:", f'  project: "{vertex_project}"', f'  location: "{vertex_location}"']
    vsnippet = "\n".join(vlines)

    if re.search(r"^vertex:\s*$", text, re.MULTILINE):
        text = re.sub(
            r"^vertex:\s*\n(?:[ \t]+\S.*\n?)*",
            vsnippet + "\n",
            text,
            flags=re.MULTILINE,
        )
    elif re.search(r"^vertex:", text, re.MULTILINE):
        text = re.sub(
            r"^vertex:\n(?:[ \t]+\S.*\n?)*",
            vsnippet + "\n",
            text,
            flags=re.MULTILINE,
        )
    else:
        text = text.rstrip() + "\n\n" + vsnippet + "\n"

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
        "model": cfg.get("model", ""),
        "api_key_masked": masked,
        "api_key_set": bool(api_key),
        "vertex_project": cfg.get("vertex_project", ""),
        "vertex_location": cfg.get("vertex_location", ""),
    }


@app.put("/api/llm/config")
def api_put_llm_config(body: LlmConfig):
    """Save LLM provider and api_key to config.yaml."""
    existing = _load_llm_config()
    llm_cfg = {
        "provider": body.provider if body.provider else existing.get("provider", ""),
        "model": body.model if body.model else existing.get("model", ""),
        "api_key": body.api_key if body.api_key else existing.get("api_key", ""),
        "vertex_project": body.vertex_project if body.vertex_project else existing.get("vertex_project", ""),
        "vertex_location": body.vertex_location if body.vertex_location else existing.get("vertex_location", ""),
    }
    _save_llm_config(llm_cfg)
    return {"status": "saved"}


# --- Reddit config helpers & endpoints ---


def _load_reddit_config() -> dict:
    """Load Reddit config from config.yaml, falling back to env vars."""
    cfg = load_config()
    reddit = cfg.get("reddit", {}) or {}
    client_id = reddit.get("client_id", "") or os.environ.get("REDDIT_CLIENT_ID", "")
    client_secret = reddit.get("client_secret", "") or os.environ.get("REDDIT_CLIENT_SECRET", "")
    return {"client_id": client_id, "client_secret": client_secret}


def _save_reddit_config(reddit_cfg: dict):
    """Upsert the reddit: block in config.yaml, preserving other content."""
    cfg_path = ROOT / "config.yaml"
    text = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""

    lines = ["reddit:"]
    for key in ("client_id", "client_secret"):
        val = reddit_cfg.get(key, "")
        lines.append(f'  {key}: "{val}"')
    snippet = "\n".join(lines)

    if re.search(r"^reddit:\s*\r?$", text, re.MULTILINE):
        text = re.sub(
            r"^reddit:\s*\r?\n(?:[ \t]+\S.*\r?\n?)*",
            snippet + "\n",
            text,
            flags=re.MULTILINE,
        )
    else:
        text = text.rstrip() + "\n\n" + snippet + "\n"

    cfg_path.write_text(text, encoding="utf-8")


@app.get("/api/reddit/config")
def api_get_reddit_config():
    """Return Reddit config with masked client_secret + configured flag."""
    cfg = _load_reddit_config()
    client_secret = cfg.get("client_secret", "")
    masked = ""
    if client_secret:
        masked = client_secret[:4] + "..." + client_secret[-4:] if len(client_secret) > 8 else "***"
    return {
        "client_id": cfg.get("client_id", ""),
        "client_secret_masked": masked,
        "configured": bool(cfg.get("client_id") and cfg.get("client_secret")),
    }


@app.put("/api/reddit/config")
def api_put_reddit_config(body: RedditConfig):
    """Save Reddit client_id and client_secret to config.yaml."""
    existing = _load_reddit_config()
    reddit_cfg = {
        "client_id": body.client_id if body.client_id else existing.get("client_id", ""),
        "client_secret": body.client_secret if body.client_secret else existing.get("client_secret", ""),
    }
    _save_reddit_config(reddit_cfg)
    return {"status": "saved"}


# --- NewsAPI config helpers & endpoints ---


def _load_newsapi_config() -> dict:
    """Load NewsAPI config from config.yaml, falling back to env vars."""
    cfg = load_config()
    newsapi = cfg.get("newsapi", {}) or {}
    api_key = newsapi.get("api_key", "") or os.environ.get("NEWSAPI_KEY", "")
    return {"api_key": api_key}


def _save_newsapi_config(newsapi_cfg: dict):
    """Upsert the newsapi: block in config.yaml, preserving other content."""
    cfg_path = ROOT / "config.yaml"
    text = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""

    lines = ["newsapi:"]
    for key in ("api_key",):
        val = newsapi_cfg.get(key, "")
        lines.append(f'  {key}: "{val}"')
    snippet = "\n".join(lines)

    if re.search(r"^newsapi:\s*\r?$", text, re.MULTILINE):
        text = re.sub(
            r"^newsapi:\s*\r?\n(?:[ \t]+\S.*\r?\n?)*",
            snippet + "\n",
            text,
            flags=re.MULTILINE,
        )
    else:
        text = text.rstrip() + "\n\n" + snippet + "\n"

    cfg_path.write_text(text, encoding="utf-8")


@app.get("/api/newsapi/config")
def api_get_newsapi_config():
    """Return NewsAPI config with masked api_key + configured flag."""
    cfg = _load_newsapi_config()
    api_key = cfg.get("api_key", "")
    masked = ""
    if api_key:
        masked = api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else "***"
    return {
        "api_key_masked": masked,
        "configured": bool(api_key),
    }


@app.put("/api/newsapi/config")
def api_put_newsapi_config(body: NewsApiConfig):
    """Save NewsAPI api_key to config.yaml."""
    existing = _load_newsapi_config()
    newsapi_cfg = {
        "api_key": body.api_key if body.api_key else existing.get("api_key", ""),
    }
    _save_newsapi_config(newsapi_cfg)
    return {"status": "saved"}


# ── Adzuna config ──────────────────────────────────────────────────────


def _load_adzuna_config() -> dict:
    """Load Adzuna config from config.yaml, falling back to env vars."""
    cfg = load_config()
    adzuna = cfg.get("adzuna", {}) or {}
    app_id = adzuna.get("app_id", "") or os.environ.get("ADZUNA_APP_ID", "")
    app_key = adzuna.get("app_key", "") or os.environ.get("ADZUNA_APP_KEY", "")
    return {"app_id": app_id, "app_key": app_key}


def _save_adzuna_config(adzuna_cfg: dict):
    """Upsert the adzuna: block in config.yaml, preserving other content."""
    cfg_path = ROOT / "config.yaml"
    text = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""

    lines = ["adzuna:"]
    for key in ("app_id", "app_key"):
        val = adzuna_cfg.get(key, "")
        lines.append(f'  {key}: "{val}"')
    snippet = "\n".join(lines)

    if re.search(r"^adzuna:\s*\r?$", text, re.MULTILINE):
        text = re.sub(
            r"^adzuna:\s*\r?\n(?:[ \t]+\S.*\r?\n?)*",
            snippet + "\n",
            text,
            flags=re.MULTILINE,
        )
    else:
        text = text.rstrip() + "\n\n" + snippet + "\n"

    cfg_path.write_text(text, encoding="utf-8")


@app.get("/api/adzuna/config")
def api_get_adzuna_config():
    """Return Adzuna config with masked keys + configured flag."""
    cfg = _load_adzuna_config()
    app_id = cfg.get("app_id", "")
    app_key = cfg.get("app_key", "")
    id_masked = ""
    key_masked = ""
    if app_id:
        id_masked = app_id[:4] + "..." + app_id[-4:] if len(app_id) > 8 else "***"
    if app_key:
        key_masked = app_key[:4] + "..." + app_key[-4:] if len(app_key) > 8 else "***"
    return {
        "app_id_masked": id_masked,
        "app_key_masked": key_masked,
        "configured": bool(app_id and app_key),
    }


@app.put("/api/adzuna/config")
def api_put_adzuna_config(body: AdzunaConfig):
    """Save Adzuna app_id and app_key to config.yaml."""
    existing = _load_adzuna_config()
    adzuna_cfg = {
        "app_id": body.app_id if body.app_id else existing.get("app_id", ""),
        "app_key": body.app_key if body.app_key else existing.get("app_key", ""),
    }
    _save_adzuna_config(adzuna_cfg)
    return {"status": "saved"}


# ── Hunter.io config ──────────────────────────────────────────────────


def _load_hunter_config() -> dict:
    """Load Hunter config from config.yaml, falling back to env vars."""
    cfg = load_config()
    hunter = cfg.get("hunter", {}) or {}
    api_key = hunter.get("api_key", "") or os.environ.get("HUNTER_API_KEY", "")
    return {"api_key": api_key}


def _save_hunter_config(hunter_cfg: dict):
    """Upsert the hunter: block in config.yaml, preserving other content."""
    cfg_path = ROOT / "config.yaml"
    text = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""

    lines = ["hunter:"]
    for key in ("api_key",):
        val = hunter_cfg.get(key, "")
        lines.append(f'  {key}: "{val}"')
    snippet = "\n".join(lines)

    if re.search(r"^hunter:\s*\r?$", text, re.MULTILINE):
        text = re.sub(
            r"^hunter:\s*\r?\n(?:[ \t]+\S.*\r?\n?)*",
            snippet + "\n",
            text,
            flags=re.MULTILINE,
        )
    else:
        text = text.rstrip() + "\n\n" + snippet + "\n"

    cfg_path.write_text(text, encoding="utf-8")


@app.get("/api/hunter/config")
def api_get_hunter_config():
    """Return Hunter config with masked api_key + configured flag."""
    cfg = _load_hunter_config()
    api_key = cfg.get("api_key", "")
    masked = ""
    if api_key:
        masked = api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else "***"
    return {
        "api_key_masked": masked,
        "configured": bool(api_key),
    }


@app.put("/api/hunter/config")
def api_put_hunter_config(body: HunterConfig):
    """Save Hunter api_key to config.yaml."""
    existing = _load_hunter_config()
    hunter_cfg = {
        "api_key": body.api_key if body.api_key else existing.get("api_key", ""),
    }
    _save_hunter_config(hunter_cfg)
    return {"status": "saved"}


# --- Tavily config ---

def _load_tavily_config() -> dict:
    """Load Tavily config from config.yaml, falling back to env vars."""
    cfg = load_config()
    tavily = cfg.get("tavily", {}) or {}
    api_key = tavily.get("api_key", "") or os.environ.get("TAVILY_API_KEY", "")
    return {"api_key": api_key}


def _save_tavily_config(tavily_cfg: dict):
    """Upsert the tavily: block in config.yaml, preserving other content."""
    cfg_path = ROOT / "config.yaml"
    text = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""

    lines = ["tavily:"]
    for key in ("api_key",):
        val = tavily_cfg.get(key, "")
        lines.append(f'  {key}: "{val}"')
    snippet = "\n".join(lines)

    if re.search(r"^tavily:\s*\r?$", text, re.MULTILINE):
        text = re.sub(
            r"^tavily:\s*\r?\n(?:[ \t]+\S.*\r?\n?)*",
            snippet + "\n",
            text,
            flags=re.MULTILINE,
        )
    else:
        text = text.rstrip() + "\n\n" + snippet + "\n"

    cfg_path.write_text(text, encoding="utf-8")


@app.get("/api/tavily/config")
def api_get_tavily_config():
    """Return Tavily config with masked api_key + configured flag."""
    cfg = _load_tavily_config()
    api_key = cfg.get("api_key", "")
    masked = ""
    if api_key:
        masked = api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else "***"
    return {
        "api_key_masked": masked,
        "configured": bool(api_key),
    }


@app.put("/api/tavily/config")
def api_put_tavily_config(body: TavilyConfig):
    """Save Tavily api_key to config.yaml."""
    existing = _load_tavily_config()
    tavily_cfg = {
        "api_key": body.api_key if body.api_key else existing.get("api_key", ""),
    }
    _save_tavily_config(tavily_cfg)
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
