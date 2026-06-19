"""compiler/solver_io/gurobi_miqp.py

Gurobi MIQP (Rosenberg-quadratized) solver I/O for HUBOBench.

Reduces the canonical HUBO to quadratic via Rosenberg, builds a Gurobi MIQP
over (canonical + aux) variables, and after optimize() strips the aux
variables and re-evaluates the ORIGINAL polynomial to get the canonical
energy.

New SQL-era contract:
    encode_problem(objective, num_variables, max_degree, config)
        -> (model_state, pre_submission_flags)
    decode_response(model_state, objective, num_variables, config,
                    pre_submission_flags)
        -> (solution_row, samples_rows)   # samples_rows is always [] (Gurobi)

solution_row carries only outcome fields the writer inserts:
    status, best_energy, best_vars_json, wall_clock_s, algorithmic_time_s

Aux-constraint violations (z != x_i*x_j, meaning penalty M was too weak) do
NOT invalidate the result: the canonical energy is re-evaluated on the true
polynomial over a valid x in {0,1}^n. They mean the returned x may be
suboptimal, so the status is SUBOPTIMAL_GAP. The per-run violation count is
printed (not stored) so penalty-M calibration can be reconsidered.
"""

from __future__ import annotations

from typing import Any

from compiler.reduction import rosenberg
from compiler.solver_io.helpers.decode_common import (
    evaluate_polynomial,
    best_vars_json_from_assignment,
    flags_json,
    FLAG_AUX_VIOLATION,
    FLAG_DYNAMIC_RANGE_WARNING,
)

try:
    import gurobipy as gp
    from gurobipy import GRB
    _GUROBI_AVAILABLE = True
except ImportError:
    _GUROBI_AVAILABLE = False
    gp = None   # type: ignore
    GRB = None  # type: ignore

SOLVER_NAME = "gurobi_miqp"
LIMITS_DOSSIER_VERSION = "0.4"

_GUROBI_DR_DEGRADED = 1e9
_GUROBI_DR_WARNING  = 1e6


def classify_gurobi_status(status_code: int, sol_count: int) -> str:
    """Map a Gurobi status code + SolCount to a canonical status.

    OPTIMAL with an incumbent          -> OK
    TIME_LIMIT / SUBOPTIMAL w/ incumbent -> SUBOPTIMAL_GAP (done, not optimal)
    Any limit code with NO incumbent   -> TIMEOUT
    INFEASIBLE / UNBOUNDED / error      -> API_ERROR
    """
    if not _GUROBI_AVAILABLE:
        return "API_ERROR"
    if status_code == GRB.OPTIMAL and sol_count > 0:
        return "OK"
    # Limit-style stops: time, iteration, node, solution-count, etc.
    limitish = {
        GRB.TIME_LIMIT, GRB.ITERATION_LIMIT, GRB.NODE_LIMIT,
        GRB.SOLUTION_LIMIT, GRB.WORK_LIMIT, GRB.SUBOPTIMAL,
    }
    if status_code in limitish:
        return "SUBOPTIMAL_GAP" if sol_count > 0 else "TIMEOUT"
    if sol_count > 0:
        # Some terminal status we did not name, but an incumbent exists.
        return "SUBOPTIMAL_GAP"
    return "API_ERROR"


def _dynamic_range(terms: list[dict[str, Any]]) -> float:
    nz = [abs(t["coef"]) for t in terms if t["coef"] != 0.0]
    return (max(nz) / min(nz)) if nz else 0.0


def _check_pre_submission(objective: dict[str, Any]) -> tuple[int, bool]:
    """Return (recommended_numeric_focus, flagged).

    flagged is True if any G1/G2 coefficient-range warning fired. No hard
    rejects for Gurobi. Flag detail is not stored, only the boolean.
    """
    dr = _dynamic_range(objective["terms"])
    if dr > _GUROBI_DR_DEGRADED:
        return 3, True
    if dr > _GUROBI_DR_WARNING:
        return 2, True
    return 0, False

# ─────────────────────────────────────────────────────────────────────────────
# Encode
# ─────────────────────────────────────────────────────────────────────────────

