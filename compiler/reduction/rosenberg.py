from __future__ import annotations
from collections import Counter, defaultdict
from itertools import combinations
from typing import Any

"""
Rosenberg reduction for HUBO polynomials.

Reduces a polynomial of any degree to degree 2 over an extended binary variable
set, with penalty terms that enforce the auxiliary-variable definitions in the
objective.

Reference: Rosenberg (1975), reformulated in Boros & Hammer (2002). Standard
construction: for binary variables x, y ∈ {0, 1}, replace the product xy with
a new binary variable z and add to the (minimisation) objective the penalty

    M * (xy - 2xz - 2yz + 3z)

which evaluates to 0 when z = xy and to a positive integer ≥ 1 otherwise. So
for sufficiently large M, no optimal solution to the augmented problem can
violate z = xy. See below for the truth-table verification.

Two entry points, both taking the SQL-era inputs (objective dict + the
canonical variable count) rather than a fat nested instance dict:

    reduce(objective, num_variables)  → dict with reduced_terms, aux_mapping,
        penalty_M_per_aux, penalty_M_max, ...
        Computes the full reduced polynomial. Used at solve time by
        gurobi_miqp.encode_problem.

    profile(objective, num_variables) → dict with method, penalty_M_max,
                        n_aux_variables, n_quadratic_terms

`objective` is {"terms": [...], "constant": float} exactly as produced by the
shared instance loader; `num_variables` is the typed instances.num_variables
column. No parameters block, no file, no blob.

Method: greedy_pair_reuse. Alternative methods (optimal_pair_reuse, degree_cascade) are out of scope.
"""

# Zero-coefficient threshold for merging. Same value as
# docs/hubobench/problem_schema.md §3.3 rule 5, kept consistent so the
# reduced polynomial canonicalisation matches the input canonicalisation.
_ZERO_TOL = 1e-12

_PENALTY_MARGIN = 1e-3


# ----------------------------------------------------------------------------
# §1. The reduction mathematics
# ----------------------------------------------------------------------------

# The classical Rosenberg construction: enforce z = xy for binary x, y by adding
# the polynomial P(x, y, z) = xy - 2xz - 2yz + 3z to a minimisation objective,
# scaled by a sufficiently large penalty M.
#
# Truth-table verification (all 8 binary assignments):
#
#   x  y  z  | xy  -2xz  -2yz  +3z  | total | z == xy correct?
#   ---------|---------------------|-------|------------------
#   0  0  0  |  0    0     0     0  |   0   | 0 == 0 ✓
#   0  0  1  |  0    0     0     3  |   3   | 1 != 0 ✗ penalised by 3M
#   0  1  0  |  0    0     0     0  |   0   | 0 == 0 ✓
#   0  1  1  |  0    0    -2     3  |   1   | 1 != 0 ✗ penalised by 1M
#   1  0  0  |  0    0     0     0  |   0   | 0 == 0 ✓
#   1  0  1  |  0   -2     0     3  |   1   | 1 != 0 ✗ penalised by 1M
#   1  1  0  |  1    0     0     0  |   1   | 0 != 1 ✗ penalised by 1M
#   1  1  1  |  1   -2    -2     3  |   0   | 1 == 1 ✓
#
# So P is 0 exactly when z = xy and ≥ 1 otherwise. The penalty M * P drives
# the solver toward z = xy for any M strictly greater than the largest
# improvement obtainable by violating it.


# ----------------------------------------------------------------------------
# §2. The greedy_pair_reuse algorithm
# ----------------------------------------------------------------------------
#
# The greedy step: while any term has degree ≥ 3, find the pair (i, j) that
# appears most often across all high-degree terms, introduce z_{ij} = x_i x_j,
# and substitute. Each substitution reduces the degree of every term
# containing both i and j by exactly 1.
#
# This is "greedy" because it commits to the most common pair without
# considering downstream effects. The optimal pair-reuse problem
# (minimising n_aux variables) is NP-hard; greedy is the standard heuristic
# in the literature and gives empirically good results for sparse HUBOs.


