#!/usr/bin/env python3
"""Deterministic scale word-list generator (no third-party deps).

Generates a JSONL dictionary exercising the features that matter for
this project: shared prefixes, shared suffixes, duplicate words (to be
summed), unicode (combining marks, emoji, CJK), and skewed weights.

Usage:
    python3 tools/gen_words.py --n 1000000 --seed 7 --out /tmp/words.jsonl

The output is deterministic for a given (n, seed).  Generated files are
build inputs, not repository content - do not commit them.
"""

import argparse
import json
import random
import sys

HEADS = ["data", "clean", "dict", "word", "term", "entity", "record",
         "标准", "清洗", "词", "用户", "catalog", "suffix", "prefix"]
MIDS = ["_", "-", ".", ""]
SUFFIXES = ["tion", "ing", "er", "ly", "ment", "ness", "able", "al",
            "_v1", "_v2", "_final", "_tmp", "001", "01"]
UNICODE_BITS = ["é", "é", "😀", "🎉", "中", "文", "ü", "ø"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, required=True, help="number of lines")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dup-every", type=int, default=17,
                    help="every Nth line is a duplicate of a previous word")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    emitted: list[str] = []
    n = args.n
    with open(args.out, "w", encoding="utf-8") as f:
        for i in range(n):
            if emitted and i % args.dup_every == 0:
                word = emitted[rng.randrange(len(emitted))]
            else:
                parts = [rng.choice(HEADS)]
                if rng.random() < 0.7:
                    parts.append(rng.choice(MIDS))
                    parts.append(rng.choice(HEADS))
                if rng.random() < 0.5:
                    parts.append(rng.choice(SUFFIXES))
                if rng.random() < 0.15:
                    parts.append(str(rng.randrange(10 ** 6)))
                if rng.random() < 0.05:
                    parts.append(rng.choice(UNICODE_BITS))
                word = "".join(parts)
            emitted.append(word)
            # zipf-ish skew: most weights small, a few huge
            r = rng.random()
            weight = 1 if r < 0.8 else (rng.randrange(1, 100) if r < 0.98
                                        else rng.randrange(10 ** 6, 10 ** 9))
            f.write(json.dumps({"word": word, "weight": weight},
                               ensure_ascii=False) + "\n")
    print(f"wrote {n} lines to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
