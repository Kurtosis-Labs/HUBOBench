# Gurobi Limits Dossier

**Status:** Draft v0.5
**Owner:** Chen Mingda
**Solver version pinned:** Gurobi Optimizer 13.x. Verify with `gurobipy.gurobi.version()`.

---

## 0. Sources

Gurobi is an exact deterministic solver with effectively unbounded model size. Its binding constraints are formulation class, numerical conditioning, and time/memory, not raw variable count.

| Tier | Meaning |
|---|---|
| 1 | Authoritative — official reference manual / user guide |
| 2 | Semi-official — vendor help-centre / KB article |
| 3 | Assumption — inferred, not documented; flagged inline as [ASSUMPTION] |

| Tag | Source | URL | Tier |
|---|---|---|---|
| [REF] | Gurobi Optimizer Reference Manual (v13.x current) | https://docs.gurobi.com/projects/optimizer/en/current/ | 1 |
| [REL13] | Release Notes — Gurobi 13.0 | https://docs.gurobi.com/projects/optimizer/en/current/reference/releasenotes/changes.html | 1 |
| [PARAMS] | Parameter Reference | https://docs.gurobi.com/projects/optimizer/en/current/reference/parameters.html | 1 |
| [CON] | Constraints — Modeling concepts | https://docs.gurobi.com/projects/optimizer/en/current/concepts/modeling/constraints.html | 1 |
| [NUM] | Tolerances and User-Scaling | https://docs.gurobi.com/projects/optimizer/en/current/concepts/numericguide/tolerances_scaling.html | 1 |
| [NLIN] | Nonlinear Constraints | https://docs.gurobi.com/projects/optimizer/en/current/features/nonlinear.html | 1 |
| [KB-TYPES] | "What types of models can Gurobi solve?" | https://support.gurobi.com/hc/en-us/articles/360013156432 | 2 |
| [KB-LIC] | "Model too large for size-limited Gurobi license" | https://support.gurobi.com/hc/en-us/articles/360051597492 | 2 |
| [KB-RESTRICT] | "What does Restricted license mean?" | https://support.gurobi.com/hc/en-us/articles/29682074018833 | 2 |
| [KB-MIPGAP] | "What is the MIPGap?" | https://support.gurobi.com/hc/en-us/articles/8265539575953 | 2 |

---

## 1. Problem class accepted

Gurobi 13.x expresses and solves, in any combination of continuous and integer variables [KB-TYPES, REL13]:

| Class | Acronym | Support |
|---|---|---|
| Linear programming | LP / MILP | Native, exact |
| Convex quadratic programming | QP / MIQP | Native, exact |
| Convex quadratically-constrained programming | QCP / MIQCP | Native, exact |
| Non-convex QP / QCP | — | Native (default `NonConvex=-1` auto-detects) |
| Second-order cone constraints | SOCP | Native [KB-TYPES] |
| General nonlinear (multivariate composite) | NL | Native via `nlfunc` / `addGenConstrNL` / expression trees [NLIN] |
| SOS1 / SOS2 constraints | — | Native, with internal big-M reformulation governed by `PreSOS1BigM`, `PreSOS2BigM` [CON] |

- **Variable domain(s):** continuous, integer, binary, any combination.
- **Max polynomial degree:** no fixed maximum.

- **Direction:** minimisation or maximisation, both native.
- **Native constraint support:** equality, ≤, ≥; linear, quadratic, SOS, and nonlinear forms. Right-hand sides are constants in matrix-oriented APIs (C, MATLAB, R) but arbitrary expressions in object-oriented APIs (C++, Java, .NET, Python). [CON]
- **Coefficient type:** IEEE 754 double precision throughout. [NUM]

### 1.1 v13 deprecations

Per [REL13] "Deprecated functionality":

- **Function Constraints are deprecated:** `addGenConstrExp`, `addGenConstrLog`, `addGenConstrPow`, `addGenConstrSin`, `addGenConstrCos`, `addGenConstrTan`, `addGenConstrPoly`, etc., along with their attributes (`FuncPieceError`, `FuncPieceLength`, `FuncPieceRatio`, `FuncPieces`, `FuncNonlinear`) and same-named parameters. The replacement is `nlfunc` / `addGenConstrNL`, introduced in v12 and now the canonical nonlinear path. [REL13, NLIN]
- **Interactive shell removed.** Use `gurobipy` directly from a Python interpreter or notebook. [REL13]

---

## 2. Operating modes

