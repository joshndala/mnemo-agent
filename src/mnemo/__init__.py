"""mnemo — local-first agent memory CLI."""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("mnemo-agent")
except PackageNotFoundError:
    __version__ = "unknown"

__author__ = "Joshua Ndala"
