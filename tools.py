"""Agent tools for Deepshika — the capabilities the agent can invoke."""

import difflib
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup
from ddgs import DDGS

ROOT = Path(__file__).parent

# Configurable paths — overridden per-topic by set_paths()
_paths = {
    "root": ROOT,
    "data_dir": ROOT / "data",
    "patches_dir": ROOT / "data" / "pending_patches",
    "research_dir": ROOT / "data" / "research",
}


def set_paths(data_dir: Path, patches_dir: Path, research_dir: Path = None):
    """Configure paths for the active topic. Called once at cycle start."""
    _paths["data_dir"] = data_dir
    _paths["patches_dir"] = patches_dir
    if research_dir:
        _paths["research_dir"] = research_dir


def _resolve_file_path(path: str) -> Path:
    """Resolve a relative path, remapping data/research/ to the active topic's research dir."""
    # If the path targets research files, use the topic-specific research dir
    if path.startswith("data/research/"):
        filename = path[len("data/research/"):]
        return _paths["research_dir"] / filename
    return ROOT / path


def _load_config() -> dict:
    cfg_path = ROOT / "config.yaml"
    if cfg_path.exists():
        return yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    return {}


# --- Tool definitions (OpenAI function-calling format for OpenRouter) ---

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read the contents of a file in the project. "
                "Use this to inspect your own source code or data files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path from project root, e.g. 'agent.py' or 'data/identity.json'",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_edit",
            "description": (
                "Propose an edit to a modifiable file. This generates a unified diff "
                "and saves it as a pending patch — it does NOT apply the change. "
                "A human must approve the patch before it takes effect."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path of the file to edit",
                    },
                    "new_content": {
                        "type": "string",
                        "description": "The complete new content for the file",
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "Why this change improves the agent — explain the purpose clearly",
                    },
                },
                "required": ["path", "new_content", "reasoning"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_to_file",
            "description": (
                "Append content to the end of an existing research file. "
                "Use this instead of propose_edit when you want to ADD to a file "
                "without rewriting it. Much safer for growing documents like tool_opportunities.md. "
                "Only works for files under data/research/."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path of the research file, e.g. 'data/research/tool_opportunities.md'",
                    },
                    "content": {
                        "type": "string",
                        "description": "The content to append to the end of the file",
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "Why you are adding this content",
                    },
                },
                "required": ["path", "content", "reasoning"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Run the project's test suite (pytest). Returns pass/fail output.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reflect",
            "description": (
                "Log an observation or thought without proposing any change. "
                "Use this when you want to note something for future cycles."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "observation": {
                        "type": "string",
                        "description": "What you observed or concluded this cycle",
                    },
                },
                "required": ["observation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web using DuckDuckGo. Returns top results with title, URL, and snippet. "
                "Use this to find current data, verify claims, discover recent funding rounds, "
                "companies, research papers, and news. Max 3 searches per cycle to avoid rate limits."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query — be specific and targeted",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Number of results to return (default 5, max 10)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": (
                "Fetch and read the text content of a web page. Returns cleaned text "
                "(HTML stripped). Use this after web_search to read a specific page in detail. "
                "Max 3 fetches per cycle to avoid rate limits."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The full URL to fetch",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Max characters to return (default 8000, max 15000)",
                    },
                },
                "required": ["url"],
            },
        },
    },
]


# --- Tool implementations ---


def handle_read_file(path: str) -> str:
    """Read a project file. Returns contents or error message."""
    target = _resolve_file_path(path).resolve()
    # Security: ensure the path stays within the project
    if not str(target).startswith(str(ROOT.resolve())):
        return "ERROR: Path escapes project directory."
    if not target.exists():
        return f"ERROR: File not found: {path}"
    if target.is_dir():
        return "ERROR: Path is a directory, not a file."
    try:
        return target.read_text(encoding="utf-8")
    except Exception as e:
        return f"ERROR: {e}"


