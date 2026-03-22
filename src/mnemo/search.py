"""TF-IDF keyword search engine for in-memory fact retrieval."""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import NamedTuple

from mnemo.models import AgentDump, Fact


class SearchResult(NamedTuple):
    fact: Fact
    score: float
    agent: str


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9]+", text.lower())


def _tf(tokens: list[str]) -> dict[str, float]:
    counts = Counter(tokens)
    total = len(tokens) or 1
    return {t: c / total for t, c in counts.items()}


class FactIndex:
    """In-memory TF-IDF index over one or more AgentDumps."""

    def __init__(self) -> None:
        self._docs: list[tuple[str, Fact, dict[str, float]]] = []  # (agent, fact, tf)
        self._df: Counter[str] = Counter()

    def add_dump(self, dump: AgentDump) -> None:
        for fact in dump.facts:
            tokens = _tokenize(fact.to_text())
            tf = _tf(tokens)
            self._docs.append((dump.agent, fact, tf))
            for term in set(tokens):
                self._df[term] += 1

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        if not self._docs:
            return []

        q_tokens = _tokenize(query)
        n = len(self._docs)
        scores: list[tuple[float, str, Fact]] = []

        for agent, fact, doc_tf in self._docs:
            score = 0.0
            for term in q_tokens:
                if term not in doc_tf:
                    continue
                tf = doc_tf[term]
                idf = math.log((n + 1) / (self._df.get(term, 0) + 1)) + 1
                score += tf * idf
            if score > 0:
                scores.append((score, agent, fact))

        scores.sort(key=lambda x: x[0], reverse=True)
        return [
            SearchResult(fact=f, score=s, agent=ag)
            for s, ag, f in scores[:limit]
        ]


def search_dumps(
    dumps: list[AgentDump], query: str, limit: int = 5
) -> list[SearchResult]:
    idx = FactIndex()
    for d in dumps:
        idx.add_dump(d)
    return idx.search(query, limit=limit)


# ─── Diff helpers ─────────────────────────────────────────────────────────────


def diff_dumps(
    dump_a: AgentDump, dump_b: AgentDump
) -> tuple[list[Fact], list[Fact], list[Fact]]:
    """Compare two dumps on (entity, attribute, value) triples.

    Returns (added, removed, common).
    """

    def key(f: Fact) -> tuple[str, str, str]:
        return (f.entity.lower(), f.attribute.lower(), f.value.lower())

    keys_a = {key(f): f for f in dump_a.facts}
    keys_b = {key(f): f for f in dump_b.facts}

    added = [f for k, f in keys_b.items() if k not in keys_a]
    removed = [f for k, f in keys_a.items() if k not in keys_b]
    common = [f for k, f in keys_a.items() if k in keys_b]
    return added, removed, common
