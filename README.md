# Deepshika

A self-modifying AI research agent with human-in-the-loop approval, budget controls, and a web dashboard.

Deepshika runs autonomous research cycles — searching the web, synthesizing findings into markdown files, and proposing edits to its own code — all governed by a constitution that a supervisor LLM enforces before any change is applied.

## How It Works

```
┌─────────────────────────────────────────────────────┐
│                    RESEARCH CYCLE                    │
│                                                     │
│  Wake → Build Context → Think (multi-turn LLM)      │
│    → Use Tools (search, read, write)                │
│    → Propose Patches → Supervisor Review            │
│    → Human Approval → Apply & Commit → Log → Sleep  │
└─────────────────────────────────────────────────────┘
```

Each cycle, the agent:

1. **Gathers context** — mission, budget remaining, recent cycles, research files, git history
2. **Thinks** — up to 10 tool-use turns per cycle with Claude via the OpenAI SDK
3. **Proposes changes** — generates diffs saved to `pending_patches/`
4. **Supervisor reviews** — a second LLM call checks the patch against the [constitution](#constitution)
5. **Human approves** — in `full` mode you confirm every change; in `research` mode, research files auto-apply
6. **Commits & logs** — applied patches are git-committed; every cycle is recorded to `cycles.jsonl`

## Features

- **Multi-topic** — run isolated research projects, each with its own mission, budget, and output files
- **Constitution + Supervisor** — safety-first design; every file edit is reviewed against 7 immutable principles
- **Budget enforcement** — per-day and total cost caps with mid-cycle checks
- **Web dashboard** — create topics, start/stop agents, view research, edit missions, monitor costs
- **LLM-scaffolded topics** — when you create a topic, an LLM generates the mission, agent parameters, and output file structure
- **Web research tools** — DuckDuckGo search + URL fetching built in (rate-limited per cycle)
- **Notion publishing** — push research output to Notion pages with one click
- **Configurable LLM provider** — switch between OpenRouter, Anthropic, or OpenAI without code changes

## Quick Start

### 1. Install

```bash
pip install -e .
```

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env`:

```env
# "anthropic", "openrouter", or "openai"
LLM_PROVIDER=anthropic

# API key for your chosen provider
LLM_API_KEY=sk-ant-...
```

### 3. Create a topic

```bash
deepshika topic create "market-analysis" --description "Analyze the micro-SaaS market landscape"
```

This generates `mission.md`, `agent_parameters.md`, and pre-structured research output files via LLM.

### 4. Run

**CLI** — run 5 research cycles:
```bash
deepshika run --cycles 5
```

**Dashboard** — web UI at `http://127.0.0.1:8000`:
```bash
deepshika dashboard
```

### 5. Review

```bash
deepshika review    # approve/reject pending patches
deepshika watch     # live stats (budget, cycles, research files)
deepshika costs     # spending summary
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `deepshika init` | Initialize project structure |
| `deepshika run [--cycles N]` | Run N research cycles for the active topic |
| `deepshika status` | Show active topic status |
| `deepshika review` | Review and approve/reject pending patches |
| `deepshika log [-n N]` | Show recent cycle logs |
| `deepshika costs` | Display cost summary |
| `deepshika watch` | Live monitoring dashboard |
| `deepshika topic create NAME` | Create a new research topic |
| `deepshika topic list` | List all topics |
| `deepshika topic switch NAME` | Switch active topic |
| `deepshika topic status` | Show active topic details |
| `deepshika dashboard [--port N]` | Launch web dashboard |
| `deepshika run-topic NAME` | Run a specific topic (used by dashboard) |

## Agent Tools

The agent has access to these tools during each cycle:

| Tool | Description |
|------|-------------|
| `read_file` | Read any project file (research, source code, data) |
| `propose_edit` | Generate a diff and save as a pending patch for review |
| `append_to_file` | Append content to a research file |
| `web_search` | Search the web via DuckDuckGo (max 3/cycle) |
| `web_fetch` | Fetch and parse a URL to clean text (max 3/cycle) |
| `run_tests` | Run the pytest test suite |
| `reflect` | Log an observation without proposing changes |

## Constitution

Every proposed change is reviewed against 7 immutable principles:

1. **Preserve core identity** — change capabilities, not values
2. **Propose, never force** — all changes require human approval
3. **Minimize harm** — no deletions, no arbitrary shell execution
4. **Stay reversible** — every change revertible via git
5. **Be transparent** — explain *why*, not just *what*
6. **Respect budget** — stop gracefully at cost limits
7. **Log everything** — append-only cycle records

## Project Structure

```
deepshika/
├── agent.py             # CLI + core research cycle
├── tools.py             # Agent tool definitions + dispatch
├── supervisor.py        # Constitution review logic
├── costs.py             # Budget tracking + enforcement
├── dashboard.py         # FastAPI web UI + Notion integration
├── llm.py               # LLM provider factory (OpenRouter/Anthropic/OpenAI)
├── CONSTITUTION.md      # 7 immutable safety principles
├── config.yaml          # Global config (model, mode, budget, pricing)
├── .env.example         # Environment variable template
├── static/index.html    # Dashboard SPA
└── topics/
    └── {topic-name}/
        ├── mission.md           # Research goals and output specs
        ├── agent_parameters.md     # Agent parameters for this topic
        ├── identity.json        # Agent identity metadata
        ├── agent_config.yaml    # Per-topic budget overrides
        └── data/
            ├── research/        # Agent's markdown output files
            ├── pending_patches/ # Diffs awaiting human review
            ├── costs.json       # Topic cost ledger
            └── cycles.jsonl     # Topic cycle log
```

## LLM Provider Configuration

Set `LLM_PROVIDER` in `.env` to switch providers. The OpenAI SDK is used for all providers (OpenRouter and Anthropic both expose OpenAI-compatible endpoints).

| Provider | `LLM_PROVIDER` | `LLM_API_KEY` |
|----------|----------------|---------------|
| Anthropic | `anthropic` | `sk-ant-...` |
| OpenRouter | `openrouter` | `sk-or-...` |
| OpenAI | `openai` | `sk-...` |

Model names in `config.yaml` use OpenRouter format (`anthropic/claude-opus-4.6`). When using Anthropic directly, `resolve_model()` auto-converts to the correct format (`claude-opus-4-6`).

**Backwards compatible** — if `LLM_PROVIDER` isn't set, defaults to `openrouter` and falls back to `OPENROUTER_API_KEY`.

## Notion Integration

To publish research to Notion, add to `.env`:

```env
NOTION_TOKEN=secret_...
NOTION_PAGE_ID=your-parent-page-id
```

Then use the dashboard's "Publish to Notion" button or call:
```
POST /api/topics/{name}/publish-to-notion
```

## Cost Tracking

Costs are tracked per-topic with configurable budgets:

```yaml
# config.yaml (global)
budget:
  max_per_day_usd: 10.00
  max_total_usd: 10.00

pricing:
  input_per_mtok: 5.00
  output_per_mtok: 25.00
```

Per-topic budgets can override the global config in `topics/{name}/agent_config.yaml`. Budget is checked at cycle start and mid-cycle — the agent stops gracefully when limits are reached.

## Modes

- **`research`** — auto-approves research file edits, refuses code changes. Good for hands-off operation.
- **`full`** — human approval required for all changes. For when the agent is modifying its own code.

Set in `config.yaml`:
```yaml
mode: research  # or "full"
```

## License

MIT