def handle_propose_edit(path: str, new_content: str, reasoning: str) -> str:
    """Generate a diff and save as a pending patch. Does NOT apply the change."""
    config = _load_config()

    # Check if file is protected
    protected = config.get("protected_files", [])
    if path in protected:
        return f"REFUSED: '{path}' is a protected file and cannot be modified."

    # Check if file is in the modifiable list or under research_dir
    modifiable = config.get("modifiable_files", [])
    research_dir = config.get("research_dir", "data/research")
    is_research = path.startswith(research_dir + "/") and (path.endswith(".md") or path.endswith(".txt"))
    mode = config.get("mode", "full")

    # In research mode, only research files are allowed
    if mode == "research" and not is_research:
        return f"REFUSED: In research mode — only files under {research_dir}/*.md or *.txt are editable."

    if path not in modifiable and not is_research:
        return f"REFUSED: '{path}' is not in the modifiable files list."

    target = _resolve_file_path(path).resolve()
    if not str(target).startswith(str(ROOT.resolve())):
        return "REFUSED: Path escapes project directory."

    # Read current content (empty if file doesn't exist yet)
    if target.exists():
        old_lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    else:
        old_lines = []

    new_lines = new_content.splitlines(keepends=True)
    # Ensure final newline
    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] += "\n"

    diff = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{path}", tofile=f"b/{path}",
        lineterm="",
    ))

    if not diff:
        return "NO CHANGE: The proposed content is identical to the current file."

    diff_text = "\n".join(diff)

    # Save patch
    patches_dir = _paths["patches_dir"]
    patches_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    patch_name = f"{ts}_{path.replace('/', '_')}.patch"
    patch_path = patches_dir / patch_name

    patch_data = {
        "path": path,
        "reasoning": reasoning,
        "diff": diff_text,
        "new_content": new_content,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
    }
    patch_path.write_text(json.dumps(patch_data, indent=2), encoding="utf-8")

    line_count = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    return (
        f"Patch saved: {patch_name}\n"
        f"File: {path}\n"
        f"Lines added/changed: {line_count}\n"
        f"Reasoning: {reasoning}\n"
        f"Awaiting human review."
    )


def handle_append_to_file(path: str, content: str, reasoning: str) -> str:
    """Append content to an existing research file. Creates if it doesn't exist."""
    config = _load_config()
    research_dir = config.get("research_dir", "data/research")

    target = _resolve_file_path(path).resolve()
    if not str(target).startswith(str(ROOT.resolve())):
        return "REFUSED: Path escapes project directory."

    target.parent.mkdir(parents=True, exist_ok=True)

    # Read existing content
    existing = target.read_text(encoding="utf-8") if target.exists() else ""

    # Ensure separator between existing and new content
    separator = "\n\n" if existing and not existing.endswith("\n\n") else "\n" if existing and not existing.endswith("\n") else ""
    new_full = existing + separator + content

    # Save as patch (same flow as propose_edit so it goes through approval)
    old_lines = existing.splitlines(keepends=True)
    new_lines = new_full.splitlines(keepends=True)
    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] += "\n"
        new_full = new_full + "\n"

    diff = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{path}", tofile=f"b/{path}",
        lineterm="",
    ))

    if not diff:
        return "NO CHANGE: Content already present."

    diff_text = "\n".join(diff)

    patches_dir = _paths["patches_dir"]
    patches_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    patch_name = f"{ts}_{path.replace('/', '_')}.patch"
    patch_path = patches_dir / patch_name

    patch_data = {
        "path": path,
        "reasoning": reasoning,
        "diff": diff_text,
        "new_content": new_full,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
    }
    patch_path.write_text(json.dumps(patch_data, indent=2), encoding="utf-8")

    appended_lines = content.count("\n") + 1
    return (
        f"Patch saved: {patch_name}\n"
        f"File: {path} (appended {appended_lines} lines)\n"
        f"Reasoning: {reasoning}\n"
        f"Awaiting review."
    )


def handle_run_tests() -> str:
    """Run pytest and return output. Timeout after 30 seconds."""
    try:
        result = subprocess.run(
            ["python", "-m", "pytest", "tests/", "-v", "--tb=short"],
            capture_output=True, text=True, timeout=30,
            cwd=str(ROOT),
        )
        output = result.stdout + result.stderr
        return output[-3000:] if len(output) > 3000 else output
    except subprocess.TimeoutExpired:
        return "ERROR: Tests timed out after 30 seconds."
    except Exception as e:
        return f"ERROR running tests: {e}"


