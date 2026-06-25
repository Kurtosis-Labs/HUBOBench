"""compiler/solver_io/dirac3.py

Dirac-3 solver I/O for HUBOBench.

Encodes the canonical polynomial into a sample-hamiltonian-integer payload and
decodes the qci_client response into preshaped SQL rows.

New SQL-era contract:
    encode_problem(objective, num_variables, max_degree, config)
        -> (payload, flags)
    decode_response(raw_response, objective, num_variables, config, flagged)
        -> (solution_row, samples_rows)

flags from encode is (hard_reject_list, flagged_bool):
    - hard rejects (F1/F2/F6) raise ValueError with the structured list.
    - flagged_bool is True iff an F5 dynamic-range warning fired (run proceeds
      but the solution is marked FLAGGED).
"""

from __future__ import annotations

from typing import Any

from main.compiler.solver_io.helpers.decode_common import (
    group_samples,
    build_sample_rows,
    best_from_samples,
    energy_mismatch,
    flags_json,
    FLAG_ENERGY_MISMATCH,
    FLAG_DYNAMIC_RANGE_WARNING,
)

SOLVER_NAME = "dirac3"
LIMITS_DOSSIER_VERSION = "0.6"
JOB_TYPE_INTEGER = "sample-hamiltonian-integer"

_TOTAL_LEVEL_BUDGET = 949
_BINARY_LEVELS_PER_VARIABLE = 2
_PER_DEGREE_VARIABLE_CAP = {1: 949, 2: 949, 3: 135, 4: 39, 5: 19}
_MAX_POLYNOMIAL_DEGREE = 5
_DYNAMIC_RANGE_WARNING_THRESHOLD = 200.0

def _check_feasibility(
    objective: dict[str, Any], num_variables: int
) -> tuple[list[dict[str, Any]], bool]:
    """Return (hard_rejects, flagged).

    hard_rejects: list of {id,name,detail} for F1/F2/F6 (non-empty blocks the run).
    flagged: True iff F5 dynamic-range warning fired (advisory; run proceeds).
    """
    terms = objective["terms"]
    observed_max_degree = max((len(t["vars"]) for t in terms), default=0)
    sum_levels = _BINARY_LEVELS_PER_VARIABLE * num_variables
    nz = [abs(t["coef"]) for t in terms if t["coef"] != 0.0]
    dyn_range = (max(nz) / min(nz)) if nz else 0.0

    hard_rejects: list[dict[str, Any]] = []
    if observed_max_degree > _MAX_POLYNOMIAL_DEGREE:
        hard_rejects.append({"id": "F6", "name": "degree_overflow",
                             "detail": {"observed_max_degree": observed_max_degree,
                                        "max_allowed": _MAX_POLYNOMIAL_DEGREE}})
    if sum_levels > _TOTAL_LEVEL_BUDGET:
        hard_rejects.append({"id": "F1", "name": "level_budget_overflow",
                             "detail": {"observed_sum_levels": sum_levels,
                                        "max_allowed": _TOTAL_LEVEL_BUDGET}})
    cap = _PER_DEGREE_VARIABLE_CAP.get(observed_max_degree)
    if cap is not None and num_variables > cap:
        hard_rejects.append({"id": "F2", "name": "variable_count_overflow",
                             "detail": {"observed_n_variables": num_variables,
                                        "max_degree": observed_max_degree,
                                        "max_allowed_for_degree": cap}})

    flagged = dyn_range > _DYNAMIC_RANGE_WARNING_THRESHOLD
    return hard_rejects, flagged


# ─────────────────────────────────────────────────────────────────────────────
# Encode
# ─────────────────────────────────────────────────────────────────────────────

