# <Solver Name> Limits Dossier

**Status:** Draft v0.1
**Owner:** <name>
**Solver version pinned:** <exact version string + how to verify, e.g. `pip show <pkg>` / `gurobi.version()`>
**Last verified against docs:** <date>

> A dossier describes ONE solver: what it accepts, where it breaks, how it is configured, and how it is accessed. It does not describe what any consuming system does with the solver. Do not reference other dossiers, downstream tools, scoring, or routing.

---

## 0. Sources

> Every numerical or behavioural claim in this dossier must trace to a source tag below, OR be explicitly marked an assumption. Assumptions are allowed only when non-blocking and must say so inline with [ASSUMPTION].

| Tier | Meaning |
|---|---|
| 1 | Authoritative — official reference manual / user guide |
| 2 | Semi-official — vendor help-centre / KB article |
| 3 | Assumption — inferred, not documented; flagged inline as [ASSUMPTION] |

| Tag | Source | URL | Tier |
|---|---|---|---|
| [TAG] | ... | ... | 1 |

---

## 1. Problem class accepted

> The mathematical object the solver consumes. State it once, precisely.

- **Canonical form:** <the objective/constraint form, with the equation>
- **Variable domain(s):** <binary / integer-bounded / continuous / spin>
- **Max polynomial degree:** <value, or "unbounded" with the cost consequence>
- **Connectivity:** <all-to-all / embedded / sparse>
- **Coefficient type:** <float / int; precision>
- **Direction:** <min / max / both; how to flip>
- **Native constraint support:** <none / sum / equality / inequality>
- **Constant terms:** <accepted / rejected / ignored>
- **Term encoding:** <exact input format the API expects>
- **Duplicate terms:** <summed / rejected / undefined>

---

## 2. Operating modes  [omit if single-mode]

> Only if the solver exposes distinct modes (job types, samplers, solver paths) that change accepted input or limits. One subsection per mode.

### 2.x <mode name / API string>

- Domain / constraint differences from §1
- Mode-specific identifier in responses, if any

---

## 3. Hard limits

> Values that cause a REJECTED submission or a hard error if exceeded. These are the bright lines. One row per limit, each with a source tag. If a "limit" is only advisory, it belongs in §4, not here.

| Quantity | Limit | Trigger if exceeded | Source |
|---|---|---|---|
| | | error / reject | [TAG] |

---

## 4. Soft limits and numerical conditioning

> Values that DEGRADE solution quality without erroring. Coefficient dynamic range, tolerances, magnitude recommendations. State the threshold and the documented consequence of crossing it. Do not state what to do about it.

| Quantity | Recommended / threshold | Documented consequence above threshold | Source |
|---|---|---|---|
| | | | [TAG] |

> Include the relevant tolerance defaults here if the solver exposes them (feasibility, integrality, gap, etc.) as a sub-table.

---

## 5. Configuration parameters

> Every parameter a consumer may set, with type, default, range, and effect. This is the contract a consumer's config dict is built against. Mark any parameter that MUST be pinned for cross-run reproducibility.

| Parameter | Type | Default | Range / options | Effect | Pin for reproducibility? |
|---|---|---|---|---|---|
| | | | | | |

> Note any parameters that are deprecated or removed in the pinned version.

---

## 6. API, runtime, and access

> How the solver is reached and what bounds the request. Fill only the rows that apply; write N/A for in-process solvers.

| Aspect | Value | Source |
|---|---|---|
| Endpoint / invocation | | |
| Client library + min version | | |
| Authentication | | |
| Job lifecycle / states | | |
| Billable / timed quantity | | |
| Server-side timeout | | |
| Rate limiting | | |
| Payload size limit | | |
| Warm-start / initial-state support | <yes/no — capability only> | |
| Intermediate-iterate visibility | | |
| Reproducibility determinants | <seed, threads, host, version> | |

### 6.x <Further description of API>


---

## 7. Failure modes

> The core downstream-facing section. Each row is a condition a feasibility
> checker COULD detect. State the signal and WHEN it is observable. Do NOT
> state the response — that is the consumer's decision, not the solver's
> property.

| ID | Mode | Detectable | Signal (exact, computable) | Source |
|---|---|---|---|---|
| X1 | <short name> | pre-submission / post-result / during | <the precise condition> | [TAG] |

> "Detectable" has exactly three values: pre-submission (checkable from the
> problem before sending), post-result (only visible in the response), during
> (observable while running). No fourth category, no policy column.

---

## 8. Version log

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | | | Initial draft |