def handle_reflect(observation: str) -> str:
    """Log an observation. Returns confirmation."""
    return f"Observation logged: {observation}"


# --- Web tools ---

_web_search_count = 0  # per-cycle counter, reset in dispatch_tool wrapper
_web_fetch_count = 0
_WEB_CALLS_PER_CYCLE = 3


def reset_web_counters():
    """Call at the start of each cycle to reset rate limit counters."""
    global _web_search_count, _web_fetch_count
    _web_search_count = 0
    _web_fetch_count = 0


def handle_web_search(query: str, max_results: int = 5) -> str:
    """Search the web via DuckDuckGo. Returns formatted results."""
    global _web_search_count
    _web_search_count += 1
    if _web_search_count > _WEB_CALLS_PER_CYCLE:
        return f"RATE LIMIT: Max {_WEB_CALLS_PER_CYCLE} web searches per cycle. Save remaining searches for next cycle."

    max_results = min(int(max_results), 10)

    for attempt in range(3):
        try:
            time.sleep(2)  # polite delay
            results = DDGS().text(query, max_results=max_results, backend="auto")
            if not results:
                return f"No results found for: {query}"

            output = []
            for i, r in enumerate(results, 1):
                output.append(
                    f"{i}. **{r.get('title', 'No title')}**\n"
                    f"   URL: {r.get('href', 'N/A')}\n"
                    f"   {r.get('body', 'No snippet')}\n"
                )
            return f"Search results for: {query}\n\n" + "\n".join(output)

        except Exception as e:
            err = str(e).lower()
            if "ratelimit" in err or "rate" in err:
                wait = 5 * (attempt + 1)
                if attempt < 2:
                    time.sleep(wait)
                    continue
                return f"RATE LIMITED: DuckDuckGo rate limit hit after retries. Try again next cycle."
            return f"ERROR searching web: {e}"

    return "ERROR: Web search failed after all retries."


def handle_web_fetch(url: str, max_chars: int = 8000) -> str:
    """Fetch a URL and return cleaned text content."""
    global _web_fetch_count
    _web_fetch_count += 1
    if _web_fetch_count > _WEB_CALLS_PER_CYCLE:
        return f"RATE LIMIT: Max {_WEB_CALLS_PER_CYCLE} web fetches per cycle. Save remaining for next cycle."

    max_chars = min(int(max_chars), 15000)

    try:
        time.sleep(1)  # polite delay
        resp = requests.get(
            url,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (compatible; research-agent/1.0)"},
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove script, style, nav elements
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()

        text = soup.get_text(separator="\n", strip=True)

        # Collapse multiple newlines
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        text = "\n".join(lines)

        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n[... truncated at {max_chars} chars]"

        return f"Content from: {url}\n\n{text}"

    except requests.Timeout:
        return f"ERROR: Timeout fetching {url}"
    except requests.HTTPError as e:
        return f"ERROR: HTTP {e.response.status_code} fetching {url}"
    except Exception as e:
        return f"ERROR fetching {url}: {e}"


# --- Dispatcher ---

TOOL_HANDLERS = {
    "read_file": lambda args: handle_read_file(args["path"]),
    "propose_edit": lambda args: handle_propose_edit(args["path"], args["new_content"], args.get("reasoning", "")),
    "append_to_file": lambda args: handle_append_to_file(args["path"], args.get("content", ""), args.get("reasoning", "")),
    "run_tests": lambda args: handle_run_tests(),
    "reflect": lambda args: handle_reflect(args["observation"]),
    "web_search": lambda args: handle_web_search(args["query"], args.get("max_results", 5)),
    "web_fetch": lambda args: handle_web_fetch(args["url"], args.get("max_chars", 8000)),
}


def dispatch_tool(name: str, args: dict) -> str:
    handler = TOOL_HANDLERS.get(name)
    if not handler:
        return f"ERROR: Unknown tool '{name}'"
    return handler(args)
