# Canonical Solution Schema

**Status:** Draft 0.3.0
**Owner:** Chen Mingda

---

## 0. Purpose

Defines two things:

1. **The canonical solution dict** — the Python dict that every `compiler/solver_io/*.decode_response()` must return. This is the public contract for implementing new solver_io modules.

2. **The SQL persistence schema** — how the runner maps that dict into `hubobench.db`.

---

## 1. Storage Architecture

Solutions are SQL-first. No canonical solution JSON files are written to disk.

```
hubobench.db
├── runs               — one row per benchmark batch
├── solver_configs     — one row per unique solver configuration
├── solutions          — one row per (problem_hash, solver_config_id, run_id)
└── samples            — one row per unique sample per stochastic solution
```

The runner holds both database connections simultaneously and handles all writes. The solver_io layer has no awareness of SQL — it receives a raw solver response and returns a canonical Python dict.

---

## 2. `runs` Table

One row per benchmark batch. Written by the runner before any solves begin.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `run_id` | TEXT PK | No | Unique batch identifier. Format: `run_{uuid4_hex[:8]}`. |
| `solution_schema_version` | TEXT | No | This document's version — `"0.3.0"`. |
| `created_at` | TEXT | No | ISO 8601 UTC timestamp. |
| `notes` | TEXT | Yes | Optional description of this benchmark batch. |

---

## 3. `solver_configs` Table

One row per unique solver configuration. Written by the runner on first use of a configuration; subsequent runs reuse the existing row.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `solver_config_id` | INTEGER AUTOINCREMENT | No | Surrogate key. Stable FK for `solutions`. |
| `solver_name` | TEXT | No | `dirac3` \| `gurobi_miqp` \| `SA_OpenJij` |
| `solver_version` | TEXT | Yes | Solver library version string. Null if unavailable. |
| `limits_dossier_version` | TEXT | No | Dossier version governing feasibility thresholds for this config. |
| `config_json` | TEXT | No | Full solver parameter dict as JSON with sorted keys. |

---

## 4. `solutions` Table

One row per `(problem_hash, solver_config_id, run_id)`. Written by the runner after `decode_response` returns.

Failed runs (`HARD_REJECT`, `TIMEOUT` with no incumbent, `API_ERROR`) are written with `best_energy = NULL` and `best_vars_json = NULL`. Only rows where `best_energy IS NOT NULL` are eligible for label assignment in `benchmark_records`.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `solution_id` | INTEGER PK | No | Surrogate key. Stable FK for `samples`. |
| `problem_hash` | TEXT FK | No | Links to `instances.problem_hash`. |
| `solver_config_id` | INTEGER FK | No | Links to `solver_configs.solver_config_id`. |
| `run_id` | TEXT FK | No | Links to `runs.run_id`. |
| `status` | TEXT | No | See §8 for valid values. |
| `best_energy` | REAL | Yes | Canonical $f(x^*) + c_0$. Null for failed runs. |
| `best_vars_json` | TEXT | Yes | JSON array of 0/1 integers length N. The winning variable assignment. Null for failed runs. |
| `wall_clock_s` | REAL | Yes | See §9. Null for `HARD_REJECT`. |
| `algorithmic_time_s` | REAL | Yes | See §9. Null for failed runs. |
| `flags` | TEXT | YES | Flags raised during run |

---

## 5. `samples` Table

One row per unique sample per stochastic solver solution.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `sample_id` | INTEGER PK | No | Surrogate key |
| `solution_id` | INTEGER FK | No | Links to `solutions.solution_id`. |
| `sample_rank` | INTEGER | No | 0 = best energy within this solution. |
| `energy` | REAL | No | Canonical $f(x) + c_0$ for this sample. |
| `count` | INTEGER | No | Times this exact assignment appeared in the raw solver response. Weight signal for confidence scoring. |
| `vars` | BLOB | No | Raw bytes: one byte per variable, value 0 or 1. Length N bytes. |

