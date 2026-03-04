"""Agent tools for Deepshika — the capabilities the agent can invoke."""

import difflib
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import re as _re

import requests
import yaml
from bs4 import BeautifulSoup
from ddgs import DDGS

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    _YT_AVAILABLE = True
except ImportError:
    _YT_AVAILABLE = False

try:
    import praw
    _REDDIT_AVAILABLE = True
except ImportError:
    _REDDIT_AVAILABLE = False

try:
    from pytrends.request import TrendReq
    _PYTRENDS_AVAILABLE = True
except ImportError:
    _PYTRENDS_AVAILABLE = False

try:
    from newsapi import NewsApiClient
    _NEWSAPI_AVAILABLE = True
except ImportError:
    _NEWSAPI_AVAILABLE = False

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
    {
        "type": "function",
        "function": {
            "name": "youtube_transcript",
            "description": (
                "Fetch the transcript of a YouTube video, filtered to only relevant segments. "
                "You MUST provide keywords to filter the transcript — only segments containing "
                "at least one keyword are returned. This keeps token usage low and output focused. "
                "Use this ONLY when a video from search results is clearly relevant to your research. "
                "Prefer web_fetch on articles/blogs when text sources are available. "
                "Counts toward the web fetch rate limit (max 3 fetches per cycle)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "video": {
                        "type": "string",
                        "description": "YouTube video URL (e.g. https://www.youtube.com/watch?v=xxx) or video ID",
                    },
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Keywords to filter transcript segments by relevance (e.g. ['saas', 'revenue', 'pricing']). Only segments containing at least one keyword are returned.",
                    },
                    "include_timestamps": {
                        "type": "boolean",
                        "description": "Whether to include timestamps (default false — plain text is more compact)",
                    },
                },
                "required": ["video", "keywords"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hackernews_search",
            "description": (
                "Search Hacker News stories and comments via the Algolia API. "
                "Returns titles, points, comment counts, and matching text. "
                "You MUST provide keywords to filter results — only items containing "
                "at least one keyword are returned. Great for founder discussions, "
                "Show HN launches, Ask HN threads, and technical insights. "
                "Counts toward the web fetch rate limit (max 3 fetches per cycle)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query for Hacker News (e.g. 'micro saas pricing')",
                    },
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Keywords to filter results (e.g. ['revenue', 'mrr', 'pricing']). Only items containing at least one keyword are returned.",
                    },
                    "tags": {
                        "type": "string",
                        "enum": ["story", "comment", "show_hn", "ask_hn"],
                        "description": "Content type to search (default 'story'). Use 'show_hn' for product launches, 'ask_hn' for founder questions.",
                    },
                    "sort": {
                        "type": "string",
                        "enum": ["relevance", "date"],
                        "description": "Sort by relevance or date (default 'relevance'). Use 'date' for recent launches/trends.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max results to return (default 5, max 10)",
                    },
                    "min_points": {
                        "type": "integer",
                        "description": "Minimum points/upvotes to filter by (default 1). Use higher values like 10+ to find high-signal content.",
                    },
                },
                "required": ["query", "keywords"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "google_trends",
            "description": (
                "Get Google Trends data for keywords — interest over time and related queries. "
                "Use this to validate demand for a micro-SaaS idea by checking if search interest "
                "is growing or declining. Compare up to 5 related keywords. "
                "Returns normalized interest scores (0-100) and rising/top related queries. "
                "Counts toward the web fetch rate limit (max 3 fetches per cycle)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "1-5 keywords to check trends for (e.g. ['meeting transcription', 'ai notetaker'])",
                    },
                    "timeframe": {
                        "type": "string",
                        "enum": ["3m", "12m", "5y"],
                        "description": "Time range: '3m' (3 months), '12m' (12 months), '5y' (5 years). Default '12m'.",
                    },
                    "include_related": {
                        "type": "boolean",
                        "description": "Whether to include related rising/top queries (default true). Set false to save tokens.",
                    },
                },
                "required": ["keywords"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stackexchange_search",
            "description": (
                "Search Stack Exchange sites (Stack Overflow, Server Fault, Super User, etc.) "
                "for questions related to a topic. Returns question titles, scores, answer counts, "
                "tags, and view counts. High-score unanswered questions indicate real unmet needs. "
                "You MUST provide keywords to filter results — only questions containing "
                "at least one keyword in the title are returned. "
                "Great for finding developer pain points, integration frustrations, and workflow gaps. "
                "Counts toward the web fetch rate limit (max 3 fetches per cycle)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (e.g. 'construction management software API')",
                    },
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Keywords to filter questions (e.g. ['integration', 'api', 'workflow']). Only questions containing at least one keyword are returned.",
                    },
                    "site": {
                        "type": "string",
                        "description": "Stack Exchange site to search (default 'stackoverflow'). Other options: 'serverfault', 'superuser', 'softwareengineering', etc.",
                    },
                    "sort": {
                        "type": "string",
                        "enum": ["relevance", "votes", "creation", "activity"],
                        "description": "Sort order (default 'relevance'). Use 'votes' for highest-signal questions.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max questions to return (default 5, max 10)",
                    },
                    "tagged": {
                        "type": "string",
                        "description": "Semicolon-separated tags to filter by (e.g. 'python;api'). Only questions with ALL tags are returned.",
                    },
                },
                "required": ["query", "keywords"],
            },
        },
    },
]

