"""prefix(prefix, k): ranking, ties, edge cases, per-k cross-checks."""

import random
import unittest

from helpers import DictTestCase, build_dict, brute_prefix

from dicttool.api import Dictionary

# explicit code points to avoid any source-encoding ambiguity
PRECOMPOSED = "\u00e9"        # precomposed e-acute
DECOMPOSED = "é"        # e + combining acute
CAPITAL_E = "\u00c9"            # capital E-acute
EMOJI = "\U0001F600"          # grinning face


class TestPrefix(DictTestCase):
    def test_weight_desc_then_codepoint_asc(self):
        rows = [("b", 5), ("a", 5), ("c", 5), ("d", 7)]
        out, _ = build_dict(self.tmp, rows)
        with Dictionary.open(out) as d:
            # equal weights -> ascending code points
            self.assertEqual(d.prefix("", 4),
                             [("d", 7), ("a", 5), ("b", 5), ("c", 5)])

    def test_only_complete_words_with_prefix(self):
        rows = [("abc", 3), ("abd", 9), ("ab", 1), ("b", 100), ("abcd", 2)]
        out, _ = build_dict(self.tmp, rows)
        with Dictionary.open(out) as d:
            self.assertEqual(d.prefix("ab", 10),
                             [("abd", 9), ("abc", 3), ("abcd", 2), ("ab", 1)])
            self.assertEqual(d.prefix("abc", 10), [("abc", 3), ("abcd", 2)])
            self.assertEqual(d.prefix("x", 10), [])

    def test_k_zero_and_empty_prefix(self):
        rows = [("a", 1), ("b", 2)]
        out, _ = build_dict(self.tmp, rows)
        with Dictionary.open(out) as d:
            self.assertEqual(d.prefix("a", 0), [])
            self.assertEqual(d.prefix("", 0), [])
            stats = {}
            d.prefix("", 0, stats=stats)
            self.assertEqual(stats["stopped_by"], "k=0")
            # empty prefix = top-k of the whole dictionary
            self.assertEqual(d.prefix("", 1), [("b", 2)])

    def test_unicode_ordering_is_by_codepoint(self):
        # code points: U+0065 'e' < U+00C9 < U+00E9 < U+1F600.
        # No case/normalisation folding: the precomposed U+00E9 and the
        # decomposed sequence U+0065 U+0301 are different words and must
        # both survive.
        self.assertNotEqual(PRECOMPOSED, DECOMPOSED)
        rows = [(PRECOMPOSED, 1), (CAPITAL_E, 1), (EMOJI, 1), (DECOMPOSED, 1)]
        out, _ = build_dict(self.tmp, rows)
        with Dictionary.open(out) as d:
            got = d.prefix("", 4)
            self.assertEqual([w for w, _ in got],
                             [DECOMPOSED, CAPITAL_E, PRECOMPOSED, EMOJI])
            # prefix "e" matches only the decomposed word
            self.assertEqual(d.prefix("e", 4), [(DECOMPOSED, 1)])
            self.assertEqual(d.prefix(CAPITAL_E, 4), [(CAPITAL_E, 1)])
            # no normalisation folding in either direction
            self.assertEqual(d.prefix(PRECOMPOSED, 4), [(PRECOMPOSED, 1)])

    def test_long_prefix(self):
        long_head = "præfix-" * 30  # 210 code points
        rows = [(long_head + "a", 1), (long_head + "b", 2), ("other", 3)]
        out, _ = build_dict(self.tmp, rows)
        with Dictionary.open(out) as d:
            self.assertEqual(d.prefix(long_head, 2),
                             [(long_head + "b", 2), (long_head + "a", 1)])
            self.assertEqual(d.prefix(long_head + "a", 2), [(long_head + "a", 1)])

    def test_per_k_against_full_enumeration(self):
        rng = random.Random(20240101)
        alphabet = "abcde" + PRECOMPOSED + EMOJI
        words = set()
        while len(words) < 400:
            words.add("".join(rng.choice(alphabet)
                              for _ in range(rng.randrange(1, 6))))
        rows = [(w, rng.randrange(0, 50)) for w in sorted(words)]
        out, _ = build_dict(self.tmp, rows)
        entries = sorted(rows)
        with Dictionary.open(out) as d:
            for prefix in ["", "a", "ab", PRECOMPOSED, EMOJI, "zzz", "abcab"]:
                full = brute_prefix(entries, prefix, len(entries))
                for k in range(0, min(len(full), 12) + 1):
                    self.assertEqual(d.prefix(prefix, k), full[:k],
                                     f"prefix={prefix!r} k={k}")

    def test_same_state_different_words_ranking(self):
        """All words below share the terminal sink state; ranking ranges
        must still separate them exactly."""
        rows = [("ab", 9), ("cb", 100), ("db", 50), ("eb", 1)]
        out, _ = build_dict(self.tmp, rows)
        with Dictionary.open(out) as d:
            terms = {d.explain(w)["terminal_state"] for w, _ in rows}
            self.assertEqual(len(terms), 1)  # one shared terminal state
            entries = sorted(rows)
            full = brute_prefix(entries, "", len(rows))
            for k in range(0, 5):
                self.assertEqual(d.prefix("", k), full[:k])
            self.assertEqual(d.prefix("", 4),
                             [("cb", 100), ("db", 50), ("ab", 9), ("eb", 1)])


if __name__ == "__main__":
    unittest.main()