Validation (all values in {0, 1}) is enforced before insertion.

---

## 6. Canonical Solution Dict

The Python dict that every `decode_response` implementation must return. This is the sole public contract for implementing a new solver_io module. The runner reads this dict and writes to SQL — the solver_io layer has no SQL awareness.

```python
{
    # Outcome
    "status":             str,           # see §8
    "best_energy":        float | None,  # None for failed runs
    "best_vars":          list[int] | None,  # 0/1 assignment; None for failed runs

    # Timing
    "wall_clock_s":       float | None,  # see §9
    "algorithmic_time_s": float | None,  # see §9

    # Solver version
    "solver_version":     str | None,    # library version string

    # Samples (stochastic solvers only; empty list for Gurobi)
    "samples": [
        {
            "rank":   int,       # 0 = best energy
            "energy": float,     # canonical f(x) + constant
            "count":  int,       # occurrences in raw response
            "vars":   list[int], # 0/1 assignment of length N
        },
        ...
    ],
}
```

### 6.1 Rules

- `best_vars` must match `samples[best_sample_index].vars` for stochastic solvers.
- `samples` must be empty for any status other than `OK` or `SUBOPTIMAL_GAP`.
- `samples` must be sorted by `energy` ascending (rank 0 = minimum energy).
- `count` values must sum to the total number of reads submitted to the sampler.

---

## 7. Field Mapping: Dict → SQL

How the runner maps `decode_response` output to SQL writes.

| Dict field | SQL destination | Notes |
|---|---|---|
| `status` | `solutions.status` | Direct write |
| `best_energy` | `solutions.best_energy` | Direct write |
| `best_vars` | `solutions.best_vars_json` | `json.dumps(best_vars)` |
| `wall_clock_s` | `solutions.wall_clock_s` | Direct write |
| `algorithmic_time_s` | `solutions.algorithmic_time_s` | Direct write |
| `solver_version` | `solver_configs.solver_version` | Written at config upsert time |
| `samples[i].rank` | `samples.sample_rank` | Per row |
| `samples[i].energy` | `samples.energy` | Per row |
| `samples[i].count` | `samples.count` | Per row |
| `samples[i].vars` | `samples.vars` | `bytes(vars_list)` |

The runner also provides `problem_hash`, `solver_config_id`, and `run_id` from its own context — these are not in the dict.

---

## 8. Status Codes

| Value | Meaning | `best_energy` | `samples` |
|---|---|---|---|
| `OK` | Solver returned results normally. | Populated | Non-empty |
| `SUBOPTIMAL_GAP` | Gurobi only: time/node limit hit with a feasible incumbent. | Populated | Non-empty (1 sample) |
| `TIMEOUT` | Wall-clock budget exhausted with no feasible sample produced. | Null | Empty |
| `HARD_REJECT` | Pre-submission feasibility check refused the instance. | Null | Empty |
| `API_ERROR` | Solver-side error: rejected payload, network failure, license error. | Null | Empty |

`SUBOPTIMAL_GAP` is Gurobi-only. All stochastic solvers return `OK` if any samples were produced.

---

## 9. Timing Field Definitions

| Field | Dirac-3 | Gurobi | SA / OpenJij |
|---|---|---|---|
| `wall_clock_s` | End-to-end: encode → API submit → queue wait → device execute → receive → decode | encode → `model.optimize()` → decode | encode → `sample_hubo()` → decode |
| `algorithmic_time_s` | `device_usage_s` from job result — billed device time, queue stripped | `model.Runtime` — solver computation time only | Equal to `wall_clock_s` — SA is in-process with no overhead to strip |

---

## 10. Version Log

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-05-11 | M. Chen | Initial draft |
| 0.2 | 2026-06-08 | M. Chen | Tightened draft, eliminated unnecessary columns |
| 0.3.0 | 2026-06-12 | M. Chen | SQL-first redesign. Canonical solution dict replaces JSON file format. Storage schema defined across runs, solver_configs, solutions, samples tables. |