# Reddit tool — only included when PRAW is installed and credentials are configured
_REDDIT_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "reddit_search",
        "description": (
            "Search Reddit for posts and comments matching a query, filtered by keywords. "
            "Returns post titles, scores, selftext, and top matching comments. "
            "You MUST provide keywords to filter results — only posts/comments containing "
            "at least one keyword are returned. Great for founder discussions, product feedback, "
            "and market insights. Counts toward the web fetch rate limit (max 3 fetches per cycle)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query for Reddit (e.g. 'micro saas pricing strategy')",
                },
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Keywords to filter posts and comments (e.g. ['revenue', 'mrr', 'pricing']). Only content containing at least one keyword is returned.",
                },
                "subreddit": {
                    "type": "string",
                    "description": "Subreddit to search in (e.g. 'SaaS'). Omit to search all of Reddit.",
                },
                "max_posts": {
                    "type": "integer",
                    "description": "Max posts to return (default 5, max 10)",
                },
                "sort": {
                    "type": "string",
                    "enum": ["relevance", "hot", "top", "new"],
                    "description": "Sort order for results (default 'relevance')",
                },
            },
            "required": ["query", "keywords"],
        },
    },
}


_NEWSAPI_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "startup_news",
        "description": (
            "Search recent startup and tech news articles from 150,000+ sources "
            "(TechCrunch, VentureBeat, The Verge, Wired, etc.). "
            "Returns article titles, descriptions, sources, and URLs. "
            "Great for finding funding rounds, product launches, competitor announcements, "
            "and industry trends. Articles are up to 1 month old. "
            "You MUST provide keywords to filter results — only articles containing "
            "at least one keyword in the title or description are returned. "
            "Counts toward the web fetch rate limit (max 3 fetches per cycle)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (e.g. 'micro saas funding', 'AI startup seed round')",
                },
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Keywords to filter articles (e.g. ['funding', 'seed', 'launch']). Only articles containing at least one keyword are returned.",
                },
                "sources": {
                    "type": "string",
                    "description": "Comma-separated source IDs to search (e.g. 'techcrunch,the-verge,wired'). Omit to search all sources.",
                },
                "sort_by": {
                    "type": "string",
                    "enum": ["relevancy", "publishedAt", "popularity"],
                    "description": "Sort order (default 'relevancy'). Use 'publishedAt' for most recent.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max articles to return (default 5, max 10)",
                },
            },
            "required": ["query", "keywords"],
        },
    },
}


def _newsapi_configured() -> bool:
    """Check if NewsAPI key is available in config.yaml or env vars."""
    if not _NEWSAPI_AVAILABLE:
        return False
    cfg = _load_config()
    newsapi = cfg.get("newsapi", {}) or {}
    api_key = newsapi.get("api_key", "") or os.environ.get("NEWSAPI_KEY", "")
    return bool(api_key)