Gurobi operating modes is determined by the degree in the problem. Degree changes the solver path and auxiliary-state handling:

  | Degree | Path |
  |---|---|
  | 1 | LP / MILP |
  | 2 | (MI)QP / (MI)QCP |
  | ≥ 3 | User-supplied quadratization to degree 2, OR native nonlinear handling via `nlfunc` |

  Degree-≥3 handling, stated as two capabilities (a consumer chooses which to use; the solver supports both):
  - **Quadratization to degree 2.** Reduce the high-degree polynomial to a QUBO/MIQP by introducing auxiliary variables for each degree-$k$ monomial with $k > 2$ (e.g. Rosenberg substitution with implication constraints). The caller controls the encoding and penalty coefficients.
  - **Native nonlinear handling.** Declare the higher-order polynomial as a nonlinear constraint (typically an epigraph auxiliary `t`, then `t = polynomial(x)`, then `min t`). Gurobi introduces its own auxiliary state via spatial branch-and-bound. The caller does not control the encoding.

## 3. Hard limits

Values that cause a rejected submission or hard error if exceeded.

| Quantity | Limit | Trigger if exceeded | Source |
|---|---|---|---|
| Integer-valued parameter storage (`MAXINT`) | 2,000,000,000 | parameter value rejected | [PARAMS] |

There is **no fixed limit** on variables, constraints, or nonzeros from the solver itself under a full license; models with millions of variables routinely solve once presolve runs. [KB-LIC]

---

## 4. Soft limits and numerical conditioning

Gurobi's limits here are not coefficient-magnitude caps but tolerances that interact with coefficient magnitude.

### 4.1 Default tolerances [PARAMS, NUM]

| Parameter | Default | Range | Meaning | Source |
|---|---|---|---|---|
| `FeasibilityTol` | 1e-6 | [1e-9, 1e-2] | Primal feasibility; $a \cdot x \le b$ accepted if $(a \cdot x) - b \le$ tol. Absolute, does not scale with problem | [NUM] |
| `OptimalityTol` | 1e-6 | [1e-9, 1e-2] | Dual feasibility. Absolute | [NUM] |
| `IntFeasTol` | 1e-5 | [1e-9, 1e-1] | $x$ integral if $\lvert x - \text{floor}(x + 0.5) \rvert \le$ tol. Absolute | [NUM] |
| `MarkowitzTol` | 0.0078125 | [1e-4, 0.999] | Simplex factorization pivot tolerance | [PARAMS] |
| `BarConvTol` | 1e-8 | [0, 1] | Barrier convergence | [PARAMS] |
| `MIPGap` | 1e-4 | [0, ∞] | Relative MIP gap at which optimality is declared | [KB-MIPGAP] |
| `MIPGapAbs` | 1e-10 | [0, ∞] | Absolute MIP gap | [PARAMS] |

### 4.2 Recommended magnitude and range bounds [NUM]

Magnitude recommendations (what individual values should be on the order of):

| Quantity | Recommended | Source |
|---|---|---|
| Right-hand-side values (incl. budgets) | ≤ $10^6$ | [NUM] §"Recommended Ranges" |
| Variable-bound magnitudes | ≤ $10^6$ | [NUM] §"Recommended Ranges" |
| Objective value at "good" solutions | ≤ $10^6$, ideally > 1 | [NUM] §"Recommended Ranges" |

Range recommendations (ratio of max to min nonzero):

| Quantity | Recommended | Source |
|---|---|---|
| Constraint matrix coefficient range | within 6 orders of magnitude (max/min ≤ $10^6$) and within $[10^{-3}, 10^{6}]$ absolute | [NUM] §"Advanced User-Scaling" |
| Big-M values in indicator-style reformulations | as small as feasible | [NUM] §"Dealing with Big-M Constraints" |

Above the $10^6$ matrix-coefficient ratio, [NUM] demonstrates that Gurobi can report a feasible model as "Infeasible or unbounded" purely because the coefficient range exceeds what fixed tolerances can resolve. This is a silent-failure mode (see F-table G6).

### 4.3 Coefficient dynamic-range diagnostic

The dynamic-range ratio over the constraint matrix $A$:

$$
\text{ratio} = \frac{\max_{i,j} |A_{ij}|}{\min_{i,j : A_{ij} \ne 0} |A_{ij}|}
$$

| Ratio | Documented consequence | Source |
|---|---|---|
| ≤ $10^6$ | Within recommended range | [NUM] §"Advanced User-Scaling" |
| $10^6$ – $10^9$ | Resolution increasingly strained; `NumericFocus=2` mitigates | [NUM] |
| > $10^9$ | High risk of scaling artifacts and false infeasibility; `NumericFocus=3` mitigates | [NUM] |

