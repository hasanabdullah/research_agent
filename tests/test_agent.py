"""Sanity tests for Deepshika — the agent runs these on itself."""

import json
import sys
from pathlib import Path

# Ensure project root is on sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import costs
import tools


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
    # Point PATCHES_DIR to a temp location
    monkeypatch.setattr(tools, "PATCHES_DIR", tmp_path)
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
    monkeypatch.setattr(tools, "PATCHES_DIR", tmp_path)
    # Create an existing research file
    research_dir = tools.ROOT / "data" / "research"
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


def test_append_to_file_refuses_non_research():
    """append_to_file should refuse files outside research dir."""
    result = tools.handle_append_to_file(
        "agent.py",
        "# hacked",
        "test",
    )
    assert "REFUSED" in result


def test_reflect():
    """reflect should confirm the observation."""
    result = tools.handle_reflect("I notice my tests are passing.")
    assert "Observation logged" in result


def test_dispatch_unknown_tool():
    """dispatch_tool should handle unknown tool names."""
    result = tools.dispatch_tool("hack_the_planet", {})
    assert "Unknown tool" in result


def test_cost_ledger_roundtrip(tmp_path, monkeypatch):
    """Cost recording should persist and accumulate."""
    monkeypatch.setattr(costs, "COSTS_FILE", tmp_path / "costs.json")
    monkeypatch.setattr(costs, "DATA_DIR", tmp_path)

    pricing = {"input_per_mtok": 1.0, "output_per_mtok": 5.0}
    costs.record_call(1000, 500, pricing, label="test")
    costs.record_call(2000, 1000, pricing, label="test2")

    total = costs.get_total_usd()
    # 1000*1/1M + 500*5/1M + 2000*1/1M + 1000*5/1M = 0.001 + 0.0025 + 0.002 + 0.005 = 0.0105
    assert abs(total - 0.0105) < 0.0001


def test_identity_valid():
    """data/identity.json should be valid JSON with required fields."""
    identity_path = ROOT / "data" / "identity.json"
    assert identity_path.exists(), "identity.json missing"
    data = json.loads(identity_path.read_text())
    assert "name" in data
    assert "version" in data
    assert "traits" in data


def test_constitution_exists():
    """CONSTITUTION.md must exist and not be empty."""
    path = ROOT / "CONSTITUTION.md"
    assert path.exists(), "CONSTITUTION.md missing"
    text = path.read_text()
    assert len(text) > 100, "CONSTITUTION.md seems too short"
    assert "Preserve core identity" in text
