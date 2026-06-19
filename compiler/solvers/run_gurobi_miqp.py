"""compiler/solvers/run_gurobi_miqp.py

Gurobi miqp orchestrator. Loads from hubobench.db, builds the model via
the solver_io, runs optimize(), decodes into a preshaped outcome row (no
samples), and writes it via the shared writer.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from compiler.solver_io import gurobi_miqp
from compiler.solver_io.helpers.instance_loader import load_instance
from compiler.solver_io.helpers.solution_writer import write_solution

SOLVER_NAME = gurobi_miqp.SOLVER_NAME
LIMITS_DOSSIER_VERSION = gurobi_miqp.LIMITS_DOSSIER_VERSION

DEFAULT_CONFIG: dict[str, Any] = {
    "TimeLimit":              300.0,
    "MIPGap":                 1e-4,
    "Threads":                8,
    "Seed":                   42,
    "non_default_parameters": {},
}


def run(
    conn: sqlite3.Connection,
    problem_hash: str,
    run_id: str,
    solver_config_id: int,
    config: dict[str, Any] | None = None,
) -> str:
    config = config or DEFAULT_CONFIG
    inst = load_instance(conn, problem_hash)

    # ----- Encode -----
    try:
        model_state, flagged = gurobi_miqp.encode_problem(
            inst.objective, inst.num_variables, inst.max_degree, config
        )
    except (ImportError, ValueError):
        sol_row, samp = gurobi_miqp.build_failure_row("API_ERROR")
        write_solution(conn, problem_hash=problem_hash,
                       solver_config_id=solver_config_id, run_id=run_id,
                       solution_row=sol_row, samples_rows=samp)
        return sol_row["status"]

    # ----- Optimize (runner owns the wall clock) -----
    model = model_state["model"]
    t0 = time.perf_counter()
    try:
        model.optimize()
    except Exception:
        sol_row, samp = gurobi_miqp.build_failure_row(
            "API_ERROR", wall_clock_s=time.perf_counter() - t0
        )
        write_solution(conn, problem_hash=problem_hash,
                       solver_config_id=solver_config_id, run_id=run_id,
                       solution_row=sol_row, samples_rows=samp)
        return sol_row["status"]
    model_state["wall_clock_s"] = time.perf_counter() - t0

    # ----- Decode + persist -----
    sol_row, samp = gurobi_miqp.decode_response(
        model_state, inst.objective, inst.num_variables, flagged
    )
    write_solution(conn, problem_hash=problem_hash,
                   solver_config_id=solver_config_id, run_id=run_id,
                   solution_row=sol_row, samples_rows=samp)
    return sol_row["status"]