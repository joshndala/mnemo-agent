"""Letta adapter — calls real API when letta-client is installed, stubs otherwise."""

from __future__ import annotations

from typing import Any

from mnemo.models import AgentDump, Fact


def _require_letta() -> Any:
    try:
        from letta_client import Letta  # type: ignore
        return Letta
    except ImportError:
        raise ImportError(
            "letta adapter requires: pip install letta-client\n"
            "Or: pip install 'mnemo[letta]'"
        )


def dump_from_letta(base_url: str, agent_id: str, agent_name: str) -> AgentDump:
    """Fetch memories from a Letta agent and return a normalized AgentDump."""
    Letta = _require_letta()
    client = Letta(base_url=base_url)

    # Letta: GET /v1/agents/{agent_id}/memory/messages or /core_memory
    try:
        memory_blocks = client.agents.memory.retrieve(agent_id=agent_id)
    except Exception as exc:
        raise RuntimeError(f"Letta API error: {exc}") from exc

    facts: list[Fact] = []

    # Letta returns human/persona/custom memory blocks
    for block in getattr(memory_blocks, "blocks", []):
        label = getattr(block, "label", "memory")
        value = getattr(block, "value", "")
        block_id = str(getattr(block, "id", ""))

        if not value:
            continue

        # Treat each newline-separated line as a separate fact
        for line in str(value).splitlines():
            line = line.strip()
            if not line:
                continue
            facts.append(
                Fact(
                    entity=agent_id,
                    attribute=label,
                    value=line,
                    source="letta",
                    confidence=1.0,
                    metadata={"block_id": block_id, "agent_id": agent_id},
                )
            )

    return AgentDump(agent=agent_name, source="letta", facts=facts)


def load_to_letta(dump: AgentDump, base_url: str, agent_id: str) -> int:
    """Append facts to a Letta agent's core memory. Returns count pushed."""
    Letta = _require_letta()
    client = Letta(base_url=base_url)

    pushed = 0
    for fact in dump.facts:
        try:
            client.agents.core_memory.modify(
                agent_id=agent_id,
                label="human",
                value=fact.to_text(),
                append=True,
            )
            pushed += 1
        except Exception as exc:  # noqa: BLE001
            import click
            click.echo(f"  ⚠  Failed to push fact {fact.id}: {exc}", err=True)

    return pushed
