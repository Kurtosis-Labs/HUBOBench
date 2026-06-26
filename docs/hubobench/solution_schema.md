# Canonical Solution Schema

**Status:** Draft 0.5.0
**Owner:** Chen Mingda

---

## 0. Purpose

Defines two things:

1. **The canonical solution row** — the Python dict that every `compiler/solver_io/*.decode_response()` must return as its first element. This is the public contract for implementing new solver_io modules.

2. **The SQL persistence schema** — how the runner maps that dict into `hubobench.db`.

---

## 1. Storage Architecture

Solutions are SQL-first. No canonical solution JSON files are written to disk.

```
hubobench.db
├── runs               — one row per benchmark batch
├── solver_configs     — one row per unique solver configuration
├── solutions          — one row per (problem_hash, solver_config_id)
└── samples            — one row per unique sample per stochastic solution
```

The runner holds the database connection and handles all writes. The solver_io layer has no awareness of SQL: `decode_response` receives a raw solver response and returns `(solution_row, samples_rows)` as preshaped Python dicts. The runner injects the orchestration keys (`problem_hash`, `solver_config_id`, `run_id`) and writes.

---

## 2. `runs` Table

One row per benchmark batch. Written by the runner before any solves begin.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `run_id` | TEXT PK | No | Unique batch identifier. Format: `run_{YYYYMMDDTHHMMSSZ}_{uuid4_hex[:6]}`. |
| `solution_schema_version` | TEXT | No | This document's version — `"0.5.0"`. |
| `created_at` | TEXT | No | ISO 8601 UTC timestamp (column default). |
| `notes` | TEXT | Yes | Optional description of this benchmark batch. |

---

## 3. `solver_configs` Table

One row per unique solver **identity**. Written by the runner on first use of an identity; subsequent runs with the same identity reuse the existing row.

Identity is **content-addressed**: `solver_identity_hash` = SHA-256 over `(solver_name, source_commit, config, environment_digest, dep_lock_digest)`. Any change to the benchmark code commit, the dependency lock, the container/host, or the config forks a new identity — so results from materially different solvers are never pooled under one `solver_config_id`. Provenance is captured by the runner (`main/compiler/solver_io/helpers/identity.py`); runs **refuse a dirty git tree** unless `HUBOBENCH_ALLOW_DIRTY=1`.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `solver_config_id` | INTEGER PK AUTOINCREMENT | No | Surrogate key for a unique solver identity. Stable FK for `solutions`. |
| `solver_name` | TEXT | No | `dirac3` \| `gurobi_miqp` \| `gurobi_nlfunc` \| `SA_OpenJij` — the human-readable solver type. |
| `limits_dossier_version` | TEXT | No | Dossier version governing feasibility thresholds for this config. |
| `config_json` | TEXT | No | Full solver parameter dict as JSON with sorted keys. |
| `solver_identity_hash` | TEXT | No | The identity anchor — SHA-256 over name + commit + config + environment + dep-lock. |
| `source_commit` | TEXT | No | `git HEAD` at run time (`+dirty` if `HUBOBENCH_ALLOW_DIRTY=1` was set). |
| `environment_digest` | TEXT | No | Container image digest if set (`HUBOBENCH_CONTAINER_DIGEST`), else a host fingerprint. |
| `dep_lock_digest` | TEXT | No | SHA-256 of `uv.lock`. |

`UNIQUE (solver_identity_hash)` — the key the aggregator resolves against to find or create the surrogate `solver_config_id`. (`solver_version` was removed; the precise dependency state lives in `dep_lock_digest`.)

---

## 4. `solutions` Table

One row per `(problem_hash, solver_config_id)`. Written by the runner after `decode_response` returns.

`UNIQUE (problem_hash, solver_config_id)` is the identity. `run_id` is **not** part of the key — it is a "last touched by" payload column. A retry of a failed run upserts the existing row in place (`ON CONFLICT(problem_hash, solver_config_id) DO UPDATE`), stamping the new `run_id`, rather than inserting a duplicate. This is what makes failed instances recomputable without row proliferation.

Failed runs (`HARD_REJECT`, `TIMEOUT` with no incumbent, `API_ERROR`) are written with `best_energy = NULL` and `best_vars_json = NULL`. Only rows where `best_energy IS NOT NULL` are eligible for label assignment in `benchmark_records`.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `solution_id` | INTEGER PK | No | Surrogate key. Stable FK for `samples`. |
| `problem_hash` | TEXT FK | No | Links to `instances.problem_hash`. |
| `solver_config_id` | INTEGER FK | No | Links to `solver_configs.solver_config_id`. |
| `run_id` | TEXT FK | No | Links to `runs.run_id`. "Last touched by" — updated on every upsert. |
| `status` | TEXT | No | Termination status. See §8 for valid values. |
| `best_energy` | REAL | Yes | Canonical $f(x^*) + c_0$. Null for failed runs. |
| `best_vars_json` | TEXT | Yes | JSON array of 0/1 integers length N. The winning variable assignment. Null for failed runs. |
| `wall_clock_s` | REAL | Yes | See §9. Null for `HARD_REJECT`. |
| `algorithmic_time_s` | REAL | Yes | See §9. Null for failed runs. |
| `flags` | TEXT | Yes | JSON list of orthogonal annotations. See §8.1. `'[]'` = checked and clean; `NULL` = the row predates flag computation (never checked). |

