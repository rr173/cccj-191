"""Validation of source JSONL and query arguments."""

import json
import os
import unittest

from helpers import DictTestCase, write_jsonl

from dicttool.api import Dictionary
from dicttool.builder import build
from dicttool.errors import ValidationError


class TestValidate(DictTestCase):
    def _build_raw(self, lines):
        src = os.path.join(self.tmp, "src.jsonl")
        with open(src, "w", encoding="utf-8") as f:
            for ln in lines:
                f.write(ln + "\n")
        with self.assertRaises(ValidationError):
            build(src, os.path.join(self.tmp, "out.mdawg"))

    def test_empty_word_rejected(self):
        self._build_raw([json.dumps({"word": "", "weight": 1})])

    def test_lone_surrogate_rejected(self):
        # a JSON escape that decodes to an unpaired surrogate
        self._build_raw(['{"word": "a\\ud800b", "weight": 1}'])
        self._build_raw(['{"word": "\\udfff", "weight": 1}'])

    def test_bad_weights_rejected(self):
        self._build_raw([json.dumps({"word": "a", "weight": -1})])
        self._build_raw([json.dumps({"word": "a", "weight": 1.5})])
        self._build_raw([json.dumps({"word": "a", "weight": "3"})])
        self._build_raw([json.dumps({"word": "a", "weight": True})])
        self._build_raw([json.dumps({"word": "a", "weight": None})])
        self._build_raw([json.dumps({"word": "a", "weight": 1 << 64})])

    def test_malformed_lines_rejected(self):
        self._build_raw(["not json"])
        self._build_raw([json.dumps(["a", 1])])           # not an object
        self._build_raw([json.dumps({"word": "a"})])       # missing weight
        self._build_raw([json.dumps({"weight": 1})])       # missing word
        self._build_raw([json.dumps({"word": 5, "weight": 1})])
        self._build_raw([""])                              # blank line

    def test_error_message_has_line_number(self):
        src = os.path.join(self.tmp, "src.jsonl")
        with open(src, "w") as f:
            f.write('{"word": "ok", "weight": 1}\n')
            f.write('{"word": "", "weight": 1}\n')
        try:
            build(src, os.path.join(self.tmp, "o.mdawg"))
            self.fail("expected ValidationError")
        except ValidationError as exc:
            self.assertIn("line 2", str(exc))

    def test_max_word_len_enforced(self):
        src = os.path.join(self.tmp, "src.jsonl")
        write_jsonl(src, [("x" * 300, 1)])
        with self.assertRaises(ValidationError):
            build(src, os.path.join(self.tmp, "o.mdawg"), max_word_len=256)
        # but configurable
        out = os.path.join(self.tmp, "ok.mdawg")
        build(src, out, max_word_len=512)
        with Dictionary.open(out) as d:
            self.assertEqual(d.word_count, 1)

    def test_query_argument_validation(self):
        from helpers import build_dict
        out, _ = build_dict(self.tmp, [("a", 1)])
        with Dictionary.open(out) as d:
            with self.assertRaises(ValidationError):
                d.prefix("a", -1)
            with self.assertRaises(ValidationError):
                d.suggest("a", 3, 5)       # max_edits out of range
            with self.assertRaises(ValidationError):
                d.suggest("a", -1, 5)
            with self.assertRaises(ValidationError):
                d.prefix("x" * 300, 1)     # longer than max_word_len
            with self.assertRaises(ValidationError):
                d.suggest("a\ud800", 1, 5)  # lone surrogate in query


if __name__ == "__main__":
    unittest.main()
