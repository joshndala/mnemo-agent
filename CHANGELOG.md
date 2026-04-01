# Changelog

All notable changes to **mnemo** will be documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres to [Semantic Versioning](https://semver.org/).

---

## [0.5.0] — 2026-03-31

### Added
- **Semantic search** — `mnemo recall "query" --method semantic|hybrid` via fastembed (ONNX, no PyTorch); `mnemo[semantic]` optional extra
  - `semantic` mode: cosine similarity using `BAAI/bge-small-en-v1.5` (384-dim, ~130MB on first use, cached)
  - `hybrid` mode: `0.7 × semantic + 0.3 × tfidf`, both max-normalized to [0, 1]
  - MCP `search_memory` tool: new `mode` param (`tfidf` | `semantic` | `hybrid`)
  - REST `GET /search?mode=semantic|hybrid` and `GET /agents/{agent}/search?mode=...`
  - Graceful `ImportError` if `fastembed` is not installed — suggests `pip install mnemo-agent[semantic]`
- **`mnemo ingest`** — auto-extract facts from chat exports; `mnemo[ingest]` optional extra adds `anthropic` + `openai` SDKs
  - Supported formats: Claude.ai JSON, ChatGPT `conversations.json`, Cursor, plain text (auto-detected)
  - Extractors: `claude` (`claude-haiku-4-5`), `openai` (`gpt-4o-mini`), `ollama` (local), `heuristic` (regex, zero deps), `auto` (picks best from env)
  - `--extractor-url` covers OpenAI-compatible providers: Groq, Gemini (`generativelanguage.googleapis.com/v1beta/openai/`), LMStudio
  - Always shows a preview table + `[y/N]` prompt before writing; `--dry-run` skips the prompt
  - `--entity` to override the default entity name; `--limit` to cap extracted facts
  - New `"ingest"` value added to `Fact.source` Literal
- **Python SDK** — programmatic access without shelling out; `mnemo[sdk]` optional extra adds `httpx`
  - `MnemoClient(agent, *, base, url, timeout)` — sync client with local (direct file I/O) and remote (`mnemo serve` HTTP) backends
  - `AsyncMnemoClient` — async wrapper; local via `asyncio.to_thread()`, remote via `httpx.AsyncClient`
  - Methods: `add()`, `recall()`, `search()`, `list_facts()`, `get()`, `retract()`, `edit()`, `info()`, `dump()`
  - `watch(poll_interval=0.5)` — async generator polling `latest.json` mtime; yields newly-seen facts by id (local only)
  - Auto-init: first call creates the agent directory if it doesn't exist
  - `MnemoClient`, `AsyncMnemoClient`, `Fact`, `AgentDump` now exported from `mnemo` top-level
  - 41 new tests in `tests/test_client.py` (local, remote mocked, async, watch)

### Notes
- `mnemo[semantic]` model downloads ~130MB on first use to `~/.cache/fastembed/`; subsequent calls are instant
- `mnemo ingest --extractor auto` checks `ANTHROPIC_API_KEY` → `OPENAI_API_KEY` → Ollama HTTP ping → heuristic
- SDK remote mode (`url=`) requires `mnemo[sdk]`; local mode works with no extra deps

---

## [0.4.0] — 2026-03-28

### Added
- **`mnemo ui`** — local web dashboard, opens automatically in the browser at `http://localhost:7742/ui`
- **Multi-agent overview** — default view lists all agents as cards showing fact count, dump count, last updated, and top tags
- **Create agent from UI** — modal with name validation (no CLI required)
- **Delete agent from UI** — confirmation modal before permanently removing an agent and all its facts
- **Per-agent detail view** — all facts in a table with entity, attribute, value, confidence bar, tag pills, relative age
- **Entity/attribute filter chips** — one-click filters above the facts table, no typing required
- **Tag filter** in sidebar (per-agent)
- **Quick search** in top bar — jumps to search view with results inline
- **Import dump** — upload a JSON dump file and merge new facts into the current agent (deduplicates by ID)
- **Export dump** — one-click download of the agent's `latest.json` as `<agent>-dump.json`
- **Add / Edit / Retract** facts from the UI — slide-in panel with confidence slider, source dropdown, tag input; inline retract confirmation
- **Read-only mode** — `mnemo ui --read-only` hides all write actions
- **Deep-link support** — `mnemo ui --agent job-prep` opens directly on that agent (`#/agent/job-prep`)
- **Hash-based routing** — browser back button navigates between agent list and agent detail
- Multi-agent REST API (`GET/POST /agents`, `DELETE /agents/<name>`, `GET /agents/<name>/facts`, `GET /agents/<name>/search`, `GET /agents/<name>/export`, `POST /agents/<name>/import`, `POST /agents/<name>/rpc`)
- `delete_agent()` added to `storage.py`

### Notes
- `mnemo ui` does not require `--agent`; it discovers all agents automatically
- `mnemo serve --agent <name>` is unchanged — still single-agent MCP server for Claude Desktop / Cursor
- UI requires `uvicorn` (`pip install uvicorn` or `pip install mnemo-agent[serve]`)
- Default port is 7742 (override with `--port`)

---

## [0.3.1] — 2026-03-27

### Fixed
- README and CHANGELOG updated to reflect 0.3.0 features (MCP stdio, conflict detection, new tools)

---

## [0.3.0] — 2026-03-26

### Added
- **stdio MCP transport** — `mnemo serve --agent <name> --stdio` lets Claude Desktop, Cursor, and other MCP clients connect by spawning mnemo as a subprocess (no HTTP required)
- **Proper JSON-RPC 2.0** at `POST /` — implements `initialize`, `tools/list`, `tools/call`, `ping`, and notification handling per the MCP 2024-11-05 spec
- **`retract_fact` MCP tool** — remove a fact by ID or prefix from any MCP client
- **`edit_fact` MCP tool** — update a fact's value, attribute, or confidence score from any MCP client
- **Tag support on MCP tools** — `upsert_fact` accepts a `tags` array; `list_facts` and `search_memory` accept a `tag` filter
- **`--stdio` flag on `mnemo serve`** — switches from HTTP to stdio transport; HTTP mode now prints a ready-to-paste Claude Desktop config JSON snippet
- **`/facts?tag=`** and **`/search?tag=`** query params on REST endpoints
- Fact IDs shown in `list_facts` output so MCP clients can retract/edit by ID
- Read-only mode now blocks `retract_fact` and `edit_fact` in addition to `upsert_fact`
- 37 new tests in `tests/test_server.py` covering JSON-RPC 2.0, all 6 tools, REST endpoints, and stdio transport

- **Write-time conflict detection** on `mnemo add` — if an existing fact shares the same `(entity, attribute)` pair, prompts with `[o] overwrite  [k] keep both  [a] abort`; `--force` skips the prompt for non-interactive scripts
- 7 new conflict detection tests added to `tests/test_cli.py::TestAdd`

### Fixed
- `POST /` route catches `JSONDecodeError` and returns a proper JSON-RPC parse error (`-32700`) instead of a 500

### Notes
- Legacy routes `/mcp/list_tools` and `/mcp/call_tool` are kept for backwards compatibility
- Claude Desktop config: add `{"mcpServers": {"mnemo-<agent>": {"command": "mnemo", "args": ["serve", "--agent", "<agent>", "--stdio"]}}}` to `~/.claude/claude_desktop_config.json`

---

## [0.2.0] — 2026-03-24

### Added
- **`mnemo retract <fact-id>`** — remove a fact by ID or unique prefix, with confirmation prompt (`--yes` to skip)
- **`mnemo edit <fact-id>`** — update a fact's `--value`, `--attribute`, or `--confidence` in place
- **`--tag` on `mnemo add`** — attach one or more tags to a fact (`--tag decision --tag auth`); stored in `metadata.tags`
- **`--tag` filter on `mnemo recall` and `mnemo search`** — narrow results to facts with a specific tag
- **`--format plain`** on `mnemo show` — compact single-line output per fact, showing ID prefix, entity, attribute, value, confidence, and tags; useful for piping and scripting
- **`mnemo remote add/list/remove`** — manage named remote URLs (`file://`, `s3://`, `r2://`) per agent
- **`mnemo push`** — upload the agent's latest dump to a named remote
- **`mnemo pull`** — download and merge a remote dump into local storage (timestamp-based merge; `--dry-run` supported)
- **S3 and Cloudflare R2 backends** — `mnemo[s3]` extra adds `boto3`; R2 uses S3-compatible API with `endpoint_url`
- **Interactive credential prompting** — `mnemo remote add` prompts for AWS/R2 access key and secret on first use; credentials stored at `~/.mnemo/credentials` (mode 600); `--no-creds` skips prompting and falls back to the boto3 credential chain
- **`remotes` field in `MnemoConfig`** — per-agent named remote registry persisted to `config.yaml`
- GitHub Actions CI/CD (`publish.yml`) — runs tests on every push, publishes to PyPI on `v*` tags via OIDC trusted publisher (no token in repo)
- 54 new tests in `tests/test_remote.py` covering merge logic, credential storage, file/S3 backends, remote CLI, and push/pull

### Fixed
- MCP server field renamed `input_schema` → `inputSchema` (camelCase required by MCP protocol spec)
- `upsert_fact` now updates `dump_ts` on every write

### Notes
- Package renamed from `mnemo` to `mnemo-agent` on PyPI (`mnemo` was already taken); CLI command `mnemo` is unchanged
- Install: `pip install mnemo-agent` or `pip install mnemo-agent[s3]`

---

## [0.1.0] — 2026-03-21

### Added
- `mnemo init` — scaffold agent directory (`~/.mnemo/<agent>/dumps/`, `config.yaml`, `latest.json`)
- `mnemo add` — append a fact with entity, attribute, value, confidence, source
- `mnemo dump` — export memories to a timestamped JSON dump (local, Mem0, or Letta)
- `mnemo load` — import a dump into local store, Mem0, or Letta (with `--dry-run`)
- `mnemo ls` — rich table listing all agents, fact counts, and last dump timestamp
- `mnemo show` — display dump contents as a rich table or raw JSON (`--format json`)
- `mnemo diff` — set-diff two dumps; optional `--html` Mermaid report and `--graph` PNG
- `mnemo recall` — TF-IDF keyword search across one or all agents
- `mnemo search` — alias for `recall` with a higher default result limit
- `mnemo migrate` — filter dump by `--min-conf` and push to a target store
- `mnemo serve` — FastAPI MCP server exposing `search_memory`, `list_facts`, `upsert_fact`, `get_agent_info`
- Normalized memory schema — `{id, entity, attribute, value, source, timestamp, confidence, metadata}`
- Pydantic v2 models (`Fact`, `AgentDump`, `MnemoConfig`)
- Local-only TF-IDF search engine (stdlib, no ML deps)
- Mem0 adapter (`pip install mnemo-agent[mem0]`) — dump/load via `mem0ai`
- Letta adapter (`pip install mnemo-agent[letta]`) — dump/load via `letta-client`
- Parquet export optional extra (`pip install mnemo-agent[parquet]`)
- Diff graph PNG optional extra (`pip install mnemo-agent[graph]`)
- `MNEMO_AGENT` and `MNEMO_DIR` environment variable support
- 23 pytest tests covering all commands and core engine

### Notes
- v1 search is TF-IDF keyword only; vector embeddings are planned for a future release
- Mem0 and Letta adapters fail gracefully if the optional package is not installed

---

[0.5.0]: https://github.com/joshndala/mnemo-agent/releases/tag/v0.5.0
[0.4.0]: https://github.com/joshndala/mnemo-agent/releases/tag/v0.4.0
[0.3.1]: https://github.com/joshndala/mnemo-agent/releases/tag/v0.3.1
[0.3.0]: https://github.com/joshndala/mnemo-agent/releases/tag/v0.3.0
[0.2.0]: https://github.com/joshndala/mnemo-agent/releases/tag/v0.2.0
[0.1.0]: https://github.com/joshndala/mnemo-agent/releases/tag/v0.1.0
