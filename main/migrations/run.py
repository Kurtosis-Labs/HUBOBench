"""Idempotent HUBOBench schema-migration runner.

    python -m main.migrations.run [--db data/hubobench.db]

Walks STEPS (an ordered, explicit list) and applies every step not yet recorded
in the schema_migrations tracking table, recording each (step_id + applied_at)
as it goes and committing per step. Re-running once all steps are applied is a
no-op. Add new steps by writing a module under steps/ and appending it to STEPS.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone

from main.constants import PROBLEM_SCHEMA_VERSION, SOLUTION_SCHEMA_VERSION
from main.migrations.steps import m0001_v03_to_v04, m0002_solver_identity

DEFAULT_DB = "data/hubobench.db"

# Ordered, explicit migration steps. Append new steps to the END — order is the
# application order and must never be reshuffled once a step has shipped.
STEPS = [
    m0001_v03_to_v04,
    m0002_solver_identity,
]


def _ensure_tracking_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "    step_id    TEXT PRIMARY KEY,"
        "    applied_at TEXT NOT NULL"
        ")"
    )


def _applied_ids(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT step_id FROM schema_migrations")}


def run(conn: sqlite3.Connection, *, now: str | None = None) -> list[str]:
    """Apply all pending steps in order. Returns the step_ids applied on THIS call.

    `now` overrides the recorded timestamp (used by tests for determinism).
    """
    _ensure_tracking_table(conn)
    done = _applied_ids(conn)
    applied_now: list[str] = []
    for step in STEPS:
        if step.STEP_ID in done:
            continue
        result = step.apply(conn)
        stamp = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            "INSERT INTO schema_migrations (step_id, applied_at) VALUES (?, ?)",
            (step.STEP_ID, stamp),
        )
        conn.commit()
        applied_now.append(step.STEP_ID)
        print(f"[migrate] applied {step.STEP_ID}: {result}")
    return applied_now


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply pending HUBOBench schema migrations (idempotent)."
    )
    parser.add_argument(
        "--db", default=DEFAULT_DB, help=f"Path to hubobench.db. Default: {DEFAULT_DB}"
    )
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        applied = run(conn)
    finally:
        conn.commit()
        conn.close()

    if applied:
        print(f"[migrate] done; applied {len(applied)} step(s): {applied}")
    else:
        print("[migrate] up to date; nothing to apply.")
    print(
        f"[migrate] current target versions: "
        f"problem={PROBLEM_SCHEMA_VERSION} solution={SOLUTION_SCHEMA_VERSION}"
    )


if __name__ == "__main__":
    main()