def _reddit_credentials_configured() -> bool:
    """Check if Reddit API credentials are available in config.yaml or env vars."""
    if not _REDDIT_AVAILABLE:
        return False
    cfg = _load_config()
    reddit = cfg.get("reddit", {}) or {}
    client_id = reddit.get("client_id", "") or os.environ.get("REDDIT_CLIENT_ID", "")
    client_secret = reddit.get("client_secret", "") or os.environ.get("REDDIT_CLIENT_SECRET", "")
    return bool(client_id and client_secret)


def get_active_tools() -> list[dict]:
    """Return tool definitions, including credential-gated tools only when configured."""
    tools = list(TOOL_DEFINITIONS)
    if _reddit_credentials_configured():
        tools.append(_REDDIT_TOOL_DEFINITION)
    if _newsapi_configured():
        tools.append(_NEWSAPI_TOOL_DEFINITION)
    return tools


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

    # Read existing content — check pending patches first so sequential
    # appends within a single cycle build on each other instead of each
    # one starting from the on-disk file (which hasn't been updated yet).
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    patches_dir = _paths["patches_dir"]
    if patches_dir.exists():
        for pf in sorted(patches_dir.glob("*.patch")):
            try:
                pd = json.loads(pf.read_text(encoding="utf-8"))
                if pd.get("status") == "pending" and pd.get("path") == path:
                    existing = pd["new_content"]
            except Exception:
                pass

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


def _extract_video_id(video: str) -> str | None:
    """Extract YouTube video ID from a URL or bare ID."""
    # Already a bare ID (11 chars, alphanumeric + - _)
    if _re.fullmatch(r"[\w-]{11}", video):
        return video
    # Standard and short URLs
    m = _re.search(r"(?:v=|youtu\.be/|/embed/|/shorts/)([\w-]{11})", video)
    return m.group(1) if m else None


def handle_youtube_transcript(video: str, keywords: list[str] | None = None, include_timestamps: bool = False) -> str:
    """Fetch a YouTube video transcript filtered by keywords. Shares rate limit with web_fetch."""
    if not _YT_AVAILABLE:
        return "ERROR: youtube-transcript-api is not installed. Run: pip install youtube-transcript-api"

    if not keywords:
        return "ERROR: keywords parameter is required. Provide relevant terms to filter the transcript."

    global _web_fetch_count
    _web_fetch_count += 1
    if _web_fetch_count > _WEB_CALLS_PER_CYCLE:
        return f"RATE LIMIT: Max {_WEB_CALLS_PER_CYCLE} web fetches per cycle. Save remaining for next cycle."

    video_id = _extract_video_id(video)
    if not video_id:
        return f"ERROR: Could not extract video ID from: {video}"

    try:
        transcript = list(YouTubeTranscriptApi().fetch(video_id))
        total_segments = len(transcript)

        # Keyword filtering: keep segments where any keyword appears (case-insensitive).
        # Also include 1 segment before and after each match for context.
        kw_lower = [k.lower() for k in keywords]
        matched_indices = set()
        for i, entry in enumerate(transcript):
            text_lower = entry.text.lower()
            if any(kw in text_lower for kw in kw_lower):
                matched_indices.update([max(0, i - 1), i, min(total_segments - 1, i + 1)])

        if not matched_indices:
            return (
                f"Transcript for video {video_id} had {total_segments} segments "
                f"but NONE matched keywords {keywords}. Video may not be relevant."
            )

        filtered = [transcript[i] for i in sorted(matched_indices)]

        if include_timestamps:
            lines = []
            for entry in filtered:
                mins, secs = divmod(int(entry.start), 60)
                lines.append(f"[{mins:02d}:{secs:02d}] {entry.text}")
            text = "\n".join(lines)
        else:
            # Group consecutive segments, separate non-consecutive with "..."
            parts = []
            prev_idx = -2
            for idx in sorted(matched_indices):
                if idx != prev_idx + 1:
                    if parts:
                        parts.append("...")
                parts.append(transcript[idx].text)
                prev_idx = idx
            text = " ".join(parts)

        if len(text) > 10000:
            text = text[:10000] + "\n\n[... truncated at 10000 chars]"

        return (
            f"Transcript for YouTube video {video_id} "
            f"({len(filtered)}/{total_segments} segments matched keywords {keywords}):\n\n{text}"
        )

    except Exception as e:
        err = str(e)
        if "disabled" in err.lower() or "no transcript" in err.lower():
            return f"ERROR: No transcript available for video {video_id} (subtitles may be disabled)"
        return f"ERROR fetching transcript for {video_id}: {e}"