def encode_problem(
    objective: dict[str, Any],
    num_variables: int,
    max_degree: int,
    config: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Reduce to quadratic via Rosenberg and build a Gurobi MIQP.

    Returns (model_state, flagged) where flagged indicates a G1/G2
    coefficient-range warning fired during pre-submission.

    Raises:
        ImportError: gurobipy not installed.
        ValueError:  Threads or Seed missing from config (reproducibility).
    """
    if not _GUROBI_AVAILABLE:
        raise ImportError("gurobipy is not installed; cannot encode for Gurobi MIQP.")
    if "Threads" not in config:
        raise ValueError("config['Threads'] is required (gurobi_limits.md §4.5).")
    if "Seed" not in config:
        raise ValueError("config['Seed'] is required (gurobi_limits.md §4.5).")

    recommended_nf, flagged = _check_pre_submission(objective)

    reduction     = rosenberg.reduce(objective, num_variables)
    reduced_terms = reduction["reduced_terms"]
    aux_mapping   = reduction["aux_mapping"]

    model = gp.Model("hubobench_miqp")
    model.setParam("OutputFlag", 0)
    model.setParam("NonConvex", 2)
    model.setParam("TimeLimit", float(config["TimeLimit"]))
    model.setParam("MIPGap",    float(config["MIPGap"]))
    model.setParam("Threads",   int(config["Threads"]))
    model.setParam("Seed",      int(config["Seed"]))
    if "NumericFocus" in config:
        model.setParam("NumericFocus", int(config["NumericFocus"]))
    elif recommended_nf > 0:
        model.setParam("NumericFocus", recommended_nf)
    for k, v in config.get("non_default_parameters", {}).items():
        model.setParam(k, v)

    canonical_vars = [model.addVar(vtype=GRB.BINARY, name=f"x_{i}")
                      for i in range(num_variables)]
    aux_vars = [model.addVar(vtype=GRB.BINARY, name=f"y_{vi}_{vj}")
                for _aux_idx, vi, vj in aux_mapping]
    assert len(aux_vars) == reduction["n_aux_variables"], \
        "rosenberg.reduce contract violation: aux_mapping vs n_aux_variables"

    all_vars = canonical_vars + aux_vars
    obj_expr: Any = gp.QuadExpr()
    for term in reduced_terms:
        vl = term["vars"]; c = float(term["coef"])
        if len(vl) == 1:
            obj_expr.add(all_vars[vl[0]], c)
        elif len(vl) == 2:
            obj_expr.add(all_vars[vl[0]] * all_vars[vl[1]], c)
        else:
            raise RuntimeError(
                f"reduced_terms has a degree-{len(vl)} term; expected <= 2."
            )
    model.setObjective(obj_expr, GRB.MINIMIZE)
    model.update()

    return (
        {
            "model":          model,
            "canonical_vars": canonical_vars,
            "aux_vars":       aux_vars,
            "reduction":      reduction,
            "flagged":        flagged,
            "wall_clock_s":   None,   # stamped by the runner around optimize()
        },
        flagged,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Decode
# ─────────────────────────────────────────────────────────────────────────────

def decode_response(
    model_state: dict[str, Any],
    objective: dict[str, Any],
    num_variables: int,
    flagged: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Strip aux vars, re-evaluate the canonical polynomial, shape outcome row.

    Returns (solution_row, []) — Gurobi writes no samples; its assignment lives
    in solution_row.best_vars_json.

    Aux-constraint violations are counted and the count is PRINTED (not stored).
    A nonzero count forces status SUBOPTIMAL_GAP (valid x, possibly non-optimal).
    """
    if not _GUROBI_AVAILABLE:
        raise ImportError("gurobipy is not installed; cannot decode Gurobi response.")

    model          = model_state["model"]
    canonical_vars = model_state["canonical_vars"]
    aux_vars       = model_state["aux_vars"]
    reduction      = model_state["reduction"]
    constant       = objective.get("constant", 0.0)
    terms          = objective["terms"]
    aux_mapping    = reduction["aux_mapping"]

    status_code = int(model.Status)
    base_status = classify_gurobi_status(status_code, int(model.SolCount))

    algorithmic_time_s = float(getattr(model, "Runtime", 0.0))
    wall_clock_s = model_state.get("wall_clock_s")
    if wall_clock_s is None:
        wall_clock_s = algorithmic_time_s

    if model.SolCount == 0:
        nosol_flags = [FLAG_DYNAMIC_RANGE_WARNING] if flagged else []
        solution_row = {
            "status":             base_status,
            "best_energy":        None,
            "best_vars_json":     None,
            "wall_clock_s":       float(wall_clock_s),
            "algorithmic_time_s": algorithmic_time_s,
            "flags":              flags_json(nosol_flags),
        }
        return solution_row, []

    canonical_assignment = [int(round(v.X)) for v in canonical_vars]
    aux_assignment       = [int(round(v.X)) for v in aux_vars]

    # ----- Aux-constraint integrity: count violations -----
    aux_violations = 0
    for aux_idx, var_i, var_j in aux_mapping:
        x_i = canonical_assignment[var_i]
        x_j = canonical_assignment[var_j]
        local = aux_idx - num_variables
        y     = aux_assignment[local]
        if y != x_i * x_j:
            aux_violations += 1

    if aux_violations:
        # Printed, never stored. A high count means penalty M is too weak.
        print(
            f"[gurobi_miqp] aux-constraint violations this run: "
            f"{aux_violations}/{len(aux_mapping)} "
            f"(penalty_M_max={reduction.get('penalty_M_max')!r}); "
            f"if this is large, reconsider penalty M calibration."
        )

    # Canonical energy: ALWAYS the true polynomial on the returned x.
    canonical_energy = evaluate_polynomial(terms, canonical_assignment) + constant
    if aux_violations and base_status == "OK":
        status = "SUBOPTIMAL_GAP"
    else:
        status = base_status

    # Flags: orthogonal annotations. AUX_VIOLATION records that the suboptimality
    # cause was penalty-M weakness; DYNAMIC_RANGE_WARNING records poor pre-submission
    # conditioning. No ENERGY_MISMATCH for miqp (ObjVal != HUBO by construction).
    flags: list[str] = []
    if aux_violations:
        flags.append(FLAG_AUX_VIOLATION)
    if flagged:
        flags.append(FLAG_DYNAMIC_RANGE_WARNING)

    solution_row = {
        "status":             status,
        "best_energy":        float(canonical_energy),
        "best_vars_json":     best_vars_json_from_assignment(canonical_assignment),
        "wall_clock_s":       float(wall_clock_s),
        "algorithmic_time_s": algorithmic_time_s,
        "flags":              flags_json(flags),
    }
    return solution_row, []


def build_failure_row(
    status: str,
    algorithmic_time_s: float = 0.0,
    wall_clock_s: float | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Outcome row for a run that never produced a result (encode/optimize error)."""
    return (
        {
            "status":             status,
            "best_energy":        None,
            "best_vars_json":     None,
            "wall_clock_s":       float(wall_clock_s if wall_clock_s is not None
                                        else algorithmic_time_s),
            "algorithmic_time_s": float(algorithmic_time_s),
            "flags":              flags_json([]),
        },
        [],
    )