---

## 5. `samples` Table

One row per unique sample per stochastic solver solution. Gurobi writes no samples (its single assignment lives in `solutions.best_vars_json`).

| Column | Type | Nullable | Description |
|---|---|---|---|
| `sample_id` | INTEGER PK | No | Surrogate key |
| `solution_id` | INTEGER FK | No | Links to `solutions.solution_id`. |
| `sample_rank` | INTEGER | No | 0 = best energy within this solution. |
| `energy` | REAL | No | Canonical $f(x) + c_0$ for this sample. |
| `count` | INTEGER | No | Times this exact assignment appeared in the raw solver response. Weight signal for confidence scoring. |
| `vars` | BLOB | No | Raw bytes: one byte per variable, value 0 or 1. Length N bytes. Read back via `numpy.frombuffer(vars, dtype=uint8)`. |

Validation (all values in {0, 1}) is enforced before insertion.

---

## 6. Canonical Solution Row

`decode_response` returns a tuple `(solution_row, samples_rows)`:

- `solution_row` is the dict below — the outcome fields the writer inserts into `solutions`.
- `samples_rows` is a list of sample dicts (empty for Gurobi).

The runner injects `problem_hash`, `solver_config_id`, and `run_id`; the solver_io layer never sees them.

```python
# solution_row — returned as the FIRST tuple element
{
    "status":             str,           # termination only; see §8
    "best_energy":        float | None,  # canonical f(x*) + c0; None for failed runs
    "best_vars_json":     str | None,    # JSON array string of 0/1 ints; None for failed runs
    "wall_clock_s":       float | None,  # see §9
    "algorithmic_time_s": float | None,  # see §9
    "flags":              str,           # JSON list string; '[]' when clean. See §8.1
}

# samples_rows — returned as the SECOND tuple element (empty list for Gurobi)
[
    {
        "sample_rank": int,    # 0 = best energy
        "energy":      float,  # canonical f(x) + constant
        "count":       int,    # occurrences in raw response
        "vars":        bytes,  # raw bytes, one per variable (0/1), length N
    },
    ...
]
```

### 6.1 Rules

- `best_vars_json` must match the rank-0 sample's assignment for stochastic solvers.
- `samples_rows` must be empty for any status other than `OK` or `SUBOPTIMAL_GAP`.
- `samples_rows` must be sorted by `energy` ascending (rank 0 = minimum energy).
- `count` values sum to the total number of reads/samples returned by the solver.
- `flags` is always a valid JSON list string (`'[]'` when clean), never `None`, from a decode. `NULL` in the column is reserved for pre-0.4.0 rows.

---

## 7. Field Mapping: Dict → SQL

How the runner maps `decode_response` output to SQL writes.

| Dict field | SQL destination | Notes |
|---|---|---|
| `status` | `solutions.status` | Direct write |
| `best_energy` | `solutions.best_energy` | Direct write |
| `best_vars_json` | `solutions.best_vars_json` | Direct write (already a JSON string) |
| `wall_clock_s` | `solutions.wall_clock_s` | Direct write |
| `algorithmic_time_s` | `solutions.algorithmic_time_s` | Direct write |
| `flags` | `solutions.flags` | Direct write (JSON list string) |
| `samples_rows[i].sample_rank` | `samples.sample_rank` | Per row |
| `samples_rows[i].energy` | `samples.energy` | Per row |
| `samples_rows[i].count` | `samples.count` | Per row |
| `samples_rows[i].vars` | `samples.vars` | Direct write (already bytes) |

The runner provides `problem_hash`, `solver_config_id`, and `run_id` from its own context; the aggregator captures the run provenance (`source_commit`, `environment_digest`, `dep_lock_digest`) at config-upsert time. None of these are in the decode output.

---

## 8. Status Codes

`status` records **only how a run terminated**. Precedence when several apply: `HARD_REJECT > API_ERROR > TIMEOUT > SUBOPTIMAL_GAP > OK`.

| Value | Meaning | `best_energy` | `samples` |
|---|---|---|---|
| `OK` | Solver returned results normally. | Populated | Non-empty (stochastic) / empty (Gurobi) |
| `SUBOPTIMAL_GAP` | A feasible incumbent was returned but optimality is not guaranteed: Gurobi hit a time/node limit with an incumbent, or a Rosenberg aux-constraint violation means the returned x may be suboptimal. | Populated | Non-empty (Gurobi: assignment in `best_vars_json`) |
| `TIMEOUT` | Wall-clock / device budget exhausted with no feasible sample produced. | Null | Empty |
| `HARD_REJECT` | Pre-submission feasibility check refused the instance. | Null | Empty |
| `API_ERROR` | Solver-side error: rejected payload, network failure, license/allocation exhausted. | Null | Empty |