# --- Reddit tool ---


def _get_reddit_client():
    """Create a read-only PRAW Reddit instance from configured credentials."""
    cfg = _load_config()
    reddit_cfg = cfg.get("reddit", {}) or {}
    client_id = reddit_cfg.get("client_id", "") or os.environ.get("REDDIT_CLIENT_ID", "")
    client_secret = reddit_cfg.get("client_secret", "") or os.environ.get("REDDIT_CLIENT_SECRET", "")
    return praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent="deepshika-research-agent/1.0",
    )


def handle_reddit_search(
    query: str,
    keywords: list[str],
    subreddit: str | None = None,
    max_posts: int = 5,
    sort: str = "relevance",
) -> str:
    """Search Reddit for posts+comments filtered by keywords. Shares rate limit with web_fetch."""
    if not _REDDIT_AVAILABLE:
        return "ERROR: praw is not installed. Run: pip install praw"

    if not keywords:
        return "ERROR: keywords parameter is required. Provide relevant terms to filter Reddit results."

    global _web_fetch_count
    _web_fetch_count += 1
    if _web_fetch_count > _WEB_CALLS_PER_CYCLE:
        return f"RATE LIMIT: Max {_WEB_CALLS_PER_CYCLE} web fetches per cycle. Save remaining for next cycle."

    max_posts = min(int(max_posts), 10)
    sort = sort if sort in ("relevance", "hot", "top", "new") else "relevance"
    kw_lower = [k.lower() for k in keywords]

    try:
        reddit = _get_reddit_client()
        sub = reddit.subreddit(subreddit) if subreddit else reddit.subreddit("all")

        # Over-fetch 3x to have enough after keyword filtering
        fetch_limit = max_posts * 3
        posts = list(sub.search(query, sort=sort, limit=fetch_limit))

        matched = []
        for post in posts:
            if len(matched) >= max_posts:
                break

            title_lower = post.title.lower()
            body_lower = (post.selftext or "").lower()

            # Check title and body for keyword matches
            title_match = any(kw in title_lower for kw in kw_lower)
            body_match = any(kw in body_lower for kw in kw_lower)

            # Check top comments for keyword matches
            post.comment_sort = "best"
            post.comments.replace_more(limit=0)
            top_comments = post.comments[:10]
            matching_comments = []
            for comment in top_comments:
                comment_lower = comment.body.lower()
                if any(kw in comment_lower for kw in kw_lower):
                    body_text = comment.body[:500]
                    if len(comment.body) > 500:
                        body_text += "..."
                    matching_comments.append(body_text)
                    if len(matching_comments) >= 5:
                        break

            if title_match or body_match or matching_comments:
                selftext = (post.selftext or "")[:1500]
                if len(post.selftext or "") > 1500:
                    selftext += "..."
                matched.append({
                    "title": post.title,
                    "subreddit": str(post.subreddit),
                    "score": post.score,
                    "url": f"https://reddit.com{post.permalink}",
                    "selftext": selftext,
                    "matching_comments": matching_comments,
                })

        if not matched:
            return (
                f"0 posts matched keywords {keywords} for query '{query}' "
                f"(searched {len(posts)} posts in r/{subreddit or 'all'}). "
                f"Try broader keywords or a different query."
            )

        # Format output
        parts = [
            f"Reddit search: '{query}' — {len(matched)} posts matched keywords {keywords}\n"
        ]
        for i, p in enumerate(matched, 1):
            entry = (
                f"\n--- Post {i} ---\n"
                f"Title: {p['title']}\n"
                f"Subreddit: r/{p['subreddit']} | Score: {p['score']}\n"
                f"URL: {p['url']}\n"
            )
            if p["selftext"]:
                entry += f"Content: {p['selftext']}\n"
            if p["matching_comments"]:
                entry += f"Matching comments ({len(p['matching_comments'])}):\n"
                for j, c in enumerate(p["matching_comments"], 1):
                    entry += f"  Comment {j}: {c}\n"
            parts.append(entry)

        output = "\n".join(parts)
        if len(output) > 10000:
            output = output[:10000] + "\n\n[... truncated at 10000 chars]"
        return output

    except Exception as e:
        return f"ERROR searching Reddit: {e}"


