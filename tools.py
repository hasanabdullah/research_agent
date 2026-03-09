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

try:
    from edgar import Company as _EdgarCompany
    _EDGAR_AVAILABLE = True
except ImportError:
    _EdgarCompany = None
    _EDGAR_AVAILABLE = False


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
    """Resolve a relative path, remapping data/ subdirs to the active topic's data dir."""
    # If the path targets research files, use the topic-specific research dir
    if path.startswith("data/research/"):
        filename = path[len("data/research/"):]
        return _paths["research_dir"] / filename
    # If the path targets data/ subdirs (e.g. data/profile_data/), resolve
    # relative to the active topic's data dir so agent can find topic-specific
    # profile files instead of looking in the deepshika root.
    if path.startswith("data/"):
        topic_data = _paths["data_dir"]
        relative = path[len("data/"):]
        resolved = topic_data / relative
        if resolved.exists():
            return resolved
        # Fall through to ROOT if not found in topic dir
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
    {
        "type": "function",
        "function": {
            "name": "github_search",
            "description": (
                "Search GitHub repositories by topic, keyword, or technology. "
                "Returns repo names, stars, descriptions, languages, and recent activity. "
                "Great for tracking open-source competitors, developer tool adoption, "
                "and technology trends. You MUST provide keywords to filter results — "
                "only repos containing at least one keyword in the name or description are returned. "
                "Counts toward the web fetch rate limit (max 3 fetches per cycle)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (e.g. 'construction management software', 'veterinary clinic app')",
                    },
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Keywords to filter repos (e.g. ['management', 'scheduling', 'crm']). Only repos containing at least one keyword are returned.",
                    },
                    "sort": {
                        "type": "string",
                        "enum": ["stars", "forks", "updated", "best-match"],
                        "description": "Sort order (default 'stars'). Use 'updated' for recently active projects.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max repos to return (default 5, max 10)",
                    },
                },
                "required": ["query", "keywords"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "patent_search",
            "description": (
                "Search US patents via the USPTO PatentsView API. "
                "Returns patent titles, assignees, dates, and abstracts. "
                "Use this to track innovation by company or technology area, "
                "identify emerging technologies, and assess market maturity. "
                "You MUST provide keywords to filter results — only patents containing "
                "at least one keyword in the title or abstract are returned. "
                "Counts toward the web fetch rate limit (max 3 fetches per cycle)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query for patent titles/abstracts (e.g. 'construction scheduling AI')",
                    },
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Keywords to filter patents (e.g. ['scheduling', 'optimization', 'AI']). Only patents containing at least one keyword are returned.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max patents to return (default 5, max 10)",
                    },
                },
                "required": ["query", "keywords"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wikipedia_search",
            "description": (
                "Search Wikipedia articles for industry background, market definitions, "
                "and technology overviews. Returns article titles, summaries, and URLs. "
                "Use this for quick industry primers and to understand market context. "
                "You MUST provide keywords to filter results — only articles containing "
                "at least one keyword in the title or summary are returned. "
                "Counts toward the web fetch rate limit (max 3 fetches per cycle)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (e.g. 'veterinary practice management software')",
                    },
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Keywords to filter articles (e.g. ['veterinary', 'practice', 'software']). Only articles containing at least one keyword are returned.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max articles to return (default 5, max 10)",
                    },
                },
                "required": ["query", "keywords"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sec_filings",
            "description": (
                "Search SEC EDGAR for company filings OR read specific sections of 10-K reports. "
                "TWO MODES: (1) Search mode (default) — find filings by company/industry, returns metadata. "
                "Keywords required to filter results. "
                "(2) Section mode — set 'section' parameter to extract actual content from a company's "
                "latest 10-K report. Use ticker symbol as query (e.g. 'PCOR', 'AAPL'). "
                "Available sections: 'business' (Item 1), 'risk_factors' (Item 1A), 'mda' (Item 7 MD&A). "
                "Section mode requires edgartools library. Keywords optional in section mode. "
                "Use this for industry analysis — check if public competitors are growing, "
                "find revenue data from annual reports, and assess market size. "
                "Counts toward the web fetch rate limit (max 3 fetches per cycle)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Company name, industry term, or ticker symbol. Use ticker (e.g. 'PCOR', 'AAPL') when using section mode.",
                    },
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Keywords to filter filings (e.g. ['construction', 'software', 'saas']). Required in search mode, optional in section mode.",
                    },
                    "filing_type": {
                        "type": "string",
                        "enum": ["10-K", "10-Q", "S-1", "8-K"],
                        "description": "Filing type to search (default '10-K' annual reports). 'S-1' for IPO filings, '8-K' for major events.",
                    },
                    "section": {
                        "type": "string",
                        "enum": ["business", "risk_factors", "mda"],
                        "description": "Extract a specific section from the latest filing. 'business' = Item 1 (company overview), 'risk_factors' = Item 1A, 'mda' = Item 7 (Management Discussion & Analysis). Requires edgartools.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max filings to return in search mode (default 5, max 10)",
                    },
                },
                "required": ["query"],
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


def _adzuna_configured() -> bool:
    cfg = _load_config()
    adzuna = cfg.get("adzuna", {}) or {}
    app_id = adzuna.get("app_id", "") or os.environ.get("ADZUNA_APP_ID", "")
    app_key = adzuna.get("app_key", "") or os.environ.get("ADZUNA_APP_KEY", "")
    return bool(app_id and app_key)


def _hunter_configured() -> bool:
    cfg = _load_config()
    hunter = cfg.get("hunter", {}) or {}
    api_key = hunter.get("api_key", "") or os.environ.get("HUNTER_API_KEY", "")
    return bool(api_key)


