"""Bounded-search evidence: a huge high-fanout low-weight branch must
not be enumerated when the ranking upper bound already decides top-1.
Also: frontier quota behaviour and stats honesty."""

import unittest

from helpers import DictTestCase, build_dict

from dicttool.api import Dictionary
from dicttool.errors import FrontierOverflowError

FANOUT = 50_000


class TestFanoutBound(DictTestCase):
    @classmethod
    def setUpClass(cls):
        # one heavy word, plus a huge fanout of weight-1 words under 'q'
        import tempfile
        cls._tmp = tempfile.mkdtemp(prefix="dicttool-fanout-")
        rows = [("zzz", 10 ** 9)] + [(f"q{i:06d}", 1) for i in range(FANOUT)]
        cls.pkg, cls.stats = build_dict(cls._tmp, rows)

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_build_compresses_fanout(self):
        # q000000..q049999 share almost all suffix structure
        self.assertLess(self.stats.node_count, 3000)
        self.assertEqual(self.stats.unique_words, FANOUT + 1)

    def test_prefix_top1_stops_via_bound(self):
        with Dictionary.open(self.pkg) as d:
            stats = {}
            got = d.prefix("", 1, stats=stats)
            self.assertEqual(got, [("zzz", 10 ** 9)])
            # the bound let us stop after a handful of expansions; we did
            # NOT walk the q-branch (50k words) nor read its weights
            self.assertLessEqual(stats["expansions"], 10)
            self.assertLessEqual(stats["weight_lookups"], 2)
            self.assertLessEqual(stats["arcs_scanned"], 20)
            self.assertEqual(stats["stopped_by"], "k")

    def test_prefix_inside_fanout_is_exact_but_bounded(self):
        with Dictionary.open(self.pkg) as d:
            stats = {}
            got = d.prefix("q0123", 3, stats=stats)
            self.assertEqual([w for w, _ in got],
                             ["q012300", "q012301", "q012302"])
            # only the digit states below "q0123" were touched
            self.assertLessEqual(stats["expansions"], 40)

    def test_suggest_top1_stops_via_bound(self):
        with Dictionary.open(self.pkg) as d:
            stats = {}
            got = d.suggest("zzz", 0, 1, stats=stats)
            self.assertEqual(got, [("zzz", 10 ** 9, 0)])
            self.assertLessEqual(stats["expansions"], 10)
            # the whole q-branch is pruned by the distance row minimum
            self.assertGreaterEqual(stats["pruned_by_distance"], 1)
            self.assertLessEqual(stats["weight_lookups"], 2)

    def test_frontier_quota_is_enforced_loudly(self):
        with Dictionary.open(self.pkg) as d:
            # an absurdly small quota must fail loudly, never silently
            # drop candidates
            with self.assertRaises(FrontierOverflowError):
                d.prefix("", 5, max_frontier=3)
            # with a sane quota the same query succeeds
            self.assertEqual(len(d.prefix("", 5, max_frontier=1000)), 5)


if __name__ == "__main__":
    unittest.main()
