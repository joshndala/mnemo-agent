# Changelog

All notable changes to **mnemo** will be documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres to [Semantic Versioning](https://semver.org/).

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

[0.3.0]: https://github.com/joshndala/mnemo-agent/releases/tag/v0.3.0
[0.2.0]: https://github.com/joshndala/mnemo-agent/releases/tag/v0.2.0
[0.1.0]: https://github.com/joshndala/mnemo-agent/releases/tag/v0.1.0
