"""Validation of source JSONL entries and query arguments.

Rules (per spec):
- every line is a JSON object with "word" and "weight";
- "word" is a non-empty string, at most ``max_word_len`` Unicode code
  points, containing no lone surrogate code points (U+D800..U+DFFF);
  no case folding, no normalization - comparison is by code point;
- "weight" is a non-negative integer that fits in an unsigned 64-bit
  word; booleans and floats are rejected even when numerically integral;
- unknown extra keys are tolerated (forward compatibility) but the two
  required keys must be present and correctly typed.
"""

from __future__ import annotations

import json

from .errors import ValidationError

#: Hard default ceiling on word length, in code points.  The suggest DP
#: allocates rows of len(text)+1 cells and frontier paths are bounded by
#: len(text)+max_edits, so a clear bound on word length keeps every
#: query-time allocation predictable.  Configurable at build time.
DEFAULT_MAX_WORD_LEN = 256

#: Ceiling on the query text length for suggest, in code points.
MAX_QUERY_TEXT_LEN = 4096

#: Weights are stored as unsigned 64-bit integers.
MAX_WEIGHT = (1 << 64) - 1

#: Levenshtein thresholds supported by suggest().
MIN_EDITS = 0
MAX_EDITS = 2


def _has_lone_surrogate(s: str) -> bool:
    return any(0xD800 <= ord(ch) <= 0xDFFF for ch in s)


def validate_word(word, max_word_len: int, where: str) -> str:
    """Validate a dictionary word; return it unchanged if acceptable."""
    if not isinstance(word, str):
        raise ValidationError(f"{where}: 'word' must be a string, got {type(word).__name__}")
    if len(word) == 0:
        raise ValidationError(f"{where}: empty 'word' is not allowed")
    if len(word) > max_word_len:
        raise ValidationError(
            f"{where}: word of {len(word)} code points exceeds max_word_len={max_word_len}"
        )
    if _has_lone_surrogate(word):
        raise ValidationError(f"{where}: word contains a lone surrogate code point")
    return word


def validate_weight(weight, where: str) -> int:
    """Validate a non-negative integer weight; return it as int."""
    # bool is a subclass of int in Python; True/False are not valid weights.
    if isinstance(weight, bool) or not isinstance(weight, int):
        raise ValidationError(
            f"{where}: 'weight' must be a non-negative integer, got {weight!r}"
        )
    if weight < 0:
        raise ValidationError(f"{where}: negative weight {weight}")
    if weight > MAX_WEIGHT:
        raise ValidationError(f"{where}: weight {weight} exceeds uint64 range")
    return weight


def parse_line(line: str, lineno: int, max_word_len: int) -> tuple[str, int]:
    """Parse and validate one source JSONL line.

    Returns (word, weight).  Raises ValidationError with the line number.
    """
    where = f"line {lineno}"
    try:
        obj = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{where}: invalid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise ValidationError(f"{where}: expected a JSON object, got {type(obj).__name__}")
    if "word" not in obj:
        raise ValidationError(f"{where}: missing key 'word'")
    if "weight" not in obj:
        raise ValidationError(f"{where}: missing key 'weight'")
    word = validate_word(obj["word"], max_word_len, where)
    weight = validate_weight(obj["weight"], where)
    return word, weight


def validate_query_text(text, name: str, max_len: int) -> str:
    """Validate a query string (prefix or suggest text)."""
    if not isinstance(text, str):
        raise ValidationError(f"query argument {name!r} must be a string")
    if len(text) > max_len:
        raise ValidationError(
            f"query argument {name!r} has {len(text)} code points, limit is {max_len}"
        )
    if _has_lone_surrogate(text):
        raise ValidationError(f"query argument {name!r} contains a lone surrogate")
    return text


def validate_k(k) -> int:
    if isinstance(k, bool) or not isinstance(k, int):
        raise ValidationError(f"'k' must be an integer, got {k!r}")
    if k < 0:
        raise ValidationError(f"'k' must be >= 0, got {k}")
    return k


def validate_max_edits(max_edits) -> int:
    if isinstance(max_edits, bool) or not isinstance(max_edits, int):
        raise ValidationError(f"'max_edits' must be an integer, got {max_edits!r}")
    if not (MIN_EDITS <= max_edits <= MAX_EDITS):
        raise ValidationError(
            f"'max_edits' must be between {MIN_EDITS} and {MAX_EDITS}, got {max_edits}"
        )
    return max_edits
