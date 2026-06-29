"""Single source of truth for HUBOBench schema versions.

The two versions are independent and evolve separately:

- PROBLEM_SCHEMA_VERSION  (0.4.0) — stamped into instances.problem_schema_version
  (see main/data/encoding/instance_builder.py).
- SOLUTION_SCHEMA_VERSION (0.5.0) — stamped into runs.solution_schema_version
  (see main/compiler/agg_runner.py → ensure_run → solution_writer). Bumped to
  0.5.0 for the content-addressed solver_configs identity and the
  write_solution overwrite guard.

Bumping either constant only changes the version stamped onto *new* rows.
docs/schema.sql is the single source of truth for the schema: a fresh DB is born
at the current versions, so there is no migration step. The live corpus was
brought to these versions by a one-time migration that has since been removed.

Out of scope here: per-solver LIMITS_DOSSIER_VERSION values, which are
independent and are NOT unified with these.
"""

from __future__ import annotations

PROBLEM_SCHEMA_VERSION = "0.5.0"
SOLUTION_SCHEMA_VERSION = "0.5.0"