# --- Hacker News tool ---

_HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search"
_HN_SEARCH_DATE_URL = "https://hn.algolia.com/api/v1/search_by_date"


def handle_hackernews_search(
    query: str,
    keywords: list[str],
    tags: str = "story",
    sort: str = "relevance",
    max_results: int = 5,
    min_points: int = 1,
) -> str:
    """Search Hacker News via Algolia API, filtered by keywords. Shares rate limit with web_fetch."""
    if not keywords:
        return "ERROR: keywords parameter is required. Provide relevant terms to filter HN results."

    global _web_fetch_count
    _web_fetch_count += 1
    if _web_fetch_count > _WEB_CALLS_PER_CYCLE:
        return f"RATE LIMIT: Max {_WEB_CALLS_PER_CYCLE} web fetches per cycle. Save remaining for next cycle."

    max_results = min(int(max_results), 10)
    tags = tags if tags in ("story", "comment", "show_hn", "ask_hn") else "story"
    kw_lower = [k.lower() for k in keywords]

    url = _HN_SEARCH_DATE_URL if sort == "date" else _HN_SEARCH_URL
    params = {
        "query": query,
        "tags": tags,
        "hitsPerPage": max_results * 3,
    }
    if min_points > 1:
        params["numericFilters"] = f"points>={min_points}"

    try:
        time.sleep(1)
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        hits = data.get("hits", [])

        if not hits:
            return f"No Hacker News results for: {query}"

        matched = []
        for hit in hits:
            if len(matched) >= max_results:
                break

            title = hit.get("title") or hit.get("story_title") or ""
            story_text = hit.get("story_text") or ""
            comment_text = hit.get("comment_text") or ""

            # Strip HTML from comment text
            if comment_text:
                comment_text = BeautifulSoup(comment_text, "html.parser").get_text()

            searchable = f"{title} {story_text} {comment_text}".lower()
            if not any(kw in searchable for kw in kw_lower):
                continue

            matched.append(hit)

        if not matched:
            return (
                f"0 items matched keywords {keywords} for query '{query}' on HN "
                f"(searched {len(hits)} {tags} results). Try broader keywords."
            )

        parts = [f"Hacker News search: '{query}' — {len(matched)} {tags} results matched keywords {keywords}\n"]

        for i, hit in enumerate(matched, 1):
            if tags == "comment":
                comment_text = hit.get("comment_text") or ""
                if comment_text:
                    comment_text = BeautifulSoup(comment_text, "html.parser").get_text()
                if len(comment_text) > 500:
                    comment_text = comment_text[:500] + "..."
                entry = (
                    f"\n--- Comment {i} ---\n"
                    f"On: {hit.get('story_title', 'N/A')}\n"
                    f"Author: {hit.get('author', 'N/A')}\n"
                    f"HN: https://news.ycombinator.com/item?id={hit.get('objectID', '')}\n"
                    f"Comment: {comment_text}\n"
                )
            else:
                title = hit.get("title") or ""
                story_text = hit.get("story_text") or ""
                if story_text:
                    story_text = BeautifulSoup(story_text, "html.parser").get_text()
                if len(story_text) > 500:
                    story_text = story_text[:500] + "..."
                entry = (
                    f"\n--- Story {i} ---\n"
                    f"Title: {title}\n"
                    f"Points: {hit.get('points', 0)} | Comments: {hit.get('num_comments', 0)} | Author: {hit.get('author', 'N/A')}\n"
                    f"URL: {hit.get('url') or 'N/A'}\n"
                    f"HN: https://news.ycombinator.com/item?id={hit.get('objectID', '')}\n"
                )
                if story_text:
                    entry += f"Content: {story_text}\n"

            parts.append(entry)

        output = "\n".join(parts)
        if len(output) > 10000:
            output = output[:10000] + "\n\n[... truncated at 10000 chars]"
        return output

    except Exception as e:
        return f"ERROR searching Hacker News: {e}"


