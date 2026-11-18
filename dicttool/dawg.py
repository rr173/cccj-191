"""Incremental construction of a minimal acyclic deterministic finite
automaton (DAFSA) from a sorted word list.

Minimisation semantics (chosen and documented in README.md):

    The automaton is an *unweighted acceptor*.  Two states p and q are
    equivalent iff their right-languages are identical:
        L(p) = { s : s is accepted starting from p }
             = L(q).
    For an acyclic automaton this coincides with structural signature
    equality applied bottom-up:  sig(p) == sig(q) where
        sig(p) = (terminal(p), sorted((label, canonical_child_id) ...)).
    Word weights and word identity are NOT part of the state; they live
    in a separate sorted entries table.  A terminal state therefore may
    be shared by several distinct words (e.g. "ab" and "cb" both end in
    the same terminal state of the minimal automaton) - word identity is
    always carried by the *path*, never by the state alone.

The algorithm is the classic register-based incremental minimisation for
sorted input (Daciuk et al., "Incremental construction of minimal acyclic
finite-state automata", 2000), implemented from scratch here:

- words arrive in code-point order;
- a path of *unchecked* states for the previous word is kept;
- when the next word diverges at position i, the suffix of the previous
  path below i is frozen: each frozen state is replaced by an equivalent
  registered state if one exists, otherwise it becomes the canonical
  representative of its signature;
- because children are always frozen before their parents, canonical ids
  are assigned bottom-up and arcs always point from a larger id to a
  smaller id, which gives a topological order for free (the loader still
  verifies acyclicity independently).

This is genuine right-language minimisation, not chain compression:
sibling subtrees with equal suffix structure converge to the same states
regardless of where they appear, which the tests assert explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple


class _State:
    __slots__ = ("terminal", "arcs", "id")

    def __init__(self) -> None:
        self.terminal = False
        self.arcs: Dict[int, "_State"] = {}  # code point -> child
        self.id = -1  # assigned when frozen


@dataclass
class DawgStats:
    word_count: int = 0
    trie_node_count: int = 0  # distinct prefixes incl. root, pre-minimisation
    node_count: int = 0  # frozen states incl. root
    arc_count: int = 0
    register_hits: int = 0  # times an existing state was reused
    max_word_len: int = 0


@dataclass
class FrozenDawg:
    """Serialisable result: nodes in id order.

    nodes[i] = (terminal, [(label_codepoint, child_id), ...] sorted by label)
    """

    nodes: List[Tuple[bool, List[Tuple[int, int]]]]
    start: int
    stats: DawgStats


def _common_prefix_len(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


class DawgBuilder:
    """Feed words in strictly increasing code-point order."""

    def __init__(self) -> None:
        self._root = _State()
        self._register: Dict[Tuple[bool, Tuple[Tuple[int, int], ...]], _State] = {}
        self._unchecked: List[Tuple[_State, int, _State]] = []  # (parent, label, state)
        self._prev = ""
        self._frozen: List[_State] = []
        self.stats = DawgStats(trie_node_count=1)  # count the root

    def add(self, word: str) -> None:
        if not word:
            raise ValueError("empty word cannot be inserted")
        if self.stats.word_count and word <= self._prev:
            raise ValueError("words must be added in strictly increasing order")
        cp = _common_prefix_len(word, self._prev)
        self._freeze_down_to(cp)
        parent = self._unchecked[-1][2] if self._unchecked else self._root
        for ch in word[cp:]:
            label = ord(ch)
            nxt = _State()
            parent.arcs[label] = nxt
            self._unchecked.append((parent, label, nxt))
            parent = nxt
            self.stats.trie_node_count += 1
        parent.terminal = True
        self._prev = word
        self.stats.word_count += 1
        if len(word) > self.stats.max_word_len:
            self.stats.max_word_len = len(word)

    def _freeze_down_to(self, depth: int) -> None:
        while len(self._unchecked) > depth:
            parent, label, state = self._unchecked.pop()
            sig = (
                state.terminal,
                tuple(sorted((lbl, child.id) for lbl, child in state.arcs.items())),
            )
            canonical = self._register.get(sig)
            if canonical is None:
                state.id = len(self._frozen)
                self._frozen.append(state)
                self._register[sig] = state
                canonical = state
            else:
                self.stats.register_hits += 1
            parent.arcs[label] = canonical

    def freeze(self) -> FrozenDawg:
        self._freeze_down_to(0)
        root = self._root
        root.id = len(self._frozen)
        self._frozen.append(root)
        nodes: List[Tuple[bool, List[Tuple[int, int]]]] = []
        arc_count = 0
        for st in self._frozen:
            arcs = sorted((lbl, child.id) for lbl, child in st.arcs.items())
            arc_count += len(arcs)
            nodes.append((st.terminal, arcs))
        self.stats.node_count = len(nodes)
        self.stats.arc_count = arc_count
        return FrozenDawg(nodes=nodes, start=root.id, stats=self.stats)


def compute_max_weights(
    dawg: FrozenDawg, words_and_weights: Iterator[Tuple[str, int]]
) -> List[int]:
    """Per-state ranking upper bound: the EXACT maximum weight of any
    word whose accepting path passes through the state.

    Computed by walking every word along its accepting path and taking
    the max at every state on the path - O(total word length).

    Note why a cheaper subtree propagation is NOT correct here: terminal
    sink states are shared between unrelated words (e.g. "cb" and "abc"
    end in the same state), so a child's end-weight aggregate would leak
    weights into parents that no such word passes through, producing a
    loose (though safe) bound.  The per-path computation is exact for
    the documented semantics and therefore keeps best-first expansion
    minimal.  The bound remains safe by construction: every word through
    state n contributes its own weight to max_weight[n].
    """
    n = len(dawg.nodes)
    max_weight = [0] * n
    for word, weight in words_and_weights:
        node = dawg.start
        if weight > max_weight[node]:
            max_weight[node] = weight
        for ch in word:
            label = ord(ch)
            arcs = dawg.nodes[node][1]
            nxt = None
            for lbl, tgt in arcs:
                if lbl == label:
                    nxt = tgt
                    break
            if nxt is None:  # pragma: no cover - indicates a build bug
                raise AssertionError(f"word {word!r} not accepted by automaton")
            node = nxt
            if weight > max_weight[node]:
                max_weight[node] = weight
    return max_weight
