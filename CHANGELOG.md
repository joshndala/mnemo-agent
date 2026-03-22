# Changelog

All notable changes to **mnemo** will be documented in this file.  
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres to [Semantic Versioning](https://semver.org/).

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
- Mem0 adapter (`pip install mnemo[mem0]`) — dump/load via `mem0ai`
- Letta adapter (`pip install mnemo[letta]`) — dump/load via `letta-client`
- Parquet export optional extra (`pip install mnemo[parquet]`)
- Diff graph PNG optional extra (`pip install mnemo[graph]`)
- `MNEMO_AGENT` and `MNEMO_DIR` environment variable support
- 23 pytest tests covering all commands and core engine

### Notes
- v1 search is TF-IDF keyword only; vector embeddings are planned for v0.2.0
- Mem0 and Letta adapters fail gracefully if the optional package is not installed

[0.1.0]: https://github.com/joshuandala/mnemo-agent/releases/tag/v0.1.0
