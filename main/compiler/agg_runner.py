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

Solvers are discovered dynamically (compiler/registry.py) — drop a
run_<solver>.py into compiler/solvers/ and it registers itself. What to run is
either the named solvers at their defaults (--solvers) or a declared experiment
set with per-experiment config overrides (--manifest).

Usage:
    python -m main.compiler.agg_runner                 # all solvers, all pending
    python -m main.compiler.agg_runner --solvers dirac3 SA_OpenJij
    python -m main.compiler.agg_runner --manifest experiments.json
    python -m main.compiler.agg_runner --list-solvers
    python -m main.compiler.agg_runner --db data/hubobench.db --run-id myrun_001
"""

from __future__ import annotations

import argparse
import sqlite3
import time
import uuid
from typing import Any

from main.compiler.registry import discover_solvers
from main.compiler.manifest import Experiment, load_manifest
from main.compiler.solver_io.helpers.solution_writer import (
    resolve_solver_config_id,
    pending_problem_hashes,
    ensure_run,
)
from main.compiler.solver_io.helpers.identity import capture_provenance
from main.constants import SOLUTION_SCHEMA_VERSION

DEFAULT_DB = "data/hubobench.db"

# solver_name -> runner module, built by scanning compiler/solvers/run_*.py.
# solver_name is the value stored in solver_configs.solver_name.
SOLVER_REGISTRY: dict[str, Any] = discover_solvers()


def run_batch(
    conn: sqlite3.Connection,
    experiments: list[Experiment],
    run_id: str,
    notes: str | None = None,
) -> list[dict[str, Any]]:
    """Run each experiment over its pending instances. Returns a per-experiment
    result list: [{"solver", "solver_config_id", "created", "counts"}, ...].

    Each experiment resolves its OWN solver_config_id from its config, so the
    same solver at two configs forks two identities and two pending sets.
    """
    ensure_run(conn, run_id, SOLUTION_SCHEMA_VERSION, notes)
    conn.commit()

    # Capture run provenance once (refuses on a dirty tree; see identity.py).
    provenance = capture_provenance()

    results: list[dict[str, Any]] = []

    for exp in experiments:
        module = SOLVER_REGISTRY[exp.solver]

        # ----- Resolve config id (content-addressed identity; new row if unseen) -----
        solver_config_id, created = resolve_solver_config_id(
            conn,
            solver_name=exp.solver,
            config=exp.config,
            limits_dossier_version=module.LIMITS_DOSSIER_VERSION,
            provenance=provenance,
        )
        conn.commit()

        # ----- Pending set (empty done-set if the config is brand new) -----
        pending = pending_problem_hashes(conn, solver_config_id)

        counts: dict[str, int] = {"ran": 0}
        print(
            f"[agg] {exp.solver}: config_id={solver_config_id} "
            f"({'new' if created else 'existing'}); {len(pending)} pending instances"
        )

        for ph in pending:
            try:
                status = module.run(
                    conn, ph, run_id, solver_config_id, exp.config
                )
            except Exception as exc:
                # A runner-level crash is recorded out of band and the batch
                # continues; the instance stays pending for the next run.
                print(f"[agg] {exp.solver} ERROR on {ph[:12]}: {exc}")
                counts["error_uncaught"] = counts.get("error_uncaught", 0) + 1
                conn.rollback()
                continue
            counts["ran"] += 1
            counts[status] = counts.get(status, 0) + 1
            conn.commit()

        results.append({
            "solver":           exp.solver,
            "solver_config_id": solver_config_id,
            "created":          created,
            "counts":           counts,
        })
        print(f"[agg] {exp.solver} (cfg {solver_config_id}) done: {counts}")

    return results


def _list_solvers() -> None:
    """Print the discovered registry (name, dossier version, default config)."""
    for name in sorted(SOLVER_REGISTRY):
        module = SOLVER_REGISTRY[name]
        print(name)
        print(f"    dossier_version : {module.LIMITS_DOSSIER_VERSION}")
        print(f"    default_config  : {module.DEFAULT_CONFIG}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregated HUBOBench solver runner.")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"Path to hubobench.db. Default: {DEFAULT_DB}")
    parser.add_argument(
        "--solvers", nargs="*", default=None,
        help="Subset of solver_names to run at their DEFAULT_CONFIG. Default: all.",
    )
    parser.add_argument(
        "--manifest", default=None,
        help="Path to a JSON experiment manifest (solver + config overrides). "
             "Mutually exclusive with --solvers.",
    )
    parser.add_argument(
        "--list-solvers", action="store_true",
        help="Print the discovered solver registry and exit.",
    )
    parser.add_argument("--run-id", default=None, help="Run id. Default: auto-generated.")
    parser.add_argument("--notes", default=None, help="Optional run notes.")
    args = parser.parse_args()

    if args.list_solvers:
        _list_solvers()
        return

    if args.manifest and args.solvers is not None:
        raise SystemExit("use either --manifest or --solvers, not both.")

    # Build the experiment list: from the manifest, or from named solvers at
    # their defaults (the legacy path).
    notes = args.notes
    if args.manifest:
        experiments, manifest_notes = load_manifest(args.manifest, SOLVER_REGISTRY)
        notes = notes or manifest_notes
    else:
        solver_names = args.solvers if args.solvers is not None else list(SOLVER_REGISTRY)
        unknown = [s for s in solver_names if s not in SOLVER_REGISTRY]
        if unknown:
            raise SystemExit(f"unknown solver_name(s): {unknown}; "
                             f"valid: {sorted(SOLVER_REGISTRY)}")
        experiments = [
            Experiment(solver=s, config=SOLVER_REGISTRY[s].DEFAULT_CONFIG)
            for s in solver_names
        ]

    run_id = args.run_id or f"run_{time.strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:6]}"

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        results = run_batch(conn, experiments, run_id, notes)
    finally:
        conn.commit()
        conn.close()

    print(f"\n[agg] run_id={run_id} complete.")
    for r in results:
        print(f"  {r['solver']} (cfg {r['solver_config_id']}): {r['counts']}")


if __name__ == "__main__":
    main()