def reduce(objective: dict[str, Any], num_variables: int) -> dict[str, Any]:
    """Reduce a HUBO objective to quadratic form via greedy pair reuse.

    Args:
        objective: {"terms": [{"vars": [...], "coef": float}, ...],
                    "constant": float}. Only terms and constant are read.
        num_variables: the canonical variable count (instances.num_variables).
            Auxiliary indices are allocated starting at this value.

    Returns:
        A dict with:
            method (str)        : "greedy_pair_reuse"
            reduced_terms (list): degree-≤2 terms in canonical order, with
                                  the penalty contributions already folded in
                                  and duplicates merged.
            constant (float)    : objective.constant carried through unchanged.
            aux_mapping (list)  : list of (aux_index, var_i, var_j) tuples in
                                  the order they were introduced. aux_index ≥
                                  num_variables; var_i and var_j are in
                                  [0, num_variables) (canonical variables).
            n_aux_variables(int): len(aux_mapping)
            n_quadratic_terms(int): number of degree-2 terms in reduced_terms
            n_linear_terms (int): number of degree-1 terms in reduced_terms
            penalty_M_per_aux (dict[str,float]): the authoritative per-aux
                                  penalty weights, keyed by aux index as a
                                  string. Each penalty term in reduced_terms
                                  was built from these (Verma et al. 2021
                                  tight bound).
            penalty_M_max (float): max over penalty_M_per_aux, for
                                  dynamic-range diagnostic check. Not used to
                                  build the objective. 0.0 if no auxes.
    """
    terms_in = objective["terms"]
    constant = objective.get("constant", 0.0)
    n_canonical = num_variables

    # Work with mutable copies — never modify the input terms.
    work_terms = [
        {"vars": list(t["vars"]), "coef": float(t["coef"])} for t in terms_in
    ]
    aux_mapping: list[tuple[int, int, int]] = []
    next_aux_idx = n_canonical

    # Main loop: each iteration eliminates the most common canonical-pair
    # from high-degree terms by introducing one auxiliary.
    while True:
        # Count pairs across all current degree-≥3 terms. We pair only canonical
        # variables (indices < n_canonical).
        pair_count: Counter[tuple[int, int]] = Counter()
        for t in work_terms:
            if len(t["vars"]) >= 3:
                canonical_vars = [v for v in t["vars"] if v < n_canonical]
                for i, j in combinations(canonical_vars, 2):
                    # canonical_vars is sorted because t["vars"] is sorted
                    pair_count[(i, j)] += 1

        if not pair_count:
            # All remaining degree-≥3 terms contain at most one canonical
            # variable, so no further canonical-pair substitution is possible.
            break

        # Pick the most common pair. Ties broken by Counter.most_common ordering
        # (insertion order in CPython 3.7+), which is deterministic given a
        # canonically-ordered input.
        (var_i, var_j), _freq = pair_count.most_common(1)[0]

        # Allocate a new auxiliary index. Indices are contiguous and grow
        # monotonically; the Gurobi model's variable count is
        # n_canonical + len(aux_mapping).
        aux_idx = next_aux_idx
        next_aux_idx += 1
        aux_mapping.append((aux_idx, var_i, var_j))

        # Substitute (var_i, var_j) → aux_idx in every term containing both.
        new_work: list[dict[str, Any]] = []
        for t in work_terms:
            if var_i in t["vars"] and var_j in t["vars"]:
                # Remove the pair, add the aux. The result is sorted because
                # we maintain canonical ordering at every step.
                remaining = [v for v in t["vars"] if v != var_i and v != var_j]
                new_vars = sorted(remaining + [aux_idx])
                new_work.append({"vars": new_vars, "coef": t["coef"]})
            else:
                new_work.append({"vars": list(t["vars"]), "coef": t["coef"]})
        work_terms = new_work

    # After the substitution loop completes, but before adding penalty terms:
    per_aux_M: dict[int, float] = {}
    for aux_idx, var_i, var_j in aux_mapping:
        # Sum |c_t| over terms in work_terms that contain this aux
        sum_abs = sum(abs(t["coef"]) for t in work_terms if aux_idx in t["vars"])
        # Add small relative margin; +1 absolute floor for degenerate sum=0 cases
        per_aux_M[aux_idx] = max(sum_abs * (1.0 + _PENALTY_MARGIN), sum_abs + 1.0)

    # Add penalty terms: for each substitution (aux, i, j),
    #   M * (x_i*x_j - 2*x_i*aux - 2*x_j*aux + 3*aux)
    # which is 0 iff aux = x_i*x_j (see §1).
    for aux_idx, var_i, var_j in aux_mapping:
        M_ij = per_aux_M[aux_idx]
        work_terms.append({"vars": sorted([var_i, var_j]), "coef": M_ij})
        work_terms.append({"vars": sorted([var_i, aux_idx]), "coef": -2.0 * M_ij})
        work_terms.append({"vars": sorted([var_j, aux_idx]), "coef": -2.0 * M_ij})
        work_terms.append({"vars": [aux_idx], "coef": 3.0 * M_ij})

    # Merge duplicates. The original polynomial may have been canonical (no
    # duplicates), but the penalty additions can collide with existing
    # quadratic terms (e.g., if the original problem already contained
    # x_i*x_j for the same pair we substituted).
    merged: dict[tuple[int, ...], float] = defaultdict(float)
    for t in work_terms:
        key = tuple(t["vars"])  # already sorted
        merged[key] += t["coef"]

    reduced_terms = [
        {"vars": list(k), "coef": v}
        for k, v in merged.items()
        if abs(v) > _ZERO_TOL
    ]
    # Canonical sort: by degree, then lex order of vars.
    reduced_terms.sort(key=lambda t: (len(t["vars"]), t["vars"]))

    n_quad = sum(1 for t in reduced_terms if len(t["vars"]) == 2)
    n_lin = sum(1 for t in reduced_terms if len(t["vars"]) == 1)

    # After reduction, no term should exceed degree 2.
    max_deg = max((len(t["vars"]) for t in reduced_terms), default=0)
    if max_deg > 2:
        raise RuntimeError(
            f"Rosenberg reduction did not converge to quadratic form: "
            f"max degree after reduction = {max_deg}. This is a known V1 "
            f"limitation (see §2 caveat in rosenberg.py); promote to V2 "
            f"algorithm that pairs auxiliaries with canonicals."
        )

    # penalty_M_per_aux is the authoritative penalty source: every penalty
    # term in reduced_terms was built from per_aux_M[aux_idx] (the Verma,
    # Lewis & Kochenberger 2021 tight bound M_ij = Σ_{t : y_ij ∈ t} |c_t|).
    # It is keyed by aux index as a STRING so the dict survives JSON
    # round-tripping (json.dumps coerces int keys to str silently, which
    # would otherwise desync the in-memory and on-disk forms). Consumers
    # that need the global integer index should int() the key.
    #
    # There is deliberately NO single scalar `penalty_M`. Collapsing the
    # per-aux dict to one max value invites a uniform-M reapplication that
    # would discard the tight bound. The only value that legitimately needs
    # the max is benchmark's dynamic-range diagnostic check, which gets it
    # from the explicitly diagnostic field below.
    penalty_M_per_aux = {str(aux_idx): per_aux_M[aux_idx]
                         for aux_idx, _, _ in aux_mapping}

    return {
        "method": "greedy_pair_reuse",
        "reduced_terms": reduced_terms,
        "constant": constant,
        "aux_mapping": aux_mapping,
        "n_aux_variables": len(aux_mapping),
        "n_quadratic_terms": n_quad,
        "n_linear_terms": n_lin,
        "penalty_M_per_aux": penalty_M_per_aux,
        "penalty_M_max": max(per_aux_M.values(), default=0.0),
    }