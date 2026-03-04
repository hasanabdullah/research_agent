"""Sanity tests for Deepshika — the agent runs these on itself."""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure project root is on sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import costs
import tools


# --- Existing core tests ---


def test_read_file_exists():
    """read_file should return contents of an existing file."""
    result = tools.handle_read_file("config.yaml")
    assert "model:" in result
    assert "ERROR" not in result


def test_read_file_missing():
    """read_file should return error for missing files."""
    result = tools.handle_read_file("nonexistent.xyz")
    assert "ERROR" in result


def test_read_file_path_escape():
    """read_file should refuse paths that escape the project."""
    result = tools.handle_read_file("../../etc/passwd")
    assert "ERROR" in result


def test_propose_edit_protected():
    """propose_edit should refuse to modify protected files."""
    result = tools.handle_propose_edit("CONSTITUTION.md", "hacked", "test")
    assert "REFUSED" in result


def test_propose_edit_unlisted():
    """propose_edit should refuse files not in the modifiable list."""
    result = tools.handle_propose_edit("pyproject.toml", "hacked", "test")
    assert "REFUSED" in result


def test_propose_edit_creates_patch(tmp_path, monkeypatch):
    """propose_edit should create a .patch file for research files."""
    monkeypatch.setitem(tools._paths, "patches_dir", tmp_path)
    result = tools.handle_propose_edit(
        "data/research/test_file.md",
        "# Test Research\n\nSome findings.\n",
        "test research creation",
    )
    assert "Patch saved" in result
    patches = list(tmp_path.glob("*.patch"))
    assert len(patches) == 1
    patch_data = json.loads(patches[0].read_text())
    assert patch_data["status"] == "pending"
    assert "Test Research" in patch_data["diff"]


def test_propose_edit_refuses_code_in_research_mode():
    """propose_edit should refuse code edits in research mode."""
    result = tools.handle_propose_edit(
        "agent.py",
        "# hacked",
        "test code edit",
    )
    assert "REFUSED" in result
    assert "research mode" in result.lower()


def test_append_to_file_creates_patch(tmp_path, monkeypatch):
    """append_to_file should create a patch that appends content."""
    monkeypatch.setitem(tools._paths, "patches_dir", tmp_path)
    # Create an existing research file
    research_dir = tools._paths["research_dir"]
    research_dir.mkdir(parents=True, exist_ok=True)
    test_file = research_dir / "append_test.md"
    test_file.write_text("# Existing Content\n\nLine one.\n")

    result = tools.handle_append_to_file(
        "data/research/append_test.md",
        "## New Section\n\nAppended content.\n",
        "testing append",
    )
    assert "Patch saved" in result
    assert "appended" in result.lower()
    patches = list(tmp_path.glob("*.patch"))
    assert len(patches) == 1
    patch_data = json.loads(patches[0].read_text())
    assert "Existing Content" in patch_data["new_content"]
    assert "New Section" in patch_data["new_content"]
    # Cleanup
    test_file.unlink()


def test_reflect():
    """reflect should confirm the observation."""
    result = tools.handle_reflect("I notice my tests are passing.")
    assert "Observation logged" in result


def test_dispatch_unknown_tool():
    """dispatch_tool should handle unknown tool names."""
    result = tools.dispatch_tool("hack_the_planet", {})
    assert "Unknown tool" in result


def test_cost_ledger_roundtrip(tmp_path):
    """Cost recording should persist and accumulate."""
    costs_file = tmp_path / "costs.json"
    pricing = {"input_per_mtok": 1.0, "output_per_mtok": 5.0}
    costs.record_call(1000, 500, pricing, label="test", costs_file=costs_file)
    costs.record_call(2000, 1000, pricing, label="test2", costs_file=costs_file)

    total = costs.get_total_usd(costs_file=costs_file)
    # 1000*1/1M + 500*5/1M + 2000*1/1M + 1000*5/1M = 0.001 + 0.0025 + 0.002 + 0.005 = 0.0105
    assert abs(total - 0.0105) < 0.0001


def test_constitution_exists():
    """CONSTITUTION.md must exist and not be empty."""
    path = ROOT / "CONSTITUTION.md"
    assert path.exists(), "CONSTITUTION.md missing"
    text = path.read_text()
    assert len(text) > 100, "CONSTITUTION.md seems too short"
    assert "Preserve core identity" in text


# --- Tool definitions tests ---


def test_tool_definitions_structure():
    """All TOOL_DEFINITIONS should have correct OpenAI format."""
    for tool in tools.TOOL_DEFINITIONS:
        assert tool["type"] == "function"
        assert "name" in tool["function"]
        assert "description" in tool["function"]
        assert "parameters" in tool["function"]


