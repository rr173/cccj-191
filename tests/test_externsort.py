"""External sort: memory budget forces multiple disk runs; duplicates
must still be summed across runs and the merge must be exactly sorted."""

import random
import unittest

from helpers import DictTestCase, build_dict

from dicttool.api import Dictionary


class TestExternalSort(DictTestCase):
    def test_multi_run_merge_with_duplicates(self):
        rng = random.Random(99)
        words = [f"w{rng.randrange(2000):05d}" for _ in range(5000)]
        rows = [(w, rng.randrange(1, 10)) for w in words]
        # tiny budget -> many runs
        out, stats = build_dict(self.tmp, rows, mem_budget=4096)
        self.assertGreater(stats.sort_runs, 3)
        expect = {}
        for w, wt in rows:
            expect[w] = expect.get(w, 0) + wt
        with Dictionary.open(out) as d:
            self.assertEqual(d.word_count, len(expect))
            got = list(d.entries())
            self.assertEqual(got, sorted(expect.items()))
            # spot-check queries against the summed weights
            top = sorted(expect.items(), key=lambda t: (-t[1], t[0]))[:5]
            self.assertEqual(d.prefix("", 5), top)

    def test_unicode_across_runs(self):
        rows = [("\u00e9", 1), ("a", 1), ("\U0001F600", 1), ("e", 1),
                ("\u00e9", 2), ("\U0001F600", 3), ("\u4e2d", 4)]
        out, stats = build_dict(self.tmp, rows, mem_budget=200)
        self.assertGreater(stats.sort_runs, 1)
        expect = {"\u00e9": 3, "a": 1, "\U0001F600": 4, "e": 1, "\u4e2d": 4}
        with Dictionary.open(out) as d:
            self.assertEqual(list(d.entries()),
                             sorted(expect.items()))


if __name__ == "__main__":
    unittest.main()
