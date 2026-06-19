"""compiler/solvers/run_dirac3.py

Dirac-3 orchestrator. Loads an instance from hubobench.db, encodes to the QCI
polynomial file format, uploads + submits via qci_client, decodes into
preshaped rows, and writes them via the shared writer.

run(conn, problem_hash, run_id, solver_config_id, config) -> status

Config defaults live here; edit DEFAULT_CONFIG to retune. The aggregator reads
DEFAULT_CONFIG, SOLVER_NAME, and the dossier/version via the solver_io module.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from compiler.solver_io import dirac3
from compiler.solver_io.helpers.instance_loader import load_instance
from compiler.solver_io.helpers.solution_writer import write_solution

# Load .env from the repo root explicitly so credentials resolve regardless of
# the process CWD (the aggregator may be launched from anywhere). run_dirac3 is
# at compiler/solvers/run_dirac3.py, so the repo root is two parents up.
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_ENV_PATH)

SOLVER_NAME = dirac3.SOLVER_NAME
LIMITS_DOSSIER_VERSION = dirac3.LIMITS_DOSSIER_VERSION

DEFAULT_CONFIG: dict[str, Any] = {
    "job_type":            "sample-hamiltonian-integer",
    "num_samples":         10,
    "relaxation_schedule": 2,
    "sum_constraint":      None,
    "queue_timeout_s":     300,
    "seed":                42,
}


def run(
    conn: sqlite3.Connection,
    problem_hash: str,
    run_id: str,
    solver_config_id: int,
    config: dict[str, Any] | None = None,
) -> str:
    """Run Dirac-3 on one instance and persist the result. Returns the status."""
    config = config or DEFAULT_CONFIG
    inst = load_instance(conn, problem_hash)

    # ----- Encode (feasibility gate) -----
    try:
        payload, flagged = dirac3.encode_problem(
            inst.objective, inst.num_variables, inst.max_degree, config
        )
    except ValueError:
        sol_row, samp = dirac3.build_failure_row("HARD_REJECT")
        write_solution(conn, problem_hash=problem_hash,
                       solver_config_id=solver_config_id, run_id=run_id,
                       solution_row=sol_row, samples_rows=samp)
        return sol_row["status"]

    # Credentials read at call time (after load_dotenv above).
    qci_api_url = os.getenv("QCI_API_URL")
    qci_token   = os.getenv("QCI_TOKEN")
    if not qci_api_url or not qci_token:
        raise EnvironmentError("QCI_API_URL and QCI_TOKEN must both be set.")

    # ----- Submit: upload_file -> build_job_body -> process_job -----
    import qci_client  # type: ignore
    client = qci_client.QciClient(url=qci_api_url, api_token=qci_token, timeout = float(config["queue_timeout_s"]))
    # Bound the blocking process_job so a stuck QCI queue cannot hang the batch.
    # if config.get("queue_timeout_s") is not None:
    #     client.timeout = float(config["queue_timeout_s"])

    t0 = time.perf_counter()
    try:
        file_id = client.upload_file(file=payload["file_payload"])["file_id"]
        job_body = client.build_job_body(
            job_type=payload["job_type"],
            job_params={
                "device_type":         "dirac-3",
                "num_samples":         payload["num_samples"],
                "relaxation_schedule": payload["relaxation_schedule"],
                "num_levels":          payload["num_levels"]
            },
            polynomial_file_id=file_id,
            job_name="hubobench",
        )
        raw_response = client.process_job(job_body=job_body)
    except Exception:
        sol_row, samp = dirac3.build_failure_row(
            "API_ERROR", wall_clock_s=time.perf_counter() - t0
        )
        write_solution(conn, problem_hash=problem_hash,
                       solver_config_id=solver_config_id, run_id=run_id,
                       solution_row=sol_row, samples_rows=samp)
        return sol_row["status"]

    # ----- Decode + persist -----
    sol_row, samp = dirac3.decode_response(
        raw_response, inst.objective, inst.num_variables, flagged
    )
    write_solution(conn, problem_hash=problem_hash,
                   solver_config_id=solver_config_id, run_id=run_id,
                   solution_row=sol_row, samples_rows=samp)
    return sol_row["status"]