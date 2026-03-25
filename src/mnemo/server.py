"""FastAPI MCP server for mnemo — exposes agent memory as MCP tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from mnemo.models import AgentDump, Fact
from mnemo.search import search_dumps
from mnemo.storage import (
    latest_dump_path,
    list_agents,
    load_dump,
    save_dump,
)


# ─── MCP protocol schemas ────────────────────────────────────────────────────


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
        version="0.1.0",
    )

    # Store config in app state
    app.state.agent = agent
    app.state.base = base
    app.state.read_only = read_only

    # ─── Tool registry ────────────────────────────────────────────────────

    TOOLS: list[MCPTool] = [
        MCPTool(
            name="search_memory",
            description="Search agent memory using TF-IDF keyword matching.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        ),
        MCPTool(
            name="list_facts",
            description="Return all facts stored for this agent.",
            inputSchema={
                "type": "object",
                "properties": {
                    "entity": {
                        "type": "string",
                        "description": "Filter by entity (optional)",
                    },
                    "attribute": {
                        "type": "string",
                        "description": "Filter by attribute (optional)",
                    },
                },
            },
        ),
        MCPTool(
            name="upsert_fact",
            description="Add or update a fact in agent memory.",
            inputSchema={
                "type": "object",
                "properties": {
                    "entity": {"type": "string"},
                    "attribute": {"type": "string"},
                    "value": {"type": "string"},
                    "confidence": {"type": "number", "default": 1.0},
                    "source": {
                        "type": "string",
                        "enum": ["chat", "tool", "manual"],
                        "default": "tool",
                    },
                },
                "required": ["entity", "attribute", "value"],
            },
        ),
        MCPTool(
            name="get_agent_info",
            description="Return metadata about the agent memory store.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]

    if read_only:
        TOOLS = [t for t in TOOLS if t.name != "upsert_fact"]

    # ─── Routes ───────────────────────────────────────────────────────────

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "agent": agent, "read_only": read_only}

    @app.get("/mcp/list_tools", response_model=list[MCPTool])
    def list_tools() -> list[MCPTool]:
        return TOOLS

    @app.post("/mcp/call_tool", response_model=MCPToolCallResponse)
    def call_tool(req: MCPToolCallRequest) -> MCPToolCallResponse:
        try:
            result = _dispatch(req.name, req.arguments, agent, base, read_only)
            return MCPToolCallResponse(content=[{"type": "text", "text": result}])
        except PermissionError as e:
            return MCPToolCallResponse(
                content=[{"type": "text", "text": str(e)}], is_error=True
            )
        except Exception as e:  # noqa: BLE001
            return MCPToolCallResponse(
                content=[{"type": "text", "text": f"Error: {e}"}], is_error=True
            )

    # Convenience REST endpoints
    @app.get("/facts")
    def get_facts(entity: str | None = None, attribute: str | None = None) -> dict:
        dump = _load_or_empty(agent, base)
        facts = dump.facts
        if entity:
            facts = [f for f in facts if f.entity.lower() == entity.lower()]
        if attribute:
            facts = [f for f in facts if f.attribute.lower() == attribute.lower()]
        return {"count": len(facts), "facts": [f.model_dump() for f in facts]}

    @app.get("/search")
    def search(q: str, limit: int = 5) -> dict:
        dump = _load_or_empty(agent, base)
        results = search_dumps([dump], q, limit=limit)
        return {
            "query": q,
            "results": [
                {"score": r.score, **r.fact.model_dump()} for r in results
            ],
        }

    return app


# ─── Tool dispatch ───────────────────────────────────────────────────────────


def _load_or_empty(agent: str, base: Path) -> AgentDump:
    try:
        return load_dump(latest_dump_path(agent, base))
    except (FileNotFoundError, ValueError):
        return AgentDump(agent=agent)


def _dispatch(
    tool_name: str, args: dict, agent: str, base: Path, read_only: bool
) -> str:
    import json as _json

    dump = _load_or_empty(agent, base)

    if tool_name == "search_memory":
        query = args.get("query", "")
        limit = int(args.get("limit", 5))
        results = search_dumps([dump], query, limit=limit)
        if not results:
            return "No matching memories found."
        lines = [f"Found {len(results)} result(s) for '{query}':\n"]
        for i, r in enumerate(results, 1):
            f = r.fact
            lines.append(
                f"{i}. [{r.score:.3f}] {f.entity} · {f.attribute}: {f.value} (conf={f.confidence:.2f})"
            )
        return "\n".join(lines)

    elif tool_name == "list_facts":
        entity_filter = args.get("entity")
        attr_filter = args.get("attribute")
        facts = dump.facts
        if entity_filter:
            facts = [f for f in facts if f.entity.lower() == entity_filter.lower()]
        if attr_filter:
            facts = [f for f in facts if f.attribute.lower() == attr_filter.lower()]
        if not facts:
            return "No facts found."
        lines = [f"Agent '{agent}' — {len(facts)} fact(s):\n"]
        for f in facts:
            lines.append(f"• {f.entity} · {f.attribute}: {f.value} [{f.confidence:.2f}]")
        return "\n".join(lines)

    elif tool_name == "upsert_fact":
        if read_only:
            raise PermissionError("Server is in read-only mode.")
        fact = Fact(
            entity=args.get("entity", "user"),
            attribute=args.get("attribute", "memory"),
            value=args["value"],
            confidence=float(args.get("confidence", 1.0)),
            source=args.get("source", "tool"),  # type: ignore[arg-type]
        )
        from datetime import datetime, timezone
        dump.facts.append(fact)
        dump.dump_ts = datetime.now(timezone.utc)
        save_dump(dump, latest_dump_path(agent, base))
        return f"Fact saved: {fact.entity} · {fact.attribute}: {fact.value}"

    elif tool_name == "get_agent_info":
        return (
            f"Agent: {agent}\n"
            f"Facts: {len(dump.facts)}\n"
            f"Last dump: {dump.dump_ts.isoformat()}\n"
            f"Source: {dump.source}"
        )

    else:
        raise ValueError(f"Unknown tool: {tool_name}")
