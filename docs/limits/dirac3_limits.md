# DIRAC-3 Limits Dossier

**Status:** Draft v0.6
**Owner:** Chen Mingda
**Solver version pinned:** `qci-client` ≥ 4.5.0. Verify with `pip show qci-client`.

---

## 0. Sources

Dirac3 is a stochastic solver that is able to handle higher order problems natively. However, it is subjected to a few constraints in variable count, coefficient resolution and variable size.

| Tier | Meaning |
|---|---|
| 1 | Authoritative — official reference manual / user guide |
| 2 | Semi-official — vendor help-centre / KB article |
| 3 | Assumption — inferred, not documented; flagged inline as [ASSUMPTION] |

| Tag | Source | URL | Tier |
|---|---|---|---|
| [UG] | Dirac-3 User Guide v0.0.4 (May 2025) | https://quantumcomputinginc.com/learn/support/user-guides/dirac-3-user-guide | 1 |
| [BG] | Dirac-3 Developer Beginner Guide (qci-client v4.5+) | https://quantumcomputinginc.com/learn/developer-resources/entropy-quantum-optimization/dirac-3-developer-beginner-guide | 1 |

---

## 1. Problem class accepted

Dirac-3 minimises a single real-coefficient polynomial of total degree at most 5 over a discrete or sum-constrained continuous domain. [BG]

**Canonical form (objective Hamiltonian):**

$$
E = \sum_i C_i x_i + \sum_{i,j} J_{ij} x_i x_j + \sum_{i,j,k} T_{ijk} x_i x_j x_k + \sum_{i,j,k,l} Q_{ijkl} x_i x_j x_k x_l + \sum_{i,j,k,l,m} P_{ijklm} x_i x_j x_k x_l x_m
$$

