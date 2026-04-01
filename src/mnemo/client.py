"""Python SDK for mnemo — MnemoClient and AsyncMnemoClient.

Local mode (url=None): reads/writes ~/.mnemo directly via storage + search modules.
Remote mode (url=...): talks to a running ``mnemo serve`` instance over HTTP.

httpx is a soft dependency (``mnemo[sdk]``); it is lazy-imported only when a
remote method is first called, so instantiation never requires it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from mnemo.models import AgentDump, Fact
from mnemo.storage import (
    init_agent,
    latest_dump_path,
    load_dump,
    save_dump,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _require_httpx():
    try:
        import httpx  # noqa: F401
    except ImportError:
        raise ImportError(
            "Remote MnemoClient requires httpx. Install with: pip install 'mnemo[sdk]'"
        )
    return httpx


def _resolve_base(base: Path | str | None) -> Path:
    if base is None:
        return Path.home() / ".mnemo"
    return Path(base)


def _tag_matches(fact: Fact, tag: str | None) -> bool:
    if tag is None:
        return True
    tags = fact.metadata.get("tags", [])
    return any(t.lower() == tag.lower() for t in tags)


# ─── Sync client ──────────────────────────────────────────────────────────────


class MnemoClient:
    """Synchronous mnemo client.

    Parameters
    ----------
    agent:
        Agent identifier (maps to ``~/.mnemo/<agent>/``).
    base:
        Override the mnemo root directory (default: ``~/.mnemo``).
    url:
        If provided, talk to a ``mnemo serve`` HTTP server instead of reading
        local files directly.
    timeout:
        HTTP request timeout in seconds (remote mode only).
    """

    def __init__(
        self,
        agent: str,
        *,
        base: Path | str | None = None,
        url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._agent = agent
        self._base = _resolve_base(base)
        self._url = url.rstrip("/") if url else None
        self._timeout = timeout
        self._http: Any = None  # httpx.Client, created lazily

    # ── context manager ──────────────────────────────────────────────────────

    def __enter__(self) -> "MnemoClient":
        return self

    def __exit__(self, *_: Any) -> None:
        if self._http is not None:
            self._http.close()

    # ── private helpers ──────────────────────────────────────────────────────

    def _load_dump(self) -> AgentDump:
        """Load the agent's latest dump, auto-initializing if needed."""
        path = latest_dump_path(self._agent, self._base)
        if not path.exists():
            init_agent(self._agent, self._base)
        return load_dump(path)

    def _save_dump(self, dump: AgentDump) -> None:
        path = latest_dump_path(self._agent, self._base)
        save_dump(dump, path)

    def _http_client(self):
        httpx = _require_httpx()
        if self._http is None:
            self._http = httpx.Client(timeout=self._timeout)
        return self._http

    def _get(self, path: str, **params: Any) -> Any:
        """GET request to the remote server; returns parsed JSON."""
        client = self._http_client()
        # map SDK param name "method" → server query param "mode"
        if "method" in params:
            params["mode"] = params.pop("method")
        r = client.get(f"{self._url}{path}", params={k: v for k, v in params.items() if v is not None})
        r.raise_for_status()
        return r.json()

    def _mcp_call(self, tool: str, **args: Any) -> Any:
        """POST to /mcp/call_tool; returns the parsed response dict."""
        client = self._http_client()
        payload = {"name": tool, "arguments": {k: v for k, v in args.items() if v is not None}}
        r = client.post(f"{self._url}/mcp/call_tool", json=payload)
        r.raise_for_status()
        return r.json()

    # ── read methods ─────────────────────────────────────────────────────────

    def recall(
        self,
        query: str,
        *,
        limit: int = 5,
        tag: str | None = None,
        method: str = "tfidf",
    ) -> list[Fact]:
        """Return top-``limit`` facts matching ``query``.

        Parameters
        ----------
        method:
            ``"tfidf"`` (default), ``"semantic"``, or ``"hybrid"``.
            Semantic/hybrid require ``mnemo[semantic]``.
        """
        if self._url:
            data = self._get("/search", q=query, limit=limit, method=method)
            results = [Fact.model_validate(r) for r in data.get("results", [])]
            if tag:
                results = [f for f in results if _tag_matches(f, tag)]
            return results

        # local
        from mnemo.search import hybrid_search_dumps, search_dumps, semantic_search_dumps

        dump = self._load_dump()
        dumps = [dump]
        if method == "semantic":
            results = semantic_search_dumps(dumps, query, limit=limit)
        elif method == "hybrid":
            results = hybrid_search_dumps(dumps, query, limit=limit)
        else:
            results = search_dumps(dumps, query, limit=limit)

        facts = [r.fact for r in results]
        if tag:
            facts = [f for f in facts if _tag_matches(f, tag)]
        return facts

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        tag: str | None = None,
        method: str = "tfidf",
    ) -> list[Fact]:
        """Alias for :meth:`recall` with a higher default ``limit``."""
        return self.recall(query, limit=limit, tag=tag, method=method)

    def list_facts(
        self,
        *,
        entity: str | None = None,
        attribute: str | None = None,
        tag: str | None = None,
    ) -> list[Fact]:
        """Return all facts, optionally filtered by entity, attribute, or tag."""
        if self._url:
            data = self._get("/facts", entity=entity, attribute=attribute)
            facts = [Fact.model_validate(f) for f in data.get("facts", [])]
        else:
            dump = self._load_dump()
            facts = list(dump.facts)

        if entity:
            facts = [f for f in facts if f.entity.lower() == entity.lower()]
        if attribute:
            facts = [f for f in facts if f.attribute.lower() == attribute.lower()]
        if tag:
            facts = [f for f in facts if _tag_matches(f, tag)]
        return facts

    def get(self, fact_id: str) -> Fact | None:
        """Return the fact whose ``id`` starts with ``fact_id``, or ``None``."""
        facts = self.list_facts()
        matches = [f for f in facts if f.id.startswith(fact_id)]
        return matches[0] if matches else None

    # ── write methods ────────────────────────────────────────────────────────

    def add(
        self,
        value: str,
        *,
        entity: str | None = None,
        attribute: str = "note",
        confidence: float = 1.0,
        source: str = "tool",
        tags: list[str] | None = None,
    ) -> Fact:
        """Add a new fact and return it.

        Parameters
        ----------
        entity:
            Defaults to the agent name if not provided.
        """
        ent = entity or self._agent
        metadata: dict[str, Any] = {}
        if tags:
            metadata["tags"] = tags

        if self._url:
            resp = self._mcp_call(
                "upsert_fact",
                entity=ent,
                attribute=attribute,
                value=value,
                confidence=confidence,
                source=source,
                metadata=metadata or None,
            )
            # Extract 8-char id prefix from response text, then fetch via get()
            content = resp.get("content", [])
            text = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
            # Find an 8-hex-char sequence in the response (id prefix returned by server)
            import re
            m = re.search(r"\b([0-9a-f]{8})\b", text)
            if m:
                found = self.get(m.group(1))
                if found:
                    return found
            # Fallback: reconstruct from what we sent
            return Fact(
                entity=ent,
                attribute=attribute,
                value=value,
                confidence=confidence,
                source=source,  # type: ignore[arg-type]
                metadata=metadata,
            )

        # local
        dump = self._load_dump()
        fact = Fact(
            entity=ent,
            attribute=attribute,
            value=value,
            confidence=confidence,
            source=source,  # type: ignore[arg-type]
            metadata=metadata,
        )
        dump.facts.append(fact)
        self._save_dump(dump)
        return fact

    def retract(self, fact_id: str) -> bool:
        """Remove the fact matching ``fact_id`` prefix. Returns ``False`` if not found."""
        if self._url:
            resp = self._mcp_call("retract_fact", fact_id=fact_id)
            return not resp.get("isError", False)

        # local
        dump = self._load_dump()
        original_len = len(dump.facts)
        dump.facts = [f for f in dump.facts if not f.id.startswith(fact_id)]
        if len(dump.facts) == original_len:
            return False
        self._save_dump(dump)
        return True

    def edit(
        self,
        fact_id: str,
        *,
        value: str | None = None,
        attribute: str | None = None,
        confidence: float | None = None,
    ) -> Fact:
        """Edit an existing fact in-place and return the updated fact.

        Raises ``KeyError`` if no fact matches ``fact_id``.
        """
        if self._url:
            self._mcp_call(
                "edit_fact",
                fact_id=fact_id,
                value=value,
                attribute=attribute,
                confidence=confidence,
            )
            updated = self.get(fact_id)
            if updated is None:
                raise KeyError(f"Fact not found: {fact_id}")
            return updated

        # local
        dump = self._load_dump()
        matches = [f for f in dump.facts if f.id.startswith(fact_id)]
        if not matches:
            raise KeyError(f"Fact not found: {fact_id}")
        fact = matches[0]
        if value is not None:
            fact.value = value
        if attribute is not None:
            fact.attribute = attribute
        if confidence is not None:
            fact.confidence = max(0.0, min(1.0, confidence))
        self._save_dump(dump)
        return fact

    # ── meta methods ─────────────────────────────────────────────────────────

    def info(self) -> dict[str, Any]:
        """Return basic metadata about the agent."""
        dump = self.dump()
        last_updated: str | None = None
        if dump.facts:
            ts = max(f.timestamp for f in dump.facts)
            last_updated = ts.isoformat()
        return {
            "agent": self._agent,
            "fact_count": len(dump.facts),
            "last_updated": last_updated,
        }

    def dump(self) -> AgentDump:
        """Return the full ``AgentDump`` for this agent."""
        if self._url:
            data = self._get("/facts")
            facts = [Fact.model_validate(f) for f in data.get("facts", [])]
            return AgentDump(
                agent=self._agent,
                facts=facts,
                dump_ts=datetime.now(timezone.utc),
                source="remote",
            )
        return self._load_dump()


