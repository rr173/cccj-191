"""Binary package format (single self-contained file).

Layout:

    offset 0   MAGIC        8 bytes   b"MDAWG001"
    offset 8   header_len   u64 LE
    offset 16  header       header_len bytes of UTF-8 JSON
    ...        sections     concatenated in the fixed order
                            nodes | arcs | index | entries
    ...        FOOTER_MAGIC 8 bytes   b"MDAWGEND"
    ...        digest       32 bytes  sha256 of every byte before FOOTER_MAGIC

The header JSON carries counts, section offsets/lengths and a per-section
sha256.  The footer is written last and the file is atomically renamed
into place, so:

- a truncated file fails (size mismatch vs. header-declared layout, or
  missing footer);
- an unfinished build fails (no footer / digest mismatch);
- bit corruption fails (digest or per-section hash mismatch).

Node record (20 bytes, little-endian):
    u32 arc_start | u32 arc_count | u8 flags | 3 pad | u64 max_weight
    flags bit 0 = terminal

Arc record (8 bytes):
    u32 label (Unicode code point) | u32 target node id

Index section: (word_count + 1) x u64 offsets into the entries blob.

Entries blob records:
    u32 byte_len | utf-8 bytes | u64 weight
Entries are sorted by word in code-point order (== UTF-8 byte order), so
weights are retrievable by binary search without loading the table.
"""

from __future__ import annotations

import struct

MAGIC = b"MDAWG001"
FOOTER_MAGIC = b"MDAWGEND"
FOOTER_LEN = len(FOOTER_MAGIC) + 32
HEADER_LEN_STRUCT = struct.Struct("<Q")

NODE_STRUCT = struct.Struct("<IIB3xQ")
NODE_SIZE = NODE_STRUCT.size  # 20
FLAG_TERMINAL = 0x01

ARC_STRUCT = struct.Struct("<II")
ARC_SIZE = ARC_STRUCT.size  # 8

INDEX_STRUCT = struct.Struct("<Q")
INDEX_SIZE = INDEX_STRUCT.size

ENTRY_LEN_STRUCT = struct.Struct("<I")
ENTRY_WEIGHT_STRUCT = struct.Struct("<Q")

SECTION_ORDER = ("nodes", "arcs", "index", "entries")

MAX_HEADER_LEN = 1 << 20  # 1 MiB header is far more than enough

MAX_NODE_COUNT = (1 << 32) - 1
MAX_ARC_COUNT = (1 << 32) - 1
