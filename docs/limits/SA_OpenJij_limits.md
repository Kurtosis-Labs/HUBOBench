# SA (OpenJij) Limits Dossier

**Status:** Draft v0.2
**Owner:** Chen Mingda
**Solver version pinned:** OpenJij 0.10.x (≥ 0.10.0). Verify with `pip show openjij`.

---

## 0. Sources

The OpenJij GitHub README requests that work using OpenJij specify the exact version used. [GH]

| Tier | Meaning |
|---|---|
| 1 | Authoritative — official reference manual / user guide |
| 2 | Semi-official — vendor help-centre / KB article |
| 3 | Assumption — inferred, not documented; flagged inline as [ASSUMPTION] |

| Tag | Source | URL | Tier |
|---|---|---|---|
| [REF] | OpenJij API Reference (current docs) | https://openjij.github.io/OpenJij/reference/openjij/index.html | 1 |
| [BOOK] | OpenJij Book (tutorial) | https://tutorial.openjij.org/ | 1 |
| [GH] | OpenJij GitHub repository | https://github.com/Jij-Inc/OpenJij | 1 |
| [HUBO-TUT] | HUBO tutorial page | https://tutorial.openjij.org/en/tutorial/002-HuboSolver.html | 1 |

---

## 1. Problem class accepted

OpenJij's `SASampler.sample_hubo()` accepts a polynomial of arbitrary degree over binary variables. [REF, HUBO-TUT]

**Canonical form (objective Hamiltonian):**

$$
H = \sum_{i} h_{i} \sigma_{i} + \sum_{i<j} J_{ij} \sigma_{i} \sigma_{j} + \sum_{i,j,k} K_{ijk} \sigma_{i} \sigma_{j} \sigma_{k} + \cdots
$$

- **Variable domain(s):** binary $\sigma_i \in \{0, 1\}$ ("BINARY") or spin $\sigma_i \in \{-1, +1\}$ ("SPIN"). [REF]
- **Max polynomial degree:** unbounded by the algorithm. [REF]
- **Connectivity:** all-to-all; no embedding required. [HUBO-TUT]
- **Coefficient type:** float (Python double precision). [REF]
- **Direction:** minimisation. [BOOK]
- **Native constraint support:** none. Constrained problems must be penalty-encoded by the caller before submission. [HUBO-TUT]
- **Constant terms:** algorithmically irrelevant; accepted in input. [REF]
- **Term encoding:** Python dict mapping tuple-of-indices → coefficient (float). [REF]
- **Duplicate terms:** not specified; callers should pre-merge. [ASSUMPTION]

**Term encoding example** [HUBO-TUT]:

```python
J = {(0,): -1, (0, 1): -1, (0, 1, 2): 1}
```

Linear term on variable 0, quadratic on (0,1), cubic on (0,1,2).

---

## 3. Hard limits

Values that cause a rejected submission or hard error if exceeded.

OpenJij has no documented variable-count, degree, or payload hard limit.

---

## 4. Soft limits and numerical conditioning

OpenJij provides no optimality guarantee. Sample quality depends on schedule, sweep count, and problem hardness. The binding question is sample quality at a fixed compute budget, not admissibility.

### 4.1 Per-sweep cost

For $N$ binary variables and $|T|$ total terms, one Metropolis sweep evaluates approximately $N \cdot |T|$ flip attempts in the worst case. For a dense degree-$k$ polynomial:

$$
|T| \approx \binom{N}{k} \approx \frac{N^k}{k!}
$$

So per-sweep cost grows as $O(N^{k+1} / k!)$ for dense problems.

### 4.2 Memory

Approximately $O(|T|)$ for coefficients plus $O(N)$ for variable state. Even dense quintic problems at $N \approx 20$ stay sub-megabyte. Memory is not a binding constraint.

### 4.3 Quality vs compute tradeoff

Three knobs trade quality against wall-clock:

1. `num_sweeps` — longer anneals reach lower energies up to a problem-dependent plateau.
2. `num_reads` — more independent samples improve best-found energy via order statistics; diminishing returns past ~20 for problems with clear optima.
3. Temperature schedule — `beta_min` too low wastes early sweeps; `beta_max` too low under-anneals. Auto-defaults are problem-class dependent.

### 4.4 Numerical overflow

Reported energies of `inf` or `nan` indicate coefficient magnitudes beyond double precision (post-result signal; see S6).

---

## 5. Configuration parameters

`SASampler.sample_hubo()` parameters, defaults from [REF].

### 5.1 Core sampling parameters