def get_active_tools() -> list[dict]:
    """Return tool definitions, including credential-gated tools only when configured."""
    tools = list(TOOL_DEFINITIONS)
    if _reddit_credentials_configured():
        tools.append(_REDDIT_TOOL_DEFINITION)
    if _newsapi_configured():
        tools.append(_NEWSAPI_TOOL_DEFINITION)
    # Job search tools — Muse & Remotive are always available (no key needed)
    tools.append(_MUSE_TOOL_DEFINITION)
    tools.append(_REMOTIVE_TOOL_DEFINITION)
    # Adzuna & Hunter require API keys
    if _adzuna_configured():
        tools.append(_ADZUNA_TOOL_DEFINITION)
    if _hunter_configured():
        tools.append(_HUNTER_TOOL_DEFINITION)
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
    # IMPORTANT: Only chain on other append_to_file patches, NOT propose_edit
    # patches — propose_edit replaces the entire file, so chaining on it
    # would lose all existing content.
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    patches_dir = _paths["patches_dir"]
    if patches_dir.exists():
        for pf in sorted(patches_dir.glob("*.patch")):
            try:
                pd = json.loads(pf.read_text(encoding="utf-8"))
                if (pd.get("status") == "pending"
                        and pd.get("path") == path
                        and pd.get("source") == "append_to_file"):
                    existing = pd["new_content"]
            except Exception:
                pass

    # --- Duplicate heading detection ---
    # Extract top-level headings (## Foo) from the new content and check if
    # they already appear in the existing file.  This prevents the agent from
    # appending the same company/role entry multiple times.
    import re as _re
    new_headings = _re.findall(r"^## (.+)", content, _re.MULTILINE)
    if new_headings and existing:
        dupes = [h for h in new_headings if _re.search(r"^## " + _re.escape(h) + r"\s*$", existing, _re.MULTILINE)]
        if dupes:
            return (
                f"DUPLICATE DETECTED: The following sections already exist in {path}: "
                + ", ".join(dupes)
                + ". Skip this company and move on to a different one. "
                + "Do NOT use propose_edit as a workaround — it will overwrite the entire file."
            )

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
        "source": "append_to_file",
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
_WEB_CALLS_PER_CYCLE_DEFAULT = 3


def _get_web_calls_limit() -> int:
    """Return per-cycle web call limit, configurable via config.yaml."""
    try:
        cfg = _load_config()
        return int(cfg.get("web_calls_per_cycle", _WEB_CALLS_PER_CYCLE_DEFAULT))
    except Exception:
        return _WEB_CALLS_PER_CYCLE_DEFAULT


def reset_web_counters():
    """Call at the start of each cycle to reset rate limit counters."""
    global _web_search_count, _web_fetch_count
    _web_search_count = 0
    _web_fetch_count = 0


def handle_web_search(query: str, max_results: int = 5) -> str:
    """Search the web via DuckDuckGo. Returns formatted results."""
    global _web_search_count
    _web_search_count += 1
    _limit = _get_web_calls_limit()
    if _web_search_count > _limit:
        return f"RATE LIMIT: Max {_limit} web searches per cycle. Save remaining searches for next cycle."

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
    _limit = _get_web_calls_limit()
    if _web_fetch_count > _limit:
        return f"RATE LIMIT: Max {_limit} web fetches per cycle. Save remaining for next cycle."

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
    _limit = _get_web_calls_limit()
    if _web_fetch_count > _limit:
        return f"RATE LIMIT: Max {_limit} web fetches per cycle. Save remaining for next cycle."

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
    _limit = _get_web_calls_limit()
    if _web_fetch_count > _limit:
        return f"RATE LIMIT: Max {_limit} web fetches per cycle. Save remaining for next cycle."

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
    _limit = _get_web_calls_limit()
    if _web_fetch_count > _limit:
        return f"RATE LIMIT: Max {_limit} web fetches per cycle. Save remaining for next cycle."

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
    _limit = _get_web_calls_limit()
    if _web_fetch_count > _limit:
        return f"RATE LIMIT: Max {_limit} web fetches per cycle. Save remaining for next cycle."

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
    _limit = _get_web_calls_limit()
    if _web_fetch_count > _limit:
        return f"RATE LIMIT: Max {_limit} web fetches per cycle. Save remaining for next cycle."

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
    _limit = _get_web_calls_limit()
    if _web_fetch_count > _limit:
        return f"RATE LIMIT: Max {_limit} web fetches per cycle. Save remaining for next cycle."

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


# --- GitHub search tool ---

_GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"