def encode_problem(
    objective: dict[str, Any],
    num_variables: int,
    max_degree: int,
    config: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Build the Dirac-3 payload. Returns (payload, flagged).

    Raises:
        ValueError: on hard reject (F1/F2/F6); exc.args[1] is the structured
            hard_rejects list so the runner can record a HARD_REJECT row.
    """
    hard_rejects, flagged = _check_feasibility(objective, num_variables)
    if hard_rejects:
        ids = ", ".join(r["id"] for r in hard_rejects)
        raise ValueError(f"Dirac-3 HARD_REJECT: {ids}", hard_rejects)

    terms = objective["terms"]

    data: list[dict[str, Any]] = []
    for term in terms:
        cvars = term["vars"]
        if not cvars:
            continue
        idx = [v + 1 for v in cvars]                  # 1-based
        pad = max_degree - len(idx)
        if pad > 0:
            idx = [0] * pad + idx                     # front-pad to max_degree
        data.append({"idx": idx, "val": float(term["coef"])})

    # File payload for client.upload_file(file=...). The Python converter passes
    # file_config.polynomial through untouched; the server validates it.
    file_payload = {
        "file_name": "hubobench_poly",
        "file_config": {
            "polynomial": {
                "num_variables": num_variables,
                "min_degree":    1,
                "max_degree":    max_degree,
                "data":          data,
            }
        },
    }

    payload = {
        "job_type":            JOB_TYPE_INTEGER,
        "file_payload":        file_payload,
        "num_levels":          [_BINARY_LEVELS_PER_VARIABLE] * num_variables,
        "num_samples":         int(config["num_samples"]),
        "relaxation_schedule": int(config["relaxation_schedule"]),
    }
    return payload, flagged


# ─────────────────────────────────────────────────────────────────────────────
# Decode
# ─────────────────────────────────────────────────────────────────────────────

def _parse_rfc3339nano(ts: str) -> float | None:
    """Parse a QCI RFC3339nano timestamp to POSIX seconds, or None.

    QCI emits e.g. '2026-06-16T11:09:25.78Z'. Python's fromisoformat handles
    the trailing 'Z' from 3.11+.
    """
    if not ts:
        return None
    s = ts.replace("Z", "+00:00")
    # Clamp fractional seconds to 6 digits (datetime max) if longer.
    if "." in s:
        head, frac = s.split(".", 1)
        tz = ""
        for marker in ("+", "-"):
            if marker in frac:
                frac, tz = frac.split(marker, 1)
                tz = marker + tz
                break
        frac = frac[:6]
        s = f"{head}.{frac}{tz}"
    try:
        from datetime import datetime
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def _end_to_end_seconds(job_status: dict[str, Any]) -> float:
    """completed - submitted, in seconds, from the job_status timestamp block.

    Returns 0.0 if either timestamp is missing or unparseable. This is the
    queue-inclusive end-to-end wall clock
    """
    submitted = _parse_rfc3339nano(job_status.get("submitted_at_rfc3339nano", ""))
    completed = _parse_rfc3339nano(job_status.get("completed_at_rfc3339nano", ""))
    if submitted is None or completed is None:
        return 0.0
    return max(0.0, completed - submitted)


def decode_response(
    raw_response: dict[str, Any],
    objective: dict[str, Any],
    num_variables: int,
    flagged: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Convert a qci_client response into (solution_row, samples_rows)."""
    constant = objective.get("constant", 0.0)
    terms    = objective["terms"]

    results  = raw_response.get("results", {}) or {}

    raw_solutions = results.get("solutions", [])
    raw_counts    = results.get("counts", [1] * len(raw_solutions))
    raw_energies  = results.get("energies", [])

    grouped = group_samples(raw_solutions, raw_counts)
    samples_rows = build_sample_rows(grouped, terms, constant)
    best_energy, best_vars_json = best_from_samples(samples_rows)

    flags: list[str] = []

    if flagged and samples_rows:
        flags.append(FLAG_DYNAMIC_RANGE_WARNING)

    # Divergence between our recompute and the lowest device-reported
    # energy signals a possible encoding bug (1-based indices, padding,
    # constant, or variable ordering). Recorded as ENERGY_MISMATCH.
    device_best = float(min(raw_energies)) if raw_energies else None
    if energy_mismatch(best_energy, device_best):
        flags.append(FLAG_ENERGY_MISMATCH)

    # status records ONLY termination. FLAGGED is retired; warnings are flags.
    status = "OK" if samples_rows else "TIMEOUT"

    # Timing: the process_job response nests these under job_info, not metrics.
    #   algorithmic_time_s = device_usage_s  (pure device time, ~5s)
    #   wall_clock_s       = completed - submitted  (end-to-end, queue-inclusive)
    job_info    = raw_response.get("job_info", {}) or {}
    job_result  = job_info.get("job_result", {}) or {}
    job_status  = job_info.get("job_status", {}) or {}
    algorithmic_time_s = float(job_result.get("device_usage_s", 0.0))
    wall_clock_s = _end_to_end_seconds(job_status)

    solution_row = {
        "status":             status,
        "best_energy":        best_energy,
        "best_vars_json":     best_vars_json,
        "wall_clock_s":       wall_clock_s,
        "algorithmic_time_s": algorithmic_time_s,
        "flags":              flags_json(flags),
    }
    return solution_row, samples_rows


def build_failure_row(
    status: str,
    wall_clock_s: float = 0.0,
    algorithmic_time_s: float = 0.0,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return (
        {
            "status":             status,
            "best_energy":        None,
            "best_vars_json":     None,
            "wall_clock_s":       float(wall_clock_s),
            "algorithmic_time_s": float(algorithmic_time_s),
            "flags":              flags_json([]),
        },
        [],
    )