"""dicttool - minimal acyclic deterministic dictionary automaton with
separately stored word weights, external-sort build, and bounded top-k
prefix / Levenshtein-suggest queries.

Public API:
    from dicttool.api import Dictionary
    d = Dictionary.open("dict.mdawg")
    d.prefix("ab", 10)
    d.suggest("ab", 2, 10)
    d.close()
"""

__version__ = "0.1.0"

from .errors import (
    DictToolError,
    ValidationError,
    PackageError,
    TruncatedPackageError,
    CyclicGraphError,
    BadReferenceError,
    IncompleteBuildError,
    FrontierOverflowError,
)

__all__ = [
    "__version__",
    "DictToolError",
    "ValidationError",
    "PackageError",
    "TruncatedPackageError",
    "CyclicGraphError",
    "BadReferenceError",
    "IncompleteBuildError",
    "FrontierOverflowError",
]
