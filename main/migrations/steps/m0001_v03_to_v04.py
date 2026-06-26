"""m0001 — migrate 0.3.0 schema-version strings to 0.4.0.

Decision B (version-strings-only): rewrite the version columns on existing
rows; leave the `flags` column untouched. Under the 0.4.0 rule `NULL` flags are
reserved for pre-0.4.0 rows, so leaving them alone preserves that marker rather
than fabricating `'[]'` for rows that predate the convention.

Forward-looking: on a fresh DB (no 0.3.0 rows) this updates nothing and is still
recorded as applied — a no-op. It is idempotent on its own via the `WHERE` clause,
and the runner additionally skips any step already recorded in schema_migrations.
"""

from __future__ import annotations

import sqlite3

STEP_ID = "m0001_v03_to_v04"
DESCRIPTION = "Bump 0.3.0 schema-version strings to 0.4.0 (version strings only)."


def apply(conn: sqlite3.Connection) -> dict[str, int]:
    """Rewrite 0.3.0 -> 0.4.0 on instances and runs. Returns rows touched per table."""
    instances = conn.execute(
        "UPDATE instances SET problem_schema_version = '0.4.0' "
        "WHERE problem_schema_version = '0.3.0'"
    ).rowcount
    runs = conn.execute(
        "UPDATE runs SET solution_schema_version = '0.4.0' "
        "WHERE solution_schema_version = '0.3.0'"
    ).rowcount
    return {"instances": instances, "runs": runs}