def handle_github_search(
    query: str,
    keywords: list[str],
    sort: str = "stars",
    max_results: int = 5,
) -> str:
    """Search GitHub repositories, filtered by keywords. Shares rate limit with web_fetch."""
    if not keywords:
        return "ERROR: keywords parameter is required. Provide relevant terms to filter GitHub results."

    global _web_fetch_count
    _web_fetch_count += 1
    _limit = _get_web_calls_limit()
    if _web_fetch_count > _limit:
        return f"RATE LIMIT: Max {_limit} web fetches per cycle. Save remaining for next cycle."

    max_results = min(int(max_results), 10)
    sort = sort if sort in ("stars", "forks", "updated", "best-match") else "stars"
    kw_lower = [k.lower() for k in keywords]

    params = {
        "q": query,
        "sort": sort if sort != "best-match" else "",
        "order": "desc",
        "per_page": max_results * 3,
    }
    if sort == "best-match":
        params.pop("sort")

    try:
        time.sleep(1)
        resp = requests.get(
            _GITHUB_SEARCH_URL,
            params=params,
            timeout=15,
            headers={"Accept": "application/vnd.github.v3+json"},
        )
        resp.raise_for_status()
        data = resp.json()
        repos = data.get("items", [])

        if not repos:
            return f"No GitHub repositories found for: {query}"

        matched = []
        for repo in repos:
            if len(matched) >= max_results:
                break

            name = repo.get("full_name", "")
            description = repo.get("description") or ""
            topics = " ".join(repo.get("topics", []))
            searchable = f"{name} {description} {topics}".lower()

            if any(kw in searchable for kw in kw_lower):
                matched.append(repo)

        if not matched:
            return (
                f"0 repos matched keywords {keywords} for query '{query}' "
                f"(searched {len(repos)} repos). Try broader keywords."
            )

        parts = [f"GitHub search: '{query}' — {len(matched)} repos matched keywords {keywords}\n"]

        for i, repo in enumerate(matched, 1):
            name = repo.get("full_name", "N/A")
            description = repo.get("description") or "No description"
            if len(description) > 300:
                description = description[:300] + "..."
            stars = repo.get("stargazers_count", 0)
            forks = repo.get("forks_count", 0)
            language = repo.get("language") or "N/A"
            updated = (repo.get("updated_at") or "")[:10]
            url = repo.get("html_url", "N/A")
            topics = ", ".join(repo.get("topics", [])[:5])

            entry = (
                f"\n--- Repo {i} ---\n"
                f"Name: {name}\n"
                f"Stars: {stars} | Forks: {forks} | Language: {language} | Updated: {updated}\n"
                f"URL: {url}\n"
                f"Description: {description}\n"
            )
            if topics:
                entry += f"Topics: {topics}\n"
            parts.append(entry)

        rate_remaining = resp.headers.get("X-RateLimit-Remaining", "?")
        parts.append(f"\n[GitHub API rate limit remaining: {rate_remaining}]")

        output = "\n".join(parts)
        if len(output) > 10000:
            output = output[:10000] + "\n\n[... truncated at 10000 chars]"
        return output

    except Exception as e:
        err = str(e)
        if "403" in err:
            return "ERROR: GitHub API rate limit exceeded (60 req/hr unauthenticated). Try again later."
        return f"ERROR searching GitHub: {e}"


# --- Patent search tool (PatentsView) ---

_PATENTSVIEW_URL = "https://api.patentsview.org/patents/query"


def handle_patent_search(
    query: str,
    keywords: list[str],
    max_results: int = 5,
) -> str:
    """Search US patents via PatentsView API, filtered by keywords. Shares rate limit with web_fetch."""
    if not keywords:
        return "ERROR: keywords parameter is required. Provide relevant terms to filter patent results."

    global _web_fetch_count
    _web_fetch_count += 1
    _limit = _get_web_calls_limit()
    if _web_fetch_count > _limit:
        return f"RATE LIMIT: Max {_limit} web fetches per cycle. Save remaining for next cycle."

    max_results = min(int(max_results), 10)
    kw_lower = [k.lower() for k in keywords]

    payload = {
        "q": {"_text_any": {"patent_abstract": query}},
        "f": [
            "patent_number", "patent_title", "patent_abstract",
            "patent_date", "assignee_organization",
        ],
        "o": {"per_page": max_results * 3},
        "s": [{"patent_date": "desc"}],
    }

    try:
        time.sleep(1)
        resp = requests.post(
            _PATENTSVIEW_URL,
            json=payload,
            timeout=15,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        patents = data.get("patents", [])

        if not patents:
            return f"No US patents found for: {query}"

        matched = []
        for patent in patents:
            if len(matched) >= max_results:
                break

            title = patent.get("patent_title", "")
            abstract = patent.get("patent_abstract") or ""
            assignees = " ".join(
                a.get("assignee_organization", "") or ""
                for a in (patent.get("assignees") or [])
            )
            searchable = f"{title} {abstract} {assignees}".lower()

            if any(kw in searchable for kw in kw_lower):
                matched.append(patent)

        if not matched:
            return (
                f"0 patents matched keywords {keywords} for query '{query}' "
                f"(searched {len(patents)} patents). Try broader keywords."
            )

        parts = [f"USPTO Patents: '{query}' — {len(matched)} patents matched keywords {keywords}\n"]

        for i, patent in enumerate(matched, 1):
            title = patent.get("patent_title", "N/A")
            number = patent.get("patent_number", "N/A")
            date = patent.get("patent_date", "N/A")
            abstract = patent.get("patent_abstract") or ""
            if len(abstract) > 400:
                abstract = abstract[:400] + "..."
            assignee_list = patent.get("assignees") or []
            assignees = ", ".join(
                a.get("assignee_organization", "Unknown") or "Unknown"
                for a in assignee_list[:3]
            )

            entry = (
                f"\n--- Patent {i} ---\n"
                f"Title: {title}\n"
                f"Patent #: {number} | Filed: {date}\n"
                f"Assignee(s): {assignees}\n"
                f"URL: https://patents.google.com/patent/US{number}\n"
            )
            if abstract:
                entry += f"Abstract: {abstract}\n"
            parts.append(entry)

        total_count = data.get("total_patent_count", "?")
        parts.append(f"\n[Total patents matching: {total_count}]")

        output = "\n".join(parts)
        if len(output) > 10000:
            output = output[:10000] + "\n\n[... truncated at 10000 chars]"
        return output

    except Exception as e:
        return f"ERROR searching patents: {e}"


# --- Wikipedia search tool ---

_WIKIPEDIA_API_URL = "https://en.wikipedia.org/w/api.php"


def handle_wikipedia_search(
    query: str,
    keywords: list[str],
    max_results: int = 5,
) -> str:
    """Search Wikipedia articles, filtered by keywords. Shares rate limit with web_fetch."""
    if not keywords:
        return "ERROR: keywords parameter is required. Provide relevant terms to filter Wikipedia results."

    global _web_fetch_count
    _web_fetch_count += 1
    _limit = _get_web_calls_limit()
    if _web_fetch_count > _limit:
        return f"RATE LIMIT: Max {_limit} web fetches per cycle. Save remaining for next cycle."

    max_results = min(int(max_results), 10)
    kw_lower = [k.lower() for k in keywords]

    try:
        time.sleep(1)
        # Step 1: Search for article titles
        search_params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": max_results * 3,
            "format": "json",
            "utf8": 1,
        }
        resp = requests.get(_WIKIPEDIA_API_URL, params=search_params, timeout=15)
        resp.raise_for_status()
        search_results = resp.json().get("query", {}).get("search", [])

        if not search_results:
            return f"No Wikipedia articles found for: {query}"

        # Step 2: Get summaries for matching articles
        matched_titles = []
        for sr in search_results:
            title = sr.get("title", "")
            snippet = sr.get("snippet", "")
            # Strip HTML from snippet
            snippet_text = BeautifulSoup(snippet, "html.parser").get_text()
            searchable = f"{title} {snippet_text}".lower()
            if any(kw in searchable for kw in kw_lower):
                matched_titles.append(title)
            if len(matched_titles) >= max_results:
                break

        if not matched_titles:
            return (
                f"0 articles matched keywords {keywords} for query '{query}' "
                f"(searched {len(search_results)} articles). Try broader keywords."
            )

        # Step 3: Get extracts (summaries) for matched articles
        extract_params = {
            "action": "query",
            "titles": "|".join(matched_titles),
            "prop": "extracts|info",
            "exintro": True,
            "explaintext": True,
            "exsectionformat": "plain",
            "inprop": "url",
            "format": "json",
            "utf8": 1,
        }
        resp2 = requests.get(_WIKIPEDIA_API_URL, params=extract_params, timeout=15)
        resp2.raise_for_status()
        pages = resp2.json().get("query", {}).get("pages", {})

        parts = [f"Wikipedia: '{query}' — {len(matched_titles)} articles matched keywords {keywords}\n"]

        for i, (page_id, page) in enumerate(pages.items(), 1):
            if page_id == "-1":
                continue
            title = page.get("title", "N/A")
            extract = page.get("extract") or "No summary available."
            if len(extract) > 600:
                extract = extract[:600] + "..."
            url = page.get("fullurl", f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}")

            entry = (
                f"\n--- Article {i} ---\n"
                f"Title: {title}\n"
                f"URL: {url}\n"
                f"Summary: {extract}\n"
            )
            parts.append(entry)

        output = "\n".join(parts)
        if len(output) > 10000:
            output = output[:10000] + "\n\n[... truncated at 10000 chars]"
        return output

    except Exception as e:
        return f"ERROR searching Wikipedia: {e}"


