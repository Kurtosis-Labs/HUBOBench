"""compiler/solver_io/sa_openjij.py

SA / OpenJij solver I/O for HUBOBench.

Encodes the canonical polynomial into a sample_hubo polynomial dict and decodes
the OpenJij Response into preshaped SQL rows. Runs entirely in-process.

New SQL-era contract:
    encode_problem(objective, num_variables, max_degree, config)
        -> (payload, flagged)
    decode_response(raw_response, objective, num_variables, config, flagged)
        -> (solution_row, samples_rows)
"""

from __future__ import annotations

from typing import Any

from compiler.solver_io.helpers.decode_common import (
    group_samples,
    build_sample_rows,
    best_from_samples,
    energy_mismatch,
    flags_json,
    FLAG_ENERGY_MISMATCH,
)

SOLVER_NAME = "SA_OpenJij"
LIMITS_DOSSIER_VERSION = "0.2"
VARTYPE_BINARY = "BINARY"


# ─────────────────────────────────────────────────────────────────────────────
# Encode
# ─────────────────────────────────────────────────────────────────────────────

def encode_problem(
    objective: dict[str, Any],
    num_variables: int,
    max_degree: int,
    config: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Build the SASampler.sample_hubo payload. Returns (payload, flagged=False).

    Raises:
        ValueError: if vartype is not BINARY (SPIN unsupported in V1).
    """
    vartype = config.get("vartype", VARTYPE_BINARY)
    if vartype != VARTYPE_BINARY:
        raise ValueError(
            f"SA supports vartype='BINARY' only in V1; got {vartype!r}."
        )

    terms = objective["terms"]
    polynomial = {tuple(t["vars"]): float(t["coef"]) for t in terms}

    sampler_kwargs: dict[str, Any] = {
        "num_sweeps": int(config["num_sweeps"]),
        "num_reads":  int(config["num_reads"]),
    }
    for key, cast in (
        ("num_threads", int), ("beta_min", float), ("beta_max", float),
        ("updater", str), ("temperature_schedule", str),
        ("random_number_engine", str), ("seed", int),
    ):
        if config.get(key) is not None:
            sampler_kwargs[key] = cast(config[key])

    payload = {
        "polynomial":     polynomial,
        "vartype":        vartype,
        "sampler_kwargs": sampler_kwargs,
    }
    return payload, False


# ─────────────────────────────────────────────────────────────────────────────
# Decode
# ─────────────────────────────────────────────────────────────────────────────

def decode_response(
    raw_response: Any,
    objective: dict[str, Any],
    num_variables: int,
    flagged: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Convert an OpenJij Response (duck-typed) into (solution_row, samples_rows)."""
    constant = objective.get("constant", 0.0)
    terms    = objective["terms"]

    record = getattr(raw_response, "record", None)
    info   = getattr(raw_response, "info", {}) or {}

    if record is None:
        return build_failure_row("API_ERROR")

    samples_raw     = record.sample
    occurrences_raw = getattr(record, "num_occurrences", None)
    if occurrences_raw is None:
        occurrences_raw = [1] * len(samples_raw)

    grouped = group_samples(samples_raw, occurrences_raw)
    samples_rows = build_sample_rows(grouped, terms, constant)
    best_energy, best_vars_json = best_from_samples(samples_rows)

    # Energy validation: sampler energy is NOT stored (recomputed canonically),
    # but a divergence between our recompute and the sampler's lowest reported
    # energy signals a possible encoding bug. Recorded as ENERGY_MISMATCH.
    flags: list[str] = []
    energy_raw = getattr(record, "energy", None)
    sampler_best = float(min(energy_raw)) if energy_raw is not None and len(energy_raw) else None
    if energy_mismatch(best_energy, sampler_best):
        flags.append(FLAG_ENERGY_MISMATCH)

    # status records ONLY termination. SA raises no pre-submission warning
    # (flagged is always False), so there is no DYNAMIC_RANGE_WARNING here.
    status = "OK" if samples_rows else "API_ERROR"

    # SA is in-process: algorithmic time equals wall clock (no queue/device split).
    wall_clock_s = float(info.get("wall_clock_s", 0.0))

    solution_row = {
        "status":             status,
        "best_energy":        best_energy,
        "best_vars_json":     best_vars_json,
        "wall_clock_s":       wall_clock_s,
        "algorithmic_time_s": wall_clock_s,
        "flags":              flags_json(flags),
    }
    return solution_row, samples_rows


def build_failure_row(
    status: str,
    wall_clock_s: float = 0.0,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return (
        {
            "status":             status,
            "best_energy":        None,
            "best_vars_json":     None,
            "wall_clock_s":       float(wall_clock_s),
            "algorithmic_time_s": float(wall_clock_s),
            "flags":              flags_json([]),
        },
        [],
    )