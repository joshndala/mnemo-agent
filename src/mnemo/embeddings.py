"""Embedding backend for semantic search — wraps fastembed (ONNX, no PyTorch).

Install: pip install 'mnemo[semantic]'
"""

from __future__ import annotations

import math
from typing import Any

_model: Any = None  # in-process model cache; avoids reload within one CLI invocation


def _require_fastembed() -> Any:
    try:
        from fastembed import TextEmbedding  # type: ignore
        return TextEmbedding
    except ImportError:
        raise ImportError(
            "Semantic search requires fastembed.\n"
            "Install with: pip install 'mnemo[semantic]'"
        )


def get_embeddings(
    texts: list[str],
    model_name: str = "BAAI/bge-small-en-v1.5",
) -> list[list[float]]:
    """Embed a list of texts using fastembed. Returns one vector per text.

    The loaded model is cached in-process so repeated calls within the same
    CLI invocation do not reload the ONNX model from disk.
    """
    global _model
    TextEmbedding = _require_fastembed()
    if _model is None:
        _model = TextEmbedding(model_name)
    return [vec.tolist() for vec in _model.embed(texts)]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Pure-Python cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
