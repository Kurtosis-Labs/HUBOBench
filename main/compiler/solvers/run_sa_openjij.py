"""compiler/solvers/run_sa_openjij.py

SA / OpenJij orchestrator. In-process; no API keys. Loads from hubobench.db,
samples, decodes into preshaped rows, writes via the shared writer.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from main.compiler.solver_io import sa_openjij
from main.compiler.solver_io.helpers.instance_loader import load_instance
from main.compiler.solver_io.helpers.solution_writer import write_solution

SOLVER_NAME = sa_openjij.SOLVER_NAME
LIMITS_DOSSIER_VERSION = sa_openjij.LIMITS_DOSSIER_VERSION

DEFAULT_CONFIG: dict[str, Any] = {
    "num_sweeps":           1000,
    "num_reads":            10,
    "num_threads":          4,
    "beta_min":             None,
    "beta_max":             None,
    "updater":              "METROPOLIS",
    "temperature_schedule": "GEOMETRIC",
    "vartype":              "BINARY",
    "seed":                 42,
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

    try:
        import openjij  # type: ignore
    except ImportError:
        raise ImportError("openjij is not installed. Install with: pip install openjij")

    try:
        payload, flagged = sa_openjij.encode_problem(
            inst.objective, inst.num_variables, inst.max_degree, config
        )
    except ValueError:
        sol_row, samp = sa_openjij.build_failure_row("API_ERROR")
        write_solution(conn, problem_hash=problem_hash,
                       solver_config_id=solver_config_id, run_id=run_id,
                       solution_row=sol_row, samples_rows=samp)
        return sol_row["status"]

    t0 = time.perf_counter()
    try:
        sampler = openjij.SASampler()
        raw_response = sampler.sample_hubo(
            payload["polynomial"],
            payload["vartype"],
            **payload["sampler_kwargs"],
        )
    except Exception:
        sol_row, samp = sa_openjij.build_failure_row(
            "API_ERROR", wall_clock_s=time.perf_counter() - t0
        )
        write_solution(conn, problem_hash=problem_hash,
                       solver_config_id=solver_config_id, run_id=run_id,
                       solution_row=sol_row, samples_rows=samp)
        return sol_row["status"]
    wall = time.perf_counter() - t0

    # Stamp measured wall clock into info if the sampler did not provide one.
    if getattr(raw_response, "info", None) is not None:
        raw_response.info.setdefault("wall_clock_s", wall)

    sol_row, samp = sa_openjij.decode_response(
        raw_response, inst.objective, inst.num_variables, flagged
    )
    write_solution(conn, problem_hash=problem_hash,
                   solver_config_id=solver_config_id, run_id=run_id,
                   solution_row=sol_row, samples_rows=samp)
    return sol_row["status"]