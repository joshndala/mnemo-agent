# 🧠 mnemo

> **Local-first agent memory CLI** — dump, diff, migrate, and query memories across [Mem0](https://mem0.ai), [Letta](https://letta.com), and your local filesystem.

Agents are finally getting good long‑term memory, but every framework (Mem0, Letta, Supermemory, custom Postgres) stores it differently. mnemo is a git‑like CLI for agent memory: you can dump, diff, migrate, and query what your agents know, all from your terminal, using a simple normalized schema and local‑first files. It’s designed for developers who want to own their agent’s “brain” instead of locking it into a single vendor.

Inspired by Mnemosyne (Greek goddess of memory), **mnemo** is a portable CLI for managing agent memory: capture facts, version-control dumps, compare snapshots, and sync to cloud memory providers — all from your terminal.

---

## Features

- **17 CLI commands** with rich `--help` and tab-completion
- **Normalized schema** — facts with `{entity, attribute, value, source, timestamp, confidence, metadata.tags}`
- **Multi-provider** — local JSON, Mem0, Letta (stubs → real APIs with optional deps)
- **Semantic search** — `mnemo recall "query" --method semantic|hybrid` via fastembed (ONNX, no PyTorch); TF-IDF default needs zero extra deps
- **Rich tables** — confidence color-coded (🟢 ≥0.8, 🟡 ≥0.5, 🔴 <0.5)
- **HTML + graph diffs** — visual diff between dump snapshots
- **MCP server** — JSON-RPC 2.0 + stdio transport; plug directly into Claude Desktop, Cursor, or any MCP client
- **Web UI** — `mnemo ui` opens a local dashboard: browse all agents, add/edit/retract facts, import/export dumps
- **Push/pull sync** — S3, Cloudflare R2, or local filesystem remote; timestamp-based merge
- **Auto memory from chat logs** — `mnemo ingest --file chat.json` extracts facts from Claude.ai, ChatGPT, Cursor, or plain text exports via Claude API, OpenAI, Ollama, or heuristics
- **Python SDK** — `MnemoClient` + `AsyncMnemoClient` for programmatic access; local file I/O or remote HTTP; file-watch stream; no CLI required
- **Safe writes** — `--dry-run` on load, pull, migrate, and ingest

---

## Quick Start

```bash
# Install
pip install mnemo-agent                    # core (local only)
pip install "mnemo-agent[semantic]"        # + semantic/hybrid search (fastembed, no PyTorch)
pip install "mnemo-agent[ingest]"          # + auto memory from chat logs (anthropic + openai SDKs)
pip install "mnemo-agent[s3]"             # + S3/R2 push-pull sync
pip install "mnemo-agent[sdk]"            # + Python SDK (MnemoClient, AsyncMnemoClient — httpx)
pip install "mnemo-agent[all]"            # everything (mem0 + letta + parquet + graph + s3 + semantic + ingest + sdk)

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
mnemo recall "tech stack" --agent job-prep                          # TF-IDF (default)
mnemo recall "tech decisions I've made" --agent job-prep --method semantic   # fastembed semantic
mnemo recall "auth" --agent job-prep --tag decision --method hybrid          # hybrid
mnemo search "Supabase database" --agent job-prep --limit 5

# Auto-extract facts from a Claude.ai or ChatGPT chat export
mnemo ingest --file ~/Downloads/claude_chat.json --agent job-prep
mnemo ingest --file ~/Downloads/conversations.json --format chatgpt --agent job-prep
mnemo ingest --file chat.json --agent job-prep --extractor heuristic  # offline, no API key
mnemo ingest --file chat.json --agent job-prep --dry-run              # preview before saving

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

# Open the web dashboard (all agents, auto-opens browser)
mnemo ui
# Or jump straight to a specific agent
mnemo ui --agent job-prep

# Sync to S3 (prompts for credentials on first add)
mnemo remote add origin s3://my-bucket/mnemo --agent job-prep
mnemo push --agent job-prep
mnemo pull --agent job-prep   # merges remote facts into local
```

---

## 🔍 Semantic Search

mnemo supports three search modes via `--method`:

| Mode | Description | Requires |
|---|---|---|
| `tfidf` | Keyword matching (default) — no extra deps, instant | nothing |
| `semantic` | Cosine similarity via fastembed (ONNX, no PyTorch) | `mnemo[semantic]` |
| `hybrid` | `0.7 × semantic + 0.3 × tfidf`, both max-normalized | `mnemo[semantic]` |

```bash
pip install "mnemo-agent[semantic]"   # ~30MB install, model (~130MB) downloads on first use

mnemo recall "tech decisions I've made" --method semantic
mnemo recall "outdoor activities" --method hybrid --limit 10
```

- **Model:** `BAAI/bge-small-en-v1.5` (384-dim, cached at `~/.cache/fastembed/` after first use)
- **Speed:** ~0.1–0.5s for <500 facts on CPU — fast enough for CLI use
- **MCP:** pass `"mode": "semantic"` or `"mode": "hybrid"` in `search_memory` tool args
- **REST:** `GET /search?q=query&mode=semantic` or `GET /agents/{agent}/search?q=query&mode=hybrid`

---

## 🤖 Auto Memory from Chat Logs

`mnemo ingest` parses a chat export and uses an LLM (or heuristics offline) to extract structured facts — no manual `add` required.

```bash
pip install "mnemo-agent[ingest]"   # anthropic + openai SDKs (~10MB combined)

# Auto-picks best extractor: ANTHROPIC_API_KEY → OPENAI_API_KEY → Ollama → heuristic
mnemo ingest --file ~/Downloads/claude_chat.json --agent job-prep

# Explicit extractor + model
mnemo ingest --file chat.json --agent job-prep --extractor openai --extractor-model gpt-4o

# Ollama (local, fully offline)
mnemo ingest --file chat.json --agent job-prep --extractor ollama --extractor-model llama3.2

# Gemini via OpenAI-compatible endpoint
OPENAI_API_KEY=$GEMINI_KEY mnemo ingest --file chat.json --agent job-prep \
  --extractor openai \
  --extractor-url https://generativelanguage.googleapis.com/v1beta/openai/ \
  --extractor-model gemini-1.5-flash

# Heuristic (regex patterns, zero deps)
mnemo ingest --file chat.json --agent job-prep --extractor heuristic
```

Always shows a preview table and asks `Save these N facts? [y/N]` before writing. Use `--dry-run` to preview without the prompt.

| Extractor | Default model | Requires | Notes |
|---|---|---|---|
| `auto` | — | — | Picks best available based on env |
| `claude` | `claude-haiku-4-5` | `mnemo[ingest]` | Best quality |
| `openai` | `gpt-4o-mini` | `mnemo[ingest]` | Also covers Groq, Gemini, LMStudio via `--extractor-url` |
| `ollama` | `llama3.2` | `mnemo[ingest]` | Shortcut for `localhost:11434` — fully local |
| `heuristic` | — | nothing | Regex patterns, offline fallback |

**Supported formats:** Claude.ai JSON, ChatGPT `conversations.json`, Cursor, plain text (auto-detected).

---

## 🐍 Python SDK

Use mnemo programmatically — no CLI required. Works locally (reads `~/.mnemo` directly) or remotely (talks to `mnemo serve`).

```bash
pip install "mnemo-agent[sdk]"   # adds httpx for remote mode; local mode needs nothing extra
```

```python
from mnemo import MnemoClient, AsyncMnemoClient

# ── Sync local (reads ~/.mnemo directly) ──────────────────────────────────
client = MnemoClient(agent="job-prep")

fact = client.add("Lead with Supabase story", attribute="interview_tip", confidence=0.9)
results = client.recall("Supabase", method="hybrid")   # tfidf | semantic | hybrid
facts   = client.list_facts(attribute="interview_tip", tag="tip")
client.edit(fact.id[:8], value="Updated wording")
client.retract(fact.id[:8])

info = client.info()   # {"agent": "job-prep", "fact_count": 12, "last_updated": "..."}
dump = client.dump()   # returns AgentDump

# ── Sync remote (talks to mnemo serve) ───────────────────────────────────
remote = MnemoClient(agent="job-prep", url="http://localhost:8080")
remote.add("Rust for systems work", attribute="tool")

# ── Async (local or remote) ───────────────────────────────────────────────
import asyncio

async def main():
    async with AsyncMnemoClient(agent="job-prep") as client:
        fact  = await client.add("async fact", attribute="note")
        found = await client.recall("fact", method="tfidf")
        print(found)

asyncio.run(main())

# ── File-watch stream (local only) ────────────────────────────────────────
async def watch_demo():
    async with AsyncMnemoClient(agent="job-prep") as client:
        async for fact in client.watch(poll_interval=0.5):
            print(f"New: {fact.entity} · {fact.attribute}: {fact.value}")
```

### SDK method reference

| Method | Description |
|---|---|
| `add(value, *, entity, attribute, confidence, source, tags)` | Add a fact; entity defaults to agent name |
| `recall(query, *, limit=5, tag, method)` | Search — `tfidf` / `semantic` / `hybrid` |
| `search(query, *, limit=10, ...)` | Alias for `recall` with higher default limit |
| `list_facts(*, entity, attribute, tag)` | Return all facts, optionally filtered |
| `get(fact_id)` | Lookup by id prefix; returns `None` if missing |
| `retract(fact_id)` | Remove by id prefix; returns `False` if not found |
| `edit(fact_id, *, value, attribute, confidence)` | Mutate in-place; raises `KeyError` if missing |
| `info()` | `{"agent", "fact_count", "last_updated"}` |
| `dump()` | Full `AgentDump` |
| `watch(*, poll_interval=0.5)` | Async generator — yields new facts as they appear (local only) |

`AsyncMnemoClient` mirrors every method as `async def`. Both clients work as context managers.

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
| `mnemo recall "query" [--method tfidf\|semantic\|hybrid] [--tag <tag>]` | Search across all agents — TF-IDF (default), semantic (fastembed), or hybrid |
| `mnemo search "query" [--limit 10] [--method tfidf\|semantic\|hybrid] [--tag <tag>]` | Alias for recall with higher default limit (10) |
| `mnemo ingest --file chat.json --agent <name> [--extractor auto\|claude\|openai\|ollama\|heuristic]` | Auto-extract facts from Claude.ai, ChatGPT, Cursor, or plain text exports |
| `mnemo retract <fact-id> --agent <name>` | Remove a fact by ID or 8-char prefix |
| `mnemo edit <fact-id> --agent <name>` | Edit value/attribute/confidence of an existing fact |
| `mnemo migrate --dump f.json --target mem0 --agent name` | Migrate between providers |
| `mnemo serve --agent <name> [--port 8080] [--stdio] [--read-only]` | MCP server — HTTP (JSON-RPC 2.0) or stdio for Claude Desktop / Cursor |
| `mnemo ui [--agent <name>] [--port 7742] [--read-only]` | Open web dashboard — all agents overview, per-agent facts/search/diff/import/export |
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
│   ├── __init__.py          # version + SDK exports (MnemoClient, AsyncMnemoClient, Fact, AgentDump)
│   ├── cli.py               # Click CLI (all commands)
│   ├── client.py            # Python SDK: MnemoClient + AsyncMnemoClient (local + remote backends)
│   ├── models.py            # Pydantic: Fact, AgentDump, MnemoConfig
│   ├── storage.py           # Local file I/O (JSON, YAML, credentials)
│   ├── search.py            # TF-IDF, semantic, and hybrid search + diff engine
│   ├── embeddings.py        # fastembed backend for semantic search (optional dep)
│   ├── remotes.py           # Push/pull backends: FileBackend, S3Backend
│   ├── server.py            # FastAPI MCP server (HTTP + JSON-RPC 2.0) + multi-agent UI server
│   ├── stdio_server.py      # stdio MCP transport (Claude Desktop / Cursor)
│   ├── static/
│   │   └── ui.html          # Single-file web dashboard (Alpine.js + Tailwind CDN)
│   └── adapters/
│       ├── mem0_adapter.py     # Mem0 API → normalized facts
│       ├── letta_adapter.py    # Letta API → normalized facts
│       └── ingest_adapter.py   # Chat log parsers + LLM/heuristic fact extractors
├── tests/
│   ├── test_cli.py          # CLI command tests
│   ├── test_client.py       # SDK tests: local, remote (mocked), async, file-watch
│   ├── test_ingest.py       # Chat parsers, heuristic extractor, ingest CLI tests
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
| `GET /search?q=query` | REST: search memories (`?mode=tfidf\|semantic\|hybrid`, `?tag=` filter supported) |
| `GET /health` | Health check with version info |
| `GET /docs` | Swagger UI |

### Available MCP tools

| Tool | Description |
|---|---|
| `search_memory` | Keyword search; `mode` param: `tfidf` (default), `semantic`, `hybrid`; supports `tag` filter |
| `list_facts` | List all facts; filterable by `entity`, `attribute`, `tag`; shows IDs |
| `upsert_fact` | Add a fact; supports `tags` array |
| `retract_fact` | Remove a fact by ID or 8-char prefix |
| `edit_fact` | Update a fact's `value`, `attribute`, or `confidence` |
| `get_agent_info` | Agent name, fact count, last updated timestamp |

`retract_fact` and `edit_fact` are disabled when `--read-only` is set.

---

## 🖥 Web Dashboard

```bash
mnemo ui                        # opens http://localhost:7742/ui
mnemo ui --agent job-prep       # deep-links to that agent
mnemo ui --port 8080 --read-only
```

`mnemo ui` requires `uvicorn` (`pip install uvicorn`). The browser opens automatically.

### Agent list view
- Cards for every agent — fact count, dump count, last updated, top tags
- **Create** a new agent directly from the UI
- **Delete** an agent (confirmation required)
- Click any card to open the agent detail view

### Agent detail view
- **Facts table** — entity, attribute, value, confidence bar, tags, relative age
- **Filter chips** — one-click entity/attribute filters above the table; tag filter in sidebar
- **Add / Edit / Retract** facts with a slide-in panel
- **Import** — upload a dump JSON, merges new facts by ID
- **Export** — download the agent's latest dump as `<agent>-dump.json`
- **Search** — TF-IDF results with relevance scores (semantic/hybrid available via MCP/CLI)
- **Diff** — upload a second dump file and see added/removed/unchanged facts side by side

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
| `ANTHROPIC_API_KEY` | Enables `mnemo ingest --extractor claude` (auto-detected) |
| `OPENAI_API_KEY` | Enables `mnemo ingest --extractor openai` (auto-detected; also used for Groq/Gemini via `--extractor-url`) |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | S3 credentials (alternative to prompting) |
| `R2_ACCOUNT_ID` | Cloudflare R2 account ID (alternative to prompting) |

---

## Tests

```bash
pip install "mnemo-agent[dev,s3]"
pytest tests/ -v

# Include semantic search tests
pip install "mnemo-agent[dev,s3,semantic]"
pytest tests/ -v
```

187+ tests across `test_cli.py`, `test_client.py`, `test_ingest.py`, `test_remote.py`, and `test_server.py`. Semantic search tests are auto-skipped when `mnemo[semantic]` is not installed. Ingest tests mock all external API calls. SDK remote tests mock `_get`/`_mcp_call` to avoid live HTTP.

The web UI (`mnemo ui`) is served by the same FastAPI process as `mnemo serve` and is covered by the existing server tests.

---

## Roadmap

- [x] Push/pull sync to S3, R2, and local filesystem remotes
- [x] Write-time conflict detection with overwrite / keep-both / abort prompt
- [x] Full MCP 2024-11-05 protocol — JSON-RPC 2.0 + stdio transport (Claude Desktop / Cursor)
- [x] Web UI dashboard — multi-agent overview, per-agent facts/search/diff/import/export
- [x] Semantic search — `--method semantic|hybrid` via fastembed (ONNX, no PyTorch); hybrid combines TF-IDF + cosine with configurable alpha
- [x] Auto memory from chat logs — `mnemo ingest` extracts facts from Claude.ai/ChatGPT/Cursor/plain exports via Claude API, OpenAI-compatible (Groq, Gemini, Ollama), or offline heuristics
- [x] Python SDK — `MnemoClient` + `AsyncMnemoClient` with local and remote backends; `watch()` file-stream; `pip install mnemo-agent[sdk]`
- [ ] Parquet export for analytics
- [ ] `mnemo audit` — fact provenance trace
- [ ] Snapshot history browser in UI

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