# ─── Async client ─────────────────────────────────────────────────────────────


class AsyncMnemoClient:
    """Async wrapper around :class:`MnemoClient`.

    Local calls delegate via ``asyncio.to_thread()``.
    Remote calls use ``httpx.AsyncClient`` directly.

    Use as an async context manager::

        async with AsyncMnemoClient(agent="job-prep") as client:
            facts = await client.recall("tech stack")
    """

    def __init__(
        self,
        agent: str,
        *,
        base: Path | str | None = None,
        url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._agent = agent
        self._base = _resolve_base(base)
        self._url = url.rstrip("/") if url else None
        self._timeout = timeout
        self._sync = MnemoClient(agent=agent, base=self._base, url=url, timeout=timeout)
        self._async_http: Any = None  # httpx.AsyncClient for remote calls

    # ── context manager ──────────────────────────────────────────────────────

    async def __aenter__(self) -> "AsyncMnemoClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._async_http is not None:
            await self._async_http.aclose()
        if self._sync._http is not None:
            self._sync._http.close()

    # ── remote async helpers ─────────────────────────────────────────────────

    def _async_http_client(self):
        httpx = _require_httpx()
        if self._async_http is None:
            self._async_http = httpx.AsyncClient(timeout=self._timeout)
        return self._async_http

    async def _aget(self, path: str, **params: Any) -> Any:
        client = self._async_http_client()
        if "method" in params:
            params["mode"] = params.pop("method")
        r = await client.get(
            f"{self._url}{path}",
            params={k: v for k, v in params.items() if v is not None},
        )
        r.raise_for_status()
        return r.json()

    async def _amcp_call(self, tool: str, **args: Any) -> Any:
        client = self._async_http_client()
        payload = {"name": tool, "arguments": {k: v for k, v in args.items() if v is not None}}
        r = await client.post(f"{self._url}/mcp/call_tool", json=payload)
        r.raise_for_status()
        return r.json()

    # ── async read methods ───────────────────────────────────────────────────

    async def recall(
        self,
        query: str,
        *,
        limit: int = 5,
        tag: str | None = None,
        method: str = "tfidf",
    ) -> list[Fact]:
        if self._url:
            data = await self._aget("/search", q=query, limit=limit, method=method)
            results = [Fact.model_validate(r) for r in data.get("results", [])]
            if tag:
                results = [f for f in results if _tag_matches(f, tag)]
            return results
        return await asyncio.to_thread(self._sync.recall, query, limit=limit, tag=tag, method=method)

    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
        tag: str | None = None,
        method: str = "tfidf",
    ) -> list[Fact]:
        return await self.recall(query, limit=limit, tag=tag, method=method)

    async def list_facts(
        self,
        *,
        entity: str | None = None,
        attribute: str | None = None,
        tag: str | None = None,
    ) -> list[Fact]:
        if self._url:
            data = await self._aget("/facts", entity=entity, attribute=attribute)
            facts = [Fact.model_validate(f) for f in data.get("facts", [])]
            if entity:
                facts = [f for f in facts if f.entity.lower() == entity.lower()]
            if attribute:
                facts = [f for f in facts if f.attribute.lower() == attribute.lower()]
            if tag:
                facts = [f for f in facts if _tag_matches(f, tag)]
            return facts
        return await asyncio.to_thread(self._sync.list_facts, entity=entity, attribute=attribute, tag=tag)

    async def get(self, fact_id: str) -> Fact | None:
        return await asyncio.to_thread(self._sync.get, fact_id)

    # ── async write methods ──────────────────────────────────────────────────

    async def add(
        self,
        value: str,
        *,
        entity: str | None = None,
        attribute: str = "note",
        confidence: float = 1.0,
        source: str = "tool",
        tags: list[str] | None = None,
    ) -> Fact:
        if self._url:
            ent = entity or self._agent
            metadata: dict[str, Any] = {}
            if tags:
                metadata["tags"] = tags
            resp = await self._amcp_call(
                "upsert_fact",
                entity=ent,
                attribute=attribute,
                value=value,
                confidence=confidence,
                source=source,
                metadata=metadata or None,
            )
            content = resp.get("content", [])
            text = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
            import re
            m = re.search(r"\b([0-9a-f]{8})\b", text)
            if m:
                found = await self.get(m.group(1))
                if found:
                    return found
            return Fact(
                entity=ent,
                attribute=attribute,
                value=value,
                confidence=confidence,
                source=source,  # type: ignore[arg-type]
                metadata=metadata,
            )
        return await asyncio.to_thread(
            self._sync.add, value, entity=entity, attribute=attribute,
            confidence=confidence, source=source, tags=tags,
        )

    async def retract(self, fact_id: str) -> bool:
        if self._url:
            resp = await self._amcp_call("retract_fact", fact_id=fact_id)
            return not resp.get("isError", False)
        return await asyncio.to_thread(self._sync.retract, fact_id)

    async def edit(
        self,
        fact_id: str,
        *,
        value: str | None = None,
        attribute: str | None = None,
        confidence: float | None = None,
    ) -> Fact:
        if self._url:
            await self._amcp_call(
                "edit_fact",
                fact_id=fact_id,
                value=value,
                attribute=attribute,
                confidence=confidence,
            )
            updated = await self.get(fact_id)
            if updated is None:
                raise KeyError(f"Fact not found: {fact_id}")
            return updated
        return await asyncio.to_thread(
            self._sync.edit, fact_id, value=value, attribute=attribute, confidence=confidence
        )

    # ── async meta ───────────────────────────────────────────────────────────

    async def info(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._sync.info)

    async def dump(self) -> AgentDump:
        if self._url:
            data = await self._aget("/facts")
            facts = [Fact.model_validate(f) for f in data.get("facts", [])]
            return AgentDump(
                agent=self._agent,
                facts=facts,
                dump_ts=datetime.now(timezone.utc),
                source="remote",
            )
        return await asyncio.to_thread(self._sync.dump)

    # ── watch ─────────────────────────────────────────────────────────────────

    async def watch(self, *, poll_interval: float = 0.5) -> AsyncIterator[Fact]:
        """Poll ``latest.json`` and yield each newly-seen fact by id.

        Only supported for local clients (``url=None``).

        Parameters
        ----------
        poll_interval:
            Seconds between mtime checks (default: 0.5).
        """
        if self._url:
            raise NotImplementedError("watch() is only supported for local clients")

        path = latest_dump_path(self._agent, self._base)
        seen_ids: set[str] = set()
        last_mtime: float = 0.0

        while True:
            try:
                mtime = path.stat().st_mtime
                if mtime > last_mtime:
                    last_mtime = mtime
                    agent_dump = await asyncio.to_thread(load_dump, path)
                    for fact in agent_dump.facts:
                        if fact.id not in seen_ids:
                            seen_ids.add(fact.id)
                            yield fact
            except FileNotFoundError:
                pass
            await asyncio.sleep(poll_interval)
