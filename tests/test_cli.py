"""CLI end-to-end: build from source, reopen the package, query, dump,
diff against the canonical expectation, verify against brute force."""

import json
import os
import subprocess
import sys
import unittest

from helpers import DictTestCase, write_jsonl

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_cli(*args, expect=0):
    proc = subprocess.run(
        [sys.executable, "-m", "dicttool", *args],
        cwd=REPO, capture_output=True, text=True)
    if proc.returncode != expect:
        raise AssertionError(
            f"cli {args} -> rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}")
    return proc


class TestCli(DictTestCase):
    def setUp(self):
        super().setUp()
        self.rows = [("ab", 9), ("cb", 100), ("a", 1), ("aa", 2),
                     ("ab", 1), ("z", 3)]
        self.src = os.path.join(self.tmp, "src.jsonl")
        write_jsonl(self.src, self.rows)
        self.pkg = os.path.join(self.tmp, "d.mdawg")

    def test_build_query_dump_diff_verify(self):
        # build
        run_cli("build", "--src", self.src, "--out", self.pkg)
        self.assertTrue(os.path.exists(self.pkg))

        # prefix query from a freshly opened package
        proc = run_cli("prefix", "--dict", self.pkg, "--prefix", "a",
                       "--k", "3", "--stats")
        got = [json.loads(ln) for ln in proc.stdout.splitlines()]
        self.assertEqual([(r["word"], r["weight"]) for r in got],
                         [("ab", 10), ("aa", 2), ("a", 1)])
        self.assertIn('"expansions"', proc.stderr)

        # suggest
        proc = run_cli("suggest", "--dict", self.pkg, "--text", "ab",
                       "--max-edits", "1", "--k", "4")
        got = [json.loads(ln) for ln in proc.stdout.splitlines()]
        self.assertEqual(got[0], {"word": "ab", "weight": 10, "distance": 0})
        self.assertEqual(got[1]["word"], "cb")  # weight 100 but distance 1
        self.assertEqual(got[1]["distance"], 1)

        # dump + diff against independently computed canonical form
        canon = os.path.join(self.tmp, "canon.jsonl")
        run_cli("dump", "--dict", self.pkg, "--out", canon)
        expect = {}
        for w, wt in self.rows:
            expect[w] = expect.get(w, 0) + wt
        want_lines = [json.dumps({"word": w, "weight": expect[w]},
                                 ensure_ascii=False)
                      for w in sorted(expect)]
        with open(canon, encoding="utf-8") as f:
            got_lines = [ln.rstrip("\n") for ln in f]
        self.assertEqual(got_lines, want_lines)

        # verify: graph answers vs brute force over the canonical table
        run_cli("verify", "--dict", self.pkg, "--prefix", "a", "--k", "3")
        run_cli("verify", "--dict", self.pkg, "--text", "ab",
                "--max-edits", "2", "--k", "5")
        run_cli("verify", "--dict", self.pkg, "--random-queries", "20",
                "--k", "4")

        # check --deep
        proc = run_cli("check", "--dict", self.pkg, "--deep")
        report = json.loads(proc.stdout)["check"]
        self.assertEqual(report["structural"], "ok")
        self.assertEqual(report["max_weights"], "recomputed-equal")

    def test_cli_errors(self):
        run_cli("build", "--src", self.src, "--out", self.pkg)
        # suggest with max-edits outside 0..2 -> usage/validation error
        run_cli("suggest", "--dict", self.pkg, "--text", "a",
                "--max-edits", "3", "--k", "1", expect=2)
        # missing package
        run_cli("check", "--dict", self.pkg + ".nope", expect=3)
        # truncated package
        bad = os.path.join(self.tmp, "bad.mdawg")
        with open(self.pkg, "rb") as f, open(bad, "wb") as g:
            g.write(f.read(100))
        run_cli("check", "--dict", bad, expect=3)


if __name__ == "__main__":
    unittest.main()
