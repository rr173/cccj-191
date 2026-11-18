"""Error hierarchy for dicttool.

Every failure mode required by the spec has a dedicated, catchable type:

- ValidationError        : bad input JSONL line or bad query argument
- PackageError           : base for any package-file problem
  - TruncatedPackageError: file shorter than header/footer declare
  - IncompleteBuildError : footer commit marker missing / hash mismatch
  - CyclicGraphError     : arc graph is not acyclic
  - BadReferenceError    : arc target / section offset out of range
- FrontierOverflowError  : query frontier exceeded the caller's memory quota
"""


class DictToolError(Exception):
    """Base class for all dicttool errors."""


class ValidationError(DictToolError):
    """Invalid source JSONL line or invalid query argument.

    The message always carries enough context (line number / argument name)
    for a data-cleaning operator to locate the problem.
    """


class PackageError(DictToolError):
    """Base class for package-file integrity problems."""


class TruncatedPackageError(PackageError):
    """The file is shorter than the structure declared in its header."""


class IncompleteBuildError(PackageError):
    """The commit footer is missing or the content hash does not match.

    A build writes the footer (magic + sha256 over everything before it)
    as its very last action and then atomically renames the file into
    place, so any file without a valid footer was never a finished build.
    """


class CyclicGraphError(PackageError):
    """The automaton contains a cycle; it is not a valid acyclic dictionary
    graph.  Detected by an iterative three-colour DFS at open/check time."""


class BadReferenceError(PackageError):
    """An arc target, section offset, or index entry points outside the
    valid range."""


class FrontierOverflowError(DictToolError):
    """The query frontier (candidate heap) exceeded the configured memory
    quota.  This is a deterministic, documented behaviour: re-run with a
    larger --max-frontier.  The query never silently drops candidates."""
