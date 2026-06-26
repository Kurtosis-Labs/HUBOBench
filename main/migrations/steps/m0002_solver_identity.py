"""m0002 — content-addressed solver identity.

Transforms `solver_configs` from the config-only identity
(`UNIQUE(solver_name, config_json)`, with a `solver_version` column) to the
content-addressed identity (`UNIQUE(solver_identity_hash)`, with provenance
columns and no `solver_version`). See issue #8.

Decision: **no backfill.** Pre-existing rows have no provenance, so rather than
fabricate legacy sentinels this step REGENERATES — it clears `solver_configs`
and its dependent `solutions` / `samples` (instances are kept; the solvers are
simply re-run). On a fresh DB created from the current `schema.sql`,
`solver_configs` is already in the new shape, so this step is a recorded no-op.
"""

from __future__ import annotations

import sqlite3

STEP_ID = "m0002_solver_identity"
DESCRIPTION = (
    "Content-address solver identity (UNIQUE(solver_identity_hash)); "
    "no backfill — regenerate."
)

_NEW_SOLVER_CONFIGS = """
CREATE TABLE solver_configs (
    solver_config_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    solver_name             TEXT    NOT NULL,
    limits_dossier_version  TEXT    NOT NULL,
    config_json             TEXT    NOT NULL,
    solver_identity_hash    TEXT    NOT NULL,
    source_commit           TEXT    NOT NULL,
    environment_digest      TEXT    NOT NULL,
    dep_lock_digest         TEXT    NOT NULL,
    UNIQUE (solver_identity_hash)
)
"""


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def apply(conn: sqlite3.Connection) -> dict:
    """Rebuild solver_configs to the content-addressed schema. No-op if already current."""
    if "solver_identity_hash" in _columns(conn, "solver_configs"):
        # Fresh DB (schema.sql already defines the new shape) — nothing to migrate.
        return {"status": "already current"}

    # Old schema. No backfill: clear provenance-less results, then rebuild.
    n_cfg = conn.execute("SELECT COUNT(*) FROM solver_configs").fetchone()[0]
    n_sol = conn.execute("SELECT COUNT(*) FROM solutions").fetchone()[0]
    n_samp = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]

    conn.execute("DELETE FROM samples")
    conn.execute("DELETE FROM solutions")
    conn.execute("DROP TABLE solver_configs")
    conn.execute(_NEW_SOLVER_CONFIGS)

    if n_cfg:
        print(
            f"[m0002] regenerated identity: cleared {n_cfg} solver_configs, "
            f"{n_sol} solutions, {n_samp} samples (instances kept — re-run the solvers)."
        )
    return {"cleared_solver_configs": n_cfg, "cleared_solutions": n_sol, "cleared_samples": n_samp}
