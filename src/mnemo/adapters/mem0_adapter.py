"""Mem0 adapter — calls real API when mem0ai is installed, stubs otherwise."""

from __future__ import annotations

from typing import Any

from mnemo.models import AgentDump, Fact


def _require_mem0() -> Any:
    try:
        import mem0ai  # type: ignore
        return mem0ai
    except ImportError:
        raise ImportError(
            "mem0 adapter requires: pip install mem0ai\n"
            "Or: pip install 'mnemo[mem0]'"
        )


def dump_from_mem0(api_key: str, user_id: str, agent: str) -> AgentDump:
    """Fetch all memories from Mem0 and return a normalized AgentDump."""
    mem0 = _require_mem0()
    client = mem0.MemoryClient(api_key=api_key)

    raw_memories: list[dict] = client.get_all(user_id=user_id)

    facts: list[Fact] = []
    for mem in raw_memories:
        # Mem0 returns: {id, memory, user_id, metadata, created_at, ...}
        # We normalize to entity=user_id, attribute="memory", value=text
        facts.append(
            Fact(
                id=str(mem.get("id", "")),
                entity=user_id,
                attribute="memory",
                value=str(mem.get("memory", "")),
                source="mem0",
                confidence=float(mem.get("score", 1.0)),
                metadata={
                    k: v
                    for k, v in mem.items()
                    if k not in ("id", "memory", "user_id", "score")
                },
            )
        )

    return AgentDump(agent=agent, source="mem0", facts=facts)


def load_to_mem0(dump: AgentDump, api_key: str, user_id: str) -> int:
    """Push facts from a dump into Mem0. Returns count of facts pushed."""
    mem0 = _require_mem0()
    client = mem0.MemoryClient(api_key=api_key)

    pushed = 0
    for fact in dump.facts:
        try:
            client.add(
                messages=[{"role": "user", "content": fact.to_text()}],
                user_id=user_id,
                metadata={"mnemo_id": fact.id, **fact.metadata},
            )
            pushed += 1
        except Exception as exc:  # noqa: BLE001
            import click
            click.echo(f"  ⚠  Failed to push fact {fact.id}: {exc}", err=True)

    return pushed
