"""Local filesystem storage layer for mnemo."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

import yaml

from mnemo.models import AgentDump, MnemoConfig

DEFAULT_MNEMO_DIR = Path.home() / ".mnemo"
CREDENTIALS_PATH = DEFAULT_MNEMO_DIR / "credentials"


# ─── Credentials I/O ─────────────────────────────────────────────────────────


def load_credentials(
    url: str, creds_path: Path = CREDENTIALS_PATH
) -> dict | None:
    """Return stored credentials for a remote URL, or None if not found."""
    if not creds_path.exists():
        return None
    data = yaml.safe_load(creds_path.read_text()) or {}
    return data.get(url) or None


def save_credentials(
    url: str, creds: dict, creds_path: Path = CREDENTIALS_PATH
) -> None:
    """Save credentials for a remote URL and restrict file permissions to 600."""
    data: dict = {}
    if creds_path.exists():
        data = yaml.safe_load(creds_path.read_text()) or {}
    data[url] = creds
    creds_path.parent.mkdir(parents=True, exist_ok=True)
    creds_path.write_text(yaml.dump(data, default_flow_style=False))
    creds_path.chmod(0o600)


# ─── Directory helpers ────────────────────────────────────────────────────────


def agent_dir(agent: str, base: Path = DEFAULT_MNEMO_DIR) -> Path:
    return base / agent


def dumps_dir(agent: str, base: Path = DEFAULT_MNEMO_DIR) -> Path:
    return agent_dir(agent, base) / "dumps"


def config_path(agent: str, base: Path = DEFAULT_MNEMO_DIR) -> Path:
    return agent_dir(agent, base) / "config.yaml"


def latest_dump_path(agent: str, base: Path = DEFAULT_MNEMO_DIR) -> Path:
    return dumps_dir(agent, base) / "latest.json"


# ─── Init / scaffold ──────────────────────────────────────────────────────────


def init_agent(
    agent: str,
    base: Path = DEFAULT_MNEMO_DIR,
    overwrite: bool = False,
) -> tuple[Path, bool]:
    """Create the agent directory tree and starter files.

    Returns (agent_dir, was_created_fresh).
    """
    adir = agent_dir(agent, base)
    ddir = dumps_dir(agent, base)
    created_fresh = not adir.exists()

    ddir.mkdir(parents=True, exist_ok=True)

    cfg_file = config_path(agent, base)
    if not cfg_file.exists() or overwrite:
        cfg = MnemoConfig(agent=agent)
        cfg_file.write_text(yaml.dump(cfg.model_dump(), default_flow_style=False))

    latest = latest_dump_path(agent, base)
    if not latest.exists() or overwrite:
        sample = AgentDump(agent=agent, source="init")
        latest.write_text(sample.model_dump_json(indent=2))

    return adir, created_fresh


# ─── Config I/O ───────────────────────────────────────────────────────────────


def load_config(agent: str, base: Path = DEFAULT_MNEMO_DIR) -> MnemoConfig:
    path = config_path(agent, base)
    if not path.exists():
        raise FileNotFoundError(
            f"No config found for agent '{agent}'. Run: mnemo init --agent {agent}"
        )
    data = yaml.safe_load(path.read_text()) or {}
    return MnemoConfig(**data)


def save_config(cfg: MnemoConfig, base: Path = DEFAULT_MNEMO_DIR) -> Path:
    path = config_path(cfg.agent, base)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(cfg.model_dump(), default_flow_style=False))
    return path


# ─── Dump I/O ─────────────────────────────────────────────────────────────────


def save_dump(dump: AgentDump, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump.model_dump_json(indent=2))
    return path


def load_dump(path: Path) -> AgentDump:
    if not path.exists():
        raise FileNotFoundError(f"Dump file not found: {path}")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    return AgentDump.model_validate(data)


def load_dump_safe(path: Path) -> AgentDump | None:
    try:
        return load_dump(path)
    except (FileNotFoundError, ValueError):
        return None


def list_dump_files(agent: str, base: Path = DEFAULT_MNEMO_DIR) -> list[Path]:
    ddir = dumps_dir(agent, base)
    if not ddir.exists():
        return []
    return sorted(ddir.glob("*.json"), reverse=True)


# ─── Agent discovery ──────────────────────────────────────────────────────────


def list_agents(base: Path = DEFAULT_MNEMO_DIR) -> list[str]:
    if not base.exists():
        return []
    return sorted(
        d.name for d in base.iterdir() if d.is_dir() and not d.name.startswith(".")
    )


def agent_exists(agent: str, base: Path = DEFAULT_MNEMO_DIR) -> bool:
    return agent_dir(agent, base).exists()


def delete_agent(agent: str, base: Path = DEFAULT_MNEMO_DIR) -> None:
    """Permanently delete an agent directory and all its data."""
    import shutil
    adir = agent_dir(agent, base)
    if not adir.exists():
        raise FileNotFoundError(f"Agent not found: {agent}")
    shutil.rmtree(adir)


def require_agent(agent: str, base: Path = DEFAULT_MNEMO_DIR) -> Path:
    adir = agent_dir(agent, base)
    if not adir.exists():
        raise FileNotFoundError(
            f"Agent '{agent}' not initialized. Run: mnemo init --agent {agent}"
        )
    return adir


# ─── Parquet export (optional) ────────────────────────────────────────────────


def dump_to_parquet(dump: AgentDump, path: Path) -> Path:
    try:
        import pandas as pd  # type: ignore
    except ImportError:
        raise ImportError(
            "Parquet export requires: pip install mnemo[parquet]"
        )

    rows = [f.model_dump() for f in dump.facts]
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["dump_ts"] = dump.dump_ts
    df["agent"] = dump.agent
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return path