`NumericFocus` ∈ {0, 1, 2, 3} (default 0) trades wall-clock time for tighter internal tolerances. [PARAMS]

### 4.4 Tolerance scaling caveat

Rescaling a constraint by a constant does not change the solution mathematically but **does change which tolerances effectively apply**. From [NUM]: multiplying a row by 1/2 allows twice as large a violation; the documentation recommends scaling so that a violation of about $10^{-6}$ is negligible. Any rescaling preprocessing that brings magnitudes into range therefore changes the effective per-constraint feasibility tolerance, and the rescaling factor should be logged so post-solve checks apply consistent tolerances against the original problem.

---

## 5. Configuration parameters

### 5.1 Termination controls [PARAMS]

| Parameter | Type | Default | Range / options | Effect | Pin for reproducibility? |
|---|---|---|---|---|---|
| `TimeLimit` | float | infinity | ≥ 0 | Wall-clock seconds before forced termination | Yes (host-dependent) |
| `WorkLimit` | float | infinity | ≥ 0 | Deterministic work units (repeatable across runs) before termination | Yes |
| `NodeLimit` | float | infinity | ≥ 0 | MIP branch-and-bound nodes | Yes |
| `IterationLimit` | float | infinity | ≥ 0 | Simplex iterations | Yes |
| `SolutionLimit` | int | infinity | ≥ 1 | Stop after this many feasible integer solutions | Yes |
| `BestObjStop` | float | -infinity | any | Stop when an incumbent ≤ this value is found | Yes |
| `BestBdStop` | float | infinity | any | Stop when the bound proves the optimum cannot beat this value | Yes |
| `MIPGap` | float | 1e-4 | [0, ∞] | Terminate at this relative gap | Yes |
| `NoRelHeurTime` | float | 0 | ≥ 0 | Time in the NoRel heuristic before root LP | Yes |
| `NoRelHeurSolutions` | int | — (new in v13) | ≥ 0 | Stop NoRel heuristic after this many solutions | Yes |
| `NumericFocus` | int | 0 | {0,1,2,3} | Higher = tighter internal tolerances, slower | Yes |
| `Seed` | int | 0 | any | Randomisation seed | Yes — pin explicitly |
| `Threads` | int | 0 | see §5.2 | Thread count | Yes — pin explicitly |
| `IntegralityFocus` | int | 0 | {0,1} | `1` reduces trickle-flow near-integer artifacts | Yes if set |

### 5.2 Threads parameter [REL13]

- `Threads = 0` (default): up to 32 threads even if more virtual processors exist
- `Threads = -1` (new in v13): as many threads as virtual processors detected
- `Threads > 0`: exactly that many

`Threads` **must be pinned explicitly** for cross-machine reproducibility; do not rely on the default. Record the pinned value.

### 5.3 v13 NoRel heuristic notes [REL13]

- `NoRelHeurSolutions` stops the NoRel heuristic once a specified number of solutions is found.
- NoRel now honours `VarHintVal` attribute values, biasing the heuristic toward neighbourhoods near user-provided guesses.

### 5.4 Reproducibility determinants

Gurobi guarantees deterministic results for a fixed `Seed`, parameter set, thread count, and host machine. Solve time can still vary across runs on identical inputs due to hardware-level effects. Capture and pin:

- Gurobi version string (`gurobipy.gurobi.version()`)
- `Seed`, `Threads`, all non-default parameters
- `Runtime`, `Work`, `NodeCount`, `IterCount`, `BarIterCount`, `MIPGap` at termination
- `Status` code (OPTIMAL = 2, INFEASIBLE = 3, INF_OR_UNBD = 4, UNBOUNDED = 5, TIME_LIMIT = 9, etc.)

`Work` is hardware-normalised (preferred for cross-machine comparison); `Runtime` is hardware-dependent (preferred for single-host). In v13, an interrupted-and-resumed MIP shows total accumulated time/work plus a separate line for the most recent optimize call; log parsing must tolerate both formats. [REL13]

---

## 6. API, runtime, and access

