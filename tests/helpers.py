"""Shared helpers for the dicttool test-suite."""

from __future__ import annotations

import hashlib
import json
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dicttool import packformat as pf  # noqa: E402
from dicttool.builder import build  # noqa: E402
from dicttool.verify import brute_prefix, brute_suggest, levenshtein  # noqa: E402


def write_jsonl(path: str, rows: list) -> None:
    """rows: list of (word, weight) tuples or raw strings."""
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            if isinstance(row, str):
                f.write(row + "\n")
            else:
                word, weight = row
                f.write(json.dumps({"word": word, "weight": weight},
                                   ensure_ascii=False) + "\n")


def build_dict(tmp: str, rows: list, name: str = "d.mdawg", **kw):
    src = os.path.join(tmp, "src.jsonl")
    write_jsonl(src, rows)
    out = os.path.join(tmp, name)
    stats = build(src, out, **kw)
    return out, stats


def brute(entries, prefix=None, text=None, max_edits=None, k=10):
    if prefix is not None:
        return brute_prefix(entries, prefix, k)
    return brute_suggest(entries, text, max_edits, k)


# ---------------------------------------------------------------------------
# package surgery: parse a package into parts and reassemble it, optionally
# mutating section bytes.  Hashes are recomputed so that only the *structural*
# validators can catch the mutation - exactly what we want to test.
# ---------------------------------------------------------------------------

def read_package_parts(path: str):
    with open(path, "rb") as f:
        raw = f.read()
    assert raw[:8] == pf.MAGIC
    (hlen,) = pf.HEADER_LEN_STRUCT.unpack(raw[8:16])
    header = json.loads(raw[16:16 + hlen].decode("utf-8"))
    sections = {}
    for name in pf.SECTION_ORDER:
        off = header["sections"][name]["offset"]
        ln = header["sections"][name]["length"]
        sections[name] = bytearray(raw[off:off + ln])
    return header, sections


def write_package_parts(path: str, header: dict, sections: dict) -> None:
    """Reassemble a package from (possibly mutated) parts, recomputing all
    hashes so the file is 'well-formed' except for the injected fault."""
    for name in pf.SECTION_ORDER:
        header["sections"][name]["length"] = len(sections[name])
        header["sections"][name]["sha256"] = hashlib.sha256(
            bytes(sections[name])).hexdigest()
    # offset fixpoint (same scheme as the builder)
    offsets = {n: 0 for n in pf.SECTION_ORDER}
    for _ in range(10):
        for n in pf.SECTION_ORDER:
            header["sections"][n]["offset"] = offsets[n]
        hb = json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
        base = 8 + 8 + len(hb)
        pos = base
        new = {}
        for n in pf.SECTION_ORDER:
            new[n] = pos
            pos += len(sections[n])
        if new == offsets:
            break
        offsets = new
    digest = hashlib.sha256()
    body = pf.MAGIC + pf.HEADER_LEN_STRUCT.pack(len(hb)) + hb
    for n in pf.SECTION_ORDER:
        body += bytes(sections[n])
    digest.update(body)
    with open(path, "wb") as f:
        f.write(body)
        f.write(pf.FOOTER_MAGIC)
        f.write(digest.digest())


def mutate_arc_target(header: dict, sections: dict, arc_index: int, new_target: int):
    arcs = sections["arcs"]
    struct.pack_into("<I", arcs, arc_index * pf.ARC_SIZE + 4, new_target)


class DictTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dicttool-test-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
