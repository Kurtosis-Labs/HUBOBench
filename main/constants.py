"""Single source of truth for HUBOBench schema versions.

Both versions are independent and both currently sit at 0.4.0:

- PROBLEM_SCHEMA_VERSION  — stamped into instances.problem_schema_version
  (see main/data/encoding/instance_builder.py).
- SOLUTION_SCHEMA_VERSION — stamped into runs.solution_schema_version
  (see main/compiler/agg_runner.py → ensure_run → solution_writer).

Bumping either constant only changes the version stamped onto *new* rows.
Existing rows are migrated explicitly by an ordered step under main/migrations/
(see m0001_v03_to_v04). The two versions are deliberately separate so the
problem and solution schemas can evolve independently.

Out of scope here: per-solver LIMITS_DOSSIER_VERSION values, which are
independent and are NOT unified with these.
"""

from __future__ import annotations

PROBLEM_SCHEMA_VERSION = "0.4.0"
SOLUTION_SCHEMA_VERSION = "0.4.0"
