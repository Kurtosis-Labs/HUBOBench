from __future__ import annotations

"""
benchmarks/hash.py

Canonical problem-hash utility for HUBOBench.

Implements the problem_hash specification from
docs/hubobench/problem_schema.md §6 (serialisation rules).

Public API:
    compute_problem_hash(objective)   → 64-char hex string

problem_hash is the content address of an instance's polynomial and serves as
the instances-table PK (see data/encoding/instance_builder §4).

The hash input is exactly the canonical objective: the term list and the
constant. Two instances are the same iff they have the same
polynomial, where "the same polynomial" means the same set of (coef, vars)
terms and the same constant offset.
"""

import hashlib
import json
from typing import Any


# ---------------------------------------------------------------------------
# §0. Serialisation primitive
# ---------------------------------------------------------------------------


def _canonical_json(obj: Any) -> str:
    """Serialize obj to the canonical JSON string for hashing.

    Rules (problem_schema.md §6.3):
    - Float: Python 3.7+ json.dumps uses the same algorithm as repr(float)
             (shortest decimal that round-trips); no further processing needed.
    - Integer: plain decimal, no leading zeros (json.dumps default).
    - JSON: sort_keys=True, separators=(',', ':'), ensure_ascii=True.
    - nan/inf: not permitted in the schema; allow_nan=False raises ValueError
               if any are present in the object tree.

    Args:
        obj: any JSON-serialisable Python object.

    Returns:
        Compact canonical JSON string.

    Raises:
        ValueError: if obj contains float nan or inf.
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _sha256(text: str) -> str:
    """SHA-256 of a UTF-8 string, returned as 64-char lowercase hex digest."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash_obj(obj: Any) -> str:
    """Canonical JSON → UTF-8 → SHA-256 → lowercase hex.  One-liner helper."""
    return _sha256(_canonical_json(obj))


# ---------------------------------------------------------------------------
# §1. problem_hash
# ---------------------------------------------------------------------------


def compute_problem_hash(objective: dict[str, Any]) -> str:
    """Compute the canonical problem hash from an objective block.

    The input is the canonical objective: the `terms` list and the scalar
    `constant`. This is exactly the shape stored in instances.objective_json,
    so the hash can be recomputed from the stored blob alone with no other
    columns.

    Spec: problem_schema.md §6.1 and §6.3.

    Input shape hashed (the objective_json blob):
        {
            "terms":    [{"coef": <float>, "vars": [<int>, ...]}, ...],
            "constant": <float>
        }

    Identity semantics:
        Two objectives hash equal iff they have the same constant and the same
        multiset of (coef, vars) terms. Variable count and domain are not part
        of identity (see module docstring).

    Canonicalisation applied here (so callers need not pre-normalise):
        - Indices within each term sorted ascending.
        - Terms sorted by (degree asc, vars lex asc).
        - coef coerced to float, vars to int.
        - Key order within each term fixed by sort_keys (coef before vars).
      The generator already emits terms in this order; the sort is a defensive
      idempotent no-op for in-house instances and a correctness guarantee for
      any externally supplied objective that is not pre-sorted.

    Args:
        objective: dict with "terms" (list of {"coef", "vars"}) and "constant".
            Accepts the parsed instances.objective_json blob directly.

    Returns:
        64-character lowercase hex SHA-256 digest.

    Raises:
        KeyError: if "terms" is absent.
        ValueError: if any coefficient is nan or inf.
    """
    terms = objective["terms"]
    constant = float(objective.get("constant", 0.0))

    # Normalise each term: sort indices ascending, coerce types.
    normalised_terms = [
        {"coef": float(t["coef"]), "vars": sorted(int(v) for v in t["vars"])}
        for t in terms
    ]

    # Sort the term list canonically: (degree, vars-lexicographic).
    normalised_terms.sort(key=lambda t: (len(t["vars"]), t["vars"]))

    canonical = {
        "terms":    normalised_terms,
        "constant": constant,
    }

    return _hash_obj(canonical)