from __future__ import annotations

"""
benchmarks/hash.py

Canonical problem-hash utility for HUBOBench.

Implements the problem_hash specification from
docs/hubobench/problem_schema.md §9 (serialisation rules).

Public API:
    compute_problem_hash(instance)   → 64-char hex string

problem_hash is the content address of an instance's polynomial and serves as
the instances-table PK (see data/encoding/instance_builder §4). Corpus-integrity
verification — re-derive each stored row's hash and confirm it matches the PK —
lives in benchmarks/verify_corpus.py, which reuses compute_problem_hash.
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


def compute_problem_hash(instance: dict[str, Any]) -> str:
    """Compute the canonical problem hash from a problem instance.

    Hashes only the minimum data needed to reconstruct the problem:
    the canonicalised objective polynomial and variable parameters.
    Metadata (instance_id, generator, diagnostics, rosenberg_reduction,
    ground_truth) is excluded — two instances are the same iff they have
    the same polynomial over the same variable set in the same domain.

    Spec: problem_schema.md §6.1 and §6.3.

    Input shape hashed:
        {
            "objective": {
                "sense":    "minimize",
                "constant": <float>,
                "terms":    [{"coef": <float>, "vars": [<int>, ...]}, ...]
            },
            "parameters": {
                "n_variables":     <int>,
                "variable_domain": "binary_01"
            }
        }

    Terms are sorted by (degree asc, vars lex asc) — canonical order per
    problem_schema.md §5. Keys within each term are sorted alphabetically
    by json.dumps(sort_keys=True): "coef" before "vars".

    Args:
        instance: canonical problem instance (problem_schema.md).

    Returns:
        64-character lowercase hex SHA-256 digest.
    """
    objective  = instance["objective"]
    parameters = instance["parameters"]

    # Re-sort terms defensively: canonical schema mandates this order but a
    # freshly received instance from an external generator may not guarantee it.
    terms_sorted = sorted(
        objective["terms"],
        key=lambda t: (len(t["vars"]), t["vars"]),
    )

    canonical = {
        "objective": {
            "sense":    str(objective["sense"]),
            "constant": float(objective.get("constant", 0.0)),
            "terms": [
                {"coef": float(t["coef"]), "vars": [int(v) for v in t["vars"]]}
                for t in terms_sorted
            ],
        },
        "parameters": {
            "n_variables":     int(parameters["n_variables"]),
            "variable_domain": str(parameters["variable_domain"]),
        },
    }

    return _hash_obj(canonical)
