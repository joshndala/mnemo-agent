# 🧠 mnemo

> **Local-first agent memory CLI** — dump, diff, migrate, and query memories across [Mem0](https://mem0.ai), [Letta](https://letta.com), and your local filesystem.

Agents are finally getting good long‑term memory, but every framework (Mem0, Letta, Supermemory, custom Postgres) stores it differently. mnemo is a git‑like CLI for agent memory: you can dump, diff, migrate, and query what your agents know, all from your terminal, using a simple normalized schema and local‑first files. It’s designed for developers who want to own their agent’s “brain” instead of locking it into a single vendor.

Inspired by Mnemosyne (Greek goddess of memory), **mnemo** is a portable CLI for managing agent memory: capture facts, version-control dumps, compare snapshots, and sync to cloud memory providers — all from your terminal.

---

## Features

- **15 CLI commands** with rich `--help` and tab-completion
- **Normalized schema** — facts with `{entity, attribute, value, source, timestamp, confidence, metadata.tags}`
- **Multi-provider** — local JSON, Mem0, Letta (stubs → real APIs with optional deps)
- **TF-IDF search** — `mnemo recall "query"` with zero external ML deps, filterable by `--tag`
- **Rich tables** — confidence color-coded (🟢 ≥0.8, 🟡 ≥0.5, 🔴 <0.5)
- **HTML + graph diffs** — visual diff between dump snapshots
- **MCP server** — JSON-RPC 2.0 + stdio transport; plug directly into Claude Desktop, Cursor, or any MCP client
- **Push/pull sync** — S3, Cloudflare R2, or local filesystem remote; timestamp-based merge
- **Safe writes** — `--dry-run` on load, pull, and migrate

---

## Quick Start

```bash
# Install
pip install mnemo-agent              # core (local only)
pip install "mnemo-agent[s3]"       # + S3/R2 push-pull sync
pip install "mnemo-agent[all]"      # everything (mem0 + letta + parquet + graph + s3)

# Initialize Joshua's job-prep agent
mnemo init --agent job-prep

# Add facts manually (entity defaults to agent name, --tag is repeatable)
mnemo add --fact "Joshua uses React, Node, Supabase, Vercel" --agent job-prep
mnemo add --fact "Joshua is based in Toronto" --agent job-prep --confidence 1.0
mnemo add --fact "Chose Supabase over Firebase for auth" --agent job-prep --attribute decision --tag decision --tag auth

# View stored memories — plain format shows IDs for retract/edit
mnemo show --agent job-prep
mnemo show --agent job-prep --format plain

# Recall using natural language, optionally filtered by tag
mnemo recall "tech stack" --agent job-prep
mnemo recall "auth" --agent job-prep --tag decision
mnemo search "Supabase database" --agent job-prep --limit 5

# Edit or remove facts by ID (use 'show --format plain' to find IDs)
mnemo retract a1b2c3d4 --agent job-prep
mnemo edit a1b2c3d4 --value "Updated wording" --agent job-prep

# List all agents
mnemo ls --pretty

# Dump to a timestamped file
mnemo dump --agent job-prep

# Load a sample dump
mnemo load --file tests/fixtures/job_prep_sample.json --agent job-prep

# Compare two agents (or two dump files)
mnemo diff --agent-a job-prep --agent-b job-prep-v2
mnemo diff dump1.json dump2.json --html diff_report.html

# Start the MCP server — HTTP mode
mnemo serve --agent job-prep --port 8080
# Or stdio mode (Claude Desktop / Cursor — no port needed)
mnemo serve --agent job-prep --stdio

# Sync to S3 (prompts for credentials on first add)
mnemo remote add origin s3://my-bucket/mnemo --agent job-prep
mnemo push --agent job-prep
mnemo pull --agent job-prep   # merges remote facts into local
```

---

## 📋 All Commands

| Command | Description |
|---|---|
| `mnemo init --agent <name>` | Initialize agent directory + config |
| `mnemo add --fact "text" --agent <name>` | Add a memory fact (`--entity`, `--attribute`, `--tag` supported) |
| `mnemo dump --agent <name> [--source mem0\|letta]` | Dump memories to JSON |
| `mnemo load --file dump.json --agent <name>` | Load dump into local/Mem0/Letta |
| `mnemo ls [--agent all]` | List agents and fact counts |
| `mnemo show --agent <name>` | Display agent's latest memories (`--format pretty\|json\|plain`) |
| `mnemo diff --agent-a <a> --agent-b <b>` | Diff two agents (or `diff a.json b.json`) |
| `mnemo recall "query" [--tag <tag>]` | TF-IDF search across all agents, optional tag filter |
| `mnemo search "query" [--limit 10] [--tag <tag>]` | Alias for recall with higher default limit |
| `mnemo retract <fact-id> --agent <name>` | Remove a fact by ID or 8-char prefix |
| `mnemo edit <fact-id> --agent <name>` | Edit value/attribute/confidence of an existing fact |
| `mnemo migrate --dump f.json --target mem0 --agent name` | Migrate between providers |
| `mnemo serve --agent <name> [--port 8080] [--stdio] [--read-only]` | MCP server — HTTP (JSON-RPC 2.0) or stdio for Claude Desktop / Cursor |
| `mnemo remote add <name> <url> --agent <name>` | Add a named remote (s3://, r2://, file://) |
| `mnemo remote list --agent <name>` | List configured remotes |
| `mnemo remote remove <name> --agent <name>` | Remove a remote |
| `mnemo push [--remote origin] --agent <name>` | Push local memory to remote |
| `mnemo pull [--remote origin] --agent <name>` | Pull and merge remote memory into local |

---

## Project Structure

```
mnemo-agent/
├── src/mnemo/
│   ├── __init__.py          # version
│   ├── cli.py               # Click CLI (all commands)
│   ├── models.py            # Pydantic: Fact, AgentDump, MnemoConfig
│   ├── storage.py           # Local file I/O (JSON, YAML, credentials)
│   ├── search.py            # TF-IDF search + diff engine
│   ├── remotes.py           # Push/pull backends: FileBackend, S3Backend
│   ├── server.py            # FastAPI MCP server (HTTP + JSON-RPC 2.0)
│   ├── stdio_server.py      # stdio MCP transport (Claude Desktop / Cursor)
│   └── adapters/
│       ├── mem0_adapter.py  # Mem0 API → normalized facts
│       └── letta_adapter.py # Letta API → normalized facts
├── tests/
│   ├── test_cli.py          # CLI command tests
│   ├── test_remote.py       # Remote backends, merge, push/pull tests
│   ├── test_server.py       # MCP server: JSON-RPC 2.0, tools, stdio transport
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

## 🔌 MCP Server

mnemo implements the [MCP 2024-11-05 spec](https://spec.modelcontextprotocol.io) and supports two transports.

### stdio — Claude Desktop / Cursor

Add to `~/.claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "mnemo-job-prep": {
      "command": "mnemo",
      "args": ["serve", "--agent", "job-prep", "--stdio"]
    }
  }
}
```

That's it — Claude Desktop will spawn mnemo as a subprocess and communicate over stdin/stdout.

### HTTP — REST + JSON-RPC 2.0

```bash
mnemo serve --agent job-prep --port 8080
```

| Endpoint | Description |
|---|---|
| `POST /` | JSON-RPC 2.0 — `initialize`, `tools/list`, `tools/call`, `ping` |
| `GET /mcp/list_tools` | List available tools (legacy, kept for compatibility) |
| `POST /mcp/call_tool` | Call a tool by name (legacy, kept for compatibility) |
| `GET /facts` | REST: list all facts (`?entity=`, `?attribute=`, `?tag=`) |
| `GET /search?q=query` | REST: search memories (`?tag=` filter supported) |
| `GET /health` | Health check with version info |
| `GET /docs` | Swagger UI |

### Available MCP tools

| Tool | Description |
|---|---|
| `search_memory` | TF-IDF keyword search; supports `tag` filter |
| `list_facts` | List all facts; filterable by `entity`, `attribute`, `tag`; shows IDs |
| `upsert_fact` | Add a fact; supports `tags` array |
| `retract_fact` | Remove a fact by ID or 8-char prefix |
| `edit_fact` | Update a fact's `value`, `attribute`, or `confidence` |
| `get_agent_info` | Agent name, fact count, last updated timestamp |

`retract_fact` and `edit_fact` are disabled when `--read-only` is set.

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
remotes:
  origin: s3://my-bucket/mnemo
```

