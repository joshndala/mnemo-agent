"""Tests for MnemoClient and AsyncMnemoClient."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mnemo import AgentDump, AsyncMnemoClient, Fact, MnemoClient
from mnemo.storage import init_agent, latest_dump_path


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_base(tmp_path: Path) -> Path:
    return tmp_path / ".mnemo"


@pytest.fixture
def client(tmp_base: Path) -> MnemoClient:
    return MnemoClient(agent="test-agent", base=tmp_base)


@pytest.fixture
def seeded_client(tmp_base: Path) -> MnemoClient:
    """Client with two facts already stored."""
    c = MnemoClient(agent="test-agent", base=tmp_base)
    c.add("Python", attribute="tool", confidence=0.9)
    c.add("FastAPI", attribute="tool", confidence=0.8)
    return c


# ─── Local backend ────────────────────────────────────────────────────────────


class TestMnemoClientLocal:
    def test_add_returns_fact(self, client: MnemoClient):
        fact = client.add("Python", attribute="tool")
        assert isinstance(fact, Fact)
        assert fact.value == "Python"
        assert fact.attribute == "tool"
        assert fact.entity == "test-agent"

    def test_add_entity_override(self, client: MnemoClient):
        fact = client.add("React", entity="Joshua", attribute="tool")
        assert fact.entity == "Joshua"

    def test_add_with_tags(self, client: MnemoClient):
        fact = client.add("Supabase", attribute="tool", tags=["db", "backend"])
        assert fact.metadata["tags"] == ["db", "backend"]

    def test_add_persists_to_disk(self, client: MnemoClient, tmp_base: Path):
        client.add("Go", attribute="language")
        path = latest_dump_path("test-agent", tmp_base)
        dump = AgentDump.model_validate(json.loads(path.read_text()))
        assert any(f.value == "Go" for f in dump.facts)

    def test_recall_returns_matching_facts(self, seeded_client: MnemoClient):
        results = seeded_client.recall("Python")
        assert len(results) >= 1
        assert any(f.value == "Python" for f in results)

    def test_recall_method_tfidf(self, seeded_client: MnemoClient):
        results = seeded_client.recall("Python", method="tfidf")
        assert isinstance(results, list)

    def test_recall_method_semantic(self, tmp_base: Path):
        pytest.importorskip("fastembed")
        c = MnemoClient(agent="test-agent", base=tmp_base)
        c.add("Python", attribute="language")
        results = c.recall("Python", method="semantic")
        assert isinstance(results, list)

    def test_recall_tag_filter(self, client: MnemoClient):
        client.add("Supabase", attribute="tool", tags=["db"])
        client.add("React", attribute="tool", tags=["frontend"])
        results = client.recall("tool", tag="db")
        assert all("db" in f.metadata.get("tags", []) for f in results)

    def test_search_alias_higher_limit(self, seeded_client: MnemoClient):
        results = seeded_client.search("tool")
        assert isinstance(results, list)

    def test_list_facts_all(self, seeded_client: MnemoClient):
        facts = seeded_client.list_facts()
        assert len(facts) == 2

    def test_list_facts_entity_filter(self, client: MnemoClient):
        client.add("Python", entity="Joshua", attribute="tool")
        client.add("React", entity="Team", attribute="tool")
        facts = client.list_facts(entity="Joshua")
        assert all(f.entity == "Joshua" for f in facts)

    def test_list_facts_attribute_filter(self, client: MnemoClient):
        client.add("Python", attribute="tool")
        client.add("London", attribute="location")
        facts = client.list_facts(attribute="tool")
        assert all(f.attribute == "tool" for f in facts)

    def test_list_facts_tag_filter(self, client: MnemoClient):
        client.add("Supabase", attribute="tool", tags=["db"])
        client.add("React", attribute="tool", tags=["frontend"])
        facts = client.list_facts(tag="db")
        assert len(facts) == 1
        assert facts[0].value == "Supabase"

    def test_get_by_id_prefix(self, client: MnemoClient):
        fact = client.add("TypeScript", attribute="tool")
        prefix = fact.id[:8]
        found = client.get(prefix)
        assert found is not None
        assert found.id == fact.id

    def test_get_missing_returns_none(self, client: MnemoClient):
        assert client.get("00000000") is None

    def test_retract_removes_fact(self, client: MnemoClient):
        fact = client.add("Rust", attribute="language")
        result = client.retract(fact.id[:8])
        assert result is True
        assert client.get(fact.id[:8]) is None

    def test_retract_missing_returns_false(self, client: MnemoClient):
        assert client.retract("00000000") is False

    def test_edit_value(self, client: MnemoClient):
        fact = client.add("Flask", attribute="tool")
        updated = client.edit(fact.id[:8], value="FastAPI")
        assert updated.value == "FastAPI"
        assert updated.id == fact.id

    def test_edit_confidence(self, client: MnemoClient):
        fact = client.add("Rust", attribute="language", confidence=0.5)
        updated = client.edit(fact.id[:8], confidence=0.95)
        assert updated.confidence == pytest.approx(0.95)

    def test_edit_missing_raises(self, client: MnemoClient):
        with pytest.raises(KeyError):
            client.edit("00000000", value="x")

    def test_info_keys(self, seeded_client: MnemoClient):
        info = seeded_client.info()
        assert "agent" in info
        assert info["agent"] == "test-agent"
        assert info["fact_count"] == 2
        assert info["last_updated"] is not None

    def test_info_empty_agent(self, client: MnemoClient):
        info = client.info()
        assert info["fact_count"] == 0
        assert info["last_updated"] is None

    def test_dump_returns_agent_dump(self, seeded_client: MnemoClient):
        dump = seeded_client.dump()
        assert isinstance(dump, AgentDump)
        assert dump.agent == "test-agent"
        assert len(dump.facts) == 2

    def test_auto_init_on_first_call(self, tmp_base: Path):
        """Client should auto-create the agent dir without a prior mnemo init."""
        c = MnemoClient(agent="brand-new", base=tmp_base)
        facts = c.list_facts()
        assert facts == []
        assert (tmp_base / "brand-new" / "dumps" / "latest.json").exists()

    def test_context_manager(self, tmp_base: Path):
        with MnemoClient(agent="ctx-agent", base=tmp_base) as c:
            fact = c.add("Python")
            assert fact.value == "Python"


# ─── Remote backend (mocked) ──────────────────────────────────────────────────


class TestMnemoClientRemote:
    """Tests for remote mode; monkeypatches _get / _mcp_call to avoid real HTTP."""

    def _make_remote(self, tmp_base: Path) -> MnemoClient:
        return MnemoClient(agent="test-agent", base=tmp_base, url="http://localhost:8080")

    def test_recall_calls_search_endpoint(self, tmp_base: Path):
        c = self._make_remote(tmp_base)
        fact_data = {
            "id": "aabbccdd-0000-0000-0000-000000000000",
            "entity": "test-agent",
            "attribute": "tool",
            "value": "Python",
            "source": "tool",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "confidence": 0.9,
            "metadata": {},
        }
        with patch.object(c, "_get", return_value={"results": [fact_data]}) as mock_get:
            results = c.recall("Python", method="tfidf")
        mock_get.assert_called_once_with("/search", q="Python", limit=5, method="tfidf")
        assert len(results) == 1
        assert results[0].value == "Python"

    def test_list_facts_calls_facts_endpoint(self, tmp_base: Path):
        c = self._make_remote(tmp_base)
        with patch.object(c, "_get", return_value={"facts": []}) as mock_get:
            facts = c.list_facts(entity="Joshua")
        mock_get.assert_called_once_with("/facts", entity="Joshua", attribute=None)
        assert facts == []

    def test_add_calls_upsert_tool(self, tmp_base: Path):
        c = self._make_remote(tmp_base)
        # Simulate server returning an id prefix in the response text
        mock_resp = {"content": [{"text": "Stored fact aabbccdd with confidence 0.9"}]}
        fact_data = {
            "id": "aabbccdd-0000-0000-0000-000000000000",
            "entity": "test-agent",
            "attribute": "tool",
            "value": "Python",
            "source": "tool",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "confidence": 1.0,
            "metadata": {},
        }
        with patch.object(c, "_mcp_call", return_value=mock_resp):
            with patch.object(c, "get", return_value=Fact.model_validate(fact_data)):
                fact = c.add("Python", attribute="tool")
        assert fact.value == "Python"

    def test_retract_calls_retract_tool(self, tmp_base: Path):
        c = self._make_remote(tmp_base)
        with patch.object(c, "_mcp_call", return_value={"isError": False}) as mock_call:
            result = c.retract("aabbccdd")
        mock_call.assert_called_once_with("retract_fact", fact_id="aabbccdd")
        assert result is True

    def test_retract_returns_false_on_error(self, tmp_base: Path):
        c = self._make_remote(tmp_base)
        with patch.object(c, "_mcp_call", return_value={"isError": True}):
            result = c.retract("nonexistent")
        assert result is False

    def test_missing_httpx_raises_on_remote_call(self, tmp_base: Path):
        import sys
        c = self._make_remote(tmp_base)
        with patch.dict(sys.modules, {"httpx": None}):
            with pytest.raises(ImportError, match="mnemo\\[sdk\\]"):
                c._http_client()

    def test_dump_remote_reconstructs_agent_dump(self, tmp_base: Path):
        c = self._make_remote(tmp_base)
        fact_data = {
            "id": "aabbccdd-0000-0000-0000-000000000000",
            "entity": "test-agent",
            "attribute": "note",
            "value": "test value",
            "source": "tool",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "confidence": 1.0,
            "metadata": {},
        }
        with patch.object(c, "_get", return_value={"facts": [fact_data]}):
            dump = c.dump()
        assert isinstance(dump, AgentDump)
        assert len(dump.facts) == 1
        assert dump.source == "remote"


# ─── Async client ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestAsyncMnemoClient:
    async def test_async_add(self, tmp_base: Path):
        async with AsyncMnemoClient(agent="async-agent", base=tmp_base) as c:
            fact = await c.add("Python", attribute="tool")
        assert fact.value == "Python"
        assert fact.attribute == "tool"

    async def test_async_recall(self, tmp_base: Path):
        async with AsyncMnemoClient(agent="async-agent", base=tmp_base) as c:
            await c.add("FastAPI", attribute="tool")
            results = await c.recall("FastAPI")
        assert any(f.value == "FastAPI" for f in results)

    async def test_async_list_facts(self, tmp_base: Path):
        async with AsyncMnemoClient(agent="async-agent", base=tmp_base) as c:
            await c.add("Go", attribute="language")
            await c.add("Rust", attribute="language")
            facts = await c.list_facts(attribute="language")
        assert len(facts) == 2

    async def test_async_retract(self, tmp_base: Path):
        async with AsyncMnemoClient(agent="async-agent", base=tmp_base) as c:
            fact = await c.add("Elm", attribute="language")
            ok = await c.retract(fact.id[:8])
            assert ok is True
            gone = await c.get(fact.id[:8])
            assert gone is None

    async def test_async_edit(self, tmp_base: Path):
        async with AsyncMnemoClient(agent="async-agent", base=tmp_base) as c:
            fact = await c.add("Flask", attribute="tool")
            updated = await c.edit(fact.id[:8], value="FastAPI")
        assert updated.value == "FastAPI"

    async def test_async_info(self, tmp_base: Path):
        async with AsyncMnemoClient(agent="async-agent", base=tmp_base) as c:
            await c.add("Svelte", attribute="tool")
            info = await c.info()
        assert info["fact_count"] == 1
        assert "agent" in info

    async def test_async_context_manager_cleanup(self, tmp_base: Path):
        client = AsyncMnemoClient(agent="cleanup-agent", base=tmp_base)
        async with client as c:
            await c.add("test fact")
        # After exit, http clients should be cleaned up (no error = pass)


@pytest.mark.asyncio
class TestAsyncWatch:
    async def test_watch_yields_new_facts(self, tmp_base: Path):
        """Write a fact via sync client mid-watch, assert it's yielded."""
        import asyncio

        sync = MnemoClient(agent="watch-agent", base=tmp_base)
        # pre-seed one fact so the dump file exists
        existing = sync.add("existing", attribute="note")

        async_client = AsyncMnemoClient(agent="watch-agent", base=tmp_base)

        collected: list[Fact] = []

        async def consume():
            async for fact in async_client.watch(poll_interval=0.05):
                collected.append(fact)
                if len(collected) >= 2:
                    break

        async def inject():
            await asyncio.sleep(0.15)
            sync.add("new-fact", attribute="note")

        await asyncio.gather(consume(), inject())

        values = [f.value for f in collected]
        assert "existing" in values
        assert "new-fact" in values

    async def test_watch_raises_for_remote(self, tmp_base: Path):
        c = AsyncMnemoClient(agent="w", base=tmp_base, url="http://localhost:8080")
        with pytest.raises(NotImplementedError, match="local"):
            async for _ in c.watch():
                pass
