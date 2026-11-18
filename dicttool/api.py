"""Public read-only API.

    from dicttool.api import Dictionary

    with Dictionary.open("dict.mdawg") as d:
        d.prefix("ab", 10)
        d.suggest("ab", 2, 10, stats={})

A Dictionary holds only an mmap of the package file; it can be opened
any number of times, from any copy of the file, and exposes no mutation
methods.  Optional ``stats`` dicts receive the measured query statistics.
"""

from __future__ import annotations

from typing import Optional

from .query import DEFAULT_MAX_FRONTIER, QueryStats, prefix_search, suggest_search
from .reader import Package


class Dictionary:
    def __init__(self, pkg: Package):
        self._pkg = pkg

    @classmethod
    def open(cls, path: str) -> "Dictionary":
        return cls(Package.open(path))

    def close(self) -> None:
        self._pkg.close()

    def __enter__(self) -> "Dictionary":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------
    @property
    def word_count(self) -> int:
        return self._pkg.word_count

    @property
    def node_count(self) -> int:
        return self._pkg.node_count

    @property
    def arc_count(self) -> int:
        return self._pkg.arc_count

    @property
    def max_word_len(self) -> int:
        return self._pkg.max_word_len

    # ------------------------------------------------------------------
    def prefix(
        self,
        prefix: str,
        k: int,
        *,
        max_frontier: int = DEFAULT_MAX_FRONTIER,
        stats: Optional[dict] = None,
    ) -> list[tuple[str, int]]:
        st = QueryStats()
        res = prefix_search(self._pkg, prefix, k, max_frontier=max_frontier, stats=st)
        if stats is not None:
            stats.update(st.as_dict())
        return res

    def suggest(
        self,
        text: str,
        max_edits: int,
        k: int,
        *,
        max_frontier: int = DEFAULT_MAX_FRONTIER,
        stats: Optional[dict] = None,
    ) -> list[tuple[str, int, int]]:
        st = QueryStats()
        res = suggest_search(
            self._pkg, text, max_edits, k, max_frontier=max_frontier, stats=st
        )
        if stats is not None:
            stats.update(st.as_dict())
        return res

    # ------------------------------------------------------------------
    def lookup(self, word: str) -> Optional[int]:
        """Exact weight lookup via the entries table (not the graph)."""
        return self._pkg.find_weight(word)

    def entries(self):
        """Iterate (word, weight) in code-point order from the table."""
        return self._pkg.iter_entries()

    def explain(self, word: str) -> dict:
        """Accepting path for ``word``: state ids AND arc labels.

        State ids alone do not identify a word in a minimal automaton -
        two words whose right-languages merged can traverse the very same
        states (e.g. "ab"/"cb" in a two-word dictionary).  The word's
        identity is the sequence of arc *labels*; the weight lives only
        in the entries table.  Two words whose paths converge on the same
        state id share suffix structure while remaining distinct words.
        """
        pkg = self._pkg
        nid = pkg.start_node
        states = [nid]
        labels: list[str] = []
        for ch in word:
            tgt = pkg.child(nid, ord(ch))
            if tgt is None:
                return {"word": word, "accepted": False, "states": states,
                        "labels": labels}
            nid = tgt
            states.append(nid)
            labels.append(ch)
        _s, _c, terminal, mw = pkg.node(nid)
        return {
            "word": word,
            "accepted": terminal,
            "terminal_state": nid if terminal else None,
            "state_max_weight": mw,
            "states": states,
            "labels": labels,
            "weight": pkg.find_weight(word) if terminal else None,
        }
