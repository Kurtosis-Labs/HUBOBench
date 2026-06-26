# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

HUBOBench benchmarks HUBO (Higher-Order Unconstrained Binary Optimization) solvers. It stores problem
instances and solver results as canonical rows in a single SQLite database (`data/hubobench.db`), runs any
registered solver over the pending instances, and records results in a uniform schema so solvers are
compared on identical problems. `README.md` is the authoritative onboarding doc, especially the
"Adding a new solver" walkthrough — read it before adding a solver. This file captures the operational
details and the places where code diverges from the prose docs.

## Commands

The project uses **uv** (`uv.lock`, `requires-python >=3.12`). All code lives in a single installable
package **`main`** at the repo root (hatchling build; `uv sync` installs it editable). Every directory has
an `__init__.py`, and modules import each other absolutely as `main.<...>` (e.g.
`from main.data.config import ...`). Run entry points with `python -m main.<...>` from the **repo root**.

```bash
uv sync                       # base deps (numpy, openjij, python-dotenv)
uv sync --extra gurobi        # adds gurobipy        (needs GRB_LICENSE_FILE)
uv sync --extra dirac3        # adds qci-client, eqc-models (needs QCI_TOKEN)
```

The database is **gitignored** (`data/*.db`) and created out of band — no Python code applies the DDL.
On a fresh clone, build it from the schema before anything else:

```bash
python -c "import sqlite3; sqlite3.connect('data/hubobench.db').executescript(open('docs/schema.sql').read())"
```

Generate instances, then run solvers:

```bash
# Single instance (run as a module from the repo root).
python -m main.data.synthetic_generator --n 30 --degree 3 --density 0.5 --dr 200 --seed 42
# Full benchmark sweep (degrees × N × dynamic-range zones × densities × seeds; see DR_ZONES / N_BY_DEGREE).
python -m main.data.synthetic_generator --batch

# Run all registered solvers over their pending instances.
python -m main.compiler.agg_runner
# A subset, custom db / run id.
python -m main.compiler.agg_runner --solvers SA_OpenJij gurobi_miqp --db data/hubobench.db --run-id myrun_001
```

Apply pending schema migrations (idempotent tracking-table runner; ordered steps under `main/migrations/`):

```bash
python -m main.migrations.run        # m0001 (0.3→0.4 versions), m0002 (content-addressed solver identity), m0003 (solution 0.4→0.5)
```

There is **no test suite and no linter/formatter** configured.

API/credentialed solvers read `.env` at the repo root (copy `.env.example`): `QCI_API_URL` + `QCI_TOKEN`
for Dirac-3, `GRB_LICENSE_FILE` for Gurobi.

## Architecture

A three-layer pipeline around the SQLite DB as the single source of truth. The boundary between layers is
the design's core invariant — do not blur it.

```
agg_runner  (main/compiler/agg_runner.py)        ← orchestrator: SOLVER_REGISTRY, run_id, pending set, commits
  └─ run wrapper (main/compiler/solvers/run_<solver>.py)   ← the ONLY layer touching both the DB and the live solver
       ├─ load_instance(conn, problem_hash)           ← helpers/instance_loader.py
       ├─ encode_problem(...)  ┐
       ├─ <invoke the solver>  ├─ main/compiler/solver_io/<solver>.py   ← PURE translation: no DB, no I/O
       ├─ decode_response(...) ┘
       └─ write_solution(...)                         ← helpers/solution_writer.py (all upserts live here)
```

- **`main/compiler/solver_io/<solver>.py`** — pure functions `encode_problem` (canonical → solver input) and
  `decode_response` (solver output → preshaped row dicts), plus `build_failure_row`. Exports
  `SOLVER_NAME` and `LIMITS_DOSSIER_VERSION`. **Zero SQL/I/O awareness.** Hard-limit violations (dossier §3)
  `raise ValueError`; soft-limit warnings (dossier §4) ride through in the `flagged`/`flags` return.
- **`main/compiler/solvers/run_<solver>.py`** — orchestrator for one solver. Owns all error handling and all DB
  writes; loads credentials for API solvers. Exports `SOLVER_NAME`, `LIMITS_DOSSIER_VERSION`,
  `DEFAULT_CONFIG`, and `run(conn, problem_hash, run_id, solver_config_id, config) -> status`. Use
  `run_sa_openjij.py` as the in-process reference and `run_dirac3.py` as the upload→submit→poll API reference.
- **`main/compiler/solver_io/helpers/`** — `instance_loader.py` (`load_instance` → `LoadedInstance`),
  `solution_writer.py` (the single place all upserts, skip logic, and config resolution live),
  `decode_common.py` (canonical energy eval, sample grouping/ranking, flag constants).
- **`main/compiler/reduction/rosenberg.py`** — degree-≥3 → quadratic reduction with auxiliary variables, used by
  `gurobi_miqp.encode_problem` at solve time. (The README repo-layout omits this directory.)
