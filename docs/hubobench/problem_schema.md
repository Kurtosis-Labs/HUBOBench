# Canonical Problem Schema

**Status:** Draft 0.5.0
**Owner:** Chen Mingda

---

## 0. Purpose

Defines the canonical form of a HUBOBench problem instance: what fields are stored, where they live, and how the polynomial is hashed for identity.

Every instance generator writes to this schema. Every solver encoder reads from it.

---

## 1. Scope

| Property | V1 scope |
|---|---|
| Variable domain | `binary_01` only — each $x_i \in \{0, 1\}$ |
| Constraints | Cardinality constraint only, which is penalty-encoded into the objective |
| Objective | Single polynomial, minimisation |
| Polynomial degree | Up to 5 |
| Constants | Allowed — stored in `objective_json.constant` |

All instances are minimisation. `sense` is `"minimize"` everywhere; it participates in the identity hash (§6) but is not stored in the `objective_json` blob, since the solvers assume minimisation.

---

## 2. Storage Architecture

Instances are SQL-only. The `instances` table in `hubobench.db` is the single source of truth.

```
hubobench.db / instances table
├── typed feature columns    — analytical queries
│                              (num_variables, max_degree, density, ...)
└── objective_json BLOB      — the polynomial; read by solver encoders
```
---

## 3. `instances` Table

One row per unique HUBO problem instance. Written by the instance generator (or a format loader) at creation time. Primary key is the full 64-char content-derived hash.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `problem_hash` | TEXT PK | No | Full 64-char SHA-256. Computed by `benchmarks/hash.py :: compute_problem_hash()`. The sole identity; any stored hash from a generator is recomputed on load, not trusted. |
| `problem_schema_version` | TEXT | No | `"0.4.0"` currently. |
| `created_at` | TEXT | No | ISO 8601 UTC insertion timestamp (column default). |
| `num_variables` | INTEGER | No | Number of binary variables N. Typed column; **not** duplicated inside `objective_json`. |
| `max_degree` | INTEGER | No | Highest polynomial degree present. |
| `density` | REAL | No | Fraction of possible degree-`max_degree` monomials that are non-zero. Denominator is $C(N, \text{max\_degree})$. |
| `dynamic_range_ratio` | REAL | No | max\|c\| / min\|c\| across all non-zero coefficients. |
| `coeff_dist` | TEXT | No | Coefficient distribution: `empirical` \| `synthetic` \| `gaussian`. Generator-specific values are mapped to one of these on load (e.g. `log_uniform_signed` → `synthetic`). |
| `num_terms` | INTEGER | No | Total non-zero terms across all degrees. |
| `problem_class` | TEXT | No | `synthetic_random` \| `max_cut` \| `graph_coloring` \| `model_dependent` |
| `constraint_ratio` | REAL | No | k/N where k is the cardinality target. `0.0` for unconstrained. |
| `objective_json` | BLOB | No | Serialised JSON. See §4. |

---

## 4. `objective_json` Blob

Contains exactly the two fields a solver encoder needs.

```json
{
    "terms": [
        {"vars": [0, 2],    "coef": -0.142},
        {"vars": [1, 2, 3], "coef":  0.038}
    ],
    "constant": 0.204
}
```

| Field | Type | Description |
|---|---|---|
| `terms` | list | Each term: `{"vars": [int, ...], "coef": float}`. Variables are 0-based indices in `[0, n_variables)`. In canonical order per §5. |
| `constant` | float | Scalar offset $c_0$. Irrelevant to the optimal $x^*$ but required for objective-value comparison across solvers. |

The full polynomial evaluates as:

$$f(x) = c_0 + \sum_{t \in \text{terms}} c_t \prod_{i \in t.\text{vars}} x_i$$

The encoder read pattern (via the shared `instance_loader`):

```python
# instance_loader.load_instance(conn, problem_hash) returns a LoadedInstance:
#   problem_hash, num_variables (from the typed column),
#   max_degree (from the typed column), objective ({"terms", "constant"}).
# n_variables is NOT read from the blob — it is the typed num_variables column.
inst = load_instance(conn, problem_hash)
# encode_problem(inst.objective, inst.num_variables, inst.max_degree, config)
```

---