- **Variable domain(s):** non-negative integer (integer mode) or non-negative real under a sum constraint (continuous mode). See §2.
- **Max polynomial degree:** 5. [BG]
- **Connectivity:** all-to-all; no embedding required. [UG]
- **Coefficient type:** real-valued. $C_i$ are linear coefficients ("returns" in QCi's vocabulary); $J_{ij}$, $T_{ijk}$, $Q_{ijkl}$, $P_{ijklm}$ are real-valued interaction coefficients. [BG]
- **Direction:** minimisation only; negate coefficients to maximise. [BG]
- **Native constraint support:** sum constraint (variables sum to a fixed number). Inequality constraints must be encoded as penalty terms in the objective. [UG, BG]
- **Constant terms:** rejected by the API; `poly_indices = [0,0,0]` is refused. Constants do not affect the optimal bitstring, so this is mathematically irrelevant. [BG]
- **Term encoding:** pair of lists — `poly_coefs` (floats) and `poly_indices` (1-based variable indices, non-decreasing within each term, zero-padded for terms below maximum degree). [BG]
- **Duplicate terms:** allowed in input; the API sums duplicates into one term. [BG]

---

## 2. Operating modes

Dirac-3 exposes two `job_type` strings; the device is configured differently in each. [BG]

### 2.1 Integer mode — `sample-hamiltonian-integer`

Variables are non-negative integers with per-variable upper bounds:

$$0 \le x_i \le Z_i, \quad Z_i \ge 1$$

The number of levels for variable $i$ is $L_i = Z_i + 1$. The binary special case is $Z_i = 1$ for all $i$ ($L_i = 2$). [BG]

Device identifier in metrics responses: `dirac-3_qudit`. [BG]

### 2.2 Continuous mode — `sample-hamiltonian`

Variables are non-negative reals under a single sum constraint:

$$\sum_i x_i = R, \quad 1 \le R \le 10{,}000, \quad x_i \ge 0$$

[BG] Per-variable resolution is approximately $R / \text{dynamic range}$. [BG]

Device identifier in metrics responses: `dirac-3_normalized_qudit`. [BG]

---

## 3. Hard limits

Values that cause a rejected submission or hard API error if exceeded.

| Quantity | Limit | Trigger if exceeded | Source |
|---|---|---|---|
| Sum of `num_levels` across all variables | 949 | API error | [BG] |
| Maximum variables, degree 1 (linear) | 949 | API error | [UG] |
| Maximum variables, degree 2 (quadratic) | 949 | API error | [UG] |
| Maximum variables, degree 3 (cubic) | 135 | API error | [UG] |
| Maximum variables, degree 4 (quartic) | 39 | API error | [UG] |
| Maximum variables, degree 5 (quintic) | 19 | API error | [UG] |
| Per-variable level cap, integer mode | 17 levels ($Z_i \le 16$) | API error | [UG] §4.2 |
| Polynomial degree | 5 | API error | [BG] |
| Constant term (all-zero index list) | not accepted | API rejects `poly_indices = [0,0,0]` | [BG] |

Notes:

- The variable-count cap is set by the **maximum polynomial degree present anywhere in the problem**, not by any individual term's degree. A mostly-quadratic problem with one quartic term is bound by the degree-4 row (39 variables).
- The per-degree caps at degrees ≥ 3 are stricter than 949, so they subsume the overall 949 variable ceiling.
- For pure binary problems every variable contributes 2 levels, so the level budget caps binary problems at $\lfloor 949 / 2 \rfloor = 474$ variables.
- For problems needing more than 17 levels per variable, [UG] §4.2 recommends continuous mode.

---

## 4. Soft limits and numerical conditioning

Values that degrade solution quality without erroring. Not enforced by the API.

| Quantity | Recommended / threshold | Documented consequence above threshold | Source |
|---|---|---|---|
| Digital readout range (signal input ceiling) | up to ≈ 70 dB | input signal beyond readout range | [UG] §4.4 |
| Effective analog resolution (coupling distinguishability) | ≈ 200:1, or 23 dB | coefficients below resolution are not distinguished; degraded solutions | [UG] §4.4 |

The binding constraint for submission is the analog resolution limit, expressed as the coefficient dynamic-range ratio:

$$
\text{dynamic\_range\_ratio} = \frac{\max_t |c_t|}{\min_{t : c_t \ne 0} |c_t|} \le 200
$$

over all coefficients across all polynomial terms. [UG] §4.4

Sources of degradation [UG] §4.4: optical modulator, digital-to-analog converter, input/output signal conditioning, temperature variation, and dark counts from single-photon detection.

Effective dynamic range varies with temperature and device-level imperfections, so two runs of the same problem can experience different effective coefficient resolutions. [UG] §4.4. [ASSUMPTION] Variability on problems submitted within the analog resolution limit is negligible (non-blocking).

---

## 5. Configuration parameters

| Parameter | Type | Default | Range / options | Effect | Pin for reproducibility? |
|---|---|---|---|---|---|
| `num_samples` | int | 1 | 1–100 | Number of independent samples returned | Yes |
| `relaxation_schedule` | int | 1 | {1, 2, 3, 4} | Higher = more iterations and dissipation; higher probability of optimum at the cost of longer evolution time | Yes |
| `mean_photon_number` | float | set by schedule | 0.0000667 – 0.0066666 | Advanced; normally inherited from `relaxation_schedule` | Yes if overridden |
| `quantum_fluctuation_coefficient` | int | set by schedule | 1–100, used as $1/\sqrt{n}$ | Advanced; normally inherited from `relaxation_schedule` | Yes if overridden |

Device time scales with `relaxation_schedule` and weakly with problem size. Indicative `device_usage_s` (from observed runs, not a documented guarantee):

| Problem size | sched=1 | sched=2 | sched=3 | sched=4 |
|---|---|---|---|---|
| N=10, d=2 | 3.0s | 5.0s | 5.0s | 9.0s |
| N=30, d=3 | 5.0s | 9.2s | 9.4s | 18.0s |

**Return payload of interest** (from `job_response['results']`) [BG]:

- `solutions` — variable-value vectors returned by the device
- `energies` — corresponding device-reported objective values
- `counts` — how many of the `num_samples` runs produced each distinct solution

The `counts` field supports a duplicate-concentration measure:

$$
\text{duplicate\_concentration} = \frac{\max_s \text{counts}_s}{\text{num\_samples}}
$$

A value near 1 indicates either genuine convergence or mode collapse; the two are not distinguishable from this signal alone.

---

## 6. API, runtime, and access

| Aspect | Value | Source |
|---|---|---|
| Endpoint / invocation | `https://api.qci-prod.com` (cloud) | [BG] |
| Client library + min version | `qci-client` ≥ 4.5.0 (≥ 4.0 for multibody) | [BG] |
| Authentication | Long-lived API token auto-refreshes short-lived access tokens via `QciClient` | [BG] |
| Job lifecycle / states | `QUEUED` → `RUNNING` → `COMPLETED` (or `ERRORED`) | [BG] |
| Billable / timed quantity | `job_result.device_usage_s`, rounded to nearest second | [BG] |
| Server-side timeout | None enforced; a long job runs to completion or device error, consuming allocation | [BG] |
| Rate limiting | Not documented; the queue serialises submissions, so high rates lengthen queue time rather than reject | [BG] |
| Payload size limit | No published bound; not a binding constraint at typical sparse problem sizes | [BG] |
| Warm-start / initial-state support | No. No "initial state" parameter exists in the job body; the standard flow (`QciClient` → `get_allocations` → `build_job_body`) provides no entry point for prior-state injection | [BG] |
| Intermediate-iterate visibility | None. Lifecycle states admit no streaming or partial-results state; quantum measurement collapses to a final state, so no intermediate step exists to inspect | [BG] |
| Reproducibility determinants | Stochastic device — identical submissions can return different solutions. Per-sample timing available via `client.get_job_metrics` in nanoseconds since Unix epoch | [BG] |

Problem polynomial is uploaded as a JSON file and referenced by `file_id` in the job body. [BG]

[ASSUMPTION] Independent sampling across submissions: identical submissions produce statistically independent draws from the same distribution.

---

## 7. Failure modes

Each row is a condition a feasibility checker could detect. The response is the consumer's decision, not a property of the solver.

| ID | Mode | Detectable | Signal (exact, computable) | Source |
|---|---|---|---|---|
| F1 | Level-budget overflow | pre-submission | $\sum_i L_i > 949$ | [BG] |
| F2 | Per-degree variable-count overflow | pre-submission | $N$ exceeds the per-degree cap (§3) for the max polynomial degree present | [UG] |
| F2b | Overall variable-count overflow | pre-submission | $N > 949$ (subsumed by F2 at degrees ≥ 3) | [UG] |
| F3 | Constant-term rejection | pre-submission | any polynomial term with an all-zero index list | [BG] |
| F4 | Continuous-mode sum violation | pre-submission | $R \notin [1, 10{,}000]$ in continuous mode | [BG] |
| F5 | Coefficient dynamic-range overflow | pre-submission | $\max_t \lvert c_t \rvert / \min_{t : c_t \ne 0} \lvert c_t \rvert > 200$ | [UG] §4.4 |
| F6 | Degree overflow | pre-submission | $\deg(P) > 5$ | [BG] |
| F7 | Native-constraint mismatch | pre-submission | problem declares equality or inequality constraints | [UG] |
| F8 | Low diversity / converged | post-result | `duplicate_concentration` plateau at `relaxation_schedule` ≥ 2 with poor objective | [BG] |
| F9 | Analog-resolution drift | post-result | reproducibility check across reruns shows inconsistent energies for identical input | [UG] §4.4 |
| F10 | Queue starvation | during | job remains `QUEUED` beyond a consumer-configured timeout | [BG] |

---

## 8. Version log

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-05-11 | M. Chen | Initial draft, derived from [UG], [BG] |
| 0.2 | 2026-05-12 | M. Chen | Verification pass against QCi docs. Resolved 949 vs 954 conflict (§3). Corrected dynamic range to 200:1 / 23 dB single value (§4) |
| 0.3 | 2026-05-13 | M. Chen | Updated native sum constraint. QCi updated level budget 954 → 949; updated §3, §7 |
| 0.4 | 2026-05-29 | M. Chen | Updated hard variable-count limit; changed dynamic-range limits to soft. Removed some open questions after preliminary experiments |
| 0.5 | 2026-06-01 | M. Chen | Updated and resolved open questions |
| 0.6 | 2026-06-05 | M. Chen | Resolved minor cosmetic issues and tightened document |