- **`main/data/`** — `synthetic_generator.py` (entry point) → `encoding/instance_builder.py:assemble_instance`
  (pure: cardinality penalty, classifier features, hash, SQL row) → `encoding/{apply_cardinality,
  compute_diagnostics}.py`. `config.py` holds generator constants (`EPS_COEF`, …); the schema versions live
  in `main/constants.py` (`PROBLEM_SCHEMA_VERSION = "0.4.0"`, `SOLUTION_SCHEMA_VERSION = "0.5.0"`), migrated by `main/migrations/`.
- **`main/benchmarks/hash.py`** — `compute_problem_hash` is the live hash used by `instance_builder`. The same
  file also carries a legacy `fill_hashes` / `compute_solution_hash` API built around a nested "canonical
  solution dict" that the current SQL-era write path (`solution_writer`) does **not** use — don't wire new
  code to it without checking it's still relevant.

### Database (`docs/schema.sql` is the table contract)

Four tables: **`instances`** (PK = 64-char SHA-256 `problem_hash`; `objective_json` blob is the *sole*
on-disk copy of the polynomial — no instance files exist), **`solver_configs`**, **`runs`**,
**`solutions`** (one row per `(problem_hash, solver_config_id)`), **`samples`** (child rows of a solution).

## Invariants and non-obvious behavior

- **Energy is always recomputed canonically** as `f(x) + constant` from `terms` (`decode_common.evaluate_polynomial`),
  never trusted from the solver. A divergence between the recompute and the solver's reported energy is an
  encoding bug → recorded as the `ENERGY_MISMATCH` flag, not stored as the value.
- **`num_variables` is NOT in `objective_json`** — it's the typed `instances.num_variables` column. But it
  **is** part of the hash input (`instance_builder` §4), so instances differing only in N don't collide.
- **`samples.vars` is a raw byte blob** (1 byte/var, value 0/1), not JSON. Write `bytes(assignment)`; read
  `numpy.frombuffer(row.vars, dtype=numpy.uint8)`.
- **Solver identity is content-addressed.** `solver_configs.solver_identity_hash` = SHA-256 over
  `(solver_name, source_commit, config, environment_digest, dep_lock_digest)`
  (`main/compiler/solver_io/helpers/identity.py`); `UNIQUE(solver_identity_hash)` is the anchor. Changing the
  `DEFAULT_CONFIG` **or** the code commit, dependency lock (`uv.lock`), or container/host forks a **new**
  `solver_config_id`, under which every instance is "pending" again — so a changed solver re-runs the corpus
  rather than overwriting another solver's results. Runs **refuse a dirty git tree** (`HUBOBENCH_ALLOW_DIRTY=1`
  overrides, marking the commit `+dirty`). There is **no `solver_version` column** (`dep_lock_digest` supersedes it).
- **`solutions` upserts in place** on `(problem_hash, solver_config_id)` (the conflict key — a write overwrites
  iff that pair already exists; the `DO UPDATE` has no `WHERE`/status check); `samples` for that solution are
  deleted and rewritten on every (re)write, so a retry never mixes old and new samples.
- **`write_solution` refuses to overwrite a DONE row** (status in `DONE_STATUSES`) unless `force=True` — a
  storage-level guard so a direct/out-of-band write can't clobber a finished result. The orchestrator already
  never routes a DONE instance here, so in normal runs the guard never fires.
- **Skip / retry is driven by `solution_writer.DONE_STATUSES = ("OK", "SUBOPTIMAL_GAP", "HARD_REJECT")`.**
  A `HARD_REJECT` is **terminal** — it is *not* retried. Only `API_ERROR` and `TIMEOUT` are re-run. The
  `solution_writer` docstring and `schema.sql` comments are aligned to this (a prior drift that called
  `HARD_REJECT` retried was fixed alongside the write guard).
- **Failed runs still write a row** (`best_energy = NULL`); only non-null rows are eligible for downstream
  comparison.
- **Two timing fields:** `wall_clock_s` (end-to-end) and `algorithmic_time_s` (solver-internal; equals wall
  clock for in-process solvers like SA).
- **Naming drift to be aware of:** code emits `limits_dossier_version` (plural); some schema docs say
  `limit_dossier_version` (singular). `hash.py` reads either spelling tolerantly.

## Adding a solver

Follow `README.md` "Adding a new solver" (six steps). In short: write the limits dossier
(`docs/limits/<solver>_limits.md`, version-pinned) → `main/compiler/solver_io/<solver>.py` (encode/decode, pure)
→ `main/compiler/solvers/run_<solver>.py` (orchestrator, writes a row on every path) → import + register in
`agg_runner.SOLVER_REGISTRY` keyed by `SOLVER_NAME`. Verify with
`python -m main.compiler.agg_runner --solvers <solver>`. The binding contracts are `docs/hubobench/problem_schema.md`
(encode reads this) and `docs/hubobench/solution_schema.md` (decode returns this).