# --- SEC EDGAR tool ---

_EDGAR_FULLTEXT_URL = "https://efts.sec.gov/LATEST/search-index"
_EDGAR_COMPANY_URL = "https://efts.sec.gov/LATEST/search-index"


def _extract_sec_section(query: str, filing_type: str, section: str) -> str:
    """Extract a specific section from a company's latest filing using edgartools."""
    if not _EDGAR_AVAILABLE:
        return "ERROR: edgartools is not installed. Run: pip install edgartools"

    section_labels = {
        "business": "Item 1 — Business",
        "risk_factors": "Item 1A — Risk Factors",
        "mda": "Item 7 — Management Discussion & Analysis",
    }
    section_label = section_labels.get(section, section)

    try:
        company = _EdgarCompany(query)
        filings = company.get_filings(form=filing_type)
        if not filings:
            return f"No {filing_type} filings found for ticker '{query}'."

        filing = filings.latest() if hasattr(filings, 'latest') else filings[0]
        filing_date = getattr(filing, 'filing_date', 'N/A')
        company_name = getattr(filing, 'company', query)

        # Try to get structured object (TenK/TenQ)
        obj = None
        try:
            obj = filing.obj()
        except Exception:
            pass

        content = None

        # Attempt 1: property access on the structured object
        if obj is not None:
            prop_map = {
                "business": ["business"],
                "risk_factors": ["risk_factors"],
                "mda": ["management_discussion", "mda"],
            }
            for prop_name in prop_map.get(section, []):
                try:
                    val = getattr(obj, prop_name, None)
                    if val is not None:
                        content = str(val)
                        break
                except Exception:
                    continue

            # Attempt 2: dictionary-style access
            if not content:
                item_map = {
                    "business": ["Item 1", "ITEM 1"],
                    "risk_factors": ["Item 1A", "ITEM 1A"],
                    "mda": ["Item 7", "ITEM 7"],
                }
                for item_key in item_map.get(section, []):
                    try:
                        val = obj[item_key]
                        if val is not None:
                            content = str(val)
                            break
                    except (KeyError, TypeError, IndexError):
                        continue

        # Attempt 3: get full text and extract section by header
        if not content:
            try:
                full_text = filing.text()
                if full_text:
                    content = _extract_section_from_text(full_text, section)
            except Exception:
                pass

        if not content:
            return (
                f"Could not extract '{section_label}' from {company_name}'s "
                f"latest {filing_type} (filed {filing_date}). "
                f"The section may not be available in this filing format."
            )

        # Truncate to 5000 chars
        if len(content) > 5000:
            content = content[:5000] + "\n\n[... truncated at 5000 chars]"

        return (
            f"SEC EDGAR — {company_name} | {filing_type} (filed {filing_date})\n"
            f"Section: {section_label}\n"
            f"{'=' * 60}\n\n"
            f"{content}"
        )

    except Exception as e:
        err = str(e)
        if "No company" in err or "not found" in err.lower():
            return f"ERROR: Company '{query}' not found. Use a valid ticker symbol (e.g. 'AAPL', 'PCOR')."
        return f"ERROR extracting {section_label} for '{query}': {e}"


