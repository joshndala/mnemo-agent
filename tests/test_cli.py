"""pytest tests for mnemo CLI commands."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError
from click.testing import CliRunner

from mnemo.cli import cli
from mnemo.models import AgentDump, Fact
from mnemo.search import diff_dumps, search_dumps
from mnemo.storage import init_agent, load_dump, save_dump


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def tmp_mnemo(tmp_path: Path):
    """Return a temp directory to use as ~/.mnemo in tests."""
    return tmp_path / ".mnemo"


@pytest.fixture
def sample_dump(tmp_path: Path) -> Path:
    """Write a sample dump to a temp file and return the path."""
    dump = AgentDump(
        agent="test-agent",
        facts=[
            Fact(
                entity="Joshua",
                attribute="tech_stack",
                value="React, Node, Supabase",
                confidence=0.99,
            ),
            Fact(
                entity="Joshua",
                attribute="location",
                value="Toronto",
                confidence=1.0,
            ),
            Fact(
                entity="job-prep",
                attribute="target_role",
                value="Senior Full-Stack Engineer",
                confidence=0.9,
            ),
        ],
    )
    p = tmp_path / "sample.json"
    save_dump(dump, p)
    return p


@pytest.fixture
def initialized_agent(tmp_mnemo: Path) -> tuple[str, Path]:
    agent = "test-agent"
    init_agent(agent, base=tmp_mnemo)
    return agent, tmp_mnemo


# ─── mnemo init ───────────────────────────────────────────────────────────────


class TestInit:
    def test_creates_new_agent(self, runner, tmp_mnemo):
        result = runner.invoke(cli, ["init", "--agent", "my-agent", "--dir", str(tmp_mnemo)])
        assert result.exit_code == 0, result.output
        assert "my-agent" in result.output
        assert (tmp_mnemo / "my-agent" / "dumps").is_dir()
        assert (tmp_mnemo / "my-agent" / "config.yaml").exists()
        assert (tmp_mnemo / "my-agent" / "dumps" / "latest.json").exists()

    def test_reports_already_exists(self, runner, tmp_mnemo):
        runner.invoke(cli, ["init", "--agent", "my-agent", "--dir", str(tmp_mnemo)])
        result = runner.invoke(cli, ["init", "--agent", "my-agent", "--dir", str(tmp_mnemo)])
        assert result.exit_code == 0
        assert "Already exists" in result.output or "refreshed" in result.output.lower()


# ─── mnemo add ────────────────────────────────────────────────────────────────


class TestAdd:
    def test_adds_fact(self, runner, initialized_agent):
        agent, base = initialized_agent
        result = runner.invoke(
            cli,
            [
                "add",
                "--fact", "Joshua is a full-stack developer",
                "--agent", agent,
                "--dir", str(base),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Added" in result.output

        from mnemo.storage import latest_dump_path
        dump = load_dump(latest_dump_path(agent, base))
        assert any("full-stack developer" in f.value for f in dump.facts)

    def test_add_requires_agent(self, runner, tmp_mnemo):
        result = runner.invoke(cli, ["add", "--fact", "test fact", "--dir", str(tmp_mnemo)])
        assert result.exit_code != 0

    def test_add_custom_confidence(self, runner, initialized_agent):
        agent, base = initialized_agent
        result = runner.invoke(
            cli,
            ["add", "--fact", "Low confidence memory", "--agent", agent, "--dir", str(base), "--confidence", "0.3"],
        )
        assert result.exit_code == 0
        from mnemo.storage import latest_dump_path
        dump = load_dump(latest_dump_path(agent, base))
        low = [f for f in dump.facts if f.value == "Low confidence memory"]
        assert len(low) == 1
        assert low[0].confidence == pytest.approx(0.3)


# ─── mnemo ls ─────────────────────────────────────────────────────────────────


class TestLs:
    def test_lists_agents(self, runner, initialized_agent):
        agent, base = initialized_agent
        result = runner.invoke(cli, ["ls", "--dir", str(base)])
        assert result.exit_code == 0
        assert agent in result.output

    def test_empty_dir(self, runner, tmp_mnemo):
        result = runner.invoke(cli, ["ls", "--dir", str(tmp_mnemo)])
        assert result.exit_code == 0
        assert "No agents" in result.output


# ─── mnemo show ───────────────────────────────────────────────────────────────


class TestShow:
    def test_pretty_output(self, runner, sample_dump):
        result = runner.invoke(cli, ["show", "--dump", str(sample_dump)])
        assert result.exit_code == 0
        assert "Joshua" in result.output
        assert "tech_stack" in result.output

    def test_json_output(self, runner, sample_dump):
        result = runner.invoke(cli, ["show", "--dump", str(sample_dump), "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["agent"] == "test-agent"
        assert len(data["facts"]) == 3

    def test_missing_file(self, runner, tmp_path):
        result = runner.invoke(cli, ["show", "--dump", str(tmp_path / "nope.json")])
        assert result.exit_code != 0


# ─── mnemo load ───────────────────────────────────────────────────────────────


class TestLoad:
    def test_dry_run(self, runner, initialized_agent, sample_dump):
        agent, base = initialized_agent
        result = runner.invoke(
            cli,
            ["load", "--file", str(sample_dump), "--agent", agent, "--dir", str(base), "--dry-run"],
        )
        assert result.exit_code == 0
        assert "dry-run" in result.output.lower()

    def test_loads_locally(self, runner, initialized_agent, sample_dump):
        agent, base = initialized_agent
        result = runner.invoke(
            cli,
            ["load", "--file", str(sample_dump), "--agent", agent, "--dir", str(base)],
        )
        assert result.exit_code == 0
        from mnemo.storage import latest_dump_path
        dump = load_dump(latest_dump_path(agent, base))
        assert any(f.entity == "Joshua" for f in dump.facts)


# ─── mnemo dump ───────────────────────────────────────────────────────────────


class TestDump:
    def test_local_dump(self, runner, initialized_agent, sample_dump):
        agent, base = initialized_agent
        # First load some data
        runner.invoke(cli, ["load", "--file", str(sample_dump), "--agent", agent, "--dir", str(base)])
        # Now dump it
        out = base / "out.json"
        result = runner.invoke(
            cli,
            ["dump", "--agent", agent, "--dir", str(base), "--out", str(out)],
        )
        assert result.exit_code == 0
        assert out.exists()


# ─── mnemo diff ───────────────────────────────────────────────────────────────


class TestDiff:
    def test_diff_two_dumps(self, runner, tmp_path):
        dump_a = AgentDump(
            agent="a",
            facts=[
                Fact(entity="Joshua", attribute="city", value="Toronto", confidence=1.0),
                Fact(entity="Joshua", attribute="lang", value="Python", confidence=0.9),
            ],
        )
        dump_b = AgentDump(
            agent="b",
            facts=[
                Fact(entity="Joshua", attribute="city", value="Toronto", confidence=1.0),
                Fact(entity="Joshua", attribute="lang", value="TypeScript", confidence=0.8),
            ],
        )
        p_a = tmp_path / "a.json"
        p_b = tmp_path / "b.json"
        save_dump(dump_a, p_a)
        save_dump(dump_b, p_b)

        result = runner.invoke(cli, ["diff", str(p_a), str(p_b)])
        assert result.exit_code == 0
        assert "added" in result.output.lower() or "removed" in result.output.lower()


# ─── mnemo recall / search ────────────────────────────────────────────────────


class TestRecall:
    def test_finds_matching_fact(self, runner, initialized_agent, sample_dump):
        agent, base = initialized_agent
        runner.invoke(cli, ["load", "--file", str(sample_dump), "--agent", agent, "--dir", str(base)])
        result = runner.invoke(
            cli,
            ["recall", "tech stack React", "--agent", agent, "--dir", str(base)],
        )
        assert result.exit_code == 0
        assert "Joshua" in result.output or "No matches" in result.output

    def test_no_results(self, runner, initialized_agent):
        agent, base = initialized_agent
        result = runner.invoke(
            cli,
            ["recall", "xyzzy never matches anything", "--agent", agent, "--dir", str(base)],
        )
        assert result.exit_code == 0
        assert "No matches" in result.output


# ─── Unit: search engine ──────────────────────────────────────────────────────


class TestSearchEngine:
    def test_returns_relevant_facts(self):
        dump = AgentDump(
            agent="test",
            facts=[
                Fact(entity="Joshua", attribute="stack", value="React Node Supabase", confidence=1.0),
                Fact(entity="Joshua", attribute="hobby", value="hiking mountains", confidence=0.8),
            ],
        )
        results = search_dumps([dump], "React Supabase")
        assert len(results) > 0
        assert results[0].fact.attribute == "stack"

    def test_empty_dump(self):
        dump = AgentDump(agent="empty", facts=[])
        results = search_dumps([dump], "anything")
        assert results == []


# ─── Unit: diff engine ────────────────────────────────────────────────────────


class TestDiffEngine:
    def test_detects_added_removed(self):
        da = AgentDump(
            agent="a",
            facts=[Fact(entity="X", attribute="a", value="old", confidence=1.0)],
        )
        db = AgentDump(
            agent="b",
            facts=[Fact(entity="X", attribute="a", value="new", confidence=1.0)],
        )
        added, removed, common = diff_dumps(da, db)
        assert len(added) == 1
        assert len(removed) == 1
        assert len(common) == 0

    def test_unchanged_facts(self):
        fact = Fact(entity="X", attribute="a", value="same", confidence=1.0)
        da = AgentDump(agent="a", facts=[fact])
        db = AgentDump(agent="b", facts=[fact.model_copy()])
        added, removed, common = diff_dumps(da, db)
        assert len(common) == 1
        assert len(added) == 0
        assert len(removed) == 0


# ─── Unit: model validation ───────────────────────────────────────────────────


class TestModels:
    def test_fact_confidence_clamp(self):
        """Values outside [0, 1] are clamped by the field_validator."""
        f_high = Fact(entity="X", attribute="a", value="v", confidence=1.5)
        assert f_high.confidence == 1.0
        f_low = Fact(entity="X", attribute="a", value="v", confidence=-0.5)
        assert f_low.confidence == 0.0

    def test_fact_to_text(self):
        f = Fact(entity="Joshua", attribute="stack", value="React")
        assert "Joshua" in f.to_text()
        assert "React" in f.to_text()

    def test_dump_roundtrip(self, tmp_path):
        dump = AgentDump(
            agent="roundtrip",
            facts=[Fact(entity="E", attribute="A", value="V", confidence=0.7)],
        )
        p = tmp_path / "rt.json"
        save_dump(dump, p)
        loaded = load_dump(p)
        assert loaded.agent == "roundtrip"
        assert len(loaded.facts) == 1
        assert loaded.facts[0].confidence == pytest.approx(0.7)