Remote credentials (S3/R2 access keys) are stored separately in `~/.mnemo/credentials` with `chmod 600`. They are populated automatically when you run `mnemo remote add` — you will be prompted for them interactively. Pass `--no-creds` to skip prompting and rely on the standard boto3 credential chain (`AWS_ACCESS_KEY_ID` env var, `~/.aws/credentials`, IAM role).

---

## Environment Variables

| Variable | Description |
|---|---|
| `MNEMO_AGENT` | Default agent name (skips `--agent` flag) |
| `MNEMO_DIR` | Override base directory (default: `~/.mnemo`) |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | S3 credentials (alternative to prompting) |
| `R2_ACCOUNT_ID` | Cloudflare R2 account ID (alternative to prompting) |

---

## Tests

```bash
pip install "mnemo-agent[dev,s3]"
pytest tests/ -v
```

114 tests across `test_cli.py`, `test_remote.py`, and `test_server.py`.

---

## Roadmap

- [x] Push/pull sync to S3, R2, and local filesystem remotes
- [x] Write-time conflict detection with overwrite / keep-both / abort prompt
- [x] Full MCP 2024-11-05 protocol — JSON-RPC 2.0 + stdio transport (Claude Desktop / Cursor)
- [ ] Vector embeddings for semantic search (v2)
- [ ] Parquet export for analytics
- [ ] `mnemo audit` — fact provenance trace
- [ ] Web UI dashboard

---

## Example Use Case: `job-prep` Agent

```bash
# Bootstrap your interview prep memory
mnemo init --agent job-prep
mnemo load --file tests/fixtures/job_prep_sample.json --agent job-prep

# Connect to Claude Desktop (add to claude_desktop_config.json, then restart)
mnemo serve --agent job-prep --stdio

# Or run as an HTTP server for other MCP clients
mnemo serve --agent job-prep --port 8080

# After a practice interview, add what you learned
mnemo add --fact "Lead with Supabase migration story at FAANG interviews" \
  --agent job-prep --attribute interview_tip --confidence 0.9 --tag tip

# If you added a conflicting fact by mistake, retract it by ID prefix
mnemo show --agent job-prep --format plain   # see IDs
mnemo retract a1b2c3d4 --agent job-prep

# Before next session, recall relevant context
mnemo recall "React Supabase full-stack" --agent job-prep
```

---

## License

MIT © Joshua Ndala
