"""suggest(text, max_edits, k): distances, ranking, ties, edge cases."""

import random
import unittest

from helpers import DictTestCase, build_dict, brute_suggest, levenshtein

from dicttool.api import Dictionary

PRECOMPOSED = "\u00e9"
DECOMPOSED = "é"        # e + combining acute
EMOJI = "\U0001F600"


class TestSuggestSpec(DictTestCase):
    def setUp(self):
        super().setUp()
        # the spec's canonical small dictionary: ab/cb share suffix "b"
        # with weights 9/100, plus a/aa, combining characters and emoji
        rows = [
            ("ab", 9), ("cb", 100),
            ("a", 1), ("aa", 2),
            (PRECOMPOSED, 10), (DECOMPOSED, 20),
            (EMOJI, 7), (EMOJI + EMOJI, 8),
        ]
        self.out, _ = build_dict(self.tmp, rows)
        self.rows = rows

    def test_threshold_zero_returns_only_exact(self):
        with Dictionary.open(self.out) as d:
            self.assertEqual(d.suggest("ab", 0, 10), [("ab", 9, 0)])
            self.assertEqual(d.suggest("ac", 0, 10), [])
            self.assertEqual(d.suggest(PRECOMPOSED, 0, 10),
                             [(PRECOMPOSED, 10, 0)])

    def test_distance_zero_beats_higher_weight(self):
        """text=ab, 1 edit: cb (weight 100, distance 1) must rank AFTER
        ab (weight 9, distance 0)."""
        with Dictionary.open(self.out) as d:
            got = d.suggest("ab", 1, 10)
        self.assertEqual(got[0], ("ab", 9, 0))
        cb_pos = next(i for i, r in enumerate(got) if r[0] == "cb")
        self.assertEqual(got[cb_pos], ("cb", 100, 1))
        self.assertGreater(cb_pos, 0)
        # full expected order: d0: ab; d1 by weight: cb(100), aa(2), a(1)
        self.assertEqual(got, [("ab", 9, 0), ("cb", 100, 1),
                               ("aa", 2, 1), ("a", 1, 1)])

    def test_equal_distance_equal_weight_codepoint_tie(self):
        rows = [("x", 5), ("y", 5), ("z", 5)]
        out, _ = build_dict(self.tmp, rows)
        with Dictionary.open(out) as d:
            # all at distance 1 from "w", equal weights -> code points
            self.assertEqual(d.suggest("w", 1, 3),
                             [("x", 5, 1), ("y", 5, 1), ("z", 5, 1)])

    def test_empty_text_distances(self):
        """distance from the empty string is the word length; manual
        check of the empty-string corner."""
        rows = [("a", 1), ("bb", 2), ("ccc", 3)]
        out, _ = build_dict(self.tmp, rows)
        with Dictionary.open(out) as d:
            self.assertEqual(d.suggest("", 0, 10), [])
            self.assertEqual(d.suggest("", 1, 10), [("a", 1, 1)])
            self.assertEqual(d.suggest("", 2, 10),
                             [("a", 1, 1), ("bb", 2, 2)])
            # "ccc" needs 3 insertions: outside the supported threshold
            self.assertEqual(d.suggest("", 2, 0), [])

    def test_combining_chars_and_emoji_distinct(self):
        with Dictionary.open(self.out) as d:
            # precomposed vs decomposed are different words at distance 2
            self.assertEqual(
                levenshtein(PRECOMPOSED, DECOMPOSED), 2)
            got = d.suggest(PRECOMPOSED, 1, 10)
            # distance 0 ranks first; the decomposed form (distance 2)
            # must NOT appear at threshold 1
            self.assertEqual(got[0], (PRECOMPOSED, 10, 0))
            self.assertNotIn(DECOMPOSED, [w for w, _w, _d in got])
            got = d.suggest(PRECOMPOSED, 2, 10)
            self.assertIn((DECOMPOSED, 20, 2), got)
            # emoji are single code points
            self.assertEqual(d.suggest(EMOJI, 0, 10), [(EMOJI, 7, 0)])
            got = d.suggest(EMOJI, 1, 10)
            self.assertEqual(got[0], (EMOJI, 7, 0))
            self.assertIn((EMOJI + EMOJI, 8, 1), got)

    def test_k_zero(self):
        with Dictionary.open(self.out) as d:
            stats = {}
            self.assertEqual(d.suggest("ab", 2, 0, stats=stats), [])
            self.assertEqual(stats["stopped_by"], "k=0")

    def test_no_results(self):
        with Dictionary.open(self.out) as d:
            self.assertEqual(d.suggest("zzzzzz", 2, 5), [])


class TestSuggestCrossCheck(DictTestCase):
    def test_per_k_against_full_enumeration(self):
        rng = random.Random(77031)
        alphabet = "abc" + PRECOMPOSED + EMOJI
        words = set()
        while len(words) < 300:
            words.add("".join(rng.choice(alphabet)
                              for _ in range(rng.randrange(1, 5))))
        rows = [(w, rng.randrange(0, 30)) for w in sorted(words)]
        out, _ = build_dict(self.tmp, rows)
        entries = sorted(rows)
        texts = ["", "a", "ab", "abc", PRECOMPOSED, EMOJI, "cba", "aaa"]
        with Dictionary.open(out) as d:
            for text in texts:
                for edits in (0, 1, 2):
                    full = brute_suggest(entries, text, edits, len(entries))
                    for k in range(0, min(len(full), 10) + 1):
                        got = d.suggest(text, edits, k)
                        self.assertEqual(got, full[:k],
                                         f"text={text!r} e={edits} k={k}")

    def test_duplicate_weight_sum_affects_ranking(self):
        rows = [("ab", 9), ("ab", 1), ("cb", 100)]
        out, _ = build_dict(self.tmp, rows)
        with Dictionary.open(out) as d:
            # ab now weighs 10; still distance 0 first
            self.assertEqual(d.suggest("ab", 1, 2),
                             [("ab", 10, 0), ("cb", 100, 1)])


if __name__ == "__main__":
    unittest.main()