| Aspect | Value | Source |
|---|---|---|
| Endpoint / invocation | In-process call (local / Compute Server / Gurobi Cloud) | [REF] |
| Client library + min version | `gurobipy` 13.x | PyPI |
| Object-oriented APIs | C++, Java, .NET, Python | [REF] |
| Matrix-oriented APIs | C, MATLAB, R | [REF] |
| Modeling-language bridges | AIMMS, AMPL, GAMS, MPL, Pyomo, JuMP | [REF] |
| Authentication | License file (`gurobi.lic`) searched in standard locations; restricted license used as fallback | [KB-RESTRICT] |
| Job lifecycle / states | Synchronous in-process solve; status codes at termination (§5.4) | [REF] |
| Billable / timed quantity | Per-license-token allocation, not metered seconds | [REF] |
| Server-side timeout | None beyond user-set `TimeLimit` | [PARAMS] |
| Rate limiting | None (in-process call, not REST) | [REF] |
| Payload size limit | Limited only by host RAM/disk (local) or network/host (Cloud); no fixed published cap | [KB-LIC] |
| Warm-start / initial-state support | Yes. `VarHintVal` biases heuristics; MIP start (`Start` attribute) available | [REL13] |
| Intermediate-iterate visibility | Yes, via callbacks; `Model.optimize` accepts an optional `wheres` argument in v13 to request callbacks for specific `where` flags | [REL13] |
| Reproducibility determinants | Seed, threads, host, version, parameter set (§5.4) | [REF] |

### 6.1 Memory

No documented hard memory cap. Model size alone is a poor difficulty metric because presolve may collapse a million-variable model to a handful. Consumption depends on presolve effectiveness, branch-and-bound open-frontier width (not total nodes), cut storage, and post-`PreQLinearize` density of `Q`. It cannot be predicted analytically from problem features; measure empirically per host.

### 6.2 Concurrency and queueing

| Concern | Local / Compute Server | Gurobi Cloud |
|---|---|---|
| Per-request rate limit | None | None documented |
| Wall-clock timeout | User-set `TimeLimit` | Same |
| Payload size | Host RAM/disk | Network + host |
| Concurrent jobs | License token count + `TokenServer` config | Paid compute quota |
| Queue behaviour | None (synchronous) | Compute Server queues if workers busy |

### 6.3 v13 API additions [REL13]

- `Model.getQ`, `Model.getQCMatrices` return `scipy.sparse` representations of quadratic objective and constraint terms.
- `LinExpr.linTerms`, `QuadExpr.linTerms`, `QuadExpr.quadTerms` iterate over expression terms.
- `Model.optimize` accepts optional `wheres` (callback filtering; most relevant for Compute Server / Cloud).
- GIL released when starting an environment — multithreaded Python harness code is safer against deadlocks.

---

## 7. Failure modes

Each row is a condition a feasibility checker could detect. The response is the consumer's decision, not a property of the solver.

| ID | Mode | Detectable | Signal (exact, computable) | Source |
|---|---|---|---|---|
| G1 | Coefficient range degraded | pre-submission | dynamic-range ratio > $10^9$ | [NUM] |
| G2 | Coefficient range warning | pre-submission | $10^6 <$ ratio $\le 10^9$ | [NUM] |
| G3 | RHS / bound above recommendation | pre-submission | any $\lvert b_i \rvert$ or variable bound > $10^6$ | [NUM] |
| G4 | RHS / bound beyond numeric safety | pre-submission | any $\lvert b_i \rvert$ or variable bound > $10^{10}$ | [NUM] |
| G5 | Non-PSD quadratic objective with `PreQLinearize` off | pre-submission | $Q$ not PSD and `PreQLinearize=0` (auto-resolved by `PreQLinearize=-1` default) | [PARAMS] |
| G6 | "Infeasible or unbounded" from numerical scaling | post-solve | status code 4 with no model-side reason | [NUM] |
| G7 | Wall-clock `TimeLimit` reached | during | `TimeLimit` reached without optimal proof | [PARAMS] |
| G8 | Use of deprecated v12 API surface | pre-submission | code path uses `addGenConstrExp` / `addGenConstrLog` / Function Constraints attributes | [REL13] |

---

## 8. Version log

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-05-11 | M. Chen | Initial draft, Gurobi v12 target |
| 0.2 | 2026-05-13 | M. Chen | Verification pass against [REF], [PARAMS], [NUM]. Separated magnitude from range recommendations. Restored reproducibility section. Added source tiering |
| 0.3 | 2026-05-14 | M. Chen | Target upgraded to Gurobi 13.x. New v13 deprecation section (Function Constraints → `nlfunc`) |
| 0.4 | 2026-05-18 | M. Chen | Pre-submission hard rejects demoted to warnings; `TimeLimit` the only hard-stop |
| 0.5 | 2026-06-08 | M. Chen | Tightened dossier and minor cosmetic changes |