`DONE_STATUSES = (OK, SUBOPTIMAL_GAP, HARD_REJECT)` — the skip query treats these as completed work. `TIMEOUT` and `API_ERROR` are not done and are retried (and upserted in place) on the next run. `HARD_REJECT` is a deliberate, terminal rejection and is never retried. `write_solution` additionally refuses to overwrite any row already in a DONE status unless `force=True`, so a completed result cannot be silently clobbered.

**`FLAGGED` is retired.** In prior versions a pre-submission warning produced `status = FLAGGED`. That conflated a termination state with an annotation. Warnings now live in `flags` and never gate rerun; a warned-but-completed run keeps its real termination status (`OK` / `SUBOPTIMAL_GAP`) and is therefore DONE.

### 8.1 Flags

`flags` is a JSON list of orthogonal annotations on a result. Any number may co-occur. A flag is a persistent property of the instance/result (re-running cannot clear it), so it is recorded but **never gates rerun**.

| Flag | Meaning | Set by |
|---|---|---|
| `ENERGY_MISMATCH` | Recomputed canonical energy diverges from the solver-reported energy by > 1e-5 — a possible encoding bug. | dirac3, SA_OpenJij, gurobi_nlfunc |
| `DYNAMIC_RANGE_WARNING` | Poor coefficient conditioning flagged pre-submission. | dirac3, gurobi_miqp, gurobi_nlfunc |
| `AUX_VIOLATION` | Rosenberg auxiliary constraint $z \ne x_i x_j$ violated (penalty M too weak). Also degrades `status` to `SUBOPTIMAL_GAP`. | gurobi_miqp |

`ENERGY_MISMATCH` is **not** emitted by `gurobi_miqp`: its `model.ObjVal` is the Rosenberg-reduced objective (with penalty terms), a different function from the HUBO, so it legitimately differs from the canonical recompute and must not be flagged.

Encoding: `'[]'` means checked and clean; a non-empty list names the fired flags; `NULL` (column only, never from a decode) means the row predates flag computation.

---

## 9. Timing Field Definitions

| Field | Dirac-3 | Gurobi | SA / OpenJij |
|---|---|---|---|
| `wall_clock_s` | End-to-end: `completed_at − submitted_at` from the job status block, queue wait included. | encode → `model.optimize()` → decode (runner-stamped around `optimize()`). | encode → `sample_hubo()` → decode (runner-stamped). |
| `algorithmic_time_s` | `device_usage_s` from the job result — billed device time, queue stripped. | `model.Runtime` — solver computation time only. | Equal to `wall_clock_s` — SA is in-process with no queue/device split to strip. |

---

## 10. Version Log

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-05-11 | M. Chen | Initial draft |
| 0.2 | 2026-06-08 | M. Chen | Tightened draft, eliminated unnecessary columns |
| 0.3.0 | 2026-06-12 | M. Chen | SQL-first redesign. Canonical solution dict replaces JSON file format. Storage schema defined across runs, solver_configs, solutions, samples tables. |
| 0.4.0 | 2026-06-25 | tamkaize | `flags` is always a JSON list string (`'[]'` when clean), never `None` from a decode; `NULL` reserved for pre-0.4.0 rows. `FLAGGED` status retired (warnings live in `flags`, not the termination status). Version constants centralized in `main/constants.py`; legacy `0.3.0` rows migrated by `main/migrations/` step `m0001_v03_to_v04` (version strings only). `solver_configs` re-keyed to a content-addressed identity (drop `solver_version`; add `solver_identity_hash` + `source_commit` / `environment_digest` / `dep_lock_digest`; anchor `UNIQUE(solver_identity_hash)`), migrated by `m0002_solver_identity` — see §3. (The stamped solution version was initially held at 0.4.0; subsequently bumped to 0.5.0 — see below.) |
| 0.5.0 | 2026-06-26 | tamkaize | Stamped solution version bumped to mark the content-addressed `solver_configs` identity (m0002, §3) and a new `write_solution` overwrite guard: a completed row (status in `DONE_STATUSES` = `OK` / `SUBOPTIMAL_GAP` / `HARD_REJECT`) cannot be overwritten unless `force=True`, turning the orchestrator's skip convention into a storage-level guarantee. Retry semantics in the `solution_writer` docstring and `schema.sql` comments corrected to match `DONE_STATUSES` (`HARD_REJECT` is terminal, not retried). Solution-row *columns* are unchanged; existing `runs` re-stamped 0.4.0→0.5.0 by `m0003_solution_v05`. |