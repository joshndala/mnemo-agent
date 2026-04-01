"""mnemo — local-first agent memory CLI."""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("mnemo-agent")
except PackageNotFoundError:
    __version__ = "unknown"

__author__ = "Joshua Ndala"

from mnemo.client import AsyncMnemoClient, MnemoClient  # noqa: E402
from mnemo.models import AgentDump, Fact  # noqa: E402

__all__ = ["MnemoClient", "AsyncMnemoClient", "Fact", "AgentDump", "__version__", "__author__"]