# --- Google Trends tool ---


def handle_google_trends(
    keywords: list[str],
    timeframe: str = "12m",
    include_related: bool = True,
) -> str:
    """Get Google Trends data for keywords. Shares rate limit with web_fetch."""
    if not _PYTRENDS_AVAILABLE:
        return "ERROR: pytrends is not installed. Run: pip install pytrends"

    if not keywords:
        return "ERROR: keywords parameter is required. Provide 1-5 keywords to check trends."

    global _web_fetch_count
    _web_fetch_count += 1
    if _web_fetch_count > _WEB_CALLS_PER_CYCLE:
        return f"RATE LIMIT: Max {_WEB_CALLS_PER_CYCLE} web fetches per cycle. Save remaining for next cycle."

    keywords = keywords[:5]  # Google Trends max 5

    tf_map = {"3m": "today 3-m", "12m": "today 12-m", "5y": "today 5-y"}
    tf = tf_map.get(timeframe, "today 12-m")

    try:
        time.sleep(2)
        pytrends = TrendReq(hl="en-US", tz=360)
        pytrends.build_payload(keywords, cat=0, timeframe=tf, geo="", gprop="")

        # Interest over time
        iot = pytrends.interest_over_time()
        if iot.empty:
            return f"No Google Trends data found for keywords: {keywords}"

        parts = [f"Google Trends: {keywords} (past {timeframe})\n"]
        parts.append("--- Interest Over Time ---")

        for kw in keywords:
            if kw not in iot.columns:
                continue
            series = iot[kw]
            current = int(series.iloc[-1])
            peak = int(series.max())
            first = int(series.iloc[0])
            pct_change = round(((current - first) / max(first, 1)) * 100)

            if pct_change > 10:
                direction = "growing"
            elif pct_change < -10:
                direction = "declining"
            else:
                direction = "stable"

            # Last 6 data points
            recent = series.tail(6)
            recent_str = ", ".join(
                f"{idx.strftime('%Y-%m')}: {int(val)}" for idx, val in recent.items()
            )

            parts.append(
                f'"{kw}": Current: {current}/100 | Peak: {peak} | Trend: {pct_change:+d}% ({direction})\n'
                f"  Recent: {recent_str}"
            )

        # Related queries
        if include_related:
            try:
                related = pytrends.related_queries()
                related_parts = []
                for kw in keywords:
                    if kw not in related or not related[kw]:
                        continue
                    rising = related[kw].get("rising")
                    if rising is not None and not rising.empty:
                        items = []
                        for _, row in rising.head(5).iterrows():
                            val = row.get("value", "N/A")
                            items.append(f"  {row['query']} (+{val}%)")
                        if items:
                            related_parts.append(f'"{kw}" rising queries:\n' + "\n".join(items))
                if related_parts:
                    parts.append("\n--- Rising Related Queries ---")
                    parts.extend(related_parts)
            except Exception:
                pass  # Related queries can fail without invalidating main data

        output = "\n".join(parts)
        if len(output) > 10000:
            output = output[:10000] + "\n\n[... truncated at 10000 chars]"
        return output

    except Exception as e:
        return f"ERROR fetching Google Trends: {e}"


# --- Startup news tool (NewsAPI) ---


def _get_newsapi_client():
    """Create a NewsAPI client from configured credentials."""
    cfg = _load_config()
    newsapi_cfg = cfg.get("newsapi", {}) or {}
    api_key = newsapi_cfg.get("api_key", "") or os.environ.get("NEWSAPI_KEY", "")
    return NewsApiClient(api_key=api_key)