def _extract_section_from_text(text: str, section: str) -> str | None:
    """Extract a section from filing plain text by finding section headers."""
    # Map section names to regex patterns for 10-K item headers
    patterns = {
        "business": r"(?:^|\n)\s*(?:ITEM\s+1[.\s—\-]+BUSINESS|ITEM\s+1\b(?!\s*A))",
        "risk_factors": r"(?:^|\n)\s*(?:ITEM\s+1A[.\s—\-]+RISK\s+FACTORS|ITEM\s+1A\b)",
        "mda": r"(?:^|\n)\s*(?:ITEM\s+7[.\s—\-]+MANAGEMENT|ITEM\s+7\b(?!\s*A))",
    }
    # The next section after each item
    next_patterns = {
        "business": r"(?:^|\n)\s*ITEM\s+1A\b",
        "risk_factors": r"(?:^|\n)\s*ITEM\s+(?:1B|2)\b",
        "mda": r"(?:^|\n)\s*ITEM\s+7A\b",
    }

    start_pattern = patterns.get(section)
    end_pattern = next_patterns.get(section)
    if not start_pattern:
        return None

    start_match = _re.search(start_pattern, text, _re.IGNORECASE)
    if not start_match:
        return None

    start_pos = start_match.end()

    if end_pattern:
        end_match = _re.search(end_pattern, text[start_pos:], _re.IGNORECASE)
        if end_match:
            return text[start_pos:start_pos + end_match.start()].strip()

    # If no end marker found, take next 5000 chars
    return text[start_pos:start_pos + 5000].strip()


def handle_sec_filings(
    query: str,
    keywords: list[str] | None = None,
    filing_type: str = "10-K",
    max_results: int = 5,
    section: str | None = None,
) -> str:
    """Search SEC EDGAR filings or extract specific sections. Shares rate limit with web_fetch."""
    # Section mode: extract specific section from a company's latest filing
    if section:
        if section not in ("business", "risk_factors", "mda"):
            return "ERROR: section must be one of: 'business', 'risk_factors', 'mda'"

        global _web_fetch_count
        _web_fetch_count += 1
        _limit = _get_web_calls_limit()
        if _web_fetch_count > _limit:
            return f"RATE LIMIT: Max {_limit} web fetches per cycle. Save remaining for next cycle."

        time.sleep(1)
        return _extract_sec_section(query, filing_type, section)

    # Search mode: find filings by company/industry
    if not keywords:
        return "ERROR: keywords parameter is required in search mode. Provide relevant terms to filter SEC filings."

    _web_fetch_count += 1
    _limit = _get_web_calls_limit()
    if _web_fetch_count > _limit:
        return f"RATE LIMIT: Max {_limit} web fetches per cycle. Save remaining for next cycle."

    max_results = min(int(max_results), 10)
    filing_type = filing_type if filing_type in ("10-K", "10-Q", "S-1", "8-K") else "10-K"
    kw_lower = [k.lower() for k in keywords]

    try:
        time.sleep(1)
        # Use EDGAR full-text search API
        params = {
            "q": query,
            "forms": filing_type,
            "from": 0,
            "size": max_results * 3,
        }
        resp = requests.get(
            "https://efts.sec.gov/LATEST/search-index",
            params=params,
            timeout=15,
            headers={"User-Agent": "deepshika-research-agent contact@example.com"},
        )

        # Fallback: use edgartools if available
        if resp.status_code != 200 and _EDGAR_AVAILABLE:
            try:
                company = _EdgarCompany(query)
                filings = company.get_filings(form=filing_type)
                if not filings:
                    return f"No SEC filings found for: {query}"

                matched = []
                filing_list = list(filings.head(max_results * 3)) if hasattr(filings, 'head') else list(filings)[:max_results * 3]
                for f in filing_list:
                    if len(matched) >= max_results:
                        break
                    company_name = str(getattr(f, 'company', query))
                    filing_desc = str(getattr(f, 'description', ''))
                    searchable = f"{company_name} {filing_desc}".lower()
                    if any(kw in searchable for kw in kw_lower):
                        matched.append(f)

                if not matched:
                    return f"0 filings matched keywords {keywords} for '{query}'."

                parts = [f"SEC EDGAR: '{query}' — {len(matched)} {filing_type} filings matched keywords {keywords}\n"]
                for i, f in enumerate(matched, 1):
                    entry = (
                        f"\n--- Filing {i} ---\n"
                        f"Company: {getattr(f, 'company', 'N/A')}\n"
                        f"Form: {getattr(f, 'form', filing_type)}\n"
                        f"Date: {getattr(f, 'filing_date', 'N/A')}\n"
                        f"Description: {getattr(f, 'description', 'N/A')}\n"
                    )
                    parts.append(entry)

                output = "\n".join(parts)
                if len(output) > 10000:
                    output = output[:10000] + "\n\n[... truncated at 10000 chars]"
                return output
            except Exception:
                pass

        # Parse EDGAR API response
        if resp.status_code == 200:
            data = resp.json()
            hits = data.get("hits", {}).get("hits", [])
            if not hits:
                hits = data.get("filings", [])

            if not hits:
                return f"No SEC filings found for: {query}"

            matched = []
            for hit in hits:
                if len(matched) >= max_results:
                    break

                source = hit.get("_source", hit)
                company_name = source.get("entity_name", source.get("company_name", ""))
                form_type = source.get("form_type", source.get("forms", ""))
                file_date = source.get("file_date", source.get("filing_date", ""))
                file_desc = source.get("file_description", "")

                searchable = f"{company_name} {file_desc} {form_type}".lower()
                if any(kw in searchable for kw in kw_lower):
                    matched.append(source)

            if not matched:
                return (
                    f"0 filings matched keywords {keywords} for '{query}' "
                    f"(searched {len(hits)} filings). Try broader keywords."
                )

            parts = [f"SEC EDGAR: '{query}' — {len(matched)} {filing_type} filings matched keywords {keywords}\n"]

            for i, source in enumerate(matched, 1):
                company_name = source.get("entity_name", source.get("company_name", "N/A"))
                cik = source.get("entity_cik", source.get("cik", "N/A"))
                file_date = source.get("file_date", source.get("filing_date", "N/A"))
                file_desc = source.get("file_description", "")
                if len(file_desc) > 300:
                    file_desc = file_desc[:300] + "..."

                entry = (
                    f"\n--- Filing {i} ---\n"
                    f"Company: {company_name}\n"
                    f"CIK: {cik} | Form: {filing_type} | Filed: {file_date}\n"
                    f"URL: https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}\n"
                )
                if file_desc:
                    entry += f"Description: {file_desc}\n"
                parts.append(entry)

            output = "\n".join(parts)
            if len(output) > 10000:
                output = output[:10000] + "\n\n[... truncated at 10000 chars]"
            return output

        return f"ERROR: SEC EDGAR API returned status {resp.status_code}. The EDGAR full-text search may be temporarily unavailable."

    except Exception as e:
        return f"ERROR searching SEC filings: {e}"