## 5. Term Canonicalisation

Required for a deterministic and stable identity hash. All generators (and loaders) must enforce these rules before writing to SQL and before hashing.

1. **Indices within each term sorted ascending.** `{vars: [2, 0, 1], coef: c}` → `{vars: [0, 1, 2], coef: c}`.
2. **No repeated indices within a term.** Binary idempotency: $x_i^2 = x_i$. Generators must simplify before insertion.
3. **No duplicate terms.** Two terms with identical `vars` must be merged into one with summed coefficients.
4. **Zero-coefficient terms omitted.** Terms with $|c_t| < 10^{-15}$ are dropped.
5. **Terms sorted across the list** by `(len(vars), tuple(vars))` ascending. Linear terms first in index order, then quadratic lexicographically, then cubic, and so on.

---

## 6. Reproducibility Hash

`problem_hash` is a SHA-256 digest of the minimum data needed to uniquely identify the problem, and is the `instances` table primary key. Two instances with identical polynomials over the same variable set in the same domain produce the same hash regardless of origin. The generator computes it once at write time (`INSERT OR IGNORE`, so an existing instance is never silently mutated); `load_instance` then trusts the stored hash on the solver hot path. An **explicit** integrity check re-derives every stored row's hash and verifies it still equals the PK — run `python -m main.benchmarks.verify_corpus` (in CI, before scoring, or after any manual DB edit).

### 6.1 Input

```json
{
    "constant": <float>,
    "terms":    [{"coef": <float>, "vars": [<int>, ...]}, ...]
}
```

### 6.2 Serialisation rules

- **Float:** `repr(float)` semantics — shortest decimal that round-trips. Python 3.7+ `json.dumps` satisfies this. `nan` and `inf` are not permitted.
- **Integer:** plain decimal, no leading zeros.
- **JSON:** `json.dumps(..., sort_keys=True, separators=(',', ':'), ensure_ascii=True)`.
- **Hash:** SHA-256 of the resulting UTF-8 byte string, lowercase hex digest.

Reference implementation: `main/benchmarks/hash.py :: compute_problem_hash()`.

---

## 7. Consumption Contract

Each solver_io module reads the canonical objective (via the shared loader) and exposes this interface.

```python
# All solver_io modules expose:
encode_problem(
    objective: dict,        # {"terms": [...], "constant": float}
    num_variables: int,     # the typed instances.num_variables
    max_degree: int,        # the typed instances.max_degree
    config: dict,
) -> tuple[payload, flagged]            # flagged: bool, pre-submission warning fired

decode_response(
    raw_response,           # solver-native response object/dict
    objective: dict,
    num_variables: int,
    flagged: bool = False,  # threaded through from encode
) -> tuple[solution_row, samples_rows]  # see solution_schema.md §6
```

Notes:

- `encode_problem` returns `(payload, flagged)`. On a hard reject it raises `ValueError` whose second arg is the structured reject list; the runner records a `HARD_REJECT` row.
- `decode_response` takes `flagged` as an *input* threaded from encode (it becomes a `DYNAMIC_RANGE_WARNING`, or `AUX_VIOLATION` flag at decode), not an output.
- The return is `(solution_row, samples_rows)`, both preshaped for direct insert. See `solution_schema.md` §6 for their exact shapes (`best_vars_json` is a JSON string; sample `vars` is raw bytes).

---

## 8. Version Log

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-05-11 | M. Chen | Initial draft |
| 0.2 | 2026-06-08 | M. Chen | Tightened draft, eliminated unnecessary columns |
| 0.3.0 | 2026-06-12 | M. Chen | SQL-only redesign. Rosenberg reduction, ground truth, diagnostics, and generator metadata removed from storage — each computed at runtime by the component that needs it. |
| 0.4.0 | 2026-06-25 | tamkaize | Header/version unified to 0.4.0 (matches the `problem_schema_version` field cell). No problem-schema field changes. Version constants centralized in `main/constants.py`; legacy `0.3.0` rows migrated by migration step `m0001_v03_to_v04`. |
| 0.5.0 | 2026-06-26 | M.Chen | Hash versions have been updated to only include SQL objective json. `num_variables`, `sense`, `variable_domain` have been dropped |