"""compiler/solver_io/helpers/decode_common.py

Shared decode helpers used by every solver_io module.

These factor out the logic that was duplicated verbatim across dirac3,
sa_openjij, gurobi_miqp, and gurobi_nlfunc: evaluating the canonical
polynomial, grouping raw samples, and shaping them into the row dicts the
solution_writer inserts.
"""

from __future__ import annotations

import json
from typing import Any, Iterable


def evaluate_polynomial(terms: list[dict[str, Any]], assignment: list[int]) -> float:
    """f(x) = Σ_t c_t · Π_{i ∈ t.vars} x_i  (constant added by caller).

    Short-circuits on the first zero factor since a binary product collapses
    to 0 as soon as any variable is 0.
    """
    total = 0.0
    for t in terms:
        prod = 1
        for i in t["vars"]:
            prod *= assignment[i]
            if prod == 0:
                break
        if prod != 0:
            total += t["coef"] * prod
    return total


def group_samples(
    raw_assignments: Iterable[list[int]],
    raw_counts: Iterable[int],
) -> dict[tuple[int, ...], int]:
    """Collapse identical assignments, summing their counts.

    Returns {assignment_tuple: total_count}, insertion order preserved.
    """
    grouped: dict[tuple[int, ...], int] = {}
    for assignment, count in zip(raw_assignments, raw_counts):
        key = tuple(int(v) for v in assignment)
        grouped[key] = grouped.get(key, 0) + int(count)
    return grouped


def build_sample_rows(
    grouped: dict[tuple[int, ...], int],
    terms: list[dict[str, Any]],
    constant: float,
) -> list[dict[str, Any]]:
    """Shape grouped assignments into samples rows, sorted best-energy first.

    Each row is {sample_rank, energy, count, vars} where:
        energy = canonical f(x) + constant (device energy is NOT trusted)
        vars   = raw bytes, one byte per variable (0/1) per the samples schema
        sample_rank = 0 for the lowest-energy assignment, ascending

    The vars blob is written with bytes(assignment); read back in numpy via
    numpy.frombuffer(row.vars, dtype=numpy.uint8).
    """
    scored = []
    for assignment_tuple, count in grouped.items():
        assignment = list(assignment_tuple)
        energy = evaluate_polynomial(terms, assignment) + constant
        scored.append((energy, count, assignment))

    scored.sort(key=lambda x: x[0])  # ascending energy, stable within ties

    rows: list[dict[str, Any]] = []
    for rank, (energy, count, assignment) in enumerate(scored):
        rows.append({
            "sample_rank": rank,
            "energy":      float(energy),
            "count":       int(count),
            "vars":        bytes(assignment),   # raw byte blob, 1 byte/var
        })
    return rows


def best_from_samples(
    sample_rows: list[dict[str, Any]],
) -> tuple[float | None, str | None]:
    """Return (best_energy, best_vars_json) from rank-0, or (None, None).

    best_vars_json is the JSON array of 0/1 ints for the lowest-energy sample,
    matching solutions.best_vars_json. For stochastic solvers this mirrors the
    rank-0 samples row.
    """
    if not sample_rows:
        return None, None
    best = sample_rows[0]
    vars_list = list(best["vars"])           # bytes -> list[int]
    return float(best["energy"]), json.dumps(vars_list, separators=(",", ":"))


def best_vars_json_from_assignment(assignment: list[int]) -> str:
    """JSON array of 0/1 ints for a single assignment (Gurobi has no samples)."""
    return json.dumps([int(v) for v in assignment], separators=(",", ":"))

# ─────────────────────────────────────────────────────────────────────────────
# Flags
# ─────────────────────────────────────────────────────────────────────────────

# Canonical flag tokens. Flags are orthogonal annotations on a result, NOT
# termination states (those live in `status`).
FLAG_ENERGY_MISMATCH       = "ENERGY_MISMATCH"        # recompute != solver-reported energy
FLAG_DYNAMIC_RANGE_WARNING = "DYNAMIC_RANGE_WARNING"  # poor coefficient conditioning
FLAG_AUX_VIOLATION         = "AUX_VIOLATION"          # Rosenberg z != x_i*x_j

_ENERGY_MISMATCH_TOL = 1e-5

def energy_mismatch(recomputed: float | None, reported: float | None,
                    tol: float = _ENERGY_MISMATCH_TOL) -> bool:
    """True if both energies are present and differ by more than tol.

    Used only where the solver-reported energy is SUPPOSED to equal the
    canonical recompute (Dirac device energy, SA sampler energy, Gurobi nlfunc
    ObjVal).
    """
    if recomputed is None or reported is None:
        return False
    return abs(float(recomputed) - float(reported)) > tol


def flags_json(flags: list[str]) -> str:
    """Serialise a flag list for the solutions.flags column.

    Returns '[]' for a clean (checked, no flags) result.
    """
    return json.dumps(list(flags), separators=(",", ":"))