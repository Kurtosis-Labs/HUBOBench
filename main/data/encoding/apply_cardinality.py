"""Apply the cardinality penalty to a coefficient table.

Per §7.4: M · (Σ_i w_i - k)²

Reduction for binary variables (w_i² = w_i):
  M·(Σ w_i - k)² = M·Σ w_i² + 2M·Σ_{i<j} w_i w_j - 2Mk·Σ w_i + Mk²
                 = M·Σ w_i + 2M·Σ_{i<j} w_i w_j - 2Mk·Σ w_i + Mk²
                 = M(1-2k)·Σ w_i + 2M·Σ_{i<j} w_i w_j + Mk²

The constant Mk² is folded into objective.constant.
The penalty does NOT raise the polynomial degree (only adds linear/quadratic terms).

M is calibrated per-instance as 10 × max|coef in base objective|
"""

from __future__ import annotations
from main.data.config import CARDINALITY_M_FACTOR, EPS_COEF


def compute_penalty_M(coef_table: dict[tuple, float]) -> float:
    """M = CARDINALITY_M_FACTOR × max|coef| across the base objective."""
    if not coef_table:
        return 1.0
    max_abs = max(abs(v) for v in coef_table.values())
    return CARDINALITY_M_FACTOR * max(max_abs, 1e-12)


def apply_cardinality_penalty(
    coef_table: dict[tuple, float],
    N: int,
    k: int,
    M: float | None = None,
) -> tuple[dict[tuple, float], float]:
    """Add the cardinality penalty M·(Σ w_i - k)² to the coefficient table.

    Args:
        coef_table: existing {vars_tuple: coef} table (modified in-place copy)
        N: number of variables in the subset
        k: cardinality target
        M: penalty coefficient; computed from coef_table if None

    Returns:
        (updated_coef_table, constant_term)   where constant = M·k²
    """
    updated = dict(coef_table)  # don't mutate caller's dict

    if M is None:
        M = compute_penalty_M(coef_table)

    constant = M * k * k   # Mk² folded into objective.constant

    # Linear terms: M(1 - 2k) · w_i  for each i
    linear_coef = M * (1 - 2 * k)
    for i in range(N):
        key = (i,)
        updated[key] = updated.get(key, 0.0) + linear_coef

    # Quadratic terms: 2M · w_i w_j  for each i < j
    quad_coef = 2.0 * M
    for i in range(N):
        for j in range(i + 1, N):
            key = (i, j)
            updated[key] = updated.get(key, 0.0) + quad_coef

    # Drop near-zeros
    updated = {k: v for k, v in updated.items() if abs(v) > EPS_COEF}

    return updated, constant


def cardinality_k_for_variant(N: int, variant: str) -> int | None:
    """Return k for the given cardinality variant.

    Returns None for 'kfree' (no cardinality constraint).
    """
    if variant == "kfree":
        return None
    if variant == "khalf":
        return N // 2
    if variant == "kquarter":
        return N // 4
    raise ValueError(f"Unknown cardinality variant: {variant!r}")
