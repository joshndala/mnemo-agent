"""Pydantic data models for mnemo."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


class Fact(BaseModel):
    """A single normalized memory fact."""

    id: str = Field(default_factory=_new_id, description="UUID for this fact")
    entity: str = Field(..., description="The entity this fact is about (e.g. 'Joshua')")
    attribute: str = Field(..., description="The attribute/property (e.g. 'tech_stack')")
    value: str = Field(..., description="The value of the attribute")
    source: Literal["chat", "tool", "manual", "mem0", "letta", "import", "ingest"] = Field(
        default="manual", description="Origin of this fact"
    )
    timestamp: datetime = Field(
        default_factory=_now_utc, description="When this fact was recorded"
    )
    confidence: float = Field(
        default=1.0, description="Confidence score 0.0–1.0"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Extra key/value data"
    )

    @field_validator("confidence")
    @classmethod
    def clamp_confidence(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))

    def conf_color(self) -> str:
        """Return rich color string based on confidence."""
        if self.confidence >= 0.8:
            return "green"
        if self.confidence >= 0.5:
            return "yellow"
        return "red"

    def to_text(self) -> str:
        """Human-readable single-line representation for search indexing."""
        return f"{self.entity} {self.attribute} {self.value}"

    model_config = {"populate_by_name": True}


class AgentDump(BaseModel):
    """A full memory dump for an agent."""

    agent: str = Field(..., description="Agent identifier")
    dump_ts: datetime = Field(
        default_factory=_now_utc, description="When this dump was created"
    )
    source: str = Field(default="mnemo", description="Origin system")
    version: str = Field(default="1", description="Schema version")
    facts: list[Fact] = Field(default_factory=list, description="Memory facts")

    @property
    def fact_count(self) -> int:
        return len(self.facts)

    def get_fact_by_id(self, fact_id: str) -> Fact | None:
        return next((f for f in self.facts if f.id == fact_id), None)

    model_config = {"populate_by_name": True}


class MnemoConfig(BaseModel):
    """Per-agent config stored in config.yaml."""

    agent: str
    default_source: str = "manual"
    default_target: str = "local"
    mem0_api_key: str | None = None
    mem0_user_id: str | None = None
    letta_base_url: str | None = "http://localhost:8283"
    letta_agent_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    notes: str = ""
    remotes: dict[str, str] = Field(default_factory=dict, description="Named remote URLs")

    model_config = {"extra": "allow"}