def handle_startup_news(
    query: str,
    keywords: list[str],
    sources: str | None = None,
    sort_by: str = "relevancy",
    max_results: int = 5,
) -> str:
    """Search startup/tech news via NewsAPI, filtered by keywords. Shares rate limit with web_fetch."""
    if not _NEWSAPI_AVAILABLE:
        return "ERROR: newsapi-python is not installed. Run: pip install newsapi-python"

    if not keywords:
        return "ERROR: keywords parameter is required. Provide relevant terms to filter news articles."

    global _web_fetch_count
    _web_fetch_count += 1
    if _web_fetch_count > _WEB_CALLS_PER_CYCLE:
        return f"RATE LIMIT: Max {_WEB_CALLS_PER_CYCLE} web fetches per cycle. Save remaining for next cycle."

    max_results = min(int(max_results), 10)
    sort_by = sort_by if sort_by in ("relevancy", "publishedAt", "popularity") else "relevancy"
    kw_lower = [k.lower() for k in keywords]

    try:
        time.sleep(1)
        client = _get_newsapi_client()

        params = {
            "q": query,
            "sort_by": sort_by,
            "page_size": max_results * 3,  # over-fetch for keyword filtering
            "language": "en",
        }
        if sources:
            params["sources"] = sources

        response = client.get_everything(**params)
        articles = response.get("articles", [])

        if not articles:
            return f"No news articles found for: {query}"

        matched = []
        for article in articles:
            if len(matched) >= max_results:
                break

            title = article.get("title") or ""
            description = article.get("description") or ""
            searchable = f"{title} {description}".lower()

            if any(kw in searchable for kw in kw_lower):
                matched.append(article)

        if not matched:
            return (
                f"0 articles matched keywords {keywords} for query '{query}' "
                f"(searched {len(articles)} articles). Try broader keywords."
            )

        parts = [f"Startup news: '{query}' — {len(matched)} articles matched keywords {keywords}\n"]

        for i, article in enumerate(matched, 1):
            title = article.get("title") or "No title"
            description = article.get("description") or ""
            if len(description) > 500:
                description = description[:500] + "..."
            source_name = (article.get("source") or {}).get("name", "Unknown")
            published = article.get("publishedAt") or ""
            if published:
                published = published[:10]  # just the date part
            url = article.get("url") or "N/A"

            entry = (
                f"\n--- Article {i} ---\n"
                f"Title: {title}\n"
                f"Source: {source_name} | Date: {published}\n"
                f"URL: {url}\n"
            )
            if description:
                entry += f"Summary: {description}\n"
            parts.append(entry)

        output = "\n".join(parts)
        if len(output) > 10000:
            output = output[:10000] + "\n\n[... truncated at 10000 chars]"
        return output

    except Exception as e:
        err = str(e)
        if "apiKeyInvalid" in err or "401" in err:
            return "ERROR: NewsAPI key is invalid. Check your API key at newsapi.org/account"
        return f"ERROR searching news: {e}"


# --- Stack Exchange tool ---

_SE_API_URL = "https://api.stackexchange.com/2.3/search/advanced"