# --- Job search API tools (Adzuna, The Muse, Remotive, Hunter.io) ---

_ADZUNA_BASE_URL = "https://api.adzuna.com/v1/api/jobs"

_ADZUNA_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "adzuna_search",
        "description": (
            "Search Adzuna job board API for structured job listings with salary data. "
            "Returns job titles, companies, locations, salary estimates, and direct URLs. "
            "Free tier: 250 requests/day. Great for bulk job sourcing and salary validation. "
            "Supports filtering by title, salary range, and location. "
            "Counts toward the web fetch rate limit (max 3 fetches per cycle)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Job search query (e.g. 'Director of Product AI', 'Senior Product Manager')",
                },
                "location": {
                    "type": "string",
                    "description": "Location to search in (e.g. 'San Francisco', 'New York', 'Remote'). Omit for all US.",
                },
                "salary_min": {
                    "type": "integer",
                    "description": "Minimum annual salary filter in USD (e.g. 250000)",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max jobs to return (default 10, max 20)",
                },
                "sort_by": {
                    "type": "string",
                    "enum": ["relevance", "salary", "date"],
                    "description": "Sort order (default 'relevance'). Use 'salary' for highest-paying first.",
                },
            },
            "required": ["query"],
        },
    },
}


def handle_adzuna_search(
    query: str,
    location: str = "",
    salary_min: int = 0,
    max_results: int = 10,
    sort_by: str = "relevance",
) -> str:
    """Search Adzuna for job listings. Requires ADZUNA_APP_ID and ADZUNA_APP_KEY env vars."""
    global _web_fetch_count
    _web_fetch_count += 1
    _limit = _get_web_calls_limit()
    if _web_fetch_count > _limit:
        return f"RATE LIMIT: Max {_limit} web fetches per cycle. Save remaining for next cycle."

    cfg = _load_config()
    adzuna_cfg = cfg.get("adzuna", {}) or {}
    app_id = adzuna_cfg.get("app_id", "") or os.environ.get("ADZUNA_APP_ID", "")
    app_key = adzuna_cfg.get("app_key", "") or os.environ.get("ADZUNA_APP_KEY", "")
    if not app_id or not app_key:
        return "ERROR: Adzuna credentials required. Configure in the dashboard API Keys tab or set ADZUNA_APP_ID and ADZUNA_APP_KEY env vars. Sign up free at https://developer.adzuna.com/"

    max_results = min(int(max_results), 20)
    sort_map = {"relevance": "relevance", "salary": "salary", "date": "date"}
    sort_param = sort_map.get(sort_by, "relevance")

    params = {
        "app_id": app_id,
        "app_key": app_key,
        "results_per_page": max_results,
        "what": query,
        "content-type": "application/json",
        "sort_by": sort_param,
    }
    if location:
        params["where"] = location
    if salary_min > 0:
        params["salary_min"] = salary_min

    try:
        time.sleep(1)
        url = f"{_ADZUNA_BASE_URL}/us/search/1"
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])

        if not results:
            return f"No Adzuna results for: {query}"

        parts = [
            f"Adzuna job search: '{query}' — {len(results)} results (of {data.get('count', '?')} total)\n"
            "⚠ NOTE: Adzuna salary figures may represent estimated total compensation (base + bonus + equity), not base salary alone. Cross-reference with levels.fyi or Glassdoor before using as base salary.\n"
        ]

        for i, job in enumerate(results, 1):
            title = job.get("title", "N/A")
            company = job.get("company", {}).get("display_name", "N/A")
            loc = job.get("location", {}).get("display_name", "N/A")
            salary_min_val = job.get("salary_min", "")
            salary_max_val = job.get("salary_max", "")
            salary_str = "N/A"
            if salary_min_val and salary_max_val:
                salary_str = f"${int(salary_min_val):,} - ${int(salary_max_val):,}"
            elif salary_min_val:
                salary_str = f"${int(salary_min_val):,}+"
            created = job.get("created", "N/A")[:10]
            redirect_url = job.get("redirect_url", "N/A")
            description = job.get("description", "")
            if len(description) > 300:
                description = description[:300] + "..."

            entry = (
                f"\n--- Job {i} ---\n"
                f"Title: {title}\n"
                f"Company: {company}\n"
                f"Location: {loc}\n"
                f"Salary: {salary_str}\n"
                f"Posted: {created}\n"
                f"URL: {redirect_url}\n"
                f"Description: {description}\n"
            )
            parts.append(entry)

        output = "\n".join(parts)
        if len(output) > 10000:
            output = output[:10000] + "\n\n[... truncated at 10000 chars]"
        return output

    except Exception as e:
        return f"ERROR searching Adzuna: {e}"


