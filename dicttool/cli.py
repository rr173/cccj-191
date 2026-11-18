"""Command-line interface.

    python -m dicttool build   --src words.jsonl --out dict.mdawg [...]
    python -m dicttool prefix  --dict dict.mdawg --prefix PRE --k K [--stats]
    python -m dicttool suggest --dict dict.mdawg --text T --max-edits E --k K
    python -m dicttool dump    --dict dict.mdawg --out canonical.jsonl
    python -m dicttool check   --dict dict.mdawg [--deep]
    python -m dicttool explain --dict dict.mdawg --word W [--word W2 ...]
    python -m dicttool verify  --dict dict.mdawg [--prefix P --k K]
                               [--text T --max-edits E] [--random-queries N]

Exit codes: 0 ok, 2 validation/usage error, 3 package integrity error,
4 frontier overflow.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time

from . import __version__
from .api import Dictionary
from .builder import build as build_package
from .errors import (
    DictToolError,
    FrontierOverflowError,
    PackageError,
    ValidationError,
)
from .query import DEFAULT_MAX_FRONTIER, QueryStats, prefix_search, suggest_search
from .reader import Package
from .validate import DEFAULT_MAX_WORD_LEN, parse_line
from .verify import brute_prefix, brute_suggest


def _print_json(obj, file=sys.stdout) -> None:
    file.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _emit_stats(stats: QueryStats, args) -> None:
    if getattr(args, "stats", False):
        _print_json({"stats": stats.as_dict()}, file=sys.stderr)


def cmd_build(args) -> int:
    t0 = time.monotonic()
    stats = build_package(
        args.src,
        args.out,
        mem_budget=args.mem_budget,
        tmp_dir=args.tmp_dir,
        max_word_len=args.max_word_len,
    )
    _print_json({"build": stats.as_dict()}, file=sys.stderr)
    print(f"built {args.out}: {stats.unique_words} words, "
          f"{stats.node_count} states (trie would need {stats.trie_node_count}), "
          f"{stats.arc_count} arcs, {stats.package_bytes} bytes, "
          f"{time.monotonic() - t0:.2f}s", file=sys.stderr)
    return 0


def _open_dict(args) -> Dictionary:
    return Dictionary.open(args.dict)


def cmd_prefix(args) -> int:
    with _open_dict(args) as d:
        stats = QueryStats()
        res = prefix_search(
            d._pkg, args.prefix, args.k, max_frontier=args.max_frontier, stats=stats
        )
        for word, weight in res:
            _print_json({"word": word, "weight": weight})
        _emit_stats(stats, args)
    return 0


def cmd_suggest(args) -> int:
    with _open_dict(args) as d:
        stats = QueryStats()
        res = suggest_search(
            d._pkg, args.text, args.max_edits, args.k,
            max_frontier=args.max_frontier, stats=stats,
        )
        for word, weight, dist in res:
            _print_json({"word": word, "weight": weight, "distance": dist})
        _emit_stats(stats, args)
    return 0


def cmd_dump(args) -> int:
    out = open(args.out, "w", encoding="utf-8") if args.out != "-" else sys.stdout
    try:
        with _open_dict(args) as d:
            for word, weight in d.entries():
                out.write(
                    json.dumps({"word": word, "weight": weight}, ensure_ascii=False)
                    + "\n"
                )
    finally:
        if out is not sys.stdout:
            out.close()
    return 0


def cmd_check(args) -> int:
    pkg = Package.open(args.dict)  # full structural validation already
    report = {
        "structural": "ok",
        "words": pkg.word_count,
        "states": pkg.node_count,
        "arcs": pkg.arc_count,
        "max_word_len": pkg.max_word_len,
    }
    if args.deep:
        report["minimality"] = pkg.audit_minimality()
        pkg.audit_max_weights()
        report["max_weights"] = "recomputed-equal"
    pkg.close()
    _print_json({"check": report})
    return 0


def cmd_explain(args) -> int:
    with _open_dict(args) as d:
        for word in args.word:
            _print_json({"explain": d.explain(word)})
    return 0


def cmd_verify(args) -> int:
    failures = 0
    with _open_dict(args) as d:
        entries = list(d.entries())  # canonical table, scanned linearly
        queries = []
        if args.prefix is not None:
            queries.append(("prefix", args.prefix, args.k))
        if args.text is not None:
            queries.append(("suggest", args.text, args.max_edits, args.k))
        rng = random.Random(args.seed)
        for _ in range(args.random_queries):
            if entries and rng.random() < 0.5:
                w = entries[rng.randrange(len(entries))][0]
                queries.append(("prefix", w[: rng.randrange(len(w) + 1)], args.k))
            else:
                base = entries[rng.randrange(len(entries))][0] if entries else ""
                queries.append(("suggest", base, rng.randrange(0, 3), args.k))
        for q in queries:
            if q[0] == "prefix":
                _, p, k = q
                got = d.prefix(p, k)
                want = brute_prefix(entries, p, k)
                ok = got == want
            else:
                _, t, e, k = q
                got = d.suggest(t, e, k)
                want = brute_suggest(entries, t, e, k)
                ok = got == want
            status = "PASS" if ok else "FAIL"
            if not ok:
                failures += 1
            _print_json({"verify": status, "query": q,
                         **({} if ok else {"got": got, "want": want})})
    print(f"verify: {len(queries) - failures}/{len(queries)} passed",
          file=sys.stderr)
    return 1 if failures else 0


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dicttool", description=__doc__)
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build a dictionary package from JSONL")
    b.add_argument("--src", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--mem-budget", type=int, default=256 << 20,
                   help="bytes for in-memory sort runs (default 256MiB)")
    b.add_argument("--tmp-dir", default=None)
    b.add_argument("--max-word-len", type=int, default=DEFAULT_MAX_WORD_LEN,
                   help="maximum word length in code points (default 256)")
    b.set_defaults(func=cmd_build)

    def add_query(sp):
        sp.add_argument("--dict", required=True)
        sp.add_argument("--k", type=int, required=True)
        sp.add_argument("--max-frontier", type=int, default=DEFAULT_MAX_FRONTIER)
        sp.add_argument("--stats", action="store_true",
                        help="print measured query statistics to stderr")

    pr = sub.add_parser("prefix", help="top-k words with a given prefix")
    add_query(pr)
    pr.add_argument("--prefix", required=True)
    pr.set_defaults(func=cmd_prefix)

    su = sub.add_parser("suggest", help="top-k words within 0..2 Levenshtein edits")
    add_query(su)
    su.add_argument("--text", required=True)
    su.add_argument("--max-edits", type=int, required=True, choices=(0, 1, 2))
    su.set_defaults(func=cmd_suggest)

    du = sub.add_parser("dump", help="dump canonical entries as sorted JSONL")
    du.add_argument("--dict", required=True)
    du.add_argument("--out", required=True, help="path or '-' for stdout")
    du.set_defaults(func=cmd_dump)

    ck = sub.add_parser("check", help="validate package integrity")
    ck.add_argument("--dict", required=True)
    ck.add_argument("--deep", action="store_true",
                    help="also audit minimality and max_weight bounds")
    ck.set_defaults(func=cmd_check)

    ex = sub.add_parser("explain", help="show accepting state paths (sharing evidence)")
    ex.add_argument("--dict", required=True)
    ex.add_argument("--word", action="append", required=True)
    ex.set_defaults(func=cmd_explain)

    ve = sub.add_parser("verify", help="cross-check graph queries against a "
                                       "brute-force scan of the canonical table")
    ve.add_argument("--dict", required=True)
    ve.add_argument("--prefix", default=None)
    ve.add_argument("--text", default=None)
    ve.add_argument("--max-edits", type=int, default=2, choices=(0, 1, 2))
    ve.add_argument("--k", type=int, default=10)
    ve.add_argument("--random-queries", type=int, default=0)
    ve.add_argument("--seed", type=int, default=1234)
    ve.set_defaults(func=cmd_verify)
    return p


def main(argv=None) -> int:
    args = make_parser().parse_args(argv)
    try:
        return args.func(args)
    except FrontierOverflowError as exc:
        print(f"frontier overflow: {exc}", file=sys.stderr)
        return 4
    except PackageError as exc:
        print(f"package error: {exc}", file=sys.stderr)
        return 3
    except OSError as exc:
        print(f"package error: {exc}", file=sys.stderr)
        return 3
    except (ValidationError, DictToolError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
