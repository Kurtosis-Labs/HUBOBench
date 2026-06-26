"""m0003 — bump SOLUTION_SCHEMA_VERSION 0.4.0 -> 0.5.0 on existing runs.

0.5.0 marks the content-addressed `solver_configs` schema (m0002 / issue #8) and
the `write_solution` overwrite guard. The solution-row *columns* are unchanged,
so this step only re-stamps `runs.solution_schema_version`; new rows are already
stamped 0.5.0 by main.constants.SOLUTION_SCHEMA_VERSION.

Idempotent via the WHERE clause; a no-op (still recorded) on a fresh DB with no
0.4.0 runs. Mirrors m0001_v03_to_v04's version-string-only approach.
"""

from __future__ import annotations

import sqlite3

STEP_ID = "m0003_solution_v05"
DESCRIPTION = "Bump solution_schema_version 0.4.0 -> 0.5.0 on runs (version string only)."


def apply(conn: sqlite3.Connection) -> dict[str, int]:
    """Rewrite 0.4.0 -> 0.5.0 on runs.solution_schema_version. Returns rows touched."""
    runs = conn.execute(
        "UPDATE runs SET solution_schema_version = '0.5.0' "
        "WHERE solution_schema_version = '0.4.0'"
    ).rowcount
    return {"runs": runs}