_MUSE_BASE_URL = "https://www.themuse.com/api/public/jobs"

_MUSE_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "muse_search",
        "description": (
            "Search The Muse for curated tech company job listings and company profiles. "
            "Free API, no key required. Returns job titles, companies, locations, and descriptions. "
            "Good for discovering product roles at well-known tech companies with culture details. "
            "Counts toward the web fetch rate limit (max 3 fetches per cycle)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Job category filter (e.g. 'Product', 'Data and Analytics', 'Project and Program Management')",
                },
                "level": {
                    "type": "string",
                    "enum": ["Entry Level", "Mid Level", "Senior Level", "Management"],
                    "description": "Experience level filter (default 'Senior Level')",
                },
                "location": {
                    "type": "string",
                    "description": "Location filter (e.g. 'San Francisco, CA', 'New York, NY', 'Flexible / Remote')",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max jobs to return (default 10, max 20)",
                },
            },
            "required": [],
        },
    },
}


def handle_muse_search(
    category: str = "",
    level: str = "Senior Level",
    location: str = "",
    max_results: int = 10,
) -> str:
    """Search The Muse for curated job listings. No API key required."""
    global _web_fetch_count
    _web_fetch_count += 1
    _limit = _get_web_calls_limit()
    if _web_fetch_count > _limit:
        return f"RATE LIMIT: Max {_limit} web fetches per cycle. Save remaining for next cycle."

    max_results = min(int(max_results), 20)
    params = {"page": 1}
    if category:
        params["category"] = category
    if level:
        params["level"] = level
    if location:
        params["location"] = location

    try:
        time.sleep(1)
        resp = requests.get(_MUSE_BASE_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])[:max_results]

        if not results:
            return f"No Muse results for category='{category}', level='{level}', location='{location}'"

        total = data.get("total", "?")
        parts = [f"The Muse jobs: {len(results)} results (of {total} total)\n"]

        for i, job in enumerate(results, 1):
            title = job.get("name", "N/A")
            company = job.get("company", {}).get("name", "N/A")
            locations = ", ".join(loc.get("name", "") for loc in job.get("locations", []))
            pub_date = job.get("publication_date", "N/A")[:10]
            landing_page = job.get("refs", {}).get("landing_page", "N/A")
            contents = job.get("contents", "")
            # Strip HTML
            if contents:
                contents = BeautifulSoup(contents, "html.parser").get_text()
            if len(contents) > 400:
                contents = contents[:400] + "..."

            entry = (
                f"\n--- Job {i} ---\n"
                f"Title: {title}\n"
                f"Company: {company}\n"
                f"Location: {locations or 'N/A'}\n"
                f"Posted: {pub_date}\n"
                f"URL: {landing_page}\n"
                f"Description: {contents}\n"
            )
            parts.append(entry)

        output = "\n".join(parts)
        if len(output) > 10000:
            output = output[:10000] + "\n\n[... truncated at 10000 chars]"
        return output

    except Exception as e:
        return f"ERROR searching The Muse: {e}"


_REMOTIVE_BASE_URL = "https://remotive.com/api/remote-jobs"

_REMOTIVE_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "remotive_search",
        "description": (
            "Search Remotive for remote job listings. Free API, no key required. "
            "Returns remote-only job postings with titles, companies, salary info, and URLs. "
            "Useful for finding remote product and AI roles. "
            "Counts toward the web fetch rate limit (max 3 fetches per cycle)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (e.g. 'product manager', 'director of product', 'AI')",
                },
                "category": {
                    "type": "string",
                    "description": "Job category slug (e.g. 'product', 'data', 'software-dev', 'all-others')",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max jobs to return (default 10, max 20)",
                },
            },
            "required": [],
        },
    },
}


