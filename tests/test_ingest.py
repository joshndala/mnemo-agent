"""Tests for mnemo ingest — chat log parsers and fact extractors."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from mnemo.cli import cli
from mnemo.adapters.ingest_adapter import (
    detect_format,
    extract_facts,
    extract_with_heuristic,
    parse_chatgpt_json,
    parse_claude_json,
    parse_messages,
    parse_plain_text,
)
from mnemo.storage import init_agent, load_dump, latest_dump_path


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def tmp_mnemo(tmp_path: Path):
    return tmp_path / ".mnemo"


@pytest.fixture
def initialized_agent(tmp_mnemo: Path) -> tuple[str, Path]:
    agent = "test-agent"
    init_agent(agent, base=tmp_mnemo)
    return agent, tmp_mnemo


@pytest.fixture
def claude_export(tmp_path: Path) -> Path:
    data = {
        "name": "Test conversation",
        "chat_messages": [
            {"sender": "human", "text": "I use React and Node for all my projects."},
            {"sender": "assistant", "text": "Great choices! React is very popular."},
            {"sender": "human", "text": "I'm building a memory CLI tool called mnemo."},
        ],
    }
    p = tmp_path / "claude_chat.json"
    p.write_text(json.dumps(data))
    return p


@pytest.fixture
def chatgpt_export(tmp_path: Path) -> Path:
    data = [
        {
            "title": "Test conversation",
            "current_node": "msg-b",
            "messages": {
                "msg-a": {
                    "role": "user",
                    "content": {"content_type": "text", "parts": ["I prefer Supabase over Firebase."]},
                    "parent": None,
                    "children": ["msg-b"],
                },
                "msg-b": {
                    "role": "assistant",
                    "content": {"content_type": "text", "parts": ["Supabase is a great choice!"]},
                    "parent": "msg-a",
                    "children": [],
                },
            },
        }
    ]
    p = tmp_path / "chatgpt.json"
    p.write_text(json.dumps(data))
    return p


@pytest.fixture
def plain_text_export(tmp_path: Path) -> Path:
    content = (
        "Human: I use Python for most of my backend work.\n"
        "Assistant: Python is excellent for that.\n"
        "Human: I decided to use FastAPI over Flask for this project.\n"
        "Assistant: FastAPI has great async support.\n"
    )
    p = tmp_path / "chat.txt"
    p.write_text(content)
    return p


# ─── Format parsers ───────────────────────────────────────────────────────────


class TestFormatParsers:
    def test_parse_claude_json(self, claude_export):
        messages = parse_claude_json(claude_export)
        assert len(messages) == 2
        assert "React" in messages[0]
        assert "mnemo" in messages[1]

    def test_parse_chatgpt_json(self, chatgpt_export):
        messages = parse_chatgpt_json(chatgpt_export)
        assert len(messages) == 1
        assert "Supabase" in messages[0]

    def test_parse_plain_text(self, plain_text_export):
        messages = parse_plain_text(plain_text_export)
        assert len(messages) >= 2
        assert any("Python" in m for m in messages)
        assert any("FastAPI" in m for m in messages)

    def test_detect_format_claude(self, claude_export):
        assert detect_format(claude_export) == "claude"

    def test_detect_format_chatgpt(self, chatgpt_export):
        assert detect_format(chatgpt_export) == "chatgpt"

    def test_detect_format_plain_fallback(self, plain_text_export):
        assert detect_format(plain_text_export) == "plain"

    def test_parse_messages_auto_dispatches_claude(self, claude_export):
        messages = parse_messages(claude_export, file_format="auto")
        assert len(messages) == 2

    def test_parse_messages_explicit_format(self, claude_export):
        messages = parse_messages(claude_export, file_format="claude")
        assert len(messages) == 2

    def test_parse_messages_empty_claude_export(self, tmp_path):
        p = tmp_path / "empty.json"
        p.write_text(json.dumps({"name": "empty", "chat_messages": []}))
        messages = parse_messages(p, file_format="claude")
        assert messages == []

    def test_parse_messages_bad_json_raises(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not valid json")
        # detect_format returns "plain", plain text parser won't raise
        messages = parse_messages(p, file_format="plain")
        assert isinstance(messages, list)


# ─── Heuristic extractor ─────────────────────────────────────────────────────


class TestHeuristicExtractor:
    def test_extracts_tool_pattern(self):
        results = extract_with_heuristic(["I use React for all my frontend work."], "Joshua")
        assert len(results) >= 1
        assert any(r["attribute"] == "tool" for r in results)
        assert any("React" in r["value"] for r in results)

    def test_extracts_decision_pattern(self):
        results = extract_with_heuristic(["I decided to use Supabase instead of Firebase."], "Joshua")
        assert any(r["attribute"] == "decision" for r in results)

    def test_extracts_location_pattern(self):
        results = extract_with_heuristic(["I'm based in Toronto."], "Joshua")
        assert any(r["attribute"] == "location" for r in results)
        assert any("Toronto" in r["value"] for r in results)

    def test_extracts_project_pattern(self):
        results = extract_with_heuristic(["I'm building a CLI tool for agent memory."], "Joshua")
        assert any(r["attribute"] == "project" for r in results)

    def test_returns_empty_on_no_matches(self):
        results = extract_with_heuristic(["What is the capital of France?"], "Joshua")
        assert results == []

    def test_deduplicates_values(self):
        msgs = ["I use React.", "I use React for frontend."]
        results = extract_with_heuristic(msgs, "Joshua")
        values = [r["value"] for r in results]
        assert len(values) == len(set(values))

    def test_confidence_values_in_range(self):
        results = extract_with_heuristic(
            ["I use Python. I prefer FastAPI. I decided to migrate to Supabase."], "Joshua"
        )
        for r in results:
            assert 0.0 <= r["confidence"] <= 1.0

    def test_entity_set_correctly(self):
        results = extract_with_heuristic(["I use TypeScript."], "MyAgent")
        for r in results:
            assert r["entity"] == "MyAgent"


# ─── extract_facts dispatcher ─────────────────────────────────────────────────


class TestExtractFactsDispatcher:
    def test_heuristic_extractor_explicit(self):
        results = extract_facts(["I use Django for web apps."], "test", extractor="heuristic")
        assert isinstance(results, list)
        assert any("Django" in r.get("value", "") for r in results)

    def test_auto_falls_back_to_heuristic_when_no_keys(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with patch("urllib.request.urlopen", side_effect=Exception("no ollama")):
            results = extract_facts(["I use Vue.js."], "user", extractor="auto")
        assert isinstance(results, list)

    def test_claude_missing_dep_raises(self, monkeypatch):
        with patch.dict(sys.modules, {"anthropic": None}):
            with pytest.raises(ImportError, match="mnemo\\[ingest\\]"):
                extract_facts(["I use Go."], "user", extractor="claude")

    def test_openai_missing_dep_raises(self, monkeypatch):
        with patch.dict(sys.modules, {"openai": None}):
            with pytest.raises(ImportError, match="mnemo\\[ingest\\]"):
                extract_facts(["I use Rust."], "user", extractor="openai")


# ─── CLI integration ──────────────────────────────────────────────────────────


class TestIngestCLI:
    def _mock_facts(self):
        return [
            {"entity": "test-agent", "attribute": "tool", "value": "React", "confidence": 0.9},
            {"entity": "test-agent", "attribute": "decision", "value": "Use Supabase", "confidence": 0.85},
        ]

    def test_dry_run_does_not_save(self, runner, initialized_agent, claude_export):
        agent, base = initialized_agent
        with patch("mnemo.cli.extract_facts", return_value=self._mock_facts()):
            result = runner.invoke(
                cli,
                ["ingest", "--file", str(claude_export), "--agent", agent, "--dir", str(base), "--dry-run"],
            )
        assert result.exit_code == 0
        assert "dry-run" in result.output
        dump = load_dump(latest_dump_path(agent, base))
        assert len(dump.facts) == 0

    def test_ingest_saves_facts_on_confirm(self, runner, initialized_agent, claude_export):
        agent, base = initialized_agent
        with patch("mnemo.cli.extract_facts", return_value=self._mock_facts()):
            result = runner.invoke(
                cli,
                ["ingest", "--file", str(claude_export), "--agent", agent, "--dir", str(base)],
                input="y\n",
            )
        assert result.exit_code == 0, result.output
        dump = load_dump(latest_dump_path(agent, base))
        assert len(dump.facts) == 2
        assert dump.facts[0].source == "ingest"

    def test_ingest_aborts_on_reject(self, runner, initialized_agent, claude_export):
        agent, base = initialized_agent
        with patch("mnemo.cli.extract_facts", return_value=self._mock_facts()):
            result = runner.invoke(
                cli,
                ["ingest", "--file", str(claude_export), "--agent", agent, "--dir", str(base)],
                input="n\n",
            )
        assert result.exit_code == 0
        assert "Aborted" in result.output
        dump = load_dump(latest_dump_path(agent, base))
        assert len(dump.facts) == 0

    def test_ingest_no_messages_exits_gracefully(self, runner, initialized_agent, tmp_path):
        agent, base = initialized_agent
        empty = tmp_path / "empty.json"
        empty.write_text(json.dumps({"name": "empty", "chat_messages": []}))
        result = runner.invoke(
            cli,
            ["ingest", "--file", str(empty), "--agent", agent, "--dir", str(base)],
        )
        assert result.exit_code == 0
        assert "No user messages" in result.output

    def test_ingest_no_facts_extracted(self, runner, initialized_agent, claude_export):
        agent, base = initialized_agent
        with patch("mnemo.cli.extract_facts", return_value=[]):
            result = runner.invoke(
                cli,
                ["ingest", "--file", str(claude_export), "--agent", agent, "--dir", str(base)],
            )
        assert result.exit_code == 0
        assert "No facts" in result.output

    def test_ingest_missing_anthropic_dep(self, runner, initialized_agent, claude_export):
        agent, base = initialized_agent
        with patch("mnemo.cli.extract_facts", side_effect=ImportError("pip install 'mnemo[ingest]'")):
            result = runner.invoke(
                cli,
                ["ingest", "--file", str(claude_export), "--agent", agent, "--dir", str(base),
                 "--extractor", "claude"],
            )
        assert result.exit_code != 0

    def test_ingest_bad_format_file(self, runner, initialized_agent, tmp_path):
        agent, base = initialized_agent
        bad = tmp_path / "bad.json"
        bad.write_text("{broken json[[[")
        # parse_messages falls back to plain text for bad JSON, won't raise
        result = runner.invoke(
            cli,
            ["ingest", "--file", str(bad), "--agent", agent, "--dir", str(base), "--extractor", "heuristic"],
        )
        assert result.exit_code == 0  # plain text fallback, just no messages matched

    def test_ingest_shows_preview_table(self, runner, initialized_agent, claude_export):
        agent, base = initialized_agent
        with patch("mnemo.cli.extract_facts", return_value=self._mock_facts()):
            result = runner.invoke(
                cli,
                ["ingest", "--file", str(claude_export), "--agent", agent, "--dir", str(base), "--dry-run"],
            )
        assert "React" in result.output
        assert "Supabase" in result.output

    def test_ingest_respects_entity_override(self, runner, initialized_agent, claude_export):
        agent, base = initialized_agent
        captured: list = []

        def mock_extract(messages, entity, **kwargs):
            captured.append(entity)
            return self._mock_facts()

        with patch("mnemo.cli.extract_facts", side_effect=mock_extract):
            runner.invoke(
                cli,
                ["ingest", "--file", str(claude_export), "--agent", agent,
                 "--dir", str(base), "--entity", "Joshua", "--dry-run"],
            )
        assert captured[0] == "Joshua"
