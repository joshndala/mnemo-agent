# 🧠 mnemo

> **Local-first agent memory CLI** — dump, diff, migrate, and query memories across [Mem0](https://mem0.ai), [Letta](https://letta.com), and your local filesystem.

Agents are finally getting good long‑term memory, but every framework (Mem0, Letta, Supermemory, custom Postgres) stores it differently. mnemo is a git‑like CLI for agent memory: you can dump, diff, migrate, and query what your agents know, all from your terminal, using a simple normalized schema and local‑first files. It’s designed for developers who want to own their agent’s “brain” instead of locking it into a single vendor.

Inspired by Mnemosyne (Greek goddess of memory), **mnemo** is a portable CLI for managing agent memory: capture facts, version-control dumps, compare snapshots, and sync to cloud memory providers — all from your terminal.

---

## Features

- **11 CLI commands** with rich `--help` and tab-completion
- **Normalized schema** — facts with `{entity, attribute, value, source, timestamp, confidence}`
- **Multi-provider** — local JSON, Mem0, Letta (stubs → real APIs with optional deps)
- **TF-IDF search** — `mnemo recall "query"` with zero external ML deps
- **Rich tables** — confidence color-coded (🟢 ≥0.8, 🟡 ≥0.5, 🔴 <0.5)
- **HTML + graph diffs** — visual diff between dump snapshots
- **MCP server** — FastAPI `/mcp/list_tools` + `/mcp/call_tool` for Ollama/Claude Code agents
- **Safe writes** — `--dry-run` and `--approval` flags

---

## Quick Start

```bash
# Install
cd mnemo-agent
pip install -e .          # core (local only)
pip install -e ".[all]"   # everything (mem0 + letta + parquet + graph)

# Initialize Joshua's job-prep agent
mnemo init --agent job-prep

# Add facts manually
mnemo add --fact "Joshua uses React, Node, Supabase, Vercel" --agent job-prep
mnemo add --fact "Joshua is based in Toronto" --agent job-prep --confidence 1.0

# View stored memories (by agent name or by file path)
mnemo show --agent job-prep

# Recall using natural language
mnemo recall "tech stack" --agent job-prep
mnemo search "Supabase database" --agent job-prep --limit 5

# List all agents
mnemo ls --pretty

# Dump to a timestamped file
mnemo dump --agent job-prep

# Load a sample dump
mnemo load --file tests/fixtures/job_prep_sample.json --agent job-prep

# Compare two agents (or two dump files)
mnemo diff --agent-a job-prep --agent-b job-prep-v2
mnemo diff dump1.json dump2.json --html diff_report.html

# Start the MCP server (for Ollama / Claude Code agents)
mnemo serve --agent job-prep --port 8080
```

---

## 📋 All Commands

| Command | Description |
|---|---|
| `mnemo init --agent <name>` | Initialize agent directory + config |
| `mnemo add --fact "text" --agent <name>` | Add a memory fact |
| `mnemo dump --agent <name> [--source mem0\|letta]` | Dump memories to JSON |
| `mnemo load --file dump.json --agent <name>` | Load dump into local/Mem0/Letta |
| `mnemo ls [--agent all]` | List agents and fact counts |
| `mnemo show --agent <name>` | Display agent's latest memories (or `--dump <file>`) |
| `mnemo diff --agent-a <a> --agent-b <b>` | Diff two agents (or `diff a.json b.json`) |
| `mnemo recall "query"` | TF-IDF search across all agents |
| `mnemo search "query" [--limit 10]` | Search with higher limit |
| `mnemo migrate --dump f.json --target mem0 --agent name` | Migrate between providers |
| `mnemo serve --agent <name> [--port 8080] [--read-only]` | MCP FastAPI server |

---

## Project Structure

```
mnemo-agent/
├── src/mnemo/
│   ├── __init__.py          # version
│   ├── cli.py               # Click CLI (all commands)
│   ├── models.py            # Pydantic: Fact, AgentDump, MnemoConfig
│   ├── storage.py           # Local file I/O (JSON, YAML, Parquet)
│   ├── search.py            # TF-IDF search + diff engine
│   ├── server.py            # FastAPI MCP server
│   └── adapters/
│       ├── mem0_adapter.py  # Mem0 API → normalized facts
│       └── letta_adapter.py # Letta API → normalized facts
├── tests/
│   ├── test_cli.py          # pytest suite
│   └── fixtures/
│       └── job_prep_sample.json
├── config.yaml              # Sample agent config
├── pyproject.toml
└── requirements.txt
```

---

## Memory Schema

```json
{
  "agent": "job-prep",
  "dump_ts": "2026-03-21T23:00Z",
  "source": "manual",
  "version": "1",
  "facts": [
    {
      "id": "uuid",
      "entity": "Joshua",
      "attribute": "tech_stack",
      "value": "React, Node, Supabase, Vercel",
      "source": "chat|tool|manual|mem0|letta|import",
      "timestamp": "2026-03-21T20:00Z",
      "confidence": 0.95,
      "metadata": {}
    }
  ]
}
```

### Example: Project Memory for `advisor-prep`

```json
{
  "agent": "advisor-prep",
  "facts": [
    {
      "id": "uuid-1",
      "entity": "advisor-prep-agent",
      "attribute": "project_summary",
      "value": "CLI + agent that helps students prep for advisor meetings using UBC context.",
      "source": "manual",
      "timestamp": "2026-03-22T01:00Z",
      "confidence": 0.9,
      "metadata": { "tags": ["summary", "high-level"] }
    },
    {
      "id": "uuid-2",
      "entity": "advisor-prep-agent",
      "attribute": "decision",
      "value": "Chose Supabase over Firebase for auth due to better Postgres integration.",
      "source": "manual",
      "timestamp": "2026-03-22T01:05Z",
      "confidence": 0.95,
      "metadata": { "tags": ["decision", "auth"], "ticket": "ADR-001" }
    },
    {
      "id": "uuid-3",
      "entity": "advisor-prep-agent",
      "attribute": "stack",
      "value": "Next.js, React, Node, Supabase, Vercel.",
      "source": "manual",
      "timestamp": "2026-03-22T01:10Z",
      "confidence": 1.0,
      "metadata": { "tags": ["stack"] }
    }
  ]
}
```

---

## 🔌 MCP Server (for Ollama / Claude Code)

```bash
mnemo serve --agent job-prep --port 8080
```

| Endpoint | Description |
|---|---|
| `GET /mcp/list_tools` | List available tools (MCP schema) |
| `POST /mcp/call_tool` | Call a tool by name with arguments |
| `GET /facts` | REST: list all facts |
| `GET /search?q=query` | REST: search memories |
| `GET /docs` | Swagger UI |

### Available MCP tools

```json
{ "name": "search_memory",  "description": "TF-IDF search over agent memory" }
{ "name": "list_facts",     "description": "Return all facts, optionally filtered" }
{ "name": "upsert_fact",    "description": "Add a fact to agent memory" }
{ "name": "get_agent_info", "description": "Agent metadata and fact count" }
```

---

## ⚙️ Configuration

Each agent has `~/.mnemo/<agent>/config.yaml`:

```yaml
agent: job-prep
default_source: local
default_target: local
mem0_api_key: null          # https://app.mem0.ai
mem0_user_id: joshua
letta_base_url: http://localhost:8283
letta_agent_id: null        # from your Letta agent
tags: [job-prep, interview]
notes: Memory store for interview prep agent
```

---

## Environment Variables

| Variable | Description |
|---|---|
| `MNEMO_AGENT` | Default agent name (skips `--agent` flag) |
| `MNEMO_DIR` | Override base directory (default: `~/.mnemo`) |

---

## Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

---

## Roadmap

- [ ] Vector embeddings for semantic search (v2)
- [ ] Parquet export for analytics
- [ ] `mnemo audit` — fact provenance trace
- [ ] Web UI dashboard
- [ ] Native Ollama MCP client registration

---

## Example Use Case: `job-prep` Agent

```bash
# Bootstrap your interview prep memory
mnemo init --agent job-prep
mnemo load --file tests/fixtures/job_prep_sample.json --agent job-prep

# Ask your agent questions via MCP (Ollama / Claude Code reads from :8080)
mnemo serve --agent job-prep --port 8080

# After a practice interview, add what you learned
mnemo add --fact "Lead with Supabase migration story at FAANG interviews" \
  --agent job-prep --attribute interview_tip --confidence 0.9

# Before next session, recall relevant context
mnemo recall "React Supabase full-stack" --agent job-prep
```

---

## License

MIT © Joshua Ndala