def handle_stackexchange_search(
    query: str,
    keywords: list[str],
    site: str = "stackoverflow",
    sort: str = "relevance",
    max_results: int = 5,
    tagged: str | None = None,
) -> str:
    """Search Stack Exchange for questions, filtered by keywords. Shares rate limit with web_fetch."""
    if not keywords:
        return "ERROR: keywords parameter is required. Provide relevant terms to filter questions."

    global _web_fetch_count
    _web_fetch_count += 1
    if _web_fetch_count > _WEB_CALLS_PER_CYCLE:
        return f"RATE LIMIT: Max {_WEB_CALLS_PER_CYCLE} web fetches per cycle. Save remaining for next cycle."

    max_results = min(int(max_results), 10)
    sort = sort if sort in ("relevance", "votes", "creation", "activity") else "relevance"
    kw_lower = [k.lower() for k in keywords]

    params = {
        "q": query,
        "site": site,
        "sort": sort,
        "order": "desc",
        "pagesize": max_results * 3,
        "filter": "!nNPvSNdWme",  # includes question body excerpt
    }
    if tagged:
        params["tagged"] = tagged

    try:
        time.sleep(1)
        resp = requests.get(_SE_API_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])

        if not items:
            return f"No Stack Exchange questions found for: {query}"

        matched = []
        for item in items:
            if len(matched) >= max_results:
                break

            title = item.get("title", "")
            body_excerpt = item.get("body", "") or ""
            searchable = f"{title} {body_excerpt}".lower()

            if any(kw in searchable for kw in kw_lower):
                matched.append(item)

        if not matched:
            return (
                f"0 questions matched keywords {keywords} for query '{query}' on {site} "
                f"(searched {len(items)} questions). Try broader keywords."
            )

        parts = [f"Stack Exchange ({site}): '{query}' — {len(matched)} questions matched keywords {keywords}\n"]

        for i, item in enumerate(matched, 1):
            title = item.get("title", "No title")
            score = item.get("score", 0)
            answer_count = item.get("answer_count", 0)
            view_count = item.get("view_count", 0)
            is_answered = item.get("is_answered", False)
            tags = ", ".join(item.get("tags", [])[:5])
            link = item.get("link", "N/A")

            status = "Answered" if is_answered else "UNANSWERED"

            entry = (
                f"\n--- Question {i} ---\n"
                f"Title: {title}\n"
                f"Score: {score} | Answers: {answer_count} ({status}) | Views: {view_count}\n"
                f"Tags: {tags}\n"
                f"URL: {link}\n"
            )
            parts.append(entry)

        quota_remaining = data.get("quota_remaining", "?")
        parts.append(f"\n[API quota remaining: {quota_remaining}]")

        output = "\n".join(parts)
        if len(output) > 10000:
            output = output[:10000] + "\n\n[... truncated at 10000 chars]"
        return output

    except Exception as e:
        return f"ERROR searching Stack Exchange: {e}"


# --- Dispatcher ---

TOOL_HANDLERS = {
    "read_file": lambda args: handle_read_file(args["path"]),
    "propose_edit": lambda args: handle_propose_edit(args["path"], args["new_content"], args.get("reasoning", "")),
    "append_to_file": lambda args: handle_append_to_file(args["path"], args.get("content", ""), args.get("reasoning", "")),
    "run_tests": lambda args: handle_run_tests(),
    "reflect": lambda args: handle_reflect(args["observation"]),
    "web_search": lambda args: handle_web_search(args["query"], args.get("max_results", 5)),
    "web_fetch": lambda args: handle_web_fetch(args["url"], args.get("max_chars", 8000)),
    "youtube_transcript": lambda args: handle_youtube_transcript(args["video"], args.get("keywords"), args.get("include_timestamps", False)),
    "reddit_search": lambda args: handle_reddit_search(args["query"], args.get("keywords", []), args.get("subreddit"), args.get("max_posts", 5), args.get("sort", "relevance")),
    "hackernews_search": lambda args: handle_hackernews_search(args["query"], args.get("keywords", []), args.get("tags", "story"), args.get("sort", "relevance"), args.get("max_results", 5), args.get("min_points", 1)),
    "google_trends": lambda args: handle_google_trends(args.get("keywords", []), args.get("timeframe", "12m"), args.get("include_related", True)),
    "startup_news": lambda args: handle_startup_news(args["query"], args.get("keywords", []), args.get("sources"), args.get("sort_by", "relevancy"), args.get("max_results", 5)),
    "stackexchange_search": lambda args: handle_stackexchange_search(args["query"], args.get("keywords", []), args.get("site", "stackoverflow"), args.get("sort", "relevance"), args.get("max_results", 5), args.get("tagged")),
}


def dispatch_tool(name: str, args: dict) -> str:
    handler = TOOL_HANDLERS.get(name)
    if not handler:
        return f"ERROR: Unknown tool '{name}'"
    return handler(args)
