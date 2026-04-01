"""Chat log parsers and LLM-powered fact extractors for `mnemo ingest`.

Supported chat formats: Claude.ai JSON, ChatGPT JSON, Cursor, plain text.
Supported extractors:   claude, openai (+ any OpenAI-compatible endpoint), ollama, heuristic.

Install: pip install 'mnemo[ingest]'   (anthropic + openai SDKs)
Ollama and heuristic backends need no extra deps.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any

# ─── Extraction prompt ────────────────────────────────────────────────────────

EXTRACT_SYSTEM_PROMPT = """\
You are a memory extraction agent. Extract up to {limit} important, durable facts \
about the user from the following conversation messages.

Return a JSON array. Each object must have:
  "entity": who the fact is about (use "{entity}" unless clearly someone else)
  "attribute": short snake_case category — prefer: tech_stack, decision, goal, \
preference, skill, location, project, tool, constraint
  "value": concise but complete fact content
  "confidence": 0.0-1.0 — how clearly stated/confirmed the fact is

Focus on: technical decisions, preferences, skills, goals, personal context, projects.
Ignore: ephemeral debugging steps, one-off questions, code snippets, tool output.

Return ONLY a valid JSON array, no markdown fences, no explanation.\
"""

OLLAMA_BASE_URL = "http://localhost:11434/v1"
OLLAMA_DEFAULT_MODEL = "llama3.2"

# ─── Lazy import guards ───────────────────────────────────────────────────────


def _require_anthropic() -> Any:
    try:
        import anthropic  # type: ignore
        return anthropic
    except ImportError:
        raise ImportError(
            "Claude extraction requires the anthropic SDK.\n"
            "Install with: pip install 'mnemo[ingest]'"
        )


def _require_openai() -> Any:
    try:
        import openai  # type: ignore
        return openai
    except ImportError:
        raise ImportError(
            "OpenAI/Ollama extraction requires the openai SDK.\n"
            "Install with: pip install 'mnemo[ingest]'"
        )


# ─── Format parsers ───────────────────────────────────────────────────────────


def detect_format(path: Path) -> str:
    """Detect chat export format. Returns 'claude', 'chatgpt', or 'plain'."""
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return "plain"

    if isinstance(data, dict) and "chat_messages" in data:
        return "claude"

    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict) and "messages" in first and isinstance(first["messages"], dict):
            return "chatgpt"

    return "plain"


def parse_claude_json(path: Path) -> list[str]:
    """Parse Claude.ai JSON export. Returns user message texts."""
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    msgs = data.get("chat_messages", [])
    return [m["text"] for m in msgs if m.get("sender") == "human" and m.get("text")]


def parse_chatgpt_json(path: Path) -> list[str]:
    """Parse ChatGPT conversations.json export. Returns user message texts.

    ChatGPT messages are stored as a DAG keyed by ID. We traverse children
    from root to leaf to reconstruct conversation order.
    """
    raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    conversations = raw if isinstance(raw, list) else [raw]
    texts: list[str] = []

    for conv in conversations:
        messages: dict = conv.get("messages", {})
        if not isinstance(messages, dict):
            continue

        # Find root nodes (nodes with no parent, or parent == None)
        children_of: dict[str, list[str]] = {}
        root_ids: list[str] = []
        for msg_id, msg in messages.items():
            parent = msg.get("parent")
            if parent is None or parent not in messages:
                root_ids.append(msg_id)
            else:
                children_of.setdefault(parent, []).append(msg_id)

        # BFS traversal to collect user messages in order
        queue = list(root_ids)
        while queue:
            mid = queue.pop(0)
            msg = messages.get(mid, {})
            role = msg.get("role") or msg.get("author", {}).get("role")
            if role == "user":
                content = msg.get("content", {})
                parts = content.get("parts", []) if isinstance(content, dict) else []
                text = " ".join(str(p) for p in parts if isinstance(p, str) and p.strip())
                if text:
                    texts.append(text)
            queue.extend(children_of.get(mid, []))

    return texts


def parse_plain_text(path: Path) -> list[str]:
    """Parse plain text chat logs. Splits on Human/User/You: markers."""
    content = path.read_text(encoding="utf-8", errors="replace")
    pattern = re.compile(r"(?:^|\n)(?:Human|User|You)\s*:\s*", re.IGNORECASE)
    parts = pattern.split(content)
    # Strip assistant replies: cut at "Assistant:|Claude:|AI:" marker if present
    reply_pattern = re.compile(r"\n(?:Assistant|Claude|AI)\s*:", re.IGNORECASE)
    results: list[str] = []
    for part in parts:
        chunk = reply_pattern.split(part)[0].strip()
        if chunk:
            results.append(chunk)
    return results


def parse_cursor(path: Path) -> list[str]:
    """Parse Cursor AI chat exports. Tries JSON first, falls back to plain text."""
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return parse_plain_text(path)

    # Cursor may use an array of {role, content} objects
    if isinstance(data, list):
        texts = []
        for item in data:
            if isinstance(item, dict):
                role = item.get("role", "")
                if role in ("user", "human"):
                    content = item.get("content", "") or item.get("text", "")
                    if isinstance(content, str) and content.strip():
                        texts.append(content.strip())
        if texts:
            return texts

    # Unknown JSON shape — fall back to plain text
    return parse_plain_text(path)


def parse_messages(path: Path, file_format: str = "auto") -> list[str]:
    """Parse a chat export file and return user message texts.

    file_format: 'auto' (detect), 'claude', 'chatgpt', 'cursor', 'plain'
    Raises ValueError if the file cannot be parsed.
    """
    fmt = detect_format(path) if file_format == "auto" else file_format
    try:
        if fmt == "claude":
            return parse_claude_json(path)
        if fmt == "chatgpt":
            return parse_chatgpt_json(path)
        if fmt == "cursor":
            return parse_cursor(path)
        return parse_plain_text(path)
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(f"Failed to parse {path.name} as {fmt} format: {exc}") from exc


# ─── Private helpers ─────────────────────────────────────────────────────────


def _join_messages(messages: list[str], max_chars: int = 8000) -> str:
    """Join messages and truncate to max_chars (keeping the most recent)."""
    joined = "\n---\n".join(messages)
    if len(joined) > max_chars:
        joined = joined[-max_chars:]
    return joined


def _parse_json_response(text: str) -> list[dict]:
    """Parse a JSON array from an LLM response, stripping markdown fences."""
    text = text.strip()
    # Strip ```json ... ``` fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [d for d in data if isinstance(d, dict) and "value" in d]


# ─── Extraction backends ─────────────────────────────────────────────────────


def extract_with_claude(
    messages: list[str],
    entity: str,
    limit: int,
    model: str = "claude-haiku-4-5-20251001",
) -> list[dict]:
    """Extract facts using the Anthropic Claude API. Requires mnemo[ingest]."""
    anthropic = _require_anthropic()
    text = _join_messages(messages)
    response = anthropic.Anthropic().messages.create(
        model=model,
        max_tokens=1024,
        system=EXTRACT_SYSTEM_PROMPT.format(entity=entity, limit=limit),
        messages=[{"role": "user", "content": text}],
    )
    return _parse_json_response(response.content[0].text)


def extract_with_openai(
    messages: list[str],
    entity: str,
    limit: int,
    model: str = "gpt-4o-mini",
    base_url: str | None = None,
) -> list[dict]:
    """Extract facts using an OpenAI-compatible API.

    Covers: OpenAI, Groq, Together, LMStudio, Gemini (via base_url override).
    Requires mnemo[ingest].
    """
    openai_mod = _require_openai()
    kwargs: dict[str, Any] = {}
    if base_url:
        kwargs["base_url"] = base_url
    client = openai_mod.OpenAI(**kwargs)
    text = _join_messages(messages)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": EXTRACT_SYSTEM_PROMPT.format(entity=entity, limit=limit)},
            {"role": "user", "content": text},
        ],
        max_tokens=1024,
    )
    return _parse_json_response(response.choices[0].message.content or "")


def extract_with_ollama(
    messages: list[str],
    entity: str,
    limit: int,
    model: str = OLLAMA_DEFAULT_MODEL,
) -> list[dict]:
    """Extract facts using a local Ollama instance (localhost:11434).

    Requires the openai SDK (mnemo[ingest]) and a running Ollama server.
    """
    return extract_with_openai(messages, entity, limit, model=model, base_url=OLLAMA_BASE_URL)


_HEURISTIC_PATTERNS: list[tuple[re.Pattern, str, float]] = [
    (re.compile(r"\bI (?:use|am using|work with)\s+(.+?)(?:\.|,|$)", re.I), "tool", 0.75),
    (re.compile(r"\bI (?:prefer|like|love)\s+(.+?)(?:\.|,|$)", re.I), "preference", 0.70),
    (re.compile(r"\bI decided(?: to)?\s+(.+?)(?:\.|,|$)", re.I), "decision", 0.80),
    (re.compile(r"\bI(?:'m| am) (?:building|working on|creating)\s+(.+?)(?:\.|,|$)", re.I), "project", 0.75),
    (re.compile(r"\bI(?:'m| am) (?:based in|located in|living in)\s+(.+?)(?:\.|,|$)", re.I), "location", 0.85),
    (re.compile(r"\bmy (?:main )?(\w+) is\s+(.+?)(?:\.|,|$)", re.I), "preference", 0.70),
]


def extract_with_heuristic(messages: list[str], entity: str) -> list[dict]:
    """Extract facts using regex heuristics. No deps, fully offline.

    Lower confidence than LLM-based extraction. Returns deduplicated results.
    """
    seen: set[str] = set()
    results: list[dict] = []

    for msg in messages:
        for pattern, attribute, confidence in _HEURISTIC_PATTERNS:
            for match in pattern.finditer(msg):
                # For "my X is Y" pattern, groups are (attribute_name, value)
                if pattern.groups == 2:
                    value = match.group(2).strip()
                else:
                    value = match.group(1).strip()
                value = value.rstrip(".,;!?")
                if not value or value in seen:
                    continue
                seen.add(value)
                results.append({
                    "entity": entity,
                    "attribute": attribute,
                    "value": value,
                    "confidence": confidence,
                })

    return results


# ─── Auto-detection + dispatcher ─────────────────────────────────────────────


def _detect_extractor() -> str:
    """Pick best available extractor based on environment."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "claude"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    try:
        urllib.request.urlopen("http://localhost:11434", timeout=0.5)  # noqa: S310
        return "ollama"
    except Exception:
        pass
    return "heuristic"


def extract_facts(
    messages: list[str],
    entity: str,
    extractor: str = "auto",
    limit: int = 20,
    model: str | None = None,
    base_url: str | None = None,
) -> list[dict]:
    """Extract facts from a list of user message strings.

    extractor: 'auto' | 'claude' | 'openai' | 'ollama' | 'heuristic'
    model:     override the default model for the chosen extractor
    base_url:  override API base URL (openai-compatible endpoints: Groq, Gemini, etc.)

    Raises ImportError if the chosen extractor's SDK is not installed.
    """
    resolved = _detect_extractor() if extractor == "auto" else extractor

    if resolved == "claude":
        return extract_with_claude(messages, entity, limit, model=model or "claude-haiku-4-5-20251001")
    if resolved == "openai":
        return extract_with_openai(messages, entity, limit, model=model or "gpt-4o-mini", base_url=base_url)
    if resolved == "ollama":
        return extract_with_ollama(messages, entity, limit, model=model or OLLAMA_DEFAULT_MODEL)
    return extract_with_heuristic(messages, entity)
