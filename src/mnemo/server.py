"""FastAPI MCP server for mnemo — exposes agent memory as MCP tools.

Supports two transports:
  HTTP  — REST convenience endpoints + JSON-RPC 2.0 at POST /
  stdio — run via run_stdio() for Claude Desktop / Cursor integration
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from mnemo import __version__
from mnemo.models import AgentDump, Fact
from mnemo.search import search_dumps
from mnemo.storage import (
    delete_agent,
    dumps_dir,
    init_agent,
    latest_dump_path,
    list_agents,
    list_dump_files,
    load_dump,
    require_agent,
    save_dump,
)


# ─── MCP tool definitions ────────────────────────────────────────────────────

TOOL_REGISTRY: list[dict[str, Any]] = [
    {
        "name": "search_memory",
        "description": "Search agent memory. Supports tfidf (default), semantic, and hybrid modes. Semantic and hybrid require mnemo[semantic].",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "default": 5, "description": "Max results to return"},
                "tag": {"type": "string", "description": "Filter results to facts with this tag (optional)"},
                "mode": {
                    "type": "string",
                    "enum": ["tfidf", "semantic", "hybrid"],
                    "default": "tfidf",
                    "description": "Search mode. 'semantic' and 'hybrid' require mnemo[semantic] (fastembed).",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_facts",
        "description": "Return all facts stored for this agent, with optional filters.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity": {"type": "string", "description": "Filter by entity (optional)"},
                "attribute": {"type": "string", "description": "Filter by attribute (optional)"},
                "tag": {"type": "string", "description": "Filter by tag (optional)"},
            },
        },
    },
    {
        "name": "upsert_fact",
        "description": "Add a new fact to agent memory.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity": {"type": "string", "description": "Entity this fact is about"},
                "attribute": {"type": "string", "description": "Attribute/category (e.g. 'preference', 'decision')"},
                "value": {"type": "string", "description": "The fact content"},
                "confidence": {"type": "number", "default": 1.0, "description": "Confidence score 0–1"},
                "source": {
                    "type": "string",
                    "enum": ["chat", "tool", "manual"],
                    "default": "tool",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of tags (e.g. ['decision', 'auth'])",
                },
            },
            "required": ["entity", "attribute", "value"],
        },
    },
    {
        "name": "retract_fact",
        "description": "Remove a fact from agent memory by its ID or ID prefix.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fact_id": {
                    "type": "string",
                    "description": "Full fact ID or unique prefix (at least 4 chars). Use list_facts to find IDs.",
                },
            },
            "required": ["fact_id"],
        },
    },
    {
        "name": "edit_fact",
        "description": "Edit an existing fact's value, attribute, or confidence score.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fact_id": {"type": "string", "description": "Full fact ID or unique prefix"},
                "value": {"type": "string", "description": "New value (optional)"},
                "attribute": {"type": "string", "description": "New attribute/category (optional)"},
                "confidence": {"type": "number", "description": "New confidence score 0–1 (optional)"},
            },
            "required": ["fact_id"],
        },
    },
    {
        "name": "get_agent_info",
        "description": "Return metadata about the agent memory store (fact count, last updated).",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

WRITE_TOOLS = {"upsert_fact", "retract_fact", "edit_fact"}


# ─── Pydantic schemas for legacy REST endpoints ───────────────────────────────


class MCPTool(BaseModel):
    name: str
    description: str
    inputSchema: dict[str, Any]


class MCPToolCallRequest(BaseModel):
    name: str
    arguments: dict[str, Any] = {}


class MCPToolCallResponse(BaseModel):
    content: list[dict[str, Any]]
    is_error: bool = False


# ─── App factory ─────────────────────────────────────────────────────────────


def create_app(agent: str, base: Path, read_only: bool = False) -> FastAPI:
    app = FastAPI(
        title=f"mnemo MCP — {agent}",
        description="MCP-compatible memory server for mnemo agent memory.",
        version=__version__,
    )

    app.state.agent = agent
    app.state.base = base
    app.state.read_only = read_only

    def _active_tools() -> list[dict[str, Any]]:
        if read_only:
            return [t for t in TOOL_REGISTRY if t["name"] not in WRITE_TOOLS]
        return TOOL_REGISTRY

    # ─── UI ───────────────────────────────────────────────────────────────

    _ui_path = Path(__file__).parent / "static" / "ui.html"

    @app.get("/ui")
    def ui() -> FileResponse:
        return FileResponse(_ui_path, media_type="text/html")

    # ─── Health ───────────────────────────────────────────────────────────

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "agent": agent, "read_only": read_only, "version": __version__}

    # ─── JSON-RPC 2.0 endpoint (MCP HTTP transport) ───────────────────────

    @app.post("/")
    async def jsonrpc(request: Request) -> JSONResponse:
        import json as _json
        try:
            body = await request.json()
        except _json.JSONDecodeError as e:
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {e}"},
            })
        return JSONResponse(_handle_jsonrpc(body, agent, base, read_only, _active_tools))

    # ─── Legacy convenience routes (kept for backwards compatibility) ──────

    @app.get("/mcp/list_tools", response_model=list[MCPTool])
    def list_tools() -> list[MCPTool]:
        return [MCPTool(**t) for t in _active_tools()]

    @app.post("/mcp/call_tool", response_model=MCPToolCallResponse)
    def call_tool(req: MCPToolCallRequest) -> MCPToolCallResponse:
        try:
            result = _dispatch(req.name, req.arguments, agent, base, read_only)
            return MCPToolCallResponse(content=[{"type": "text", "text": result}])
        except PermissionError as e:
            return MCPToolCallResponse(content=[{"type": "text", "text": str(e)}], is_error=True)
        except Exception as e:  # noqa: BLE001
            return MCPToolCallResponse(content=[{"type": "text", "text": f"Error: {e}"}], is_error=True)

    # ─── REST convenience endpoints ───────────────────────────────────────

    @app.get("/facts")
    def get_facts(entity: str | None = None, attribute: str | None = None, tag: str | None = None) -> dict:
        dump = _load_or_empty(agent, base)
        facts = dump.facts
        if entity:
            facts = [f for f in facts if f.entity.lower() == entity.lower()]
        if attribute:
            facts = [f for f in facts if f.attribute.lower() == attribute.lower()]
        if tag:
            facts = [f for f in facts if tag.lower() in [t.lower() for t in f.metadata.get("tags", [])]]
        return {"count": len(facts), "facts": [f.model_dump() for f in facts]}

    @app.get("/search")
    def search(q: str, limit: int = 5, tag: str | None = None, mode: str = "tfidf") -> dict:
        dump = _load_or_empty(agent, base)
        try:
            if mode == "semantic":
                from mnemo.search import semantic_search_dumps
                results = semantic_search_dumps([dump], q, limit=limit)
            elif mode == "hybrid":
                from mnemo.search import hybrid_search_dumps
                results = hybrid_search_dumps([dump], q, limit=limit)
            else:
                results = search_dumps([dump], q, limit=limit)
        except ImportError:
            return JSONResponse({"error": "Semantic search requires: pip install 'mnemo[semantic]'"}, status_code=422)
        if tag:
            results = [r for r in results if tag.lower() in [t.lower() for t in r.fact.metadata.get("tags", [])]]
        return {
            "query": q,
            "mode": mode,
            "results": [{"score": r.score, **r.fact.model_dump()} for r in results],
        }

    return app


# ─── JSON-RPC 2.0 handler ────────────────────────────────────────────────────


def _handle_jsonrpc(
    body: dict,
    agent: str,
    base: Path,
    read_only: bool,
    active_tools_fn,
) -> dict:
    """Handle a single JSON-RPC 2.0 request and return a response dict."""
    req_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params", {})

    def ok(result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def err(code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

    # Notification — no response needed
    if req_id is None and method.startswith("notifications/"):
        return {}

    if method == "initialize":
        return ok({
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mnemo", "version": __version__},
        })

    if method == "tools/list":
        return ok({"tools": active_tools_fn()})

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        try:
            result = _dispatch(tool_name, arguments, agent, base, read_only)
            return ok({"content": [{"type": "text", "text": result}]})
        except PermissionError as e:
            return ok({"content": [{"type": "text", "text": str(e)}], "isError": True})
        except ValueError as e:
            return err(-32601, str(e))
        except Exception as e:  # noqa: BLE001
            return ok({"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True})

    # Ping (used by some MCP clients for keep-alive)
    if method == "ping":
        return ok({})

    return err(-32601, f"Method not found: {method}")


# ─── Tool dispatch ───────────────────────────────────────────────────────────


def _load_or_empty(agent: str, base: Path) -> AgentDump:
    try:
        return load_dump(latest_dump_path(agent, base))
    except (FileNotFoundError, ValueError):
        return AgentDump(agent=agent)


def _dispatch(tool_name: str, args: dict, agent: str, base: Path, read_only: bool) -> str:
    if read_only and tool_name in WRITE_TOOLS:
        raise PermissionError(f"Server is in read-only mode — '{tool_name}' is disabled.")

    dump = _load_or_empty(agent, base)

    if tool_name == "search_memory":
        query = args.get("query", "")
        limit = int(args.get("limit", 5))
        tag_filter = args.get("tag")
        mode = args.get("mode", "tfidf")
        try:
            if mode == "semantic":
                from mnemo.search import semantic_search_dumps
                results = semantic_search_dumps([dump], query, limit=limit)
            elif mode == "hybrid":
                from mnemo.search import hybrid_search_dumps
                results = hybrid_search_dumps([dump], query, limit=limit)
            else:
                results = search_dumps([dump], query, limit=limit)
        except ImportError:
            return "Semantic search requires: pip install 'mnemo[semantic]'"
        if tag_filter:
            results = [r for r in results if tag_filter.lower() in [t.lower() for t in r.fact.metadata.get("tags", [])]]
        if not results:
            return "No matching memories found."
        lines = [f"Found {len(results)} result(s) for '{query}':\n"]
        for i, r in enumerate(results, 1):
            f = r.fact
            tags = f.metadata.get("tags", [])
            tag_str = f"  tags={tags}" if tags else ""
            lines.append(f"{i}. [{r.score:.3f}] {f.entity} · {f.attribute}: {f.value} (conf={f.confidence:.2f}){tag_str}")
        return "\n".join(lines)

    elif tool_name == "list_facts":
        entity_filter = args.get("entity")
        attr_filter = args.get("attribute")
        tag_filter = args.get("tag")
        facts = dump.facts
        if entity_filter:
            facts = [f for f in facts if f.entity.lower() == entity_filter.lower()]
        if attr_filter:
            facts = [f for f in facts if f.attribute.lower() == attr_filter.lower()]
        if tag_filter:
            facts = [f for f in facts if tag_filter.lower() in [t.lower() for t in f.metadata.get("tags", [])]]
        if not facts:
            return "No facts found."
        lines = [f"Agent '{agent}' — {len(facts)} fact(s):\n"]
        for f in facts:
            tags = f.metadata.get("tags", [])
            tag_str = f"  [{', '.join(tags)}]" if tags else ""
            lines.append(f"• {f.id[:8]}  {f.entity} · {f.attribute}: {f.value} [{f.confidence:.2f}]{tag_str}")
        return "\n".join(lines)

    elif tool_name == "upsert_fact":
        from datetime import datetime, timezone
        tags = args.get("tags", [])
        fact = Fact(
            entity=args.get("entity", "user"),
            attribute=args.get("attribute", "note"),
            value=args["value"],
            confidence=float(args.get("confidence", 1.0)),
            source=args.get("source", "tool"),  # type: ignore[arg-type]
            metadata={"tags": tags} if tags else {},
        )
        dump.facts.append(fact)
        dump.dump_ts = datetime.now(timezone.utc)
        save_dump(dump, latest_dump_path(agent, base))
        tag_str = f" (tags: {tags})" if tags else ""
        return f"Fact saved: {fact.id[:8]}  {fact.entity} · {fact.attribute}: {fact.value}{tag_str}"

    elif tool_name == "retract_fact":
        fact_id = args.get("fact_id", "")
        matches = [f for f in dump.facts if f.id == fact_id or f.id.startswith(fact_id)]
        if not matches:
            raise ValueError(f"No fact found with ID or prefix: {fact_id}")
        if len(matches) > 1:
            raise ValueError(f"Ambiguous prefix '{fact_id}' matches {len(matches)} facts. Use a longer prefix.")
        fact = matches[0]
        dump.facts = [f for f in dump.facts if f.id != fact.id]
        save_dump(dump, latest_dump_path(agent, base))
        return f"Retracted: {fact.id[:8]}  {fact.entity} · {fact.attribute}: {fact.value}"

    elif tool_name == "edit_fact":
        fact_id = args.get("fact_id", "")
        matches = [f for f in dump.facts if f.id == fact_id or f.id.startswith(fact_id)]
        if not matches:
            raise ValueError(f"No fact found with ID or prefix: {fact_id}")
        if len(matches) > 1:
            raise ValueError(f"Ambiguous prefix '{fact_id}' matches {len(matches)} facts. Use a longer prefix.")
        fact = matches[0]
        if "value" in args:
            fact.value = args["value"]
        if "attribute" in args:
            fact.attribute = args["attribute"]
        if "confidence" in args:
            fact.confidence = float(args["confidence"])
        save_dump(dump, latest_dump_path(agent, base))
        return f"Updated: {fact.id[:8]}  {fact.entity} · {fact.attribute}: {fact.value} (conf={fact.confidence:.2f})"

    elif tool_name == "get_agent_info":
        return (
            f"Agent: {agent}\n"
            f"Facts: {len(dump.facts)}\n"
            f"Last dump: {dump.dump_ts.isoformat()}\n"
            f"Source: {dump.source}"
        )

    else:
        raise ValueError(f"Unknown tool: {tool_name}")


# ─── Multi-agent app factory ─────────────────────────────────────────────────


def create_multi_app(base: Path, read_only: bool = False) -> FastAPI:
    """Multi-agent FastAPI app — serves the dashboard UI and per-agent REST/RPC API."""
    import json as _json
    import re
    from datetime import datetime, timezone
    from fastapi import HTTPException
    from fastapi.responses import RedirectResponse

    app = FastAPI(title="mnemo dashboard", version=__version__)

    _ui_path = Path(__file__).parent / "static" / "ui.html"

    # ── UI & root ────────────────────────────────────────────────────────

    @app.get("/")
    def root() -> RedirectResponse:
        return RedirectResponse("/ui")

    @app.get("/ui")
    def ui() -> FileResponse:
        return FileResponse(_ui_path, media_type="text/html")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "mode": "multi", "version": __version__, "read_only": read_only}

    # ── Agent list & management ──────────────────────────────────────────

    @app.get("/agents")
    def agents_list() -> dict:
        result = []
        for name in list_agents(base):
            try:
                dump = load_dump(latest_dump_path(name, base))
                tags: set[str] = set()
                for f in dump.facts:
                    for t in f.metadata.get("tags", []):
                        tags.add(t)
                result.append({
                    "name": name,
                    "fact_count": len(dump.facts),
                    "last_updated": dump.dump_ts.isoformat(),
                    "tags": sorted(tags),
                    "dump_count": len(list_dump_files(name, base)),
                })
            except (FileNotFoundError, ValueError):
                result.append({
                    "name": name,
                    "fact_count": 0,
                    "last_updated": None,
                    "tags": [],
                    "dump_count": 0,
                })
        return {"agents": result}

    @app.post("/agents")
    async def agents_create(request: Request) -> dict:
        if read_only:
            raise HTTPException(403, "read-only mode")
        body = await request.json()
        name = (body.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "name is required")
        if not re.match(r"^[a-zA-Z0-9_-]+$", name):
            raise HTTPException(400, "name may only contain letters, numbers, hyphens, underscores")
        adir, is_new = init_agent(name, base=base)
        return {"created": is_new, "name": name, "dir": str(adir)}

    @app.delete("/agents/{agent}")
    def agents_delete(agent: str) -> dict:
        if read_only:
            raise HTTPException(403, "read-only mode")
        try:
            delete_agent(agent, base)
        except FileNotFoundError:
            raise HTTPException(404, f"Agent not found: {agent}")
        return {"deleted": agent}

    # ── Per-agent REST ───────────────────────────────────────────────────

    @app.get("/agents/{agent}/facts")
    def agent_facts(
        agent: str,
        entity: str | None = None,
        attribute: str | None = None,
        tag: str | None = None,
    ) -> dict:
        dump = _load_or_empty(agent, base)
        facts = dump.facts
        if entity:
            facts = [f for f in facts if f.entity.lower() == entity.lower()]
        if attribute:
            facts = [f for f in facts if f.attribute.lower() == attribute.lower()]
        if tag:
            facts = [f for f in facts if tag.lower() in [t.lower() for t in f.metadata.get("tags", [])]]
        return {"count": len(facts), "facts": [f.model_dump() for f in facts]}

    @app.get("/agents/{agent}/search")
    def agent_search(agent: str, q: str, limit: int = 10, tag: str | None = None, mode: str = "tfidf") -> dict:
        dump = _load_or_empty(agent, base)
        try:
            if mode == "semantic":
                from mnemo.search import semantic_search_dumps
                results = semantic_search_dumps([dump], q, limit=limit)
            elif mode == "hybrid":
                from mnemo.search import hybrid_search_dumps
                results = hybrid_search_dumps([dump], q, limit=limit)
            else:
                results = search_dumps([dump], q, limit=limit)
        except ImportError:
            return JSONResponse({"error": "Semantic search requires: pip install 'mnemo[semantic]'"}, status_code=422)
        if tag:
            results = [r for r in results if tag.lower() in [t.lower() for t in r.fact.metadata.get("tags", [])]]
        return {
            "query": q,
            "mode": mode,
            "results": [{"score": r.score, **r.fact.model_dump()} for r in results],
        }

    @app.get("/agents/{agent}/export")
    def agent_export(agent: str) -> FileResponse:
        try:
            path = latest_dump_path(agent, base)
            if not path.exists():
                raise FileNotFoundError
            return FileResponse(path, media_type="application/json", filename=f"{agent}-dump.json")
        except FileNotFoundError:
            raise HTTPException(404, f"No dump found for agent: {agent}")

    @app.post("/agents/{agent}/import")
    async def agent_import(agent: str, request: Request) -> dict:
        if read_only:
            raise HTTPException(403, "read-only mode")
        try:
            body = await request.json()
            incoming = AgentDump.model_validate(body)
        except Exception as e:
            raise HTTPException(400, f"Invalid dump: {e}")
        try:
            require_agent(agent, base)
        except FileNotFoundError:
            raise HTTPException(404, f"Agent not found: {agent}. Initialize it first.")
        dest = latest_dump_path(agent, base)
        try:
            existing = load_dump(dest)
            existing_ids = {f.id for f in existing.facts}
            new_facts = [f for f in incoming.facts if f.id not in existing_ids]
            existing.facts.extend(new_facts)
            existing.dump_ts = datetime.now(timezone.utc)
            save_dump(existing, dest)
            return {"merged": len(new_facts), "total": len(existing.facts)}
        except FileNotFoundError:
            save_dump(incoming, dest)
            return {"merged": len(incoming.facts), "total": len(incoming.facts)}

    # ── Per-agent JSON-RPC 2.0 ───────────────────────────────────────────

    @app.post("/agents/{agent}/rpc")
    async def agent_jsonrpc(agent: str, request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except _json.JSONDecodeError as e:
            return JSONResponse({
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": f"Parse error: {e}"},
            })

        def active_tools() -> list[dict]:
            if read_only:
                return [t for t in TOOL_REGISTRY if t["name"] not in WRITE_TOOLS]
            return TOOL_REGISTRY

        return JSONResponse(_handle_jsonrpc(body, agent, base, read_only, active_tools))

    return app
