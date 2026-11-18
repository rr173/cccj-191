"""Independent brute-force verifier.

These functions deliberately do NOT touch the automaton: they scan the
canonical entries table (or a JSONL file) linearly, compute full
Levenshtein distances for every word, sort complete result sets, and
only then slice the top k.  The graph query engines are checked against
these reference results, per k, by the test-suite and `dicttool verify`.
"""

from __future__ import annotations

import json
from typing import Iterable, Iterator, Optional

from .validate import parse_line, DEFAULT_MAX_WORD_LEN


def load_entries_jsonl(path: str, max_word_len: int = DEFAULT_MAX_WORD_LEN) -> list[tuple[str, int]]:
    """Load a canonical/source JSONL file, summing duplicate words."""
    acc: dict[str, int] = {}
    with open(path, "r", encoding="utf-8", errors="strict") as f:
        for lineno, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if not line.strip():
                continue
            word, weight = parse_line(line, lineno, max_word_len)
            acc[word] = acc.get(word, 0) + weight
    return sorted(acc.items())


def levenshtein(a: str, b: str) -> int:
    """Exact Levenshtein distance (insert/delete/substitute cost 1)."""
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j - 1] + (ca != cb), prev[j] + 1, cur[j - 1] + 1))
        prev = cur
    return prev[-1]


def brute_prefix(
    entries: Iterable[tuple[str, int]], prefix: str, k: int
) -> list[tuple[str, int]]:
    cands = [(w, wt) for (w, wt) in entries if w.startswith(prefix)]
    cands.sort(key=lambda t: (-t[1], t[0]))
    return cands[:k] if k else []


def brute_suggest(
    entries: Iterable[tuple[str, int]], text: str, max_edits: int, k: int
) -> list[tuple[str, int, int]]:
    cands = []
    for w, wt in entries:
        d = levenshtein(w, text)
        if d <= max_edits:
            cands.append((w, wt, d))
    cands.sort(key=lambda t: (t[2], -t[1], t[0]))
    return cands[:k] if k else []


def compare_prefix(graph_result, brute_result) -> Optional[str]:
    if list(graph_result) != list(brute_result):
        return f"prefix mismatch:\n  graph={graph_result}\n  brute={brute_result}"
    return None


def compare_suggest(graph_result, brute_result) -> Optional[str]:
    if list(graph_result) != list(brute_result):
        return f"suggest mismatch:\n  graph={graph_result}\n  brute={brute_result}"
    return None
