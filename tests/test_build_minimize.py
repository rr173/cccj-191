"""Minimisation semantics and checkable state-equivalence evidence."""

import unittest

from helpers import DictTestCase, build_dict

from dicttool.api import Dictionary
from dicttool.reader import Package


class TestMinimisation(DictTestCase):
    def test_suffix_sharing_ab_cb(self):
        """ab/cb share suffix structure but stay distinct words with
        distinct weights (9 vs 100).

        In the minimal acceptor for the two-word language {ab, cb} the
        states after "a" and after "c" ALSO merge (both have right
        language {"b"}): the two words then traverse the very same state
        sequence.  Word identity is the arc *labels*, and the weights
        live only in the entries table - exactly the separation the spec
        demands ("共享后缀不意味着不同前缀是同一个词").
        """
        out, stats = build_dict(self.tmp, [("ab", 9), ("cb", 100)])
        with Dictionary.open(out) as d:
            ea = d.explain("ab")
            ec = d.explain("cb")
            self.assertTrue(ea["accepted"] and ec["accepted"])
            self.assertEqual(ea["terminal_state"], ec["terminal_state"])
            # word identity = label sequence; weights are separate
            self.assertEqual("".join(ea["labels"]), "ab")
            self.assertEqual("".join(ec["labels"]), "cb")
            self.assertEqual(ea["weight"], 9)
            self.assertEqual(ec["weight"], 100)
            # the shared terminal's max_weight bound covers both words
            self.assertEqual(ea["state_max_weight"], 100)
        # real sharing happened: trie would need 5 states, minimal = 3
        self.assertEqual(stats.trie_node_count, 5)
        self.assertEqual(stats.node_count, 3)
        self.assertGreaterEqual(stats.register_hits, 2)

    def test_prefix_states_distinct_when_right_languages_differ(self):
        """With a/aa also present, the states after "a" and "c" differ
        (different right languages) while the terminal sink is shared."""
        rows = [("a", 1), ("aa", 2), ("ab", 9), ("cb", 100)]
        out, _ = build_dict(self.tmp, rows)
        with Dictionary.open(out) as d:
            ea = d.explain("ab")
            ec = d.explain("cb")
            self.assertEqual(ea["terminal_state"], ec["terminal_state"])
            self.assertNotEqual(ea["states"], ec["states"])

    def test_common_suffixes_collapse(self):
        """Many words ending in the same suffix share the whole suffix
        chain - evidence this is right-language minimisation, not chain
        compression."""
        suffix = "ulation"
        words = [(w + suffix, i + 1) for i, w in enumerate(
            ["ab", "cd", "ef", "gh", "ij"])]
        out, stats = build_dict(self.tmp, words)
        # trie would need 1 + 5 * len(words) states; the minimal automaton
        # needs only one copy of the suffix chain plus the divergent heads
        self.assertLess(stats.node_count, stats.trie_node_count - len(suffix) * 3)
        with Dictionary.open(out) as d:
            paths = [d.explain(w)["states"] for w, _ in words]
            # all five words traverse the same suffix states
            tails = [tuple(p[-len(suffix):]) for p in paths]
            self.assertEqual(len(set(tails)), 1)
            # while their head states differ
            heads = {p[1] for p in paths}
            self.assertEqual(len(heads), 5)

    def test_minimality_audit_passes_and_is_checkable(self):
        rows = [("a", 1), ("aa", 2), ("ab", 9), ("cb", 100),
                ("cab", 5), ("cob", 5), ("z", 3)]
        out, _ = build_dict(self.tmp, rows)
        pkg = Package.open(out)
        report = pkg.audit_minimality()
        self.assertEqual(report["states"], pkg.node_count)
        self.assertGreater(report["shared_states(in_degree>1)"], 0)
        self.assertTrue(pkg.audit_max_weights())
        pkg.close()

    def test_max_weight_bounds_exact(self):
        """max_weight of a state equals the max weight of words through it."""
        rows = [("ab", 9), ("cb", 100), ("abc", 40), ("abd", 3)]
        out, _ = build_dict(self.tmp, rows)
        with Dictionary.open(out) as d:
            # "cb" ends in the shared terminal-sink state: only "cb" and
            # "abc"/"abd"... no - the sink is shared by words ending there;
            # the bound must equal the max over exactly those words.
            self.assertEqual(d.explain("cb")["state_max_weight"], 100)
            # state after prefix "a": words ab(9), abc(40), abd(3) -> 40
            pkg = d._pkg
            nid = pkg.start_node
            nid = pkg.child(nid, ord("a"))
            _s, _c, _t, mw = pkg.node(nid)
            self.assertEqual(mw, 40)
            # "ab" ends in a state that also has arcs to c/d, so its bound
            # covers ab/abc/abd but NOT cb
            self.assertEqual(d.explain("ab")["state_max_weight"], 40)

    def test_duplicate_words_weights_summed(self):
        out, stats = build_dict(self.tmp, [("ab", 9), ("ab", 1), ("ab", 5)])
        self.assertEqual(stats.unique_words, 1)
        self.assertEqual(stats.duplicates_summed, 2)
        with Dictionary.open(out) as d:
            self.assertEqual(d.lookup("ab"), 15)
            self.assertEqual(d.prefix("ab", 1), [("ab", 15)])

    def test_empty_dictionary_builds_and_answers_empty(self):
        out, stats = build_dict(self.tmp, [])
        self.assertEqual(stats.unique_words, 0)
        with Dictionary.open(out) as d:
            self.assertEqual(d.prefix("", 5), [])
            self.assertEqual(d.suggest("abc", 2, 5), [])


if __name__ == "__main__":
    unittest.main()
