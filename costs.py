"""Cost tracking and budget enforcement for Deepshika."""

import json
from datetime import datetime, timezone
from pathlib import Path

_DEFAULT_DATA_DIR = Path(__file__).parent / "data"
_DEFAULT_COSTS_FILE = _DEFAULT_DATA_DIR / "costs.json"


def _load_ledger(costs_file: Path = None) -> dict:
    costs_file = costs_file or _DEFAULT_COSTS_FILE
    defaults = {"calls": [], "total_input_tokens": 0, "total_output_tokens": 0, "total_usd": 0.0}
    if costs_file.exists():
        data = json.loads(costs_file.read_text())
        return {**defaults, **data}
    return defaults


def _save_ledger(ledger: dict, costs_file: Path = None):
    costs_file = costs_file or _DEFAULT_COSTS_FILE
    costs_file.parent.mkdir(parents=True, exist_ok=True)
    costs_file.write_text(json.dumps(ledger, indent=2))


def record_call(input_tokens: int, output_tokens: int, pricing: dict, label: str = "", costs_file: Path = None) -> dict:
    """Record a single API call's cost. Returns the call record."""
    input_cost = (input_tokens or 0) * pricing["input_per_mtok"] / 1_000_000
    output_cost = (output_tokens or 0) * pricing["output_per_mtok"] / 1_000_000
    call_usd = input_cost + output_cost

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "label": label,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "usd": round(call_usd, 6),
    }

    ledger = _load_ledger(costs_file)
    ledger["calls"].append(record)
    ledger["total_input_tokens"] += input_tokens or 0
    ledger["total_output_tokens"] += output_tokens or 0
    ledger["total_usd"] = round(ledger["total_usd"] + call_usd, 6)
    _save_ledger(ledger, costs_file)

    return record


def get_total_usd(costs_file: Path = None) -> float:
    return _load_ledger(costs_file)["total_usd"]


def get_today_usd(costs_file: Path = None) -> float:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ledger = _load_ledger(costs_file)
    return sum(c["usd"] for c in ledger["calls"] if c["timestamp"].startswith(today))


def check_budget(config: dict, costs_file: Path = None) -> tuple[bool, str]:
    """Check if we're within budget. Returns (ok, message)."""
    budget = config.get("budget", {})
    max_total = budget.get("max_total_usd", 5.0)
    max_daily = budget.get("max_per_day_usd", 1.0)

    total = get_total_usd(costs_file)
    if total >= max_total:
        return False, f"Total budget exhausted: ${total:.4f} >= ${max_total:.2f}"

    daily = get_today_usd(costs_file)
    if daily >= max_daily:
        return False, f"Daily budget exhausted: ${daily:.4f} >= ${max_daily:.2f}"

    return True, f"Budget OK — ${total:.4f} total, ${daily:.4f} today"


def get_summary(costs_file: Path = None) -> dict:
    """Return a cost summary for display."""
    ledger = _load_ledger(costs_file)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_usd = sum(c["usd"] for c in ledger["calls"] if c["timestamp"].startswith(today))
    return {
        "total_usd": ledger["total_usd"],
        "today_usd": round(today_usd, 6),
        "total_calls": len(ledger["calls"]),
        "total_input_tokens": ledger["total_input_tokens"],
        "total_output_tokens": ledger["total_output_tokens"],
    }