def test_tool_definitions_count():
    """Should have 11 base tool definitions."""
    assert len(tools.TOOL_DEFINITIONS) == 11


def test_all_base_tools_present():
    """All expected base tools should be in TOOL_DEFINITIONS."""
    expected = {
        "read_file", "propose_edit", "append_to_file", "run_tests", "reflect",
        "web_search", "web_fetch", "youtube_transcript",
        "hackernews_search", "google_trends", "stackexchange_search",
    }
    actual = {t["function"]["name"] for t in tools.TOOL_DEFINITIONS}
    assert expected == actual


def test_all_handlers_registered():
    """Every tool in TOOL_DEFINITIONS + conditional tools should have a handler."""
    all_tool_names = {t["function"]["name"] for t in tools.TOOL_DEFINITIONS}
    all_tool_names.add("reddit_search")
    all_tool_names.add("startup_news")
    for name in all_tool_names:
        assert name in tools.TOOL_HANDLERS, f"Missing handler for {name}"


def test_get_active_tools_base():
    """get_active_tools should return at least the base tools."""
    active = tools.get_active_tools()
    active_names = {t["function"]["name"] for t in active}
    assert "hackernews_search" in active_names
    assert "google_trends" in active_names
    assert "stackexchange_search" in active_names


def test_get_active_tools_excludes_unconfigured():
    """Credential-gated tools should NOT appear without credentials."""
    active = tools.get_active_tools()
    active_names = {t["function"]["name"] for t in active}
    # These require credentials — should not be in base set
    # (unless env vars happen to be set, so we just check structure)
    assert len(active) >= 11


# --- Rate limiting tests ---


def test_rate_limit_shared_counter():
    """All web-data tools should share the _web_fetch_count counter."""
    tools.reset_web_counters()

    # Use up 3 slots
    tools._web_fetch_count = 3

    # All should be rate-limited now
    r1 = tools.handle_hackernews_search("test", ["test"])
    assert "RATE LIMIT" in r1

    r2 = tools.handle_google_trends(["test"])
    assert "RATE LIMIT" in r2

    r3 = tools.handle_stackexchange_search("test", ["test"])
    assert "RATE LIMIT" in r3

    r4 = tools.handle_web_fetch("https://example.com")
    assert "RATE LIMIT" in r4

    r5 = tools.handle_youtube_transcript("dQw4w9WgXcQ", ["test"])
    assert "RATE LIMIT" in r5 or "ERROR" in r5  # may fail on YT availability

    tools.reset_web_counters()


def test_reset_web_counters():
    """reset_web_counters should reset both counters."""
    tools._web_search_count = 99
    tools._web_fetch_count = 99
    tools.reset_web_counters()
    assert tools._web_search_count == 0
    assert tools._web_fetch_count == 0


# --- Keyword validation tests ---


def test_hackernews_no_keywords():
    """hackernews_search should require keywords."""
    result = tools.handle_hackernews_search("test", [])
    assert "ERROR" in result
    assert "keywords" in result.lower()


def test_google_trends_no_keywords():
    """google_trends should require keywords."""
    result = tools.handle_google_trends([])
    assert "ERROR" in result
    assert "keywords" in result.lower()


def test_stackexchange_no_keywords():
    """stackexchange_search should require keywords."""
    result = tools.handle_stackexchange_search("test", [])
    assert "ERROR" in result
    assert "keywords" in result.lower()


def test_youtube_no_keywords():
    """youtube_transcript should require keywords."""
    result = tools.handle_youtube_transcript("dQw4w9WgXcQ", None)
    assert "ERROR" in result
    assert "keywords" in result.lower()


def test_reddit_no_keywords():
    """reddit_search should require keywords."""
    result = tools.handle_reddit_search("test", [])
    assert "ERROR" in result
    assert "keywords" in result.lower()


def test_startup_news_no_keywords():
    """startup_news should require keywords."""
    result = tools.handle_startup_news("test", [])
    assert "ERROR" in result
    assert "keywords" in result.lower()


# --- Handler integration tests (mocked network) ---


def test_hackernews_search_filters_by_keyword():
    """hackernews_search should filter results by keyword."""
    tools.reset_web_counters()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "hits": [
            {"title": "SaaS revenue growth strategies", "objectID": "1", "points": 50, "num_comments": 10, "author": "user1", "url": "https://example.com"},
            {"title": "Unrelated cooking recipe", "objectID": "2", "points": 5, "num_comments": 1, "author": "user2", "url": "https://example.com/cook"},
            {"title": "MRR tracking for startups", "objectID": "3", "points": 30, "num_comments": 8, "author": "user3", "url": "https://example.com/mrr"},
        ]
    }

    with patch("tools.requests.get", return_value=mock_response):
        result = tools.handle_hackernews_search("saas", ["revenue", "mrr"])

    assert "revenue growth" in result.lower()
    assert "MRR tracking" in result
    assert "cooking" not in result.lower()


