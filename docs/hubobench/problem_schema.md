# Canonical Problem Schema

**Status:** Draft 0.3.0
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
| Constraints | Cardinality constraint only which is penalty-encoded into the objective |
| Objective | Single polynomial, minimisation |
| Polynomial degree | Up to 5 |
| Constants | Allowed — stored in `objective_json.constant` |

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

One row per unique HUBO problem instance. Written by the instance generator at creation time. Primary key is the full 64-char content-derived hash.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `problem_hash` | TEXT PK | No | Full 64-char SHA-256. Computed by `benchmarks/hash.py :: compute_problem_hash()`. |
| `problem_schema_version` | TEXT | No | `"0.3.0"` currently. |
| `created_at` | TEXT | No | ISO 8601 UTC insertion timestamp. |
| `num_variables` | INTEGER | No | Number of binary variables N. |
| `max_degree` | INTEGER | No | Highest polynomial degree present. |
| `density` | REAL | No | Fraction of possible degree-`max_degree` monomials that are non-zero. Denominator is $C(N, `max_degree`)$. |
| `dynamic_range_ratio` | REAL | No | max\|c\| / min\|c\| across all non-zero coefficients. |
| `coeff_dist` | TEXT | No | Coefficient distribution: `empirical` \| `synthetic` \| `gaussian`. |
| `num_terms` | INTEGER | No | Total non-zero terms across all degrees. |
| `problem_class` | TEXT | No | `synthetic_random` \| `max_cut` \| `graph_coloring` \| `model_dependent` |
| `constraint_ratio` | REAL | No | k/N where k is the cardinality target. `0.0` for unconstrained. |
| `objective_json` | BLOB | No | Serialised JSON. See §4. |

---

## 4. `objective_json` Blob

Contains two fields needed by solver encoders.

```json
{
    "terms": [
        {"vars": [0, 2],    "coef": -0.142},
        {"vars": [1, 2, 3], "coef":  0.038}
    ],
    "constant":    0.204,
}
```

| Field | Type | Description |
|---|---|---|
| `terms` | list | Each term: `{"vars": [int, ...], "coef": float}`. Variables are 0-based indices in `[0, n_variables)`. In canonical order per §5. |
| `constant` | float | Scalar offset $c_0$. Irrelevant to the optimal $x^*$ but required for objective value comparison across solvers. |

The full polynomial evaluates as:

$$f(x) = c_0 + \sum_{t \in \text{terms}} c_t \prod_{i \in t.\text{vars}} x_i$$

The encoder read pattern:

```python
row      = db.execute(
    "SELECT objective_json FROM instances WHERE problem_hash = ?", [problem_hash]
).fetchone()
instance = json.loads(row["objective_json"])

# encode_problem(instance, config) reads instance["terms"],
# instance["constant"], instance["n_variables"]
```

---

## 5. Term Canonicalisation

Required for a deterministic and stable reproducibility hash. All generators must enforce these rules before writing to SQL.

1. **Indices within each term sorted ascending.** `{vars: [2, 0, 1], coef: c}` → `{vars: [0, 1, 2], coef: c}`.
2. **No repeated indices within a term.** Binary idempotency: $x_i^2 = x_i$. Generators must simplify before insertion.
3. **No duplicate terms.** Two terms with identical `vars` must be merged into one with summed coefficients.
4. **Zero-coefficient terms omitted.** Terms with $|c_t| < 10^{-15}$ are dropped.
5. **Terms sorted across the list** by `(len(vars), tuple(vars))` ascending. Linear terms first in index order, then quadratic lexicographically, then cubic, and so on.

---

## 6. Reproducibility Hash

`problem_hash` is a SHA-256 digest of the minimum data needed to uniquely identify the problem. Two instances with identical polynomials over the same variable set in the same domain produce the same hash regardless of origin.

### 6.1 Input

```json
{
    "objective": {
        "constant": <float>,
        "terms":    [{"coef": <float>, "vars": [<int>, ...]}, ...]
    },
    "parameters": {
        "n_variables":     <int>,
        "variable_domain": "binary_01"
    }
}
```

### 6.2 Excluded fields

Everything except the polynomial and variable parameters is excluded. Schema version, generator metadata, and all derived blocks (diagnostics, rosenberg, ground truth) do not affect problem identity.

### 6.3 Serialisation rules

- **Float:** `repr(float)` semantics — shortest decimal that round-trips. Python 3.7+ `json.dumps` satisfies this. `nan` and `inf` are not permitted.
- **Integer:** plain decimal, no leading zeros.
- **JSON:** `json.dumps(..., sort_keys=True, separators=(',', ':'), ensure_ascii=True)`.
- **Hash:** SHA-256 of the resulting UTF-8 byte string, lowercase hex digest.

Reference implementation: `benchmarks/hash.py :: compute_problem_hash()`.

---

## 7. Consumption Contract

Each solver encoder reads from the `objective_json` blob and returns a tuple from `encode_problem`.

```python
# All solver_io modules expose this interface:
encode_problem(instance: dict, config: dict) -> tuple[payload, pre_submission_flags]
decode_response(raw_response, instance: dict, config: dict, host: dict, flags: list) -> dict
```

---

## 8. Version Log

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-05-11 | M. Chen | Initial draft |
| 0.2 | 2026-06-08 | M. Chen | Tightened draft, eliminated unnecessary columns |
| 0.3.0 | 2026-06-12 | M. Chen | SQL-only redesign. Rosenberg reduction, ground truth, diagnostics, and generator metadata removed from storage — each computed at runtime by the component that needs it. |