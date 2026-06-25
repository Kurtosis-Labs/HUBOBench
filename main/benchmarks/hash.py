from __future__ import annotations

"""
benchmarks/hash.py

Canonical hash utilities for HUBOBench.

Implements the hashing specification from:
- docs/hubobench/problem_schema.md   §9   (problem_hash, serialisation rules)
- docs/hubobench/solution_schema.md  §9   (solver_config_hash, solution_hash,
                                           solution_id)

Public API:
    fill_hashes(solution)            Fill all "pending" hashes in a canonical
                                     solution dict and set solution_id.
                                     Call after decode_response(), before saving.

    verify_problem_hash(instance)    Re-derive the problem hash from scratch
                                     and check it matches the stored value.
                                     Used by the evaluator at score time.

    compute_problem_hash(instance)   → 64-char hex string
    compute_solver_config_hash(ctx)  → 64-char hex string
    compute_solution_hash(...)       → 64-char hex string

Note on field name: the solver_io files emit "limits_dossier_version"
(plural). The solution schema §3.1 table uses "limit_dossier_version"
(singular). The two are inconsistent; fix the schema doc to use the plural
form so both match. This module uses whatever key the actual solution dict
contains (it reads solver_context directly), so it is resilient to whichever
spelling is in use.
"""

import hashlib
import json
from typing import Any


# ---------------------------------------------------------------------------
# §0. Serialisation primitive
# ---------------------------------------------------------------------------


def _canonical_json(obj: Any) -> str:
    """Serialize obj to the canonical JSON string for hashing.

    Rules (problem_schema.md §9.3, solution_schema.md §9.2):
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

    Spec: problem_schema.md §9.1 and §9.3.

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
    problem_schema.md §5.3. Keys within each term are sorted alphabetically
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


# ---------------------------------------------------------------------------
# §2. solver_config_hash
# ---------------------------------------------------------------------------


def compute_solver_config_hash(solver_context: dict[str, Any]) -> str:
    """Compute the canonical solver config hash.

    Pins solver identity and configuration without host or timing information.
    Two solver runs with the same solver_config_hash should be statistically
    equivalent on the same instance.

    Spec: solution_schema.md §9.1 — hashes:
        solver_id + limits_dossier_version + solver_path + solver_config

    Excludes: host, wall_clock_s, started_at, ended_at.

    Args:
        solver_context: the solver_context block from a canonical solution.

    Returns:
        64-character lowercase hex SHA-256 digest.
    """
    # Read the dossier version key tolerantly: the solver_io files emit
    # "limits_dossier_version" (plural); the schema doc says "limit_dossier_version"
    # (singular). Read whichever is present; prefer plural (what the code emits).
    dossier_version = (
        solver_context.get("limits_dossier_version")
        or solver_context.get("limit_dossier_version")
        or ""
    )

    canonical = {
        "solver_id":              str(solver_context["solver_id"]),
        "limits_dossier_version": str(dossier_version),
        "solver_path":            str(solver_context["solver_path"]),
        "solver_config":          solver_context["solver_config"],
    }

    return _hash_obj(canonical)


# ---------------------------------------------------------------------------
# §3. solution_hash
# ---------------------------------------------------------------------------


def compute_solution_hash(
    problem_hash: str,
    solver_config_hash: str,
    solution: dict[str, Any],
) -> str:
    """Compute the canonical solution hash.

    Pins the full result artifact: problem identity, solver identity, and the
    actual solver output (samples, summary, status). The first 8 hex characters
    of this hash become the solution_id.

    Spec: solution_schema.md §9.1 — hashes:
        problem_hash + solver_config_hash + samples + summary + status

    Args:
        problem_hash:       64-char hex string (top-level solution["problem_hash"]).
        solver_config_hash: 64-char hex string (from compute_solver_config_hash).
        solution:           canonical solution dict.

    Returns:
        64-character lowercase hex SHA-256 digest.
    """
    canonical = {
        "problem_hash":       str(problem_hash),
        "solver_config_hash": str(solver_config_hash),
        "samples":            solution["samples"],
        "summary":            solution["summary"],
        "status":             str(solution["status"]),
    }

    return _hash_obj(canonical)


# ---------------------------------------------------------------------------
# §4. fill_hashes — the main entry point for runners
# ---------------------------------------------------------------------------


def fill_hashes(solution: dict[str, Any]) -> dict[str, Any]:
    """Fill all "pending" hash placeholders in a canonical solution.

    Computes solver_config_hash and solution_hash, derives solution_id, and
    writes all values into the solution dict in-place. Also populates the
    reproducibility block so the three hashes are bundled together.

    Call this after decode_response() or build_failure_solution() returns,
    before writing the instance_result to disk.

    Usage in each runner (three locations each — happy path, encode error,
    and solver/API error):

        from benchmarks.hash import fill_hashes

        solution = decode_response(...)
        fill_hashes(solution)        # mutates in-place
        _save_result(solution, ...)

    Args:
        solution: canonical solution dict with "pending" hash placeholders
                  in solution_id, reproducibility.solver_config_hash, and
                  reproducibility.solution_hash.

    Returns:
        The same dict (mutated in-place and returned for chaining).

    Raises:
        ValueError: if any numeric value in samples or summary is nan or inf
                    (not permitted by the schema).
    """
    problem_hash       = solution["problem_hash"]
    solver_config_hash = compute_solver_config_hash(solution["solver_context"])
    solution_hash      = compute_solution_hash(
        problem_hash, solver_config_hash, solution
    )
    solution_id = "sol_" + solution_hash[:8]

    # Top-level identity
    solution["solution_id"] = solution_id

    # Reproducibility bundle
    solution["reproducibility"]["problem_hash"]       = problem_hash
    solution["reproducibility"]["solver_config_hash"] = solver_config_hash
    solution["reproducibility"]["solution_hash"]      = solution_hash

    return solution


# ---------------------------------------------------------------------------
# §5. Problem hash verification — used by the evaluator
# ---------------------------------------------------------------------------


def verify_problem_hash(instance: dict[str, Any]) -> bool:
    """Verify the stored problem hash against a fresh computation.

    Re-derives the problem hash from scratch and checks it matches
    instance["reproducibility_hash"]. A mismatch indicates either a
    generator bug (diagnostics computed from a different polynomial than
    stored) or file corruption.

    Used by the evaluator before scoring any solution linked to this instance.

    Args:
        instance: canonical problem instance (problem_schema.md).

    Returns:
        True iff the stored hash matches the computed hash.
    """
    stored   = instance.get("reproducibility_hash", "")
    computed = compute_problem_hash(instance)
    return stored == computed


# ---------------------------------------------------------------------------
# §6. Lower-level helpers exposed for the evaluator and validator
# ---------------------------------------------------------------------------

def derive_instance_id(instance: dict[str, Any]) -> str:
    """Return the canonical instance_id derived from the problem hash.

    Format: "inst_" + first 8 hex chars of compute_problem_hash(instance).

    Used by the instance generator and validator to set / check instance_id.

    Args:
        instance: canonical problem instance.

    Returns:
        String of the form "inst_<8-hex>".
    """
    return "inst_" + compute_problem_hash(instance)[:8]