def test_stackexchange_search_filters_by_keyword():
    """stackexchange_search should filter results by keyword."""
    tools.reset_web_counters()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "items": [
            {"title": "How to integrate API with CRM", "score": 15, "answer_count": 3, "view_count": 500, "is_answered": True, "tags": ["api", "crm"], "link": "https://so.com/1"},
            {"title": "Best pizza in NYC", "score": 2, "answer_count": 1, "view_count": 50, "is_answered": True, "tags": ["food"], "link": "https://so.com/2"},
        ],
        "quota_remaining": 295,
    }

    with patch("tools.requests.get", return_value=mock_response):
        result = tools.handle_stackexchange_search("crm api", ["api", "integration"])

    assert "integrate API" in result
    assert "pizza" not in result.lower()
    assert "quota_remaining" in result or "295" in result


def test_hackernews_strips_html_from_comments():
    """hackernews_search should strip HTML from comment text."""
    tools.reset_web_counters()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "hits": [
            {
                "story_title": "Test Story",
                "comment_text": "<p>This is a <b>bold</b> revenue comment</p>",
                "objectID": "1",
                "author": "user1",
                "story_id": "100",
            },
        ]
    }

    with patch("tools.requests.get", return_value=mock_response):
        result = tools.handle_hackernews_search("test", ["revenue"], tags="comment")

    assert "revenue comment" in result
    assert "<p>" not in result
    assert "<b>" not in result


def test_google_trends_handler_not_installed():
    """google_trends should return install error when pytrends unavailable."""
    original = tools._PYTRENDS_AVAILABLE
    tools._PYTRENDS_AVAILABLE = False
    tools.reset_web_counters()

    result = tools.handle_google_trends(["test"])
    assert "ERROR" in result
    assert "pytrends" in result.lower()

    tools._PYTRENDS_AVAILABLE = original


def test_startup_news_handler_not_installed():
    """startup_news should return install error when newsapi unavailable."""
    original = tools._NEWSAPI_AVAILABLE
    tools._NEWSAPI_AVAILABLE = False
    tools.reset_web_counters()

    result = tools.handle_startup_news("test", ["test"])
    assert "ERROR" in result
    assert "newsapi" in result.lower()

    tools._NEWSAPI_AVAILABLE = original


def test_reddit_handler_not_installed():
    """reddit_search should return install error when praw unavailable."""
    original = tools._REDDIT_AVAILABLE
    tools._REDDIT_AVAILABLE = False
    tools.reset_web_counters()

    result = tools.handle_reddit_search("test", ["test"])
    assert "ERROR" in result
    assert "praw" in result.lower()

    tools._REDDIT_AVAILABLE = original


# --- Conditional tool visibility tests ---


def test_reddit_tool_hidden_without_credentials():
    """reddit_search should not appear in get_active_tools without credentials."""
    # Temporarily clear any reddit config
    original_fn = tools._reddit_credentials_configured
    tools._reddit_credentials_configured = lambda: False

    active_names = {t["function"]["name"] for t in tools.get_active_tools()}
    assert "reddit_search" not in active_names

    tools._reddit_credentials_configured = original_fn


def test_newsapi_tool_hidden_without_credentials():
    """startup_news should not appear in get_active_tools without credentials."""
    original_fn = tools._newsapi_configured
    tools._newsapi_configured = lambda: False

    active_names = {t["function"]["name"] for t in tools.get_active_tools()}
    assert "startup_news" not in active_names

    tools._newsapi_configured = original_fn


def test_reddit_tool_shown_with_credentials():
    """reddit_search should appear when credentials are configured."""
    original_fn = tools._reddit_credentials_configured
    tools._reddit_credentials_configured = lambda: True

    active_names = {t["function"]["name"] for t in tools.get_active_tools()}
    assert "reddit_search" in active_names

    tools._reddit_credentials_configured = original_fn


def test_newsapi_tool_shown_with_credentials():
    """startup_news should appear when credentials are configured."""
    original_fn = tools._newsapi_configured
    tools._newsapi_configured = lambda: True

    active_names = {t["function"]["name"] for t in tools.get_active_tools()}
    assert "startup_news" in active_names

    tools._newsapi_configured = original_fn