| Parameter | Type | Default | Range / options | Effect | Pin for reproducibility? |
|---|---|---|---|---|---|
| `num_sweeps` | int | 1000 | ≥ 1 | Metropolis sweeps (full spin updates) per sample | Yes |
| `num_reads` | int | 1 | ≥ 1 | Independent samples collected | Yes |
| `num_threads` | int | 1 | ≥ 1 | Parallelism across `num_reads`; each thread runs an independent annealing chain | Yes |
| `beta_min` | float \| None | None (auto) | > 0 | Initial inverse temperature (high temp = low beta) | Yes if set |
| `beta_max` | float \| None | None (auto) | > beta_min | Final inverse temperature (low temp = high beta) | Yes if set |
| `seed` | int \| None | None (random) | any | Random seed | Yes — pin explicitly |

When `beta_min` / `beta_max` are unset, OpenJij computes them from problem characteristics.

### 5.2 Algorithmic configuration

| Parameter | Type | Default | Range / options | Effect | Pin for reproducibility? |
|---|---|---|---|---|---|
| `updater` | str | `METROPOLIS` | `METROPOLIS`, `HEAT_BATH`, `k-local` | Spin-update rule | Yes |
| `temperature_schedule` | str | `GEOMETRIC` | `GEOMETRIC`, `LINEAR` | Cooling schedule shape | Yes |
| `random_number_engine` | str | `XORSHIFT` | XORSHIFT, MT, others | RNG selection | Yes |
| `vartype` | str | required | `SPIN`, `BINARY` | Variable domain | Yes |

Updater notes:

- `METROPOLIS` — standard Metropolis-Hastings acceptance; default and best-tested.
- `HEAT_BATH` — Gibbs-style update; sometimes reaches lower energies on long anneals at slightly higher per-sweep cost.
- `k-local` — multi-spin update; experimental; behaviour at degrees 4–5 not separately documented.

### 5.3 What the sampler returns

The `response` object from `sample_hubo()` [REF]:

| Field | Type | Meaning |
|---|---|---|
| `response.record.sample` | numpy array | Variable assignments across reads |
| `response.record.energy` | numpy array | Objective values for each sample |
| `response.record.num_occurrences` | numpy array | Multiplicity if duplicate solutions found |
| `response.first.energy` | float | Best (lowest) energy across all reads |
| `response.first.sample` | dict | Assignment achieving best energy |

---

## 6. API, runtime, and access

| Aspect | Value | Source |
|---|---|---|
| Endpoint / invocation | In-process; runs entirely locally | [GH] |
| Client library + min version | `openjij` ≥ 0.10.0; Python with C++ backend via pybind11 | [GH] |
| Authentication | None — no API keys, no allocation | [GH] |
| Job lifecycle / states | N/A — synchronous in-process call | — |
| Billable / timed quantity | Free — open source (Apache 2.0) | [GH] |
| Server-side timeout | N/A — no server side | — |
| Rate limiting | N/A — local execution | — |
| Payload size limit | Host memory only | — |
| Warm-start / initial-state support | Yes. `sample_hubo` accepts an explicit initial-state parameter. The temperature (beta) schedule must be calibrated accordingly, or the anneal scrambles the supplied state | [REF] |
| Intermediate-iterate visibility | None. Quality checks must be post-result; there is no streaming view during the anneal | [REF] |
| Reproducibility determinants | Deterministic given fixed `seed` and same hardware | [ASSUMPTION] |

Installation: `pip install openjij`. Source build requires CMake ≥ 3.22. [GH]

### 6.1 Process-level cancellation

Because OpenJij runs in-process, a consumer can use Python signal handling or a process-level kill to enforce a wall-clock timeout. There is no graceful "stop and return best so far" interface — the sampler runs to completion of its sweep budget or is killed mid-run (in which case the partial sample is unavailable).

---

## 7. Failure modes

Each row is a condition a feasibility checker could detect. The response is the consumer's decision, not a property of the solver.

| ID | Mode | Detectable | Signal (exact, computable) | Source |
|---|---|---|---|---|
| S1 | Sample collapse with poor objective | post-result | `dominant_state_concentration > threshold` AND best energy worse than a reference threshold | [ASSUMPTION] |
| S2 | Wall-clock timeout | during | consumer-enforced kill exceeds budget | — |
| S3 | Invalid input (degree, type mismatch) | pre-submission | polynomial validation against the consumer schema fails | — |
| S4 | Numerical overflow | post-result | reported energies include `inf` or `nan` | [REF] |


---

## 8. Version log

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-05-18 | M. Chen | Initial draft. Pinned OpenJij 0.10.x. Documents `sample_hubo()` interface, parameters, scale, quality model, API characteristics, failure modes S1–S4 |
| 0.2 | 2026-06-08 | M. Chen | Tightened dossier and minor cosmetic changes |