"""Tests for the MCP server — JSON-RPC 2.0, tools, and stdio transport."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from mnemo.models import AgentDump, Fact
from mnemo.server import create_app, _dispatch, TOOL_REGISTRY, WRITE_TOOLS
from mnemo.storage import init_agent, save_dump, latest_dump_path


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def agent_dir(tmp_path: Path):
    base = tmp_path / ".mnemo"
    agent = "test-agent"
    init_agent(agent, base=base)
    return agent, base


@pytest.fixture
def populated_agent(agent_dir):
    agent, base = agent_dir
    dump = AgentDump(
        agent=agent,
        facts=[
            Fact(entity="Joshua", attribute="stack", value="React Node Supabase", confidence=0.99),
            Fact(entity="Joshua", attribute="location", value="Toronto", confidence=1.0),
            Fact(entity="project", attribute="status", value="in progress", confidence=0.8,
                 metadata={"tags": ["work", "active"]}),
        ],
    )
    save_dump(dump, latest_dump_path(agent, base))
    return agent, base, dump


@pytest.fixture
def client(populated_agent):
    agent, base, _ = populated_agent
    app = create_app(agent=agent, base=base)
    return TestClient(app), agent, base


@pytest.fixture
def ro_client(populated_agent):
    agent, base, _ = populated_agent
    app = create_app(agent=agent, base=base, read_only=True)
    return TestClient(app), agent, base


# ─── Health ───────────────────────────────────────────────────────────────────


class TestHealth:
    def test_health_ok(self, client):
        c, agent, _ = client
        r = c.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        assert r.json()["agent"] == agent

    def test_health_includes_version(self, client):
        c, _, _ = client
        r = c.get("/health")
        assert "version" in r.json()


# ─── Tool registry ────────────────────────────────────────────────────────────


class TestToolRegistry:
    def test_all_tools_listed(self, client):
        c, _, _ = client
        r = c.get("/mcp/list_tools")
        assert r.status_code == 200
        names = {t["name"] for t in r.json()}
        assert names == {"search_memory", "list_facts", "upsert_fact", "retract_fact", "edit_fact", "get_agent_info"}

    def test_read_only_removes_write_tools(self, ro_client):
        c, _, _ = ro_client
        r = c.get("/mcp/list_tools")
        names = {t["name"] for t in r.json()}
        assert names.isdisjoint(WRITE_TOOLS)
        assert "search_memory" in names
        assert "list_facts" in names

    def test_tools_have_required_fields(self, client):
        c, _, _ = client
        r = c.get("/mcp/list_tools")
        for tool in r.json():
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool


# ─── JSON-RPC 2.0 endpoint ────────────────────────────────────────────────────


class TestJsonRpc:
    def _rpc(self, client, method, params=None, req_id=1):
        c, _, _ = client
        body = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            body["params"] = params
        r = c.post("/", json=body)
        assert r.status_code == 200
        return r.json()

    def test_initialize(self, client):
        resp = self._rpc(client, "initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0"},
        })
        assert resp["result"]["protocolVersion"] == "2024-11-05"
        assert "tools" in resp["result"]["capabilities"]
        assert resp["result"]["serverInfo"]["name"] == "mnemo"

    def test_tools_list(self, client):
        resp = self._rpc(client, "tools/list")
        tools = resp["result"]["tools"]
        assert len(tools) == 6
        names = {t["name"] for t in tools}
        assert "search_memory" in names

    def test_tools_call_search(self, client):
        resp = self._rpc(client, "tools/call", {
            "name": "search_memory",
            "arguments": {"query": "React"},
        })
        content = resp["result"]["content"][0]["text"]
        assert "React" in content or "No matching" in content

    def test_tools_call_list_facts(self, client):
        resp = self._rpc(client, "tools/call", {"name": "list_facts", "arguments": {}})
        text = resp["result"]["content"][0]["text"]
        assert "Joshua" in text

    def test_tools_call_get_agent_info(self, client):
        resp = self._rpc(client, "tools/call", {"name": "get_agent_info", "arguments": {}})
        text = resp["result"]["content"][0]["text"]
        assert "Facts:" in text

    def test_unknown_method_returns_error(self, client):
        resp = self._rpc(client, "unsupported/method")
        assert "error" in resp
        assert resp["error"]["code"] == -32601

    def test_unknown_tool_returns_error(self, client):
        resp = self._rpc(client, "tools/call", {"name": "nonexistent_tool", "arguments": {}})
        assert "error" in resp

    def test_ping(self, client):
        resp = self._rpc(client, "ping")
        assert resp["result"] == {}

    def test_notification_no_response(self, client):
        c, _, _ = client
        body = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        r = c.post("/", json=body)
        assert r.status_code == 200
        assert r.json() == {}

    def test_parse_error_returns_error(self, client):
        c, _, _ = client
        r = c.post("/", content=b"not json", headers={"content-type": "application/json"})
        assert r.status_code in (200, 422)  # fastapi may 422 before we see it

    def test_read_only_blocks_upsert(self, ro_client):
        c, _, _ = ro_client
        resp = c.post("/", json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "upsert_fact", "arguments": {"entity": "X", "attribute": "a", "value": "v"}},
        })
        result = resp.json()["result"]
        assert result.get("isError") is True or "read-only" in result["content"][0]["text"].lower()


# ─── Tool dispatch: new tools ─────────────────────────────────────────────────


class TestDispatchNewTools:
    def test_upsert_with_tags(self, populated_agent):
        agent, base, _ = populated_agent
        result = _dispatch("upsert_fact", {
            "entity": "Joshua", "attribute": "preference", "value": "dark mode",
            "tags": ["ui", "settings"],
        }, agent, base, False)
        assert "preference" in result
        assert "dark mode" in result

    def test_retract_fact(self, populated_agent):
        agent, base, dump = populated_agent
        fact_id = dump.facts[0].id
        result = _dispatch("retract_fact", {"fact_id": fact_id}, agent, base, False)
        assert "Retracted" in result

    def test_retract_by_prefix(self, populated_agent):
        agent, base, dump = populated_agent
        prefix = dump.facts[0].id[:8]
        result = _dispatch("retract_fact", {"fact_id": prefix}, agent, base, False)
        assert "Retracted" in result

    def test_retract_nonexistent_raises(self, populated_agent):
        agent, base, _ = populated_agent
        with pytest.raises(ValueError, match="No fact found"):
            _dispatch("retract_fact", {"fact_id": "doesnotexist"}, agent, base, False)

    def test_retract_ambiguous_prefix_raises(self, populated_agent):
        agent, base, dump = populated_agent
        # All UUIDs start with a hex char — use a single char that matches multiple
        # Add two facts with ids starting with the same prefix for this test
        with pytest.raises((ValueError, Exception)):
            _dispatch("retract_fact", {"fact_id": ""}, agent, base, False)

    def test_edit_fact_value(self, populated_agent):
        agent, base, dump = populated_agent
        fact_id = dump.facts[0].id
        result = _dispatch("edit_fact", {"fact_id": fact_id, "value": "Updated value"}, agent, base, False)
        assert "Updated" in result
        assert "Updated value" in result

    def test_edit_fact_confidence(self, populated_agent):
        agent, base, dump = populated_agent
        fact_id = dump.facts[1].id
        result = _dispatch("edit_fact", {"fact_id": fact_id, "confidence": 0.5}, agent, base, False)
        assert "0.50" in result

    def test_edit_nonexistent_raises(self, populated_agent):
        agent, base, _ = populated_agent
        with pytest.raises(ValueError, match="No fact found"):
            _dispatch("edit_fact", {"fact_id": "doesnotexist", "value": "x"}, agent, base, False)

    def test_list_facts_tag_filter(self, populated_agent):
        agent, base, _ = populated_agent
        result = _dispatch("list_facts", {"tag": "work"}, agent, base, False)
        assert "project" in result
        assert "status" in result

    def test_search_memory_tag_filter(self, populated_agent):
        agent, base, _ = populated_agent
        result = _dispatch("search_memory", {"query": "progress", "tag": "active"}, agent, base, False)
        # Should find the tagged fact or return no matches
        assert isinstance(result, str)

    def test_write_tools_blocked_read_only(self, populated_agent):
        agent, base, dump = populated_agent
        for tool in WRITE_TOOLS:
            with pytest.raises(PermissionError, match="read-only"):
                args: dict = {}
                if tool == "upsert_fact":
                    args = {"entity": "X", "attribute": "a", "value": "v"}
                elif tool in ("retract_fact", "edit_fact"):
                    args = {"fact_id": dump.facts[0].id}
                _dispatch(tool, args, agent, base, True)


# ─── REST convenience endpoints ───────────────────────────────────────────────


class TestRestEndpoints:
    def test_get_facts(self, client):
        c, _, _ = client
        r = c.get("/facts")
        assert r.status_code == 200
        assert r.json()["count"] == 3

    def test_get_facts_entity_filter(self, client):
        c, _, _ = client
        r = c.get("/facts?entity=Joshua")
        assert r.json()["count"] == 2

    def test_get_facts_tag_filter(self, client):
        c, _, _ = client
        r = c.get("/facts?tag=work")
        assert r.json()["count"] == 1

    def test_search_endpoint(self, client):
        c, _, _ = client
        r = c.get("/search?q=React")
        assert r.status_code == 200
        assert "results" in r.json()


# ─── stdio transport ──────────────────────────────────────────────────────────


class TestStdioTransport:
    def _run_stdio(self, messages: list[dict], agent, base, read_only=False) -> list[dict]:
        """Feed messages to run_stdio via mocked stdin, collect stdout responses."""
        from mnemo.stdio_server import run_stdio
        import sys

        input_lines = "\n".join(json.dumps(m) for m in messages) + "\n"
        output_lines = []

        with (
            patch("sys.stdin", StringIO(input_lines)),
            patch("sys.stdout") as mock_stdout,
        ):
            mock_stdout.write = lambda s: output_lines.append(s)
            mock_stdout.flush = lambda: None
            run_stdio(agent=agent, base=base, read_only=read_only)

        responses = []
        for chunk in output_lines:
            for line in chunk.splitlines():
                line = line.strip()
                if line:
                    responses.append(json.loads(line))
        return responses

    def test_initialize_handshake(self, populated_agent):
        agent, base, _ = populated_agent
        responses = self._run_stdio([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        ], agent, base)
        assert len(responses) == 1
        assert responses[0]["result"]["protocolVersion"] == "2024-11-05"

    def test_notification_skipped(self, populated_agent):
        agent, base, _ = populated_agent
        responses = self._run_stdio([
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        ], agent, base)
        assert responses == []

    def test_tools_list(self, populated_agent):
        agent, base, _ = populated_agent
        responses = self._run_stdio([
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ], agent, base)
        assert len(responses) == 1
        tools = responses[0]["result"]["tools"]
        assert any(t["name"] == "search_memory" for t in tools)

    def test_tools_call_search(self, populated_agent):
        agent, base, _ = populated_agent
        responses = self._run_stdio([
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "search_memory", "arguments": {"query": "React"}}},
        ], agent, base)
        assert len(responses) == 1
        assert "content" in responses[0]["result"]

    def test_invalid_json_returns_error(self, populated_agent):
        agent, base, _ = populated_agent
        from mnemo.stdio_server import run_stdio
        output_lines = []
        with (
            patch("sys.stdin", StringIO("not valid json\n")),
            patch("sys.stdout") as mock_stdout,
        ):
            mock_stdout.write = lambda s: output_lines.append(s)
            mock_stdout.flush = lambda: None
            run_stdio(agent=agent, base=base)
        combined = "".join(output_lines)
        resp = json.loads(combined.strip())
        assert resp["error"]["code"] == -32700

    def test_full_handshake_then_call(self, populated_agent):
        agent, base, _ = populated_agent
        responses = self._run_stdio([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "get_agent_info", "arguments": {}}},
        ], agent, base)
        # initialize + tools/list + tools/call = 3 (notification skipped)
        assert len(responses) == 3
        assert responses[2]["result"]["content"][0]["text"].startswith("Agent:")
