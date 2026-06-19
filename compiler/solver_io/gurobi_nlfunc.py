"""compiler/solver_io/gurobi_nlfunc.py

Gurobi native-HUBO solver I/O for HUBOBench.

Encodes the original (high-degree) polynomial directly into a Gurobi model:
degree-1 and degree-2 terms go in as linear/quadratic; degree>=3 terms are
linearised with addGenConstrAnd auxiliaries.

New SQL-era contract mirrors gurobi_miqp:
    encode_problem(objective, num_variables, max_degree, config)
        -> (model_state, flagged)
    decode_response(model_state, objective, num_variables, config, flagged)
        -> (solution_row, [])
"""

from __future__ import annotations

from typing import Any

from compiler.solver_io.helpers.decode_common import (
    evaluate_polynomial,
    best_vars_json_from_assignment,
    energy_mismatch,
    flags_json,
    FLAG_ENERGY_MISMATCH,
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

SOLVER_NAME = "gurobi_nlfunc"
LIMITS_DOSSIER_VERSION = "0.4"

_GUROBI_DR_DEGRADED = 1e9
_GUROBI_DR_WARNING  = 1e6


def classify_gurobi_status(status_code: int, sol_count: int) -> str:
    """Identical mapping to the MIQP path (kept verbatim per module convention)."""
    if not _GUROBI_AVAILABLE:
        return "API_ERROR"
    if status_code == GRB.OPTIMAL and sol_count > 0:
        return "OK"
    limitish = {
        GRB.TIME_LIMIT, GRB.ITERATION_LIMIT, GRB.NODE_LIMIT,
        GRB.SOLUTION_LIMIT, GRB.WORK_LIMIT, GRB.SUBOPTIMAL,
    }
    if status_code in limitish:
        return "SUBOPTIMAL_GAP" if sol_count > 0 else "TIMEOUT"
    if sol_count > 0:
        return "SUBOPTIMAL_GAP"
    return "API_ERROR"


def _dynamic_range(terms: list[dict[str, Any]]) -> float:
    nz = [abs(t["coef"]) for t in terms if t["coef"] != 0.0]
    return (max(nz) / min(nz)) if nz else 0.0


def _check_pre_submission(objective: dict[str, Any]) -> tuple[int, bool]:
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
    if not _GUROBI_AVAILABLE:
        raise ImportError("gurobipy is not installed; cannot encode for Gurobi nlfunc.")
    if "Threads" not in config:
        raise ValueError("config['Threads'] is required (gurobi_limits.md §4.5).")
    if "Seed" not in config:
        raise ValueError("config['Seed'] is required (gurobi_limits.md §4.5).")

    recommended_nf, flagged = _check_pre_submission(objective)
    terms    = objective["terms"]
    constant = objective.get("constant", 0.0)

    model = gp.Model("hubobench_nlfunc")
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

    obj_expr: Any = gp.QuadExpr()
    aux_counter = 0
    for term in terms:
        vt = tuple(term["vars"]); k = len(vt); c = float(term["coef"])
        if k == 1:
            obj_expr.add(canonical_vars[vt[0]], c)
        elif k == 2:
            obj_expr.add(canonical_vars[vt[0]] * canonical_vars[vt[1]], c)
        else:
            y = model.addVar(vtype=GRB.BINARY, name=f"and_aux_{aux_counter}")
            aux_counter += 1
            model.addGenConstrAnd(y, [canonical_vars[i] for i in vt],
                                  name=f"and_constr_{aux_counter}")
            obj_expr.add(y, c)

    model.setObjective(obj_expr, GRB.MINIMIZE)
    model.ObjCon = constant
    model.update()

    return (
        {
            "model":          model,
            "canonical_vars": canonical_vars,
            "flagged":        flagged,
            "wall_clock_s":   None,
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
    if not _GUROBI_AVAILABLE:
        raise ImportError("gurobipy is not installed; cannot decode Gurobi response.")

    model          = model_state["model"]
    canonical_vars = model_state["canonical_vars"]
    constant       = objective.get("constant", 0.0)
    terms          = objective["terms"]

    base_status = classify_gurobi_status(int(model.Status), int(model.SolCount))
    algorithmic_time_s = float(getattr(model, "Runtime", 0.0))
    wall_clock_s = model_state.get("wall_clock_s")
    if wall_clock_s is None:
        wall_clock_s = algorithmic_time_s

    nosol_flags: list[str] = []
    if flagged:
        nosol_flags.append(FLAG_DYNAMIC_RANGE_WARNING)

    if model.SolCount == 0:
        return (
            {
                "status":             base_status,
                "best_energy":        None,
                "best_vars_json":     None,
                "wall_clock_s":       float(wall_clock_s),
                "algorithmic_time_s": algorithmic_time_s,
                "flags":              flags_json(nosol_flags),
            },
            [],
        )

    canonical_assignment = [int(round(v.X)) for v in canonical_vars]
    canonical_energy = evaluate_polynomial(terms, canonical_assignment) + constant

    flags = list(nosol_flags)
    # nlfunc encodes the ORIGINAL polynomial directly (AND-linearised), so
    # model.ObjVal is the HUBO objective and SHOULD equal the recompute. A
    # divergence signals an encoding bug -> ENERGY_MISMATCH.
    reported = float(getattr(model, "ObjVal", None)) if model.SolCount else None
    if energy_mismatch(canonical_energy, reported):
        flags.append(FLAG_ENERGY_MISMATCH)

    # status records ONLY termination (FLAGGED retired).
    return (
        {
            "status":             base_status,
            "best_energy":        float(canonical_energy),
            "best_vars_json":     best_vars_json_from_assignment(canonical_assignment),
            "wall_clock_s":       float(wall_clock_s),
            "algorithmic_time_s": algorithmic_time_s,
            "flags":              flags_json(flags),
        },
        [],
    )


def build_failure_row(
    status: str,
    algorithmic_time_s: float = 0.0,
    wall_clock_s: float | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
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