def handle_remotive_search(
    query: str = "",
    category: str = "",
    max_results: int = 10,
) -> str:
    """Search Remotive for remote jobs. No API key required."""
    global _web_fetch_count
    _web_fetch_count += 1
    _limit = _get_web_calls_limit()
    if _web_fetch_count > _limit:
        return f"RATE LIMIT: Max {_limit} web fetches per cycle. Save remaining for next cycle."

    max_results = min(int(max_results), 20)
    params = {}
    if query:
        params["search"] = query
    if category:
        params["category"] = category

    try:
        time.sleep(1)
        resp = requests.get(_REMOTIVE_BASE_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        jobs = data.get("jobs", [])[:max_results]

        if not jobs:
            return f"No Remotive results for query='{query}', category='{category}'"

        parts = [f"Remotive remote jobs: {len(jobs)} results\n"]

        for i, job in enumerate(jobs, 1):
            title = job.get("title", "N/A")
            company = job.get("company_name", "N/A")
            loc = job.get("candidate_required_location", "Worldwide")
            salary = job.get("salary", "Not listed")
            pub_date = job.get("publication_date", "N/A")[:10] if job.get("publication_date") else "N/A"
            url = job.get("url", "N/A")
            job_type = job.get("job_type", "N/A")
            tags = ", ".join(job.get("tags", [])[:5])
            description = job.get("description", "")
            if description:
                description = BeautifulSoup(description, "html.parser").get_text()
            if len(description) > 300:
                description = description[:300] + "..."

            entry = (
                f"\n--- Job {i} ---\n"
                f"Title: {title}\n"
                f"Company: {company}\n"
                f"Location: {loc}\n"
                f"Type: {job_type}\n"
                f"Salary: {salary}\n"
                f"Tags: {tags}\n"
                f"Posted: {pub_date}\n"
                f"URL: {url}\n"
                f"Description: {description}\n"
            )
            parts.append(entry)

        output = "\n".join(parts)
        if len(output) > 10000:
            output = output[:10000] + "\n\n[... truncated at 10000 chars]"
        return output

    except Exception as e:
        return f"ERROR searching Remotive: {e}"


_HUNTER_BASE_URL = "https://api.hunter.io/v2"

_HUNTER_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "hunter_search",
        "description": (
            "Search Hunter.io for email contacts at a company domain. "
            "Free tier: 25 searches/month — use sparingly for top networking targets only. "
            "Returns names, email addresses, titles, and confidence scores. "
            "Best for Phase 4 networking strategy to find hiring managers and recruiters. "
            "Counts toward the web fetch rate limit (max 3 fetches per cycle)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "Company domain to search (e.g. 'anthropic.com', 'openai.com', 'scale.com')",
                },
                "role": {
                    "type": "string",
                    "enum": ["management", "executive", "senior", "all"],
                    "description": "Filter by seniority level (default 'management'). Use 'executive' for C-level, 'senior' for directors.",
                },
                "department": {
                    "type": "string",
                    "enum": ["product", "engineering", "hr", "executive", "all"],
                    "description": "Filter by department (default 'product')",
                },
            },
            "required": ["domain"],
        },
    },
}


def handle_hunter_search(
    domain: str,
    role: str = "management",
    department: str = "product",
) -> str:
    """Search Hunter.io for email contacts at a domain. Requires HUNTER_API_KEY env var."""
    global _web_fetch_count
    _web_fetch_count += 1
    _limit = _get_web_calls_limit()
    if _web_fetch_count > _limit:
        return f"RATE LIMIT: Max {_limit} web fetches per cycle. Save remaining for next cycle."

    cfg = _load_config()
    hunter_cfg = cfg.get("hunter", {}) or {}
    api_key = hunter_cfg.get("api_key", "") or os.environ.get("HUNTER_API_KEY", "")
    if not api_key:
        return "ERROR: Hunter.io API key required. Configure in the dashboard API Keys tab or set HUNTER_API_KEY env var. Sign up free at https://hunter.io/"

    params = {
        "domain": domain,
        "api_key": api_key,
    }
    if role and role != "all":
        params["seniority"] = role
    if department and department != "all":
        params["department"] = department

    try:
        time.sleep(1)
        resp = requests.get(f"{_HUNTER_BASE_URL}/domain-search", params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json().get("data", {})
        emails = data.get("emails", [])

        if not emails:
            return f"No Hunter.io results for domain '{domain}' (role={role}, dept={department})"

        org = data.get("organization", "N/A")
        parts = [f"Hunter.io contacts at {domain} ({org}) — {len(emails)} results\n"]

        for i, contact in enumerate(emails, 1):
            first = contact.get("first_name", "")
            last = contact.get("last_name", "")
            name = f"{first} {last}".strip() or "N/A"
            email = contact.get("value", "N/A")
            position = contact.get("position", "N/A")
            confidence = contact.get("confidence", "N/A")
            linkedin = contact.get("linkedin", "")
            phone = contact.get("phone_number", "")

            entry = (
                f"\n--- Contact {i} ---\n"
                f"Name: {name}\n"
                f"Email: {email}\n"
                f"Title: {position}\n"
                f"Confidence: {confidence}%\n"
            )
            if linkedin:
                entry += f"LinkedIn: {linkedin}\n"
            if phone:
                entry += f"Phone: {phone}\n"
            parts.append(entry)

        output = "\n".join(parts)
        if len(output) > 10000:
            output = output[:10000] + "\n\n[... truncated at 10000 chars]"
        return output

    except Exception as e:
        return f"ERROR searching Hunter.io: {e}"


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
    "github_search": lambda args: handle_github_search(args["query"], args.get("keywords", []), args.get("sort", "stars"), args.get("max_results", 5)),
    "patent_search": lambda args: handle_patent_search(args["query"], args.get("keywords", []), args.get("max_results", 5)),
    "wikipedia_search": lambda args: handle_wikipedia_search(args["query"], args.get("keywords", []), args.get("max_results", 5)),
    "sec_filings": lambda args: handle_sec_filings(args["query"], args.get("keywords"), args.get("filing_type", "10-K"), args.get("max_results", 5), args.get("section")),
    "adzuna_search": lambda args: handle_adzuna_search(args.get("query", ""), args.get("location", ""), args.get("salary_min", 0), args.get("max_results", 10), args.get("sort_by", "relevance")),
    "muse_search": lambda args: handle_muse_search(args.get("category", ""), args.get("level", "Senior Level"), args.get("location", ""), args.get("max_results", 10)),
    "remotive_search": lambda args: handle_remotive_search(args.get("query", ""), args.get("category", ""), args.get("max_results", 10)),
    "hunter_search": lambda args: handle_hunter_search(args["domain"], args.get("role", "management"), args.get("department", "product")),
}


def dispatch_tool(name: str, args: dict) -> str:
    handler = TOOL_HANDLERS.get(name)
    if not handler:
        return f"ERROR: Unknown tool '{name}'"
    return handler(args)
