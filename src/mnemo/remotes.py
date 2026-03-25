"""Remote backend abstraction for mnemo push/pull sync."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from mnemo.models import AgentDump, Fact


# ─── Merge ────────────────────────────────────────────────────────────────────


def merge_dumps(
    local: AgentDump, remote: AgentDump
) -> tuple[AgentDump, int, int]:
    """Merge remote facts into local. Returns (merged, added_count, updated_count).

    Rules:
    - Facts only in remote: added to local.
    - Facts in both: keep whichever has the newer timestamp.
    - Facts only in local: kept as-is.
    """
    local_by_id: dict[str, Fact] = {f.id: f for f in local.facts}
    added = 0
    updated = 0

    for remote_fact in remote.facts:
        if remote_fact.id not in local_by_id:
            local_by_id[remote_fact.id] = remote_fact
            added += 1
        else:
            local_fact = local_by_id[remote_fact.id]
            if remote_fact.timestamp > local_fact.timestamp:
                local_by_id[remote_fact.id] = remote_fact
                updated += 1

    merged = AgentDump(
        agent=local.agent,
        dump_ts=datetime.now(timezone.utc),
        source=local.source,
        version=local.version,
        facts=list(local_by_id.values()),
    )
    return merged, added, updated


# ─── URL validation ───────────────────────────────────────────────────────────

SUPPORTED_SCHEMES = frozenset({"file", "s3", "r2"})


def validate_url(url: str) -> None:
    """Raise ValueError if the URL scheme is not supported."""
    scheme = urlparse(url).scheme.lower()
    if scheme not in SUPPORTED_SCHEMES:
        raise ValueError(
            f"Unsupported remote scheme: '{scheme}'. Supported: file, s3, r2"
        )


# ─── Backend interface ────────────────────────────────────────────────────────


class RemoteBackend(ABC):
    """Abstract base for remote storage backends."""

    @abstractmethod
    def upload(self, dump: AgentDump, agent: str) -> None:
        """Upload a dump to the remote."""

    @abstractmethod
    def download(self, agent: str) -> AgentDump | None:
        """Download the latest dump for an agent. Returns None if not found."""

    @staticmethod
    def from_url(url: str, credentials: dict | None = None) -> "RemoteBackend":
        """Parse a remote URL and return the appropriate backend.

        Supported schemes:
          file:///path/to/dir       Local filesystem (testing, NAS, external drive)
          s3://bucket/prefix        AWS S3
          r2://bucket/prefix        Cloudflare R2

        credentials: dict with aws_access_key_id, aws_secret_access_key, region,
                     and r2_account_id (for R2). Falls back to the boto3 credential
                     chain if not provided.
        """
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        creds = credentials or {}

        if scheme == "file":
            return FileBackend(Path(parsed.path))

        if scheme in ("s3", "r2"):
            bucket = parsed.netloc
            prefix = parsed.path.lstrip("/")
            endpoint_url = None
            if scheme == "r2":
                import os
                account_id = creds.get("r2_account_id") or os.environ.get("R2_ACCOUNT_ID")
                if not account_id:
                    raise ValueError(
                        "R2 remote requires a Cloudflare account ID. "
                        "Run 'mnemo remote add' to configure it, or set R2_ACCOUNT_ID."
                    )
                endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com"
            return S3Backend(
                bucket=bucket,
                prefix=prefix,
                endpoint_url=endpoint_url,
                aws_access_key_id=creds.get("aws_access_key_id") or None,
                aws_secret_access_key=creds.get("aws_secret_access_key") or None,
                region=creds.get("region") or None,
            )

        raise ValueError(
            f"Unsupported remote scheme: '{scheme}'. Supported: file, s3, r2"
        )


# ─── File backend ─────────────────────────────────────────────────────────────


class FileBackend(RemoteBackend):
    """Local filesystem remote — useful for external drives, NAS, or testing."""

    def __init__(self, base_path: Path) -> None:
        self.base_path = base_path

    def _path(self, agent: str) -> Path:
        return self.base_path / agent / "latest.json"

    def upload(self, dump: AgentDump, agent: str) -> None:
        path = self._path(agent)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump.model_dump_json(indent=2))

    def download(self, agent: str) -> AgentDump | None:
        path = self._path(agent)
        if not path.exists():
            return None
        try:
            return AgentDump.model_validate(json.loads(path.read_text()))
        except (json.JSONDecodeError, ValueError):
            return None


# ─── S3 / R2 backend ──────────────────────────────────────────────────────────


class S3Backend(RemoteBackend):
    """AWS S3 or Cloudflare R2 remote backend.

    Requires: pip install "mnemo-agent[s3]"
    Credentials are used in this order:
      1. Explicitly passed (stored in ~/.mnemo/credentials via 'mnemo remote add')
      2. Standard boto3 chain: AWS_ACCESS_KEY_ID env var, ~/.aws/credentials, IAM role
    """

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        endpoint_url: str | None = None,
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
        region: str | None = None,
    ) -> None:
        self.bucket = bucket
        self.prefix = prefix.rstrip("/")
        self.endpoint_url = endpoint_url
        self.aws_access_key_id = aws_access_key_id
        self.aws_secret_access_key = aws_secret_access_key
        self.region = region

    def _client(self):
        try:
            import boto3  # type: ignore
        except ImportError:
            raise ImportError(
                "S3/R2 remote requires boto3: pip install \"mnemo-agent[s3]\""
            )
        kwargs: dict = {}
        if self.endpoint_url:
            kwargs["endpoint_url"] = self.endpoint_url
        if self.aws_access_key_id:
            kwargs["aws_access_key_id"] = self.aws_access_key_id
        if self.aws_secret_access_key:
            kwargs["aws_secret_access_key"] = self.aws_secret_access_key
        if self.region:
            kwargs["region_name"] = self.region
        return boto3.client("s3", **kwargs)

    def _key(self, agent: str) -> str:
        parts = [p for p in [self.prefix, agent, "latest.json"] if p]
        return "/".join(parts)

    def upload(self, dump: AgentDump, agent: str) -> None:
        client = self._client()
        client.put_object(
            Bucket=self.bucket,
            Key=self._key(agent),
            Body=dump.model_dump_json(indent=2).encode(),
            ContentType="application/json",
        )

    def download(self, agent: str) -> AgentDump | None:
        try:
            import botocore.exceptions  # type: ignore
        except ImportError:
            raise ImportError(
                "S3/R2 remote requires boto3: pip install \"mnemo-agent[s3]\""
            )
        client = self._client()
        try:
            response = client.get_object(Bucket=self.bucket, Key=self._key(agent))
            data = json.loads(response["Body"].read().decode())
            return AgentDump.model_validate(data)
        except botocore.exceptions.ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code in ("NoSuchKey", "404", "NoSuchBucket"):
                return None
            raise
