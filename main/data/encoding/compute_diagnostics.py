"""encoding/compute_diagnostics.py

Computes the classifier feature columns for the hubobench.db instances table.

Single public function:

    compute_instance_features(coef_table, n_variables)
    → {max_degree, density, dynamic_range_ratio, num_terms}

These four values are the only diagnostics stored in SQL. All other statistics
(coef_mean, coef_std, var_appearance_counts, interaction_graph_density, etc.)
are not stored anywhere — the SQL typed columns are sufficient for querying
and classifier training.

Caller provides: coeff_dist, problem_class, constraint_ratio. These are
generator-context fields and are not derivable from the polynomial.
"""

from __future__ import annotations

import math
from collections import defaultdict


def compute_instance_features(
    coef_table: dict[tuple, float],
    n_variables: int,
) -> dict:
    """Compute the four typed classifier feature columns from a coefficient table.

    Args:
        coef_table:  {vars_tuple: coef} dict. The empty tuple key () is the
                     constant term and is excluded from all computations.
        n_variables: number of binary decision variables N.

    Returns:
        {
            "max_degree":          int    — highest degree present
            "density":             float  — fraction of C(N, max_degree) monomials
                                           that are non-zero, at max_degree only
            "dynamic_range_ratio": float  — max|c| / min|c| across all non-zero terms
            "num_terms":           int    — total non-zero terms across all degrees
        }

    Density denominator: C(N, d) — the number of possible degree-d binary
    monomials after idempotency (x_i^2 = x_i). Each monomial selects d
    distinct variables from N.
    """
    # Exclude the constant term (empty tuple key) and zero coefficients.
    nonzero_terms = [
        (vt, c) for vt, c in coef_table.items()
        if vt and abs(c) > 0.0
    ]

    if not nonzero_terms:
        return {
            "max_degree":          0,
            "density":             0.0,
            "dynamic_range_ratio": 1.0,
            "num_terms":           0,
        }

    # ── Per-degree counts ─────────────────────────────────────────────────
    n_by_degree: dict[int, int] = defaultdict(int)
    for vars_tuple, _ in nonzero_terms:
        n_by_degree[len(vars_tuple)] += 1

    max_degree = max(n_by_degree)
    num_terms  = sum(n_by_degree.values())

    # ── Density at max_degree ─────────────────────────────────────────────
    # C(N, max_degree): all possible degree-max_degree binary monomials.
    denom   = math.comb(n_variables, max_degree)
    density = n_by_degree[max_degree] / denom if denom > 0 else 0.0

    # ── Dynamic range ratio ───────────────────────────────────────────────
    abs_coefs = [abs(c) for _, c in nonzero_terms]
    dynamic_range_ratio = max(abs_coefs) / min(abs_coefs)

    return {
        "max_degree":          max_degree,
        "density":             float(density),
        "dynamic_range_ratio": float(dynamic_range_ratio),
        "num_terms":           num_terms,
    }