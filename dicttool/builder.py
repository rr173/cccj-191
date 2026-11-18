"""Build pipeline: JSONL -> validate -> external sort -> minimise -> pack.

The word stream is handled externally (run files on disk); the minimised
graph itself is built in memory (documented limitation: the *automaton*
must fit in RAM, the *dictionary* need not).  Sections are streamed to
temporary files and assembled into the final single-file package, which
is committed by writing the footer digest and atomically renaming.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from array import array
from dataclasses import dataclass, field
from typing import IO, Iterator, Optional

from . import packformat as pf
from .dawg import DawgBuilder, FrozenDawg, compute_max_weights
from .errors import DictToolError, ValidationError
from .externsort import external_sort_entries
from .validate import DEFAULT_MAX_WORD_LEN, parse_line


@dataclass
class BuildStats:
    input_lines: int = 0
    unique_words: int = 0
    duplicates_summed: int = 0
    sort_runs: int = 0
    trie_node_count: int = 0
    node_count: int = 0
    arc_count: int = 0
    register_hits: int = 0
    max_word_len: int = 0
    package_bytes: int = 0
    seconds: float = 0.0

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _iter_validated_lines(f: IO[str], max_word_len: int) -> Iterator[tuple[str, int]]:
    for lineno, line in enumerate(f, 1):
        if line.endswith("\n"):
            line = line[:-1]
        if not line.strip():
            raise ValidationError(f"line {lineno}: blank line is not a valid entry")
        yield parse_line(line, lineno, max_word_len)


def _write_entries_and_index(
    merged: Iterator[tuple[str, int]], body_dir: str
) -> tuple[str, str, int]:
    """Stream merged entries to two temp files; returns (entries_path,
    index_path, word_count)."""
    ef = tempfile.NamedTemporaryFile(prefix="dicttool-entries-", dir=body_dir, delete=False)
    idxf = tempfile.NamedTemporaryFile(prefix="dicttool-index-", dir=body_dir, delete=False)
    count = 0
    offset = 0
    try:
        for word, weight in merged:
            data = word.encode("utf-8")
            idxf.write(pf.INDEX_STRUCT.pack(offset))
            ef.write(pf.ENTRY_LEN_STRUCT.pack(len(data)))
            ef.write(data)
            ef.write(pf.ENTRY_WEIGHT_STRUCT.pack(weight))
            offset += 4 + len(data) + 8
            count += 1
        idxf.write(pf.INDEX_STRUCT.pack(offset))  # sentinel: blob length
    finally:
        ef.close()
        idxf.close()
    return ef.name, idxf.name, count


def _iter_entries_file(path: str) -> Iterator[tuple[str, int]]:
    with open(path, "rb") as f:
        while True:
            head = f.read(4)
            if not head:
                return
            (n,) = pf.ENTRY_LEN_STRUCT.unpack(head)
            data = f.read(n)
            (w,) = pf.ENTRY_WEIGHT_STRUCT.unpack(f.read(8))
            yield data.decode("utf-8", "strict"), w


def _write_graph_sections(
    dawg: FrozenDawg, body_dir: str
) -> tuple[str, str, array, array, array]:
    """Write arcs and nodes (max_weight placeholder 0) to temp files.

    Returns (arcs_path, nodes_path, arc_start, arc_count, flags) with the
    per-node metadata kept in compact arrays for the later patch pass.
    """
    arc_start = array("I")
    arc_count = array("I")
    flags = array("B")
    af = tempfile.NamedTemporaryFile(prefix="dicttool-arcs-", dir=body_dir, delete=False)
    nf = tempfile.NamedTemporaryFile(prefix="dicttool-nodes-", dir=body_dir, delete=False)
    try:
        aoff = 0
        for terminal, arcs in dawg.nodes:
            arc_start.append(aoff)
            arc_count.append(len(arcs))
            flags.append(pf.FLAG_TERMINAL if terminal else 0)
            for label, target in arcs:
                af.write(pf.ARC_STRUCT.pack(label, target))
            aoff += len(arcs)
            nf.write(pf.NODE_STRUCT.pack(0, 0, 0, 0))  # patched later
    finally:
        af.close()
        nf.close()
    return af.name, nf.name, arc_start, arc_count, flags


def _patch_nodes(nodes_path: str, arc_start, arc_count, flags, max_weight) -> None:
    with open(nodes_path, "r+b") as f:
        for nid in range(len(arc_start)):
            f.seek(nid * pf.NODE_SIZE)
            f.write(
                pf.NODE_STRUCT.pack(
                    arc_start[nid], arc_count[nid], flags[nid], max_weight[nid]
                )
            )


def _sha256_of_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build(
    src_path: str,
    out_path: str,
    *,
    mem_budget: int = 256 << 20,
    tmp_dir: Optional[str] = None,
    max_word_len: int = DEFAULT_MAX_WORD_LEN,
) -> BuildStats:
    """Build a self-contained dictionary package from a JSONL source."""
    t0 = time.monotonic()
    stats = BuildStats()
    body_dir = tmp_dir or os.path.dirname(os.path.abspath(out_path)) or "."
    tmp_paths: list[str] = []

    try:
        with open(src_path, "r", encoding="utf-8", errors="strict") as f:
            merged, sort_stats, cleanup = external_sort_entries(
                _iter_validated_lines(f, max_word_len), mem_budget, tmp_dir
            )
            try:
                entries_path, index_path, word_count = _write_entries_and_index(
                    merged, body_dir
                )
            finally:
                cleanup()
        tmp_paths += [entries_path, index_path]
        stats.input_lines = sort_stats.input_lines
        stats.unique_words = sort_stats.unique_words
        stats.duplicates_summed = sort_stats.duplicates_summed
        stats.sort_runs = sort_stats.runs
        stats.max_word_len = sort_stats.max_word_len

        # --- minimise -------------------------------------------------
        dawg_builder = DawgBuilder()
        for word, _w in _iter_entries_file(entries_path):
            dawg_builder.add(word)
        dawg = dawg_builder.freeze()
        stats.trie_node_count = dawg.stats.trie_node_count
        stats.node_count = dawg.stats.node_count
        stats.arc_count = dawg.stats.arc_count
        stats.register_hits = dawg.stats.register_hits
        if dawg.stats.node_count > pf.MAX_NODE_COUNT:
            raise DictToolError("automaton exceeds 2^32-1 states")
        if dawg.stats.arc_count > pf.MAX_ARC_COUNT:
            raise DictToolError("automaton exceeds 2^32-1 arcs")

        arcs_path, nodes_path, a_start, a_cnt, a_flags = _write_graph_sections(
            dawg, body_dir
        )
        tmp_paths += [arcs_path, nodes_path]

        start_node = dawg.start
        max_weight = compute_max_weights(dawg, _iter_entries_file(entries_path))
        _patch_nodes(nodes_path, a_start, a_cnt, a_flags, max_weight)
        del dawg, max_weight, a_start, a_cnt, a_flags

        # --- assemble package ----------------------------------------
        sections = {
            "nodes": nodes_path,
            "arcs": arcs_path,
            "index": index_path,
            "entries": entries_path,
        }
        sec_info = {}
        for name in pf.SECTION_ORDER:
            p = sections[name]
            sec_info[name] = {
                "length": os.path.getsize(p),
                "sha256": _sha256_of_file(p),
            }

        header = {
            "format": "mdawg",
            "version": 1,
            "created_unix": int(time.time()),
            "word_count": word_count,
            "node_count": stats.node_count,
            "arc_count": stats.arc_count,
            "start_node": start_node,
            "max_word_len": stats.max_word_len,
            "sections": sec_info,
        }
        # Fixpoint: offsets depend on the header length, which depends on
        # the offsets' decimal lengths.  Converges in a few iterations.
        offsets = {name: 0 for name in pf.SECTION_ORDER}
        header_bytes = b""
        for _ in range(10):
            for name in pf.SECTION_ORDER:
                header["sections"][name]["offset"] = offsets[name]
            header_bytes = json.dumps(
                header, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            base = len(pf.MAGIC) + pf.HEADER_LEN_STRUCT.size + len(header_bytes)
            new_offsets = {}
            pos = base
            for name in pf.SECTION_ORDER:
                new_offsets[name] = pos
                pos += sec_info[name]["length"]
            if new_offsets == offsets:
                break
            offsets = new_offsets
        else:  # pragma: no cover
            raise DictToolError("header offset fixpoint did not converge")

        body_len = sum(sec_info[n]["length"] for n in pf.SECTION_ORDER)
        expected_size = offsets[pf.SECTION_ORDER[0]] + body_len + pf.FOOTER_LEN

        out_dir = os.path.dirname(os.path.abspath(out_path)) or "."
        fd, tmp_out = tempfile.mkstemp(prefix=".dicttool-build-", dir=out_dir)
        tmp_paths.append(tmp_out)
        digest = hashlib.sha256()
        with os.fdopen(fd, "wb") as out:
            out.write(pf.MAGIC)
            digest.update(pf.MAGIC)
            out.write(pf.HEADER_LEN_STRUCT.pack(len(header_bytes)))
            digest.update(pf.HEADER_LEN_STRUCT.pack(len(header_bytes)))
            out.write(header_bytes)
            digest.update(header_bytes)
            for name in pf.SECTION_ORDER:
                with open(sections[name], "rb") as sf:
                    for chunk in iter(lambda: sf.read(1 << 20), b""):
                        out.write(chunk)
                        digest.update(chunk)
            out.write(pf.FOOTER_MAGIC)
            out.write(digest.digest())
            out.flush()
            os.fsync(out.fileno())
        got_size = os.path.getsize(tmp_out)
        if got_size != expected_size:  # pragma: no cover
            raise DictToolError(
                f"internal size mismatch: wrote {got_size}, expected {expected_size}"
            )
        os.replace(tmp_out, out_path)
        tmp_paths.remove(tmp_out)
        stats.package_bytes = got_size
        stats.seconds = time.monotonic() - t0
        return stats
    finally:
        for p in tmp_paths:
            try:
                os.unlink(p)
            except OSError:
                pass
