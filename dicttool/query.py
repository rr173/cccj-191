"""Top-k query engines with provably safe ranking bounds.

Both engines are *best-first* searches over a frontier heap.  They never
materialise the full result set:  candidates are expanded lazily and a
word is emitted only when no unexplored frontier entry can still outrank
it, which is exactly what allows stopping after k emissions.

Ranking bounds
--------------
prefix:  every state n carries max_weight[n] = the exact maximum weight
         of any word whose accepting path passes through n (computed at
         build time and re-verified by `check --deep`).  A frontier
         "bound" entry for the state reached by spelling p is ordered by
         the key (-max_weight, p):  every word hidden below it weighs at
         most max_weight and spells strictly greater than p (all words
         below share the prefix p, and the word p itself, if any, is
         emitted separately when the bound is expanded).  A concrete
         word w therefore pops before a bound exactly when the bound can
         no longer hide a word that would rank above w - i.e. when the
         bound's weight cap is lower, or its cap is equal but p >= w.
         This is what makes the stopping rule safe, and it degrades
         gracefully:  only genuinely tied subtrees with interleaving
         spellings force extra expansions, which the statistics report
         as measured work (nothing is claimed as pruned that was not).

suggest: a frontier entry carries the Levenshtein DP row of its spelled
         path.  min(row) never decreases along any arc (each DP step adds
         non-negative costs), so min(row) is a valid lower bound on the
         edit distance of every word below, max_weight[n] bounds their
         weight, and the path bounds their spelling as above.  Bounds
         and words share one key space (d, -weight, spelling) so a word
         is emitted only when no unexplored frontier entry can still
         contain a better-ranked word.

Stopping rule
-------------
Stop after k emissions or when the frontier is empty.  k = 0 returns
immediately.  If the frontier exceeds the caller's memory quota the
query fails loudly with FrontierOverflowError instead of silently
dropping candidates.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Optional

from .errors import FrontierOverflowError
from .reader import Package
from .validate import (
    MAX_QUERY_TEXT_LEN,
    validate_k,
    validate_max_edits,
    validate_query_text,
)

DEFAULT_MAX_FRONTIER = 500_000


@dataclass
class QueryStats:
    """Actual work performed; every number is measured, none estimated."""

    expansions: int = 0        # frontier bound entries expanded
    arcs_scanned: int = 0      # arc records read
    word_candidates: int = 0   # terminal paths turned into exact candidates
    weight_lookups: int = 0    # entries-table binary searches
    entry_probes: int = 0      # binary-search steps inside the table
    emitted: int = 0
    heap_pops: int = 0
    frontier_max: int = 0      # high-water mark of the frontier heap
    pruned_by_distance: int = 0  # suggest only: arcs whose row min > max_edits
    stopped_by: str = ""       # "k" | "exhausted" | "k=0"

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _check_frontier(heap: list, limit: int, stats: QueryStats) -> None:
    if len(heap) > stats.frontier_max:
        stats.frontier_max = len(heap)
    if len(heap) > limit:
        raise FrontierOverflowError(
            f"query frontier exceeded {limit} entries; raise --max-frontier. "
            f"No candidate was dropped; the query is safe to retry."
        )


def prefix_search(
    pkg: Package,
    prefix: str,
    k: int,
    *,
    max_frontier: int = DEFAULT_MAX_FRONTIER,
    stats: Optional[QueryStats] = None,
) -> list[tuple[str, int]]:
    """Top-k complete words starting with ``prefix``.

    Ranking: weight descending, then word ascending by code point.
    Returns [(word, weight), ...]; deterministic for k=0, empty prefix
    and missing prefixes.
    """
    validate_query_text(prefix, "prefix", pkg.max_word_len)
    validate_k(k)
    if stats is None:
        stats = QueryStats()
    probes0 = pkg.entry_probes
    try:
        return _prefix_run(pkg, prefix, k, max_frontier, stats)
    finally:
        stats.entry_probes = pkg.entry_probes - probes0


def _prefix_run(
    pkg: Package, prefix: str, k: int, max_frontier: int, stats: QueryStats
) -> list[tuple[str, int]]:
    if k == 0:
        stats.stopped_by = "k=0"
        return []

    node = pkg.start_node
    for ch in prefix:
        nxt = pkg.child(node, ord(ch))
        stats.arcs_scanned += 1
        if nxt is None:
            stats.stopped_by = "exhausted"
            return []
        node = nxt

    # Heap keys (min-heap), one shared key space for bounds and words:
    #   bound: (-max_weight, path, 1, nid)   path = spelling lower bound
    #   word:  (-weight,     word, 0, "")    exact entry
    # A bound sorts before a word iff it could still hide a better-ranked
    # word: higher weight cap, or equal cap and a strictly smaller
    # spelling bound.  On exact key equality the word pops first (tag 0):
    # everything hidden below the bound spells strictly greater than its
    # path, hence greater than that word.
    heap: list = []

    def push_bound(nid: int, path: str) -> None:
        _s, _c, _t, mw = pkg.node(nid)
        heapq.heappush(heap, ((-mw, path, 1, nid), nid, path))

    def push_word(word: str, weight: int) -> None:
        heapq.heappush(heap, ((-weight, word, 0, ""), word, weight))

    push_bound(node, prefix)
    results: list[tuple[str, int]] = []
    while heap:
        _check_frontier(heap, max_frontier, stats)
        key, payload_a, payload_b = heapq.heappop(heap)
        stats.heap_pops += 1
        if key[2] == 0:  # exact word candidate: safe to emit
            results.append((payload_a, payload_b))
            stats.emitted += 1
            if len(results) == k:
                stats.stopped_by = "k"
                return results
            continue
        # bound entry: expand one level
        nid, path = payload_a, payload_b
        stats.expansions += 1
        _s, _c, terminal, _mw = pkg.node(nid)
        if terminal:
            stats.weight_lookups += 1
            w = pkg.find_weight(path)
            stats.word_candidates += 1
            if w is None:  # pragma: no cover - package/graph inconsistency
                raise AssertionError(f"accepted word {path!r} missing from table")
            push_word(path, w)
        for label, target in pkg.arcs(nid):
            stats.arcs_scanned += 1
            push_bound(target, path + chr(label))
    stats.stopped_by = "exhausted"
    return results


# ---------------------------------------------------------------------------
# suggest
# ---------------------------------------------------------------------------

def _initial_row(m: int, inf: int) -> list[int]:
    return [min(i, inf) for i in range(m + 1)]


def _step_row(row: list[int], ch: str, text: str, inf: int) -> list[int]:
    """One Levenshtein DP transition consuming word character ``ch``."""
    m = len(text)
    nrow = [min(row[0] + 1, inf)]
    ti = text
    r = row
    append = nrow.append
    for i in range(1, m + 1):
        cost = 0 if ti[i - 1] == ch else 1
        v = r[i - 1] + cost
        u = r[i] + 1
        if u < v:
            v = u
        u = nrow[i - 1] + 1
        if u < v:
            v = u
        append(v if v < inf else inf)
    return nrow


def suggest_search(
    pkg: Package,
    text: str,
    max_edits: int,
    k: int,
    *,
    max_frontier: int = DEFAULT_MAX_FRONTIER,
    stats: Optional[QueryStats] = None,
) -> list[tuple[str, int, int]]:
    """Top-k words within Levenshtein distance <= max_edits of ``text``.

    Ranking: (distance asc, weight desc, word asc by code point).
    Returns [(word, weight, distance), ...].
    """
    validate_query_text(text, "text", MAX_QUERY_TEXT_LEN)
    validate_max_edits(max_edits)
    validate_k(k)
    if stats is None:
        stats = QueryStats()
    probes0 = pkg.entry_probes
    try:
        return _suggest_run(pkg, text, max_edits, k, max_frontier, stats)
    finally:
        stats.entry_probes = pkg.entry_probes - probes0


def _suggest_run(
    pkg: Package, text: str, max_edits: int, k: int, max_frontier: int,
    stats: QueryStats,
) -> list[tuple[str, int, int]]:
    if k == 0:
        stats.stopped_by = "k=0"
        return []

    m = len(text)
    inf = max_edits + 1  # cap; any value > max_edits is "too far"

    # Heap keys (min-heap), one shared key space:
    #   bound: (d_bound, -max_weight, path, 1)   optimistic lower bounds
    #   word:  (distance, -weight,    word, 0)   exact entry
    # d_bound = min(DP row) <= distance of every word below (row minima
    # never decrease along arcs); max_weight caps their weight; and every
    # hidden word spells strictly greater than path.  A bound therefore
    # sorts before a word iff it could still hide a better-ranked word;
    # on exact key equality the word pops first (tag 0).
    heap: list = []

    def push_bound(nid: int, row: list[int], path: str) -> None:
        _s, _c, _t, mw = pkg.node(nid)
        heapq.heappush(heap, ((min(row), -mw, path, 1), nid, row, path))

    def push_word(word: str, weight: int, dist: int) -> None:
        heapq.heappush(heap, ((dist, -weight, word, 0), word, weight, dist))

    push_bound(pkg.start_node, _initial_row(m, inf), "")
    results: list[tuple[str, int, int]] = []
    while heap:
        _check_frontier(heap, max_frontier, stats)
        item = heapq.heappop(heap)
        stats.heap_pops += 1
        key = item[0]
        if key[3] == 0:  # exact word candidate
            _k, word, weight, dist = item
            results.append((word, weight, dist))
            stats.emitted += 1
            if len(results) == k:
                stats.stopped_by = "k"
                return results
            continue
        _k, nid, row, path = item
        stats.expansions += 1
        _s, _c, terminal, _mw = pkg.node(nid)
        if terminal:
            dist = row[m]
            if dist <= max_edits:
                stats.weight_lookups += 1
                w = pkg.find_weight(path)
                stats.word_candidates += 1
                if w is None:  # pragma: no cover
                    raise AssertionError(f"accepted word {path!r} missing from table")
                push_word(path, w, dist)
        for label, target in pkg.arcs(nid):
            stats.arcs_scanned += 1
            nrow = _step_row(row, chr(label), text, inf)
            if min(nrow) <= max_edits:
                push_bound(target, nrow, path + chr(label))
            else:
                stats.pruned_by_distance += 1
    stats.stopped_by = "exhausted"
    return results
