"""External sort of (word, weight) entries with duplicate summation.

The builder must not hold the whole dictionary in memory: input lines are
validated and accumulated into in-memory *runs* bounded by a byte budget,
each run is sorted (by Unicode code point, which is exactly Python's str
ordering and also the byte ordering of UTF-8) and flushed to a temporary
file; the runs are then k-way merged with a heap, summing the weights of
identical words across runs.

Run file record layout (little-endian):
    u32 byte_len | utf-8 bytes | u64 weight
"""

from __future__ import annotations

import heapq
import os
import struct
import tempfile
from dataclasses import dataclass, field
from typing import Callable, Iterator, Optional

from .errors import ValidationError
from .validate import MAX_WEIGHT

_REC_LEN = struct.Struct("<I")
_REC_WEIGHT = struct.Struct("<Q")

#: Per-entry memory estimate used for the run budget: CPython str header
#: plus dict slot amortisation.  Documented as a heuristic, not a guarantee.
_ENTRY_OVERHEAD = 128


@dataclass
class SortStats:
    input_lines: int = 0
    unique_words: int = 0
    duplicates_summed: int = 0
    runs: int = 0
    max_word_len: int = 0
    run_bytes_written: int = 0


class _RunWriter:
    def __init__(self, tmp_dir: Optional[str]):
        fd, self.path = tempfile.mkstemp(prefix="dicttool-run-", suffix=".bin", dir=tmp_dir)
        self.f = os.fdopen(fd, "wb")
        self.bytes_written = 0

    def write(self, word: str, weight: int) -> None:
        data = word.encode("utf-8")
        self.f.write(_REC_LEN.pack(len(data)))
        self.f.write(data)
        self.f.write(_REC_WEIGHT.pack(weight))
        self.bytes_written += 8 + len(data)

    def close(self) -> None:
        self.f.close()


class _RunReader:
    __slots__ = ("f", "path")

    def __init__(self, path: str):
        self.path = path
        self.f = open(path, "rb")

    def read_next(self) -> Optional[tuple[str, int]]:
        head = self.f.read(4)
        if not head:
            return None
        (n,) = _REC_LEN.unpack(head)
        data = self.f.read(n)
        (w,) = _REC_WEIGHT.unpack(self.f.read(8))
        # strict UTF-8: our own writer only produced valid scalar values.
        return data.decode("utf-8", "strict"), w

    def close(self) -> None:
        self.f.close()


def external_sort_entries(
    entries: Iterator[tuple[str, int]],
    mem_budget: int,
    tmp_dir: Optional[str] = None,
) -> tuple[Iterator[tuple[str, int]], SortStats, Callable[[], None]]:
    """Sort (word, weight) pairs by word (code-point order), summing the
    weights of duplicate words.

    ``entries`` yields already-validated pairs.  Returns
    ``(merged_iterator, stats, cleanup)``; the caller must consume the
    iterator fully and then call ``cleanup()`` to remove temp run files
    (or call cleanup() in a finally block to abort).
    """
    stats = SortStats()
    run_paths: list[str] = []
    bucket: dict[str, int] = {}
    bucket_bytes = 0

    def flush() -> None:
        nonlocal bucket, bucket_bytes
        if not bucket:
            return
        writer = _RunWriter(tmp_dir)
        for word in sorted(bucket):
            writer.write(word, bucket[word])
        writer.close()
        stats.run_bytes_written += writer.bytes_written
        run_paths.append(writer.path)
        stats.runs += 1
        bucket = {}
        bucket_bytes = 0

    for word, weight in entries:
        stats.input_lines += 1
        if len(word) > stats.max_word_len:
            stats.max_word_len = len(word)
        prev = bucket.get(word)
        if prev is None:
            bucket[word] = weight
            bucket_bytes += len(word.encode("utf-8")) + _ENTRY_OVERHEAD
        else:
            total = prev + weight
            if total > MAX_WEIGHT:
                raise ValidationError(
                    f"weight sum for word {word!r} overflows uint64"
                )
            bucket[word] = total
            stats.duplicates_summed += 1
        if bucket_bytes >= mem_budget:
            flush()
    flush()

    readers = [_RunReader(p) for p in run_paths]

    def cleanup() -> None:
        for r in readers:
            try:
                r.close()
            except Exception:
                pass
        for p in run_paths:
            try:
                os.unlink(p)
            except OSError:
                pass

    def merged() -> Iterator[tuple[str, int]]:
        heap: list[tuple[str, int, int]] = []  # (word, weight, run_idx)
        for idx, r in enumerate(readers):
            first = r.read_next()
            if first is not None:
                heap.append((first[0], first[1], idx))
        heapq.heapify(heap)
        cur_word: Optional[str] = None
        cur_weight = 0
        have_cur = False
        while heap:
            word, weight, idx = heapq.heappop(heap)
            nxt = readers[idx].read_next()
            if nxt is not None:
                heapq.heappush(heap, (nxt[0], nxt[1], idx))
            if have_cur and word == cur_word:
                cur_weight += weight
                if cur_weight > MAX_WEIGHT:
                    raise ValidationError(
                        f"weight sum for word {word!r} overflows uint64"
                    )
                stats.duplicates_summed += 1
            else:
                if have_cur:
                    stats.unique_words += 1
                    yield cur_word, cur_weight  # type: ignore[misc]
                cur_word, cur_weight = word, weight
                have_cur = True
        if have_cur:
            stats.unique_words += 1
            yield cur_word, cur_weight  # type: ignore[misc]

    return merged(), stats, cleanup
