"""Read-only package reader with full structural validation.

Opening a package performs, in order:

1. magic + header length + header JSON parse;
2. declared-layout vs. actual file size check (truncation);
3. footer magic + sha256 over all preceding bytes (unfinished build /
   corruption);
4. per-section sha256;
5. structural checks: section sizes vs. counts, arc targets in range,
   arc labels valid scalar values and strictly sorted per node, index
   offsets monotonic and consistent with the entries blob, entry words
   strictly increasing, decodable UTF-8, length within max_word_len;
6. acyclicity of the arc graph (iterative three-colour DFS).

Only after all of this does :class:`Package` hand out cursors.  The file
is mapped read-only; nothing is cached in Python objects between opens,
so a copied package file is fully self-contained.
"""

from __future__ import annotations

import hashlib
import json
import mmap
import os
from typing import Iterator, Optional, Tuple

from . import packformat as pf
from .errors import (
    BadReferenceError,
    CyclicGraphError,
    IncompleteBuildError,
    PackageError,
    TruncatedPackageError,
)


class Package:
    """An open, validated, memory-mapped dictionary package."""

    def __init__(self) -> None:
        self._f = None
        self._mm: Optional[mmap.mmap] = None
        self.header: dict = {}
        self.word_count = 0
        self.node_count = 0
        self.arc_count = 0
        self.start_node = 0
        self.max_word_len = 0
        self._sec: dict[str, tuple[int, int]] = {}
        # stats hook: incremented on every binary-search probe
        self.entry_probes = 0

    # ------------------------------------------------------------------
    @classmethod
    def open(cls, path: str) -> "Package":
        pkg = cls()
        pkg._open(path)
        return pkg

    def _open(self, path: str) -> None:
        f = open(path, "rb")
        try:
            size = os.fstat(f.fileno()).st_size
            if size < len(pf.MAGIC) + pf.HEADER_LEN_STRUCT.size + pf.FOOTER_LEN:
                raise TruncatedPackageError(
                    f"{path}: file of {size} bytes is too small to be a package"
                )
            magic = f.read(len(pf.MAGIC))
            if magic != pf.MAGIC:
                raise PackageError(f"{path}: bad magic {magic!r}")
            (hlen,) = pf.HEADER_LEN_STRUCT.unpack(f.read(pf.HEADER_LEN_STRUCT.size))
            if hlen <= 0 or hlen > pf.MAX_HEADER_LEN:
                raise PackageError(f"{path}: implausible header length {hlen}")
            header_raw = f.read(hlen)
            if len(header_raw) != hlen:
                raise TruncatedPackageError(f"{path}: truncated inside header")
            try:
                header = json.loads(header_raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise PackageError(f"{path}: unreadable header: {exc}") from exc
            if not isinstance(header, dict) or header.get("format") != "mdawg":
                raise PackageError(f"{path}: not an mdawg header")
            if header.get("version") != 1:
                raise PackageError(f"{path}: unsupported version {header.get('version')!r}")

            sections = header.get("sections")
            if not isinstance(sections, dict) or set(sections) != set(pf.SECTION_ORDER):
                raise PackageError(f"{path}: header sections missing or unknown")
            sec: dict[str, tuple[int, int]] = {}
            pos = len(pf.MAGIC) + pf.HEADER_LEN_STRUCT.size + hlen
            for name in pf.SECTION_ORDER:
                info = sections[name]
                off, ln = int(info["offset"]), int(info["length"])
                if off != pos or ln < 0:
                    raise BadReferenceError(
                        f"{path}: section {name!r} offset/length inconsistent"
                    )
                sec[name] = (off, ln)
                pos += ln
            expected = pos + pf.FOOTER_LEN
            if size != expected:
                raise TruncatedPackageError(
                    f"{path}: size {size} != declared layout {expected} "
                    f"(truncated or corrupted)"
                )

            # footer: commit marker + digest of everything before it
            f.seek(pos)
            footer = f.read(pf.FOOTER_LEN)
            if len(footer) != pf.FOOTER_LEN or not footer.startswith(pf.FOOTER_MAGIC):
                raise IncompleteBuildError(
                    f"{path}: missing commit footer (unfinished build?)"
                )
            want_digest = footer[len(pf.FOOTER_MAGIC):]
            h = hashlib.sha256()
            f.seek(0)
            remaining = pos
            while remaining > 0:
                chunk = f.read(min(1 << 20, remaining))
                if not chunk:
                    raise TruncatedPackageError(f"{path}: truncated before footer")
                h.update(chunk)
                remaining -= len(chunk)
            if h.digest() != want_digest:
                raise IncompleteBuildError(
                    f"{path}: content digest mismatch (unfinished or corrupted build)"
                )

            self._f = f
            self._mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
            self.header = header
            self._sec = sec
            self.word_count = int(header["word_count"])
            self.node_count = int(header["node_count"])
            self.arc_count = int(header["arc_count"])
            self.start_node = int(header["start_node"])
            self.max_word_len = int(header["max_word_len"])
            self._validate_sections()
            self._validate_graph()
            self._validate_entries()
        except Exception:
            f.close()
            raise

    # ------------------------------------------------------------------
    def close(self) -> None:
        if self._mm is not None:
            self._mm.close()
            self._mm = None
        if self._f is not None:
            self._f.close()
            self._f = None

    def __enter__(self) -> "Package":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------
    def _check_section_hash(self, name: str) -> None:
        off, ln = self._sec[name]
        h = hashlib.sha256()
        h.update(self._mm[off:off + ln])
        if h.hexdigest() != self.header["sections"][name]["sha256"]:
            raise PackageError(f"section {name!r}: sha256 mismatch")

    def _validate_sections(self) -> None:
        if self._sec["nodes"][1] != self.node_count * pf.NODE_SIZE:
            raise BadReferenceError("nodes section size does not match node_count")
        if self._sec["arcs"][1] != self.arc_count * pf.ARC_SIZE:
            raise BadReferenceError("arcs section size does not match arc_count")
        if self._sec["index"][1] != (self.word_count + 1) * pf.INDEX_SIZE:
            raise BadReferenceError("index section size does not match word_count")
        if not (0 <= self.start_node < max(self.node_count, 1)):
            raise BadReferenceError("start_node out of range")
        if self.node_count == 0:
            raise PackageError("package has no states at all")
        for name in pf.SECTION_ORDER:
            self._check_section_hash(name)

    def _validate_graph(self) -> None:
        mm = self._mm
        nodes_off, _ = self._sec["nodes"]
        arcs_off, _ = self._sec["arcs"]
        prev_label = -1
        for nid in range(self.node_count):
            base = nodes_off + nid * pf.NODE_SIZE
            arc_start, arc_count, flags, _mw = pf.NODE_STRUCT.unpack_from(mm, base)
            if arc_start + arc_count > self.arc_count:
                raise BadReferenceError(f"node {nid}: arc range out of bounds")
            prev_label = -1
            for j in range(arc_count):
                label, target = pf.ARC_STRUCT.unpack_from(
                    mm, arcs_off + (arc_start + j) * pf.ARC_SIZE
                )
                if label > 0x10FFFF or 0xD800 <= label <= 0xDFFF:
                    raise PackageError(f"node {nid}: invalid arc label U+{label:04X}")
                if label <= prev_label:
                    raise PackageError(f"node {nid}: arc labels not strictly sorted")
                prev_label = label
                if target >= self.node_count:
                    raise BadReferenceError(
                        f"node {nid}: arc target {target} out of range"
                    )
        # acyclicity: iterative three-colour DFS over ALL states (a
        # tampered package could hide a cycle in unreachable states)
        WHITE, GRAY, BLACK = 0, 1, 2
        color = bytearray(self.node_count)
        for root in range(self.node_count):
            if color[root] != WHITE:
                continue
            stack = [(root, 0)]
            color[root] = GRAY
            while stack:
                nid, next_arc = stack[-1]
                base = nodes_off + nid * pf.NODE_SIZE
                arc_start, arc_count, _flags, _mw = pf.NODE_STRUCT.unpack_from(mm, base)
                if next_arc < arc_count:
                    stack[-1] = (nid, next_arc + 1)
                    _label, target = pf.ARC_STRUCT.unpack_from(
                        mm, arcs_off + (arc_start + next_arc) * pf.ARC_SIZE
                    )
                    if color[target] == GRAY:
                        raise CyclicGraphError(
                            f"package contains a cycle through state {target}"
                        )
                    if color[target] == WHITE:
                        color[target] = GRAY
                        stack.append((target, 0))
                else:
                    color[nid] = BLACK
                    stack.pop()

    def _validate_entries(self) -> None:
        mm = self._mm
        idx_off, _ = self._sec["index"]
        ent_off, ent_len = self._sec["entries"]
        prev: Optional[bytes] = None
        for i in range(self.word_count):
            (start,) = pf.INDEX_STRUCT.unpack_from(mm, idx_off + i * pf.INDEX_SIZE)
            (end,) = pf.INDEX_STRUCT.unpack_from(mm, idx_off + (i + 1) * pf.INDEX_SIZE)
            if not (0 <= start <= end <= ent_len):
                raise BadReferenceError(f"entry {i}: bad offset range")
            (blen,) = pf.ENTRY_LEN_STRUCT.unpack_from(mm, ent_off + start)
            if 4 + blen + 8 != end - start:
                raise BadReferenceError(f"entry {i}: record length mismatch")
            if blen == 0:
                raise PackageError(f"entry {i}: empty word in table")
            data = bytes(mm[ent_off + start + 4: ent_off + start + 4 + blen])
            try:
                word = data.decode("utf-8", "strict")
            except UnicodeDecodeError as exc:
                raise PackageError(f"entry {i}: invalid UTF-8") from exc
            if any(0xD800 <= ord(c) <= 0xDFFF for c in word):
                raise PackageError(f"entry {i}: lone surrogate in word")
            if len(word) > self.max_word_len:
                raise PackageError(f"entry {i}: word longer than max_word_len")
            if prev is not None and data <= prev:
                raise PackageError(f"entry {i}: words not strictly increasing")
            prev = data

    # ------------------------------------------------------------------
    # accessors used by the query engines
    # ------------------------------------------------------------------
    def node(self, nid: int) -> tuple[int, int, bool, int]:
        """Return (arc_start, arc_count, terminal, max_weight)."""
        base = self._sec["nodes"][0] + nid * pf.NODE_SIZE
        arc_start, arc_count, flags, mw = pf.NODE_STRUCT.unpack_from(self._mm, base)
        return arc_start, arc_count, bool(flags & pf.FLAG_TERMINAL), mw

    def arcs(self, nid: int) -> Iterator[tuple[int, int]]:
        """Yield (label_codepoint, target) for a node's arcs, label-sorted."""
        arc_start, arc_count, _t, _mw = self.node(nid)
        base = self._sec["arcs"][0] + arc_start * pf.ARC_SIZE
        for j in range(arc_count):
            yield pf.ARC_STRUCT.unpack_from(self._mm, base + j * pf.ARC_SIZE)

    def child(self, nid: int, label: int) -> Optional[int]:
        """Follow one arc by code point; None if absent (binary search)."""
        arc_start, arc_count, _t, _mw = self.node(nid)
        base = self._sec["arcs"][0]
        lo, hi = 0, arc_count
        while lo < hi:
            mid = (lo + hi) // 2
            lbl, tgt = pf.ARC_STRUCT.unpack_from(
                self._mm, base + (arc_start + mid) * pf.ARC_SIZE
            )
            if lbl == label:
                return tgt
            if lbl < label:
                lo = mid + 1
            else:
                hi = mid
        return None

    def entry(self, i: int) -> tuple[str, int]:
        idx_off = self._sec["index"][0]
        ent_off = self._sec["entries"][0]
        (start,) = pf.INDEX_STRUCT.unpack_from(self._mm, idx_off + i * pf.INDEX_SIZE)
        (blen,) = pf.ENTRY_LEN_STRUCT.unpack_from(self._mm, ent_off + start)
        data = self._mm[ent_off + start + 4: ent_off + start + 4 + blen]
        (w,) = pf.ENTRY_WEIGHT_STRUCT.unpack_from(
            self._mm, ent_off + start + 4 + blen
        )
        return data.decode("utf-8", "strict"), w

    def iter_entries(self) -> Iterator[tuple[str, int]]:
        for i in range(self.word_count):
            yield self.entry(i)

    def find_weight(self, word: str) -> Optional[int]:
        """Binary-search the entries table; counts probes for stats."""
        target = word.encode("utf-8")
        idx_off = self._sec["index"][0]
        ent_off = self._sec["entries"][0]
        lo, hi = 0, self.word_count
        while lo < hi:
            mid = (lo + hi) // 2
            self.entry_probes += 1
            (start,) = pf.INDEX_STRUCT.unpack_from(self._mm, idx_off + mid * pf.INDEX_SIZE)
            (blen,) = pf.ENTRY_LEN_STRUCT.unpack_from(self._mm, ent_off + start)
            data = self._mm[ent_off + start + 4: ent_off + start + 4 + blen]
            if data == target:
                (w,) = pf.ENTRY_WEIGHT_STRUCT.unpack_from(
                    self._mm, ent_off + start + 4 + blen
                )
                return w
            if bytes(data) < target:
                lo = mid + 1
            else:
                hi = mid
        return None

    # ------------------------------------------------------------------
    # audits (used by `check --deep` and tests): checkable evidence of
    # the minimisation semantics.
    # ------------------------------------------------------------------
    def audit_minimality(self) -> dict:
        """Verify no two distinct states share a structural signature.

        For an acyclic deterministic automaton, bottom-up signature
        uniqueness is exactly right-language equivalence, so passing this
        check proves the automaton is minimal under the chosen semantics.
        Also computes in-degrees as direct evidence of suffix sharing.
        """
        sig_seen: dict = {}
        in_degree = [0] * self.node_count
        for nid in range(self.node_count):
            _s, _c, terminal, _mw = self.node(nid)
            arcs = list(self.arcs(nid))
            sig = (terminal, tuple(arcs))
            other = sig_seen.get(sig)
            if other is not None:
                raise PackageError(
                    f"states {other} and {nid} have identical signatures: "
                    f"automaton is not minimal"
                )
            sig_seen[sig] = nid
            for _lbl, tgt in arcs:
                in_degree[tgt] += 1
        shared = sum(1 for d in in_degree if d > 1)
        return {
            "states": self.node_count,
            "shared_states(in_degree>1)": shared,
            "max_in_degree": max(in_degree) if in_degree else 0,
        }

    def audit_max_weights(self) -> bool:
        """Recompute the per-state max-weight bounds from the entries
        table and the graph (walking every word's accepting path), and
        require exact equality with the stored values."""
        computed = [0] * self.node_count
        for word, weight in self.iter_entries():
            nid = self.start_node
            if weight > computed[nid]:
                computed[nid] = weight
            for ch in word:
                tgt = self.child(nid, ord(ch))
                if tgt is None:
                    raise PackageError(f"word {word!r} not accepted by automaton")
                nid = tgt
                if weight > computed[nid]:
                    computed[nid] = weight
            _s, _c, terminal, _mw = self.node(nid)
            if not terminal:
                raise PackageError(f"word {word!r} ends in non-terminal state")
        for nid in range(self.node_count):
            _s, _c, _t, stored = self.node(nid)
            if stored != computed[nid]:
                raise PackageError(
                    f"state {nid}: stored max_weight {stored} != recomputed "
                    f"{computed[nid]}"
                )
        return True
