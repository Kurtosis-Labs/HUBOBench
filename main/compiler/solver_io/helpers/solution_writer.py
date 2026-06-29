"""compiler/solver_io/helpers/solution_writer.py

Shared DB-write layer for HUBOBench solver runs.

All inserts/updates into hubobench.db go through here so the upsert keys,
column lists, and skip logic live in exactly one place. solver_io decode
functions produce preshaped row dicts; this module stamps the orchestration
FKs (solver_config_id, run_id, solution_id) and writes them.

Identity model (agreed):
    solutions  — one row per (problem_hash, solver_config_id).
                 run_id is a "last touched by" payload column. Failed/missing rows are UPDATED in 
                 place on retry via ON CONFLICT(problem_hash, solver_config_id) DO UPDATE.
    samples    — child rows of a solution; deleted and rewritten whenever the
                 parent solution row is (re)written, so a retried run never
                 leaves stale samples from the previous attempt.

Skip logic (source of truth: DONE_STATUSES below):
    An instance is DONE for a given solver_config_id iff a solutions row exists
    for it with status IN ('OK','SUBOPTIMAL_GAP','HARD_REJECT'). Only the
    transient failures (API_ERROR, TIMEOUT) are NOT done and are re-run, updated
    in place. HARD_REJECT is terminal — a deliberate rejection, never retried.

Write guard:
    write_solution refuses to overwrite a row already in a DONE status (unless
    force=True). The orchestrator already never routes a DONE instance here, so
    this only ever blocks an out-of-band / direct write — it turns the skip
    convention into a storage-level guarantee that a completed result cannot be
    silently clobbered.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any



DONE_STATUSES = ("OK", "SUBOPTIMAL_GAP", "HARD_REJECT")

# Column contract for the solutions upsert. solution_id is autoincrement;
# problem_hash + solver_config_id are the conflict key; the rest are payload.
_SOLUTION_COLUMNS = (
    "problem_hash",
    "solver_config_id",
    "run_id",
    "status",
    "best_energy",
    "best_vars_json",
    "wall_clock_s",
    "algorithmic_time_s",
    "flags",
)

# Outcome fields a decode() row is allowed to carry. The runner injects the
# three it cannot know (problem_hash is known to the loader, but we let the
# runner pass it explicitly for symmetry; solver_config_id and run_id are
# pure orchestration).
_DECODE_OUTCOME_FIELDS = (
    "status",
    "best_energy",
    "best_vars_json",
    "wall_clock_s",
    "algorithmic_time_s",
    "flags",
)


# ─────────────────────────────────────────────────────────────────────────────
# solver_configs upsert + skip query
# ─────────────────────────────────────────────────────────────────────────────

def resolve_solver_config_id(
    conn: sqlite3.Connection,
    solver_name: str,
    config: dict[str, Any],
    limits_dossier_version: str,
    environment_digest: str,
) -> tuple[int, bool]:
    """
    Find or create the solver configuration row for a solver, configuration, and environment.
    
    Parameters:
    	solver_name (str): Solver name.
    	config (dict[str, Any]): Solver configuration to store as canonical JSON.
    	limits_dossier_version (str): Limits dossier version to record for new rows.
    	environment_digest (str): Digest identifying the execution environment.
    
    Returns:
    	tuple[int, bool]: The solver configuration ID and `True` when a new row was inserted, `False` when an existing row was found.
    """
    config_json = json.dumps(config, sort_keys=True, separators=(",", ":"))

    row = conn.execute(
        "SELECT solver_config_id FROM solver_configs "
        "WHERE solver_name = ? AND config_json = ? AND environment_digest = ?",
        (solver_name, config_json, environment_digest),
    ).fetchone()
    if row is not None:
        return int(row[0]), False

    cur = conn.execute(
        "INSERT INTO solver_configs "
        "(solver_name, limits_dossier_version, config_json, environment_digest) "
        "VALUES (?, ?, ?, ?)",
        (solver_name, limits_dossier_version, config_json, environment_digest),
    )
    return int(cur.lastrowid), True


def pending_problem_hashes(
    conn: sqlite3.Connection,
    solver_config_id: int,
) -> list[str]:
    """Return problem_hashes NOT yet completed under this solver_config_id.

    A two-step anti-join: every instance whose hash does not appear in
    solutions with a DONE status for this config.
    """
    placeholders = ",".join("?" for _ in DONE_STATUSES)
    sql = f"""
        SELECT problem_hash FROM instances
        WHERE problem_hash NOT IN (
            SELECT problem_hash FROM solutions
            WHERE solver_config_id = ?
              AND status IN ({placeholders})
        )
    """
    params = (solver_config_id, * DONE_STATUSES)
    return [r[0] for r in conn.execute(sql, params)]


# ─────────────────────────────────────────────────────────────────────────────
# solutions upsert + samples insert
# ─────────────────────────────────────────────────────────────────────────────

def write_solution(
    conn: sqlite3.Connection,
    *,
    problem_hash: str,
    solver_config_id: int,
    run_id: str,
    solution_row: dict[str, Any],
    samples_rows: list[dict[str, Any]],
    force: bool = False,
) -> int:
    """Upsert one solution and rewrite its samples. Returns solution_id.

    solution_row carries only the outcome fields a decoder can know:
        status, best_energy, best_vars_json, wall_clock_s, algorithmic_time_s
    This function injects problem_hash, solver_config_id, run_id, then upserts
    on (problem_hash, solver_config_id): a new instance inserts, an existing
    one (e.g. a prior failure) is updated in place with the new run_id.

    A completed result is protected: if a row already exists for this
    (problem_hash, solver_config_id) with a status in DONE_STATUSES, the write
    is refused (ValueError) unless force=True. In normal operation the
    orchestrator never routes a DONE instance here, so this only ever fires for
    an out-of-band / direct write — making "don't clobber a finished result" a
    storage-level guarantee rather than a convention upstream.

    samples_rows each carry: sample_rank, energy, count, vars (raw bytes).
    They must NOT carry solution_id — it is stamped here after the parent row
    is known. Existing samples for the solution are deleted first so a retry
    never mixes old and new samples. Gurobi passes samples_rows=[].

    Raises:
        ValueError: if solution_row has stray/missing outcome keys, if a
            samples row is missing a required field or carries solution_id, or
            if it would overwrite a DONE row without force=True.
    """
    # ── validate the outcome contract ────────────────────────────────────
    got = set(solution_row)
    expected = set(_DECODE_OUTCOME_FIELDS)
    if got != expected:
        raise ValueError(
            f"solution_row outcome mismatch for problem_hash={problem_hash}: "
            f"missing={sorted(expected - got)} stray={sorted(got - expected)}"
        )

    # ── refuse to clobber a completed result (storage-level guarantee) ────
    if not force:
        prior = conn.execute(
            "SELECT status FROM solutions "
            "WHERE problem_hash = ? AND solver_config_id = ?",
            (problem_hash, solver_config_id),
        ).fetchone()
        if prior is not None and prior[0] in DONE_STATUSES:
            raise ValueError(
                f"refusing to overwrite a completed solution (status={prior[0]}) "
                f"for problem_hash={problem_hash}, solver_config_id={solver_config_id}; "
                f"pass force=True to override."
            )

    full_row = {
        "problem_hash":     problem_hash,
        "solver_config_id": solver_config_id,
        "run_id":           run_id,
        **solution_row,
    }

    # ── upsert solutions on (problem_hash, solver_config_id) ──────────────
    cols         = ", ".join(_SOLUTION_COLUMNS)
    placeholders = ", ".join(f":{c}" for c in _SOLUTION_COLUMNS)
    update_set   = ", ".join(
        f"{c}=excluded.{c}"
        for c in _SOLUTION_COLUMNS
        if c not in ("problem_hash", "solver_config_id")
    )
    upsert = (
        f"INSERT INTO solutions ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT(problem_hash, solver_config_id) DO UPDATE SET {update_set}"
    )
    try:
        conn.execute(upsert, full_row)
    except sqlite3.Error as exc:
        raise sqlite3.Error(
            f"solutions upsert failed for problem_hash={problem_hash}, "
            f"solver_config_id={solver_config_id}: {exc}"
        ) from exc

    # ── recover the solution_id (insert OR update path) ───────────────────
    sol_id = conn.execute(
        "SELECT solution_id FROM solutions "
        "WHERE problem_hash = ? AND solver_config_id = ?",
        (problem_hash, solver_config_id),
    ).fetchone()[0]
    sol_id = int(sol_id)

    # ── rewrite samples: clear old, insert new ────────────────────────────
    conn.execute("DELETE FROM samples WHERE solution_id = ?", (sol_id,))
    for s in samples_rows:
        s_keys = set(s)
        if "solution_id" in s_keys:
            raise ValueError(
                "samples row must not carry solution_id; it is stamped by the writer"
            )
        required = {"sample_rank", "energy", "count", "vars"}
        if not required.issubset(s_keys):
            raise ValueError(
                f"samples row missing fields {sorted(required - s_keys)}"
            )
        conn.execute(
            "INSERT INTO samples (solution_id, sample_rank, energy, count, vars) "
            "VALUES (?, ?, ?, ?, ?)",
            (sol_id, int(s["sample_rank"]), float(s["energy"]),
             int(s["count"]), s["vars"]),
        )

    return sol_id


def ensure_run(
    conn: sqlite3.Connection,
    run_id: str,
    solution_schema_version: str,
    notes: str | None = None,
) -> None:
    """Insert the runs row for this batch if it does not already exist."""
    conn.execute(
        "INSERT OR IGNORE INTO runs (run_id, solution_schema_version, notes) "
        "VALUES (?, ?, ?)",
        (run_id, solution_schema_version, notes),
    )