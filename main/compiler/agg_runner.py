"""compiler/agg_runner.py

Aggregated runner: the single entry and exit point for HUBOBench solving.

Opens data/hubobench.db, creates one run row for the invocation, and for each
selected solver:
    1. resolves (or creates) its solver_configs row -> solver_config_id
    2. computes the pending problem set (instances with no completed result
       under this solver_config_id; failed attempts are retried)
    3. runs each pending instance through that solver's run() function, which
       writes results via the shared writer.

Skip logic and the in-place upsert live in solution_writer; this file only
orchestrates. One run_id is shared across all solvers/instances in the batch
and is stamped as the "last touched by" run on every row written.

Usage:
    python -m compiler.agg_runner                 # all solvers, all pending
    python -m compiler.agg_runner --solvers dirac3 SA_OpenJij
    python -m compiler.agg_runner --db data/hubobench.db --run-id myrun_001
"""

from __future__ import annotations

import argparse
import sqlite3
import time
import uuid
from typing import Any

from main.compiler.solvers import (
    run_dirac3,
    run_sa_openjij,
    run_gurobi_miqp,
    run_gurobi_nlfunc,
)
from main.compiler.solver_io.helpers.solution_writer import (
    resolve_solver_config_id,
    pending_problem_hashes,
    ensure_run,
)

SOLUTION_SCHEMA_VERSION = "0.3.0"
DEFAULT_DB = "data/hubobench.db"

# solver_name -> runner module. solver_name is the value stored in
# solver_configs.solver_name and used by the skip query.
SOLVER_REGISTRY: dict[str, Any] = {
    run_dirac3.SOLVER_NAME:        run_dirac3,
    run_sa_openjij.SOLVER_NAME:    run_sa_openjij,
    run_gurobi_miqp.SOLVER_NAME:   run_gurobi_miqp,
    run_gurobi_nlfunc.SOLVER_NAME: run_gurobi_nlfunc,
}

def run_batch(
    conn: sqlite3.Connection,
    solver_names: list[str],
    run_id: str,
    notes: str | None = None,
) -> dict[str, dict[str, int]]:
    """Run all selected solvers over their pending instances. Returns a summary.

    summary[solver_name] = {"ran": int, "skipped_done": int, status counts...}
    """
    ensure_run(conn, run_id, SOLUTION_SCHEMA_VERSION, notes)
    conn.commit()
    # host = _host_info()

    summary: dict[str, dict[str, int]] = {}

    for solver_name in solver_names:
        module = SOLVER_REGISTRY[solver_name]
        config = module.DEFAULT_CONFIG

        # ----- Resolve config id (creates the solver_configs row if new) -----
        solver_version = _solver_version(solver_name)
        solver_config_id, created = resolve_solver_config_id(
            conn,
            solver_name=solver_name,
            config=config,
            solver_version=solver_version,
            limits_dossier_version=module.LIMITS_DOSSIER_VERSION,
        )
        conn.commit()

        # ----- Pending set (empty done-set if the config is brand new) -----
        pending = pending_problem_hashes(conn, solver_config_id)

        counts: dict[str, int] = {"ran": 0}
        print(
            f"[agg] {solver_name}: config_id={solver_config_id} "
            f"({'new' if created else 'existing'}); {len(pending)} pending instances"
        )

        for ph in pending:
            try:
                status = module.run(
                    conn, ph, run_id, solver_config_id, config
                )
            except Exception as exc:
                # A runner-level crash is recorded out of band and the batch
                # continues; the instance stays pending for the next run.
                print(f"[agg] {solver_name} ERROR on {ph[:12]}: {exc}")
                counts["error_uncaught"] = counts.get("error_uncaught", 0) + 1
                conn.rollback()
                continue
            counts["ran"] += 1
            counts[status] = counts.get(status, 0) + 1
            conn.commit()

        summary[solver_name] = counts
        print(f"[agg] {solver_name} done: {counts}")

    return summary


def _solver_version(solver_name: str) -> str | None:
    """Best-effort installed library version for the solver_configs row."""
    try:
        if solver_name == "dirac3":
            import qci_client  # type: ignore
            return getattr(qci_client, "__version__", None)
        if solver_name == "SA_OpenJij":
            import openjij  # type: ignore
            return getattr(openjij, "__version__", None)
        if solver_name in ("gurobi_miqp", "gurobi_nlfunc"):
            import gurobipy  # type: ignore
            return ".".join(str(x) for x in gurobipy.gurobi.version())
    except Exception:
        return None
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregated HUBOBench solver runner.")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"Path to hubobench.db. Default: {DEFAULT_DB}")
    parser.add_argument(
        "--solvers", nargs="*", default=list(SOLVER_REGISTRY),
        help="Subset of solver_names to run. Default: all.",
    )
    parser.add_argument("--run-id", default=None, help="Run id. Default: auto-generated.")
    parser.add_argument("--notes", default=None, help="Optional run notes.")
    args = parser.parse_args()

    unknown = [s for s in args.solvers if s not in SOLVER_REGISTRY]
    if unknown:
        raise SystemExit(f"unknown solver_name(s): {unknown}; "
                         f"valid: {list(SOLVER_REGISTRY)}")

    run_id = args.run_id or f"run_{time.strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:6]}"

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        summary = run_batch(conn, args.solvers, run_id, args.notes)
    finally:
        conn.commit()
        conn.close()

    print(f"\n[agg] run_id={run_id} complete.")
    for solver, counts in summary.items():
        print(f"  {solver}: {counts}")


if __name__ == "__main__":
    main()