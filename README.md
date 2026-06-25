# HUBOBench

An open benchmarking framework for HUBO (Higher-Order Unconstrained Binary Optimization) solvers.

HUBOBench stores problem instances and solver results as canonical rows in a single SQLite database (`hubobench.db`), runs any registered solver over the pending instances, and records results in a uniform schema so that solvers can be compared on identical problems.

---

## Contents

- [Concepts](#concepts)
- [Repository layout](#repository-layout)
- [Quick start](#quick-start)
- [Adding a new solver](#adding-a-new-solver)
  - [Step 1 — Write the limits dossier](#step-1--write-the-limits-dossier)
  - [Step 2 — Write the encode function](#step-2--write-the-encode-function)
  - [Step 3 — Write the decode function](#step-3--write-the-decode-function)
  - [Step 4 — Write the run wrapper](#step-4--write-the-run-wrapper)
  - [Step 5 — Register in agg_runner](#step-5--register-in-agg_runner)
  - [Step 6 — Schema reference](#step-6--schema-reference)
- [Checklist](#checklist)

---

## Concepts

| Term | Meaning |
|---|---|
| **Instance** | One HUBO problem: a polynomial over binary variables, minimisation. Stored as one row in the `instances` table. |
| **Canonical form** | The single agreed shape of an instance (see `problem_schema.md`). Every solver reads this; no solver sees a bespoke format. |
| **solver_io module** | A pure translation layer for one solver: `encode_problem` (canonical → solver input) and `decode_response` (solver output → canonical solution dict). No database awareness. |
| **run wrapper** | The orchestrator for one solver: loads the instance, calls encode, invokes the solver, calls decode, writes the result. |
| **agg_runner** | The single entry point. Iterates selected solvers over their pending instances. |

The mental model: **agg_runner** is the conductor, the **run wrapper** is one musician, the **solver_io module** is that musician's sheet-music translator, and the **limits dossier** is the instrument's spec sheet that tells you what it physically cannot play.

---

## Repository layout

```
hubobench/                                # repo root
├── data/
│   └── hubobench.db                      # canonical instances + results (gitignored, runtime)
├── main/                                 # the installable package (import root)
│   ├── data/
│   │   ├── config.py                     # generator config + shared constants
│   │   ├── synthetic_generator.py        # generator entry point
│   │   └── encoding/                     # generator helpers
│   │       ├── instance_builder.py
│   │       ├── compute_diagnostics.py
│   │       └── apply_cardinality.py
│   ├── compiler/
│   │   ├── agg_runner.py                 # entry point; solver registry
│   │   ├── solvers/                      # run wrappers (one per solver)
│   │   │   ├── run_sa_openjij.py
│   │   │   ├── run_gurobi_miqp.py
│   │   │   └── ...
│   │   └── solver_io/                    # encode/decode (one per solver)
│   │       ├── sa_openjij.py
│   │       ├── gurobi_miqp.py
│   │       ├── ...
│   │       └── helpers/
│   │           ├── instance_loader.py    # load_instance(conn, problem_hash)
│   │           ├── solution_writer.py    # write_solution(...) + config/run helpers
│   │           └── decode_common.py      # shared decode utilities
│   └── benchmarks/
│       └── hash.py                       # canonical problem hashing
├── docs/
│   ├── limits/                           # limit dossiers
│   │   ├── dossier_template.md           # template for new limits dossiers
│   │   ├── sa_openjij_limits.md
│   │   └── ...
│   └── hubobench/
│       ├── problem_schema.md             # canonical instance shape
│       └── solution_schema.md            # canonical solution dict + SQL schema
├── pyproject.toml                        # deps + hatchling packaging (package = main)
└── README.md                             # Project description
```

---

## Quick start

```bash
# Generate synthetic instances into hubobench.db
python -m main.data.synthetic_generator --degree 4 --density 0.3 --dr 100

# Run all registered solvers over all pending instances
python -m main.compiler.agg_runner

# Run a subset
python -m main.compiler.agg_runner --solvers SA_OpenJij gurobi_miqp
```

---

## Adding a new solver

Adding a solver is six steps. The contract you implement is two functions (`encode_problem`, `decode_response`) plus a thin `run` wrapper; everything else is wiring. Use the existing **SA / OpenJij** triplet as the reference implementation — it is the simplest (in-process, no API credentials).

The data flow you are slotting into:

```
agg_runner
   └─ run wrapper (main/compiler/solvers/run_<solver>.py)
        ├─ load_instance(conn, problem_hash)        ← reads canonical instance
        ├─ encode_problem(...)   ─┐
        │                         ├─ solver_io/<solver>.py   (the part you write)
        ├─ <call the solver>      │
        ├─ decode_response(...)  ─┘
        └─ write_solution(...)                       ← writes canonical solution
```

### Step 1 — Write the limits dossier

**The dossier is the source of truth for what the solver can and cannot accept.** Write it first, because the encode function's pre-submission checks are derived directly from it.

1. Copy `dossiers/dossier_template.md` to `dossiers/<solver>_limits.md`.
2. Fill every section. The ones that drive code:
   - **§1 Problem class** — the exact input form. Determines your encode mapping.
   - **§3 Hard limits** — values that cause a rejected submission. Each becomes a pre-submission check in encode (returns a flag rather than submitting a doomed payload).
   - **§4 Soft limits** — conditioning thresholds (e.g. coefficient dynamic range) that degrade quality without erroring. Each becomes a warning flag.
   - **§7 Failure modes** — every condition a checker could detect, classified `pre-submission` / `post-result` / `during`. The `pre-submission` rows are exactly what encode checks.
3. Pin the solver version (§ header) and set a dossier version string. **The run wrapper publishes this version into `solver_configs.limits_dossier_version`**, so it must exist before Step 4.

A dossier describes only the solver. It must not reference HUBOBench internals, other dossiers, or what any consumer does with the results. See the inline guidance in the template.

### Step 2 — Write the encode function

Create `main/compiler/solver_io/<solver>.py`. Encode translates a canonical instance into the solver's native input.

**Canonical instance shape** (from `problem_schema.md` §4 — read it before writing this):

```python
objective = {
    "terms":    [{"vars": [0, 2], "coef": -0.142}, {"vars": [1, 2, 3], "coef": 0.038}],
    "constant": 0.204,
}
```

- `vars` are 0-based variable indices, sorted ascending within each term.
- Terms are pre-canonicalised: no duplicate terms, no repeated indices, no zero coefficients. You do **not** re-clean them.
- The `constant` does not change the optimal assignment but is needed for cross-solver energy comparison.

**Signature** (match the SA reference exactly):

```python
SOLVER_NAME = "<solver>"
LIMITS_DOSSIER_VERSION = "0.1"     # must match the dossier you wrote in Step 1

def encode_problem(
    objective: dict,
    num_variables: int,
    max_degree: int,
    config: dict,
) -> tuple[payload, flagged]:
    """Translate the canonical objective into the solver's native input.

    Returns:
        payload:  whatever your run wrapper hands to the solver.
        flagged:  bool (or a flags list) for pre-submission warnings derived
                  from dossier §4. Raise instead for hard rejects (dossier §3).
    """
```

Rules:
- **Hard limit violations (dossier §3):** raise `ValueError`. The run wrapper catches it and writes a `HARD_REJECT` / `API_ERROR` row without submitting.
- **Soft limit warnings (dossier §4):** do not raise. Return them in `flagged` so they ride through to the solution row.
- Keep encode pure: no DB calls, no solver invocation, no I/O. Translation only.

Worked reference — SA builds a `sample_hubo` polynomial dict and rejects non-binary vartype:

```python
terms = objective["terms"]
polynomial = {tuple(t["vars"]): float(t["coef"]) for t in terms}
# ... assemble sampler_kwargs from config ...
return payload, False
```

### Step 3 — Write the decode function

In the same module, decode translates the raw solver response into the **canonical solution dict** (from `solution_schema.md` §6 — the sole contract).

**Signature** (match the SA reference):

```python
def decode_response(
    raw_response,
    objective: dict,
    num_variables: int,
    flagged: bool = False,
) -> tuple[solution_row, samples_rows]:
```

**`solution_row` must contain:**

| Key | Type | Notes |
|---|---|---|
| `status` | str | `OK`, `SUBOPTIMAL_GAP`, `TIMEOUT`, `HARD_REJECT`, `API_ERROR` (schema §8) |
| `best_energy` | float \| None | Canonical $f(x^*) + \text{constant}$. `None` on failure |
| `best_vars_json` | str \| None | JSON array of 0/1 ints, length N. `None` on failure |
| `wall_clock_s` | float \| None | End-to-end time |
| `algorithmic_time_s` | float \| None | Solver-internal time; equals wall clock for in-process solvers |
| `flags` | str | `flags_json([...])` from `decode_common` |

**`samples_rows`** (stochastic solvers; empty list for exact solvers like Gurobi): each row `{rank, energy, count, vars}`, sorted by energy ascending, rank 0 = best. See schema §5–6.

Use the shared helpers in `main/compiler/solver_io/helpers/decode_common.py` rather than re-implementing:

| Helper | Purpose |
|---|---|
| `group_samples(samples, occurrences)` | Collapse identical assignments, summing counts |
| `build_sample_rows(grouped, terms, constant)` | Recompute canonical energy per sample and shape rows |
| `best_from_samples(rows)` | Extract `(best_energy, best_vars_json)` |
| `energy_mismatch(ours, sampler)` | Detect encode bugs: our recomputed energy vs the solver's reported energy |
| `flags_json([...])`, `FLAG_ENERGY_MISMATCH` | Flag serialisation and constants |

**Energy is always recomputed canonically from `terms` + `constant`, never trusted from the solver.** A divergence between your recompute and the solver's reported energy is an encoding bug; record it as `ENERGY_MISMATCH` rather than silently storing a wrong value.

Provide a `build_failure_row(status, wall_clock_s=0.0)` helper returning the `(solution_row, [])` shape for the failure paths your run wrapper will hit.

### Step 4 — Write the run wrapper

Create `main/compiler/solvers/run_<solver>.py`. This is the orchestrator: it is the only layer that touches both the database and the live solver.

It must expose three module-level names that `agg_runner` reads:

```python
SOLVER_NAME = <solver_io>.SOLVER_NAME
LIMITS_DOSSIER_VERSION = <solver_io>.LIMITS_DOSSIER_VERSION
DEFAULT_CONFIG = { ... }     # the parameter dict; becomes solver_configs.config_json
```

And a `run` function with this exact signature (agg_runner calls it positionally):

```python
def run(
    conn: sqlite3.Connection,
    problem_hash: str,
    run_id: str,
    solver_config_id: int,
    config: dict | None = None,
) -> str:                      # returns the status string
```

The required sequence (follow `run_sa_openjij.py` line for line):

1. `inst = load_instance(conn, problem_hash)` — gives you `inst.objective`, `inst.num_variables`, `inst.max_degree`.
2. `encode_problem(...)` inside a `try`. On `ValueError`, write a failure row via `write_solution(...)` and return early.
3. Invoke the solver inside a `try`, timing it with `time.perf_counter()`. On any exception, write a failure row with the measured wall clock and return.
4. `decode_response(...)` to get `(sol_row, samp)`.
5. `write_solution(conn, problem_hash=..., solver_config_id=..., run_id=..., solution_row=sol_row, samples_rows=samp)`.
6. `return sol_row["status"]`.

The wrapper owns all error handling and all DB writes. Encode and decode stay pure. For an API solver, this is also where credentials load and where the queue/submit/poll lifecycle lives (see `run_dirac3.py` for the three-step API pattern).

### Step 5 — Register in agg_runner

Two edits in `main/compiler/agg_runner.py`:

1. Import the wrapper:
   ```python
   from main.compiler.solvers import (
       run_sa_openjij,
       run_<solver>,        # add
       ...
   )
   ```
2. Add it to `SOLVER_REGISTRY` (keyed by `SOLVER_NAME`):
   ```python
   SOLVER_REGISTRY = {
       run_sa_openjij.SOLVER_NAME:  run_sa_openjij,
       run_<solver>.SOLVER_NAME:    run_<solver>,    # add
       ...
   }
   ```

Reproducibility provenance — the git commit, `uv.lock` digest, and container/host fingerprint — is captured automatically by the runner and folded into the solver's content-addressed identity (`solver_configs.solver_identity_hash`). There is no per-solver version step to add. Note: runs refuse a dirty git tree unless `HUBOBENCH_ALLOW_DIRTY=1`.

That is the whole registration. agg_runner now resolves the config id, computes the pending set (instances with no completed result under this config; failed attempts retry automatically), and runs your wrapper over each. Verify:

```bash
python -m main.compiler.agg_runner --solvers <solver>
```

### Step 6 — Schema reference

The two schema documents are the binding contracts. Read them; do not infer the shapes from code alone.

| Document | Defines | You touch it in |
|---|---|---|
| `problem_schema.md` | The `instances` table, the `objective_json` blob shape, term canonicalisation rules, the reproducibility hash | Step 2 (encode reads this) |
| `solution_schema.md` | The canonical solution dict, status codes, timing definitions, and the `runs` / `solver_configs` / `solutions` / `samples` tables | Step 3 (decode returns this) |

Key invariants to respect:

- **Instances are SQL-only.** The `instances` table is the single source of truth; there are no instance files on disk.
- **The solver_io layer has zero SQL awareness.** It receives a raw response and returns a dict. All persistence is the runner's job.
- **Energy is canonical.** Always $f(x) + \text{constant}$ recomputed from the polynomial, never the solver's self-reported number.
- **Failed runs still write a row** (`best_energy = NULL`), so the skip logic does not re-run permanent rejects on every batch. Only non-null rows are eligible for downstream comparison.
- **Timing has two fields.** `wall_clock_s` is end-to-end; `algorithmic_time_s` strips queue/overhead (equal to wall clock for in-process solvers). See solution_schema §9.

---

## Checklist

A new solver is complete when:

- [ ] `dossiers/<solver>_limits.md` exists, version pinned, §3 and §4 filled.
- [ ] `main/compiler/solver_io/<solver>.py` exports `SOLVER_NAME`, `LIMITS_DOSSIER_VERSION`, `encode_problem`, `decode_response`, `build_failure_row`.
- [ ] `encode_problem` raises on dossier §3 hard limits, flags on §4 soft limits, performs no I/O.
- [ ] `decode_response` returns the canonical `(solution_row, samples_rows)`; energy recomputed canonically.
- [ ] `main/compiler/solvers/run_<solver>.py` exports `SOLVER_NAME`, `LIMITS_DOSSIER_VERSION`, `DEFAULT_CONFIG`, `run`.
- [ ] `run` handles encode failure, solver failure, and success, writing a row in every case.
- [ ] Imported and registered in `agg_runner.SOLVER_REGISTRY`.
- [ ] `python -m main.compiler.agg_runner --solvers <solver>` runs end to end and writes rows.