"""Tests for remote backends, merge logic, and push/pull CLI commands."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from mnemo.cli import cli
from mnemo.models import AgentDump, Fact
from mnemo.remotes import FileBackend, RemoteBackend, S3Backend, merge_dumps, validate_url
from mnemo.storage import (
    init_agent,
    latest_dump_path,
    load_config,
    load_credentials,
    load_dump,
    save_credentials,
    save_dump,
)


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def tmp_mnemo(tmp_path: Path):
    return tmp_path / ".mnemo"


@pytest.fixture
def initialized_agent(tmp_mnemo: Path):
    agent = "test-agent"
    init_agent(agent, base=tmp_mnemo)
    return agent, tmp_mnemo


@pytest.fixture
def remote_dir(tmp_path: Path):
    return tmp_path / "remote"


def _make_dump(agent: str, facts: list[dict]) -> AgentDump:
    return AgentDump(
        agent=agent,
        facts=[Fact(**f) for f in facts],
    )


def _fact(entity="A", attribute="x", value="v", offset_seconds=0) -> dict:
    ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=offset_seconds)
    return dict(entity=entity, attribute=attribute, value=value, timestamp=ts)


# ─── Unit: merge_dumps ────────────────────────────────────────────────────────


class TestMergeDumps:
    def test_adds_facts_only_in_remote(self):
        local = _make_dump("a", [_fact(value="local-only")])
        remote = _make_dump("a", [_fact(value="remote-only")])

        merged, added, updated = merge_dumps(local, remote)

        assert added == 1
        assert updated == 0
        assert len(merged.facts) == 2
        values = {f.value for f in merged.facts}
        assert "local-only" in values
        assert "remote-only" in values

    def test_keeps_local_facts_not_in_remote(self):
        local = _make_dump("a", [_fact(value="local-only")])
        remote = _make_dump("a", [])

        merged, added, updated = merge_dumps(local, remote)

        assert added == 0
        assert updated == 0
        assert len(merged.facts) == 1
        assert merged.facts[0].value == "local-only"

    def test_newer_remote_wins_on_same_id(self):
        old_fact = Fact(**_fact(value="old", offset_seconds=0))
        new_fact = Fact(id=old_fact.id, **_fact(value="new", offset_seconds=60))

        local = AgentDump(agent="a", facts=[old_fact])
        remote = AgentDump(agent="a", facts=[new_fact])

        merged, added, updated = merge_dumps(local, remote)

        assert added == 0
        assert updated == 1
        assert len(merged.facts) == 1
        assert merged.facts[0].value == "new"

    def test_newer_local_beats_older_remote(self):
        new_fact = Fact(**_fact(value="new", offset_seconds=60))
        old_fact = Fact(id=new_fact.id, **_fact(value="old", offset_seconds=0))

        local = AgentDump(agent="a", facts=[new_fact])
        remote = AgentDump(agent="a", facts=[old_fact])

        merged, added, updated = merge_dumps(local, remote)

        assert updated == 0
        assert merged.facts[0].value == "new"

    def test_merge_both_empty(self):
        local = AgentDump(agent="a", facts=[])
        remote = AgentDump(agent="a", facts=[])

        merged, added, updated = merge_dumps(local, remote)

        assert added == 0
        assert updated == 0
        assert merged.facts == []

    def test_merged_dump_ts_is_refreshed(self):
        before = datetime.now(timezone.utc)
        local = _make_dump("a", [_fact()])
        remote = _make_dump("a", [])
        merged, _, _ = merge_dumps(local, remote)
        assert merged.dump_ts >= before

    def test_no_duplicate_facts_when_same_id(self):
        fact = Fact(**_fact(value="same"))
        local = AgentDump(agent="a", facts=[fact])
        remote = AgentDump(agent="a", facts=[Fact(id=fact.id, **_fact(value="same"))])

        merged, added, updated = merge_dumps(local, remote)

        assert len(merged.facts) == 1


# ─── Unit: validate_url ───────────────────────────────────────────────────────


class TestValidateUrl:
    def test_valid_schemes(self):
        for url in ("file:///tmp/mnemo", "s3://bucket", "r2://bucket"):
            validate_url(url)  # should not raise

    def test_invalid_scheme_raises(self):
        with pytest.raises(ValueError, match="Unsupported remote scheme"):
            validate_url("ftp://host/path")


# ─── Unit: credential storage ─────────────────────────────────────────────────


class TestCredentialStorage:
    def test_save_and_load(self, tmp_path):
        creds_file = tmp_path / "credentials"
        creds = {"aws_access_key_id": "AKIA123", "aws_secret_access_key": "secret", "region": "us-east-1"}
        save_credentials("s3://bucket/prefix", creds, creds_path=creds_file)

        loaded = load_credentials("s3://bucket/prefix", creds_path=creds_file)
        assert loaded == creds

    def test_returns_none_when_url_not_found(self, tmp_path):
        creds_file = tmp_path / "credentials"
        save_credentials("s3://other-bucket", {"aws_access_key_id": "x"}, creds_path=creds_file)
        assert load_credentials("s3://bucket/prefix", creds_path=creds_file) is None

    def test_returns_none_when_file_missing(self, tmp_path):
        assert load_credentials("s3://bucket", creds_path=tmp_path / "no-file") is None

    def test_file_permissions_are_600(self, tmp_path):
        creds_file = tmp_path / "credentials"
        save_credentials("s3://b", {"aws_access_key_id": "x"}, creds_path=creds_file)
        mode = oct(creds_file.stat().st_mode)[-3:]
        assert mode == "600"

    def test_multiple_urls_stored_independently(self, tmp_path):
        creds_file = tmp_path / "credentials"
        save_credentials("s3://bucket-a", {"aws_access_key_id": "A"}, creds_path=creds_file)
        save_credentials("s3://bucket-b", {"aws_access_key_id": "B"}, creds_path=creds_file)
        assert load_credentials("s3://bucket-a", creds_path=creds_file)["aws_access_key_id"] == "A"
        assert load_credentials("s3://bucket-b", creds_path=creds_file)["aws_access_key_id"] == "B"

    def test_overwrite_existing_url(self, tmp_path):
        creds_file = tmp_path / "credentials"
        save_credentials("s3://bucket", {"aws_access_key_id": "old"}, creds_path=creds_file)
        save_credentials("s3://bucket", {"aws_access_key_id": "new"}, creds_path=creds_file)
        assert load_credentials("s3://bucket", creds_path=creds_file)["aws_access_key_id"] == "new"


# ─── Unit: RemoteBackend.from_url ─────────────────────────────────────────────


class TestFromUrl:
    def test_file_scheme(self, tmp_path):
        backend = RemoteBackend.from_url(f"file://{tmp_path}")
        assert isinstance(backend, FileBackend)

    def test_s3_scheme(self):
        backend = RemoteBackend.from_url("s3://my-bucket/mnemo")
        assert isinstance(backend, S3Backend)
        assert backend.bucket == "my-bucket"
        assert backend.prefix == "mnemo"

    def test_s3_no_prefix(self):
        backend = RemoteBackend.from_url("s3://my-bucket")
        assert isinstance(backend, S3Backend)
        assert backend.prefix == ""

    def test_r2_scheme_requires_account_id(self):
        with pytest.raises(ValueError, match="R2_ACCOUNT_ID"):
            RemoteBackend.from_url("r2://my-bucket/mnemo")

    def test_r2_scheme_with_account_id(self, monkeypatch):
        monkeypatch.setenv("R2_ACCOUNT_ID", "abc123")
        backend = RemoteBackend.from_url("r2://my-bucket/mnemo")
        assert isinstance(backend, S3Backend)
        assert "abc123" in backend.endpoint_url

    def test_unsupported_scheme_raises(self):
        with pytest.raises(ValueError, match="Unsupported remote scheme"):
            RemoteBackend.from_url("ftp://example.com/mnemo")


# ─── Unit: FileBackend ────────────────────────────────────────────────────────


class TestFileBackend:
    def test_upload_creates_file(self, tmp_path):
        backend = FileBackend(tmp_path / "remote")
        dump = _make_dump("myagent", [_fact(value="hello")])
        backend.upload(dump, "myagent")
        assert (tmp_path / "remote" / "myagent" / "latest.json").exists()

    def test_download_returns_dump(self, tmp_path):
        backend = FileBackend(tmp_path / "remote")
        dump = _make_dump("myagent", [_fact(value="hello")])
        backend.upload(dump, "myagent")

        result = backend.download("myagent")
        assert result is not None
        assert result.agent == "myagent"
        assert result.facts[0].value == "hello"

    def test_download_returns_none_when_missing(self, tmp_path):
        backend = FileBackend(tmp_path / "remote")
        assert backend.download("nonexistent") is None

    def test_upload_creates_parent_dirs(self, tmp_path):
        backend = FileBackend(tmp_path / "deep" / "nested" / "remote")
        dump = _make_dump("myagent", [])
        backend.upload(dump, "myagent")
        assert (tmp_path / "deep" / "nested" / "remote" / "myagent" / "latest.json").exists()

    def test_download_handles_corrupt_json(self, tmp_path):
        backend = FileBackend(tmp_path / "remote")
        path = tmp_path / "remote" / "myagent" / "latest.json"
        path.parent.mkdir(parents=True)
        path.write_text("not valid json")
        assert backend.download("myagent") is None


# ─── Unit: S3Backend ──────────────────────────────────────────────────────────


class TestS3Backend:
    def _mock_client(self):
        return MagicMock()

    def test_key_with_prefix(self):
        backend = S3Backend(bucket="b", prefix="mnemo")
        assert backend._key("job-prep") == "mnemo/job-prep/latest.json"

    def test_key_without_prefix(self):
        backend = S3Backend(bucket="b", prefix="")
        assert backend._key("job-prep") == "job-prep/latest.json"

    def test_upload_calls_put_object(self):
        backend = S3Backend(bucket="b", prefix="p")
        mock_client = self._mock_client()
        with patch.object(backend, "_client", return_value=mock_client):
            dump = _make_dump("myagent", [_fact(value="hello")])
            backend.upload(dump, "myagent")
            mock_client.put_object.assert_called_once()
            call_kwargs = mock_client.put_object.call_args.kwargs
            assert call_kwargs["Bucket"] == "b"
            assert call_kwargs["Key"] == "p/myagent/latest.json"
            assert call_kwargs["ContentType"] == "application/json"

    def test_download_returns_dump(self):
        backend = S3Backend(bucket="b", prefix="p")
        dump = _make_dump("myagent", [_fact(value="hello")])
        body_mock = MagicMock()
        body_mock.read.return_value = dump.model_dump_json().encode()

        mock_client = self._mock_client()
        mock_client.get_object.return_value = {"Body": body_mock}

        with patch.object(backend, "_client", return_value=mock_client):
            result = backend.download("myagent")
            assert result is not None
            assert result.facts[0].value == "hello"

    def test_download_returns_none_on_no_such_key(self):
        import botocore.exceptions

        backend = S3Backend(bucket="b", prefix="p")
        mock_client = self._mock_client()
        error = botocore.exceptions.ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "Not found"}}, "GetObject"
        )
        mock_client.get_object.side_effect = error

        with patch.object(backend, "_client", return_value=mock_client):
            result = backend.download("myagent")
            assert result is None


# ─── CLI: mnemo remote add / list / remove ────────────────────────────────────


class TestRemoteCLI:
    def test_remote_add(self, runner, initialized_agent, remote_dir):
        agent, base = initialized_agent
        url = f"file://{remote_dir}"
        result = runner.invoke(
            cli, ["remote", "add", "origin", url, "--agent", agent, "--dir", str(base)]
        )
        assert result.exit_code == 0, result.output
        assert "origin" in result.output

        cfg = load_config(agent, base)
        assert cfg.remotes["origin"] == url

    def test_remote_add_invalid_scheme(self, runner, initialized_agent):
        agent, base = initialized_agent
        result = runner.invoke(
            cli, ["remote", "add", "bad", "ftp://host/path", "--agent", agent, "--dir", str(base)]
        )
        assert result.exit_code != 0
        assert "Unsupported" in result.output

    def test_remote_list(self, runner, initialized_agent, remote_dir):
        agent, base = initialized_agent
        url = f"file://{remote_dir}"
        runner.invoke(cli, ["remote", "add", "origin", url, "--agent", agent, "--dir", str(base)])

        result = runner.invoke(cli, ["remote", "list", "--agent", agent, "--dir", str(base)])
        assert result.exit_code == 0
        assert "origin" in result.output
        # Rich may wrap long paths — collapse newlines before checking
        assert str(remote_dir) in result.output.replace("\n", "")

    def test_remote_list_empty(self, runner, initialized_agent):
        agent, base = initialized_agent
        result = runner.invoke(cli, ["remote", "list", "--agent", agent, "--dir", str(base)])
        assert result.exit_code == 0
        assert "No remotes" in result.output

    def test_remote_remove(self, runner, initialized_agent, remote_dir):
        agent, base = initialized_agent
        url = f"file://{remote_dir}"
        runner.invoke(cli, ["remote", "add", "origin", url, "--agent", agent, "--dir", str(base)])
        result = runner.invoke(
            cli, ["remote", "remove", "origin", "--agent", agent, "--dir", str(base)]
        )
        assert result.exit_code == 0
        cfg = load_config(agent, base)
        assert "origin" not in cfg.remotes

    def test_remote_remove_nonexistent(self, runner, initialized_agent):
        agent, base = initialized_agent
        result = runner.invoke(
            cli, ["remote", "remove", "ghost", "--agent", agent, "--dir", str(base)]
        )
        assert result.exit_code != 0
        assert "No remote" in result.output

    def test_remote_add_s3_prompts_for_credentials(self, runner, initialized_agent, tmp_path, monkeypatch):
        agent, base = initialized_agent
        creds_file = tmp_path / "credentials"
        monkeypatch.setattr("mnemo.storage.CREDENTIALS_PATH", creds_file)
        monkeypatch.setattr("mnemo.cli.save_credentials",
                            lambda url, creds: __import__("mnemo.storage", fromlist=["save_credentials"])
                            .save_credentials(url, creds, creds_path=creds_file))
        monkeypatch.setattr("mnemo.cli.load_credentials",
                            lambda url: __import__("mnemo.storage", fromlist=["load_credentials"])
                            .load_credentials(url, creds_path=creds_file))

        # Simulate user entering: access key, secret key, region
        result = runner.invoke(
            cli,
            ["remote", "add", "origin", "s3://my-bucket/mnemo", "--agent", agent, "--dir", str(base)],
            input="AKIAIOSFODNN7\nsecretkey\nus-west-2\n",
        )
        assert result.exit_code == 0, result.output
        assert "Credentials saved" in result.output
        assert "origin" in result.output

    def test_remote_add_s3_blank_access_key_skips_storage(self, runner, initialized_agent, tmp_path, monkeypatch):
        agent, base = initialized_agent
        creds_file = tmp_path / "credentials"
        monkeypatch.setattr("mnemo.cli.load_credentials", lambda url: None)
        monkeypatch.setattr("mnemo.cli.save_credentials", lambda url, creds: None)

        # User leaves access key blank → skip credential storage
        result = runner.invoke(
            cli,
            ["remote", "add", "origin", "s3://my-bucket/mnemo", "--agent", agent, "--dir", str(base)],
            input="\n",
        )
        assert result.exit_code == 0, result.output
        assert "boto3" in result.output or "Skipped" in result.output

    def test_remote_add_no_creds_flag_skips_prompting(self, runner, initialized_agent):
        agent, base = initialized_agent
        result = runner.invoke(
            cli,
            ["remote", "add", "origin", "s3://my-bucket/mnemo", "--no-creds",
             "--agent", agent, "--dir", str(base)],
        )
        assert result.exit_code == 0, result.output
        # No credential prompts in output
        assert "Access Key" not in result.output

    def test_remote_add_reuses_existing_credentials(self, runner, initialized_agent, tmp_path, monkeypatch):
        agent, base = initialized_agent
        existing = {"aws_access_key_id": "EXISTING", "aws_secret_access_key": "s", "region": "us-east-1"}
        monkeypatch.setattr("mnemo.cli.load_credentials", lambda url: existing)

        result = runner.invoke(
            cli,
            ["remote", "add", "origin", "s3://my-bucket/mnemo", "--agent", agent, "--dir", str(base)],
        )
        assert result.exit_code == 0, result.output
        assert "reusing" in result.output
        # Should not prompt for keys
        assert "Access Key" not in result.output


# ─── CLI: mnemo push / pull ───────────────────────────────────────────────────


class TestPushPull:
    def _setup_remote(self, runner, agent, base, remote_dir):
        url = f"file://{remote_dir}"
        runner.invoke(cli, ["remote", "add", "origin", url, "--agent", agent, "--dir", str(base)])
        return url

    def test_push_uploads_to_remote(self, runner, initialized_agent, remote_dir):
        agent, base = initialized_agent
        self._setup_remote(runner, agent, base, remote_dir)

        # Add a fact first
        runner.invoke(cli, ["add", "--fact", "test fact", "--agent", agent, "--dir", str(base)])

        result = runner.invoke(cli, ["push", "--agent", agent, "--dir", str(base)])
        assert result.exit_code == 0, result.output
        assert "Pushed" in result.output
        assert (remote_dir / agent / "latest.json").exists()

    def test_push_no_remote_configured(self, runner, initialized_agent):
        agent, base = initialized_agent
        result = runner.invoke(cli, ["push", "--agent", agent, "--dir", str(base)])
        assert result.exit_code != 0
        assert "No remote" in result.output

    def test_pull_merges_remote_facts(self, runner, initialized_agent, remote_dir):
        agent, base = initialized_agent
        self._setup_remote(runner, agent, base, remote_dir)

        # Put a dump on the remote directly
        remote_dump = _make_dump(agent, [_fact(value="from-remote")])
        remote_path = remote_dir / agent / "latest.json"
        remote_path.parent.mkdir(parents=True)
        remote_path.write_text(remote_dump.model_dump_json())

        result = runner.invoke(cli, ["pull", "--agent", agent, "--dir", str(base)])
        assert result.exit_code == 0, result.output
        assert "new" in result.output

        local = load_dump(latest_dump_path(agent, base))
        assert any(f.value == "from-remote" for f in local.facts)

    def test_pull_dry_run_does_not_write(self, runner, initialized_agent, remote_dir):
        agent, base = initialized_agent
        self._setup_remote(runner, agent, base, remote_dir)

        remote_dump = _make_dump(agent, [_fact(value="remote-fact")])
        remote_path = remote_dir / agent / "latest.json"
        remote_path.parent.mkdir(parents=True)
        remote_path.write_text(remote_dump.model_dump_json())

        before = load_dump(latest_dump_path(agent, base))
        result = runner.invoke(
            cli, ["pull", "--dry-run", "--agent", agent, "--dir", str(base)]
        )
        assert result.exit_code == 0
        assert "dry-run" in result.output.lower()

        after = load_dump(latest_dump_path(agent, base))
        assert len(after.facts) == len(before.facts)

    def test_pull_nothing_on_remote(self, runner, initialized_agent, remote_dir):
        agent, base = initialized_agent
        self._setup_remote(runner, agent, base, remote_dir)

        result = runner.invoke(cli, ["pull", "--agent", agent, "--dir", str(base)])
        assert result.exit_code == 0
        assert "Nothing found" in result.output

    def test_push_then_pull_roundtrip(self, runner, initialized_agent, remote_dir):
        agent, base = initialized_agent
        self._setup_remote(runner, agent, base, remote_dir)

        runner.invoke(cli, ["add", "--fact", "roundtrip fact", "--agent", agent, "--dir", str(base)])
        runner.invoke(cli, ["push", "--agent", agent, "--dir", str(base)])

        # Simulate a second machine by clearing local facts
        empty = AgentDump(agent=agent)
        save_dump(empty, latest_dump_path(agent, base))

        runner.invoke(cli, ["pull", "--agent", agent, "--dir", str(base)])
        restored = load_dump(latest_dump_path(agent, base))
        assert any(f.value == "roundtrip fact" for f in restored.facts)

    def test_pull_named_remote(self, runner, initialized_agent, remote_dir):
        agent, base = initialized_agent
        url = f"file://{remote_dir}"
        runner.invoke(
            cli, ["remote", "add", "backup", url, "--agent", agent, "--dir", str(base)]
        )

        remote_dump = _make_dump(agent, [_fact(value="backup-fact")])
        remote_path = remote_dir / agent / "latest.json"
        remote_path.parent.mkdir(parents=True)
        remote_path.write_text(remote_dump.model_dump_json())

        result = runner.invoke(
            cli, ["pull", "--remote", "backup", "--agent", agent, "--dir", str(base)]
        )
        assert result.exit_code == 0
        local = load_dump(latest_dump_path(agent, base))
        assert any(f.value == "backup-fact" for f in local.facts)
