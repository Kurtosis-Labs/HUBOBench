"""encoding/instance_builder.py

Shared instance assembly for HUBOBench.

Both run_pipeline.py and synthetic_generator.py build a coef_table through
different routes, then call assemble_instance() to apply cardinality
constraints, compute classifier features, hash the polynomial, and return a
SQL row dict ready for insertion into hubobench.db.

This module performs NO database I/O and owns NO table DDL. The instances
table is created out of band from schema.sql (the single source of truth for
the table definition). assemble_instance() is a pure function: coef_table in,
(sql_row, registry_record) out, no side effects.

The column contract is exported as INSTANCE_COLUMNS and the matching
parameterised INSERT is built by build_insert_sql(). Runners import both,
validate sql_row.keys() against INSTANCE_COLUMNS before executing, and own
the write. Keeping the column list in exactly one place prevents the two
runners from drifting apart.

Public API:
    assemble_instance(coef_table, n_variables, kvariant, generator_meta) → (sql_row, registry_record)
    INSTANCE_COLUMNS              — ordered tuple of instances-table column names
    build_insert_sql()            — parameterised INSERT built from INSTANCE_COLUMNS
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from main.constants import PROBLEM_SCHEMA_VERSION
from main.data.encoding.apply_cardinality import (
    apply_cardinality_penalty,
    cardinality_k_for_variant,
    compute_penalty_M,
)
from main.data.encoding.compute_diagnostics import compute_instance_features
from main.benchmarks.hash import compute_problem_hash


# ─────────────────────────────────────────────────────────────────────────────
# Column contract — the single source of truth for the instances row shape.
#
# Order matches schema.sql exactly. Runners build their INSERT from this and
# validate sql_row.keys() against it, so a missing or stray key is caught
# loudly rather than silently dropped by named-parameter binding.
# ─────────────────────────────────────────────────────────────────────────────

INSTANCE_COLUMNS: tuple[str, ...] = (
    "problem_hash",
    "problem_schema_version",
    "created_at",
    "num_variables",
    "max_degree",
    "density",
    "dynamic_range_ratio",
    "coeff_dist",
    "num_terms",
    "problem_class",
    "constraint_ratio",
    "objective_json",
)


def build_insert_sql(table: str = "instances") -> str:
    """Build the parameterised INSERT statement from INSTANCE_COLUMNS.

    Uses INSERT OR IGNORE so re-inserting an identical problem_hash (the PK)
    is a no-op rather than an error: instance generation is idempotent on the
    polynomial hash. Named placeholders (:col) bind directly from sql_row.
    """
    cols         = ",\n        ".join(INSTANCE_COLUMNS)
    placeholders = ",\n        ".join(f":{c}" for c in INSTANCE_COLUMNS)
    return (
        f"INSERT OR IGNORE INTO {table} (\n        {cols}\n    ) "
        f"VALUES (\n        {placeholders}\n    )"
    )


# Prebuilt once; runners reuse this rather than rebuilding per row.
INSERT_SQL: str = build_insert_sql()


def insert_instance(conn, sql_row: dict[str, Any]) -> int:
    """Validate a row against the column contract and insert it.

    The single place both runners go through to write an instance. Catches
    the two failure modes you care about:

      1. Column mismatch — sql_row has stray keys or is missing required ones.
         Named-parameter binding silently ignores extra keys and only raises
         on missing ones, so a stray column (e.g. a leftover data_source)
         would otherwise pass unnoticed. We check the key set explicitly and
         raise ValueError before touching the DB.

      2. Insert failure — any sqlite3 error (constraint, type, locked db) is
         re-raised wrapped with the problem_hash for diagnosis.

    Returns conn.execute's rowcount (0 if the hash already existed and the
    INSERT OR IGNORE was a no-op, 1 if a new row was written).
    """
    import sqlite3

    expected = set(INSTANCE_COLUMNS)
    got      = set(sql_row)
    if got != expected:
        missing = expected - got
        stray   = got - expected
        raise ValueError(
            f"sql_row column mismatch for problem_hash="
            f"{sql_row.get('problem_hash', '<unknown>')}: "
            f"missing={sorted(missing)} stray={sorted(stray)}"
        )

    try:
        cur = conn.execute(INSERT_SQL, sql_row)
    except sqlite3.Error as exc:
        raise sqlite3.Error(
            f"insert failed for problem_hash="
            f"{sql_row.get('problem_hash', '<unknown>')}: {exc}"
        ) from exc

    return cur.rowcount


# ─────────────────────────────────────────────────────────────────────────────
# Derivation maps: generator_meta.instance_type → SQL column values
#
# No data_source column exists in v0.3.0. No methodology-revealing labels:
# model_dependent maps to problem_class "model_dependent" with coeff_dist
# "empirical"; synthetic maps to "synthetic_random" / "synthetic".
# ─────────────────────────────────────────────────────────────────────────────

_COEFF_DIST: dict[str, str] = {
    "model_dependent": "empirical",
    "synthetic":       "synthetic",
}

_PROBLEM_CLASS: dict[str, str] = {
    "model_dependent": "model_dependent",
    "synthetic":       "synthetic_random",
}

def coef_table_to_terms(coef_table: dict[tuple, float]) -> list[dict]:
    """Convert coefficient table to sorted term list (§7.5).

    Term list is sorted by (degree, vars) for deterministic output.
    """
    terms = []
    for key in sorted(coef_table, key=lambda k: (len(k), k)):
        terms.append({"vars": list(key), "coef": float(coef_table[key])})
    return terms


# ─────────────────────────────────────────────────────────────────────────────
# Core assembly function
# ─────────────────────────────────────────────────────────────────────────────

def assemble_instance(
    coef_table: dict[tuple, float],
    n_variables: int,
    kvariant: str,
    generator_meta: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Assemble a hubobench.db instances row from a coefficient table.

    Pure function. Entry point shared by run_pipeline.py and
    synthetic_generator.py. Applies cardinality constraints, computes
    classifier features, hashes the polynomial via benchmarks.hash, and
    returns a SQL row dict. Performs no database I/O.

    Deliberately excluded (computed by other components at their own time):
        rosenberg_reduction  — gurobi_miqp.encode_problem() at solve time
        ground_truth         — evaluator at scoring time (N ≤ 25 only)
        detailed diagnostics — not stored in SQL

    Args:
        coef_table:     {vars_tuple: coef} representing the base HUBO
                        polynomial. Not mutated: cardinality works on a copy.
        n_variables:    number of binary decision variables.
        kvariant:       "kfree" | "khalf" | "kquarter"
        generator_meta: dict with keys:
                            instance_type  (str)  "model_dependent" | "synthetic"
                            seed           (int)
                            notes          (str)  partial; cardinality_k appended

    Returns:
        (sql_row, registry_record)

        sql_row keys are exactly INSTANCE_COLUMNS, ready for the runner to
        bind against build_insert_sql().

        registry_record carries run-manifest fields:
            instance_id, problem_hash, N, kvariant, seed, notes
        Callers merge pipeline-specific fields before writing the manifest.
    """
    # ── §1: Cardinality penalty ───────────────────────────────────────────
    k = cardinality_k_for_variant(n_variables, kvariant)
    constant = 0.0
    if k is not None:
        M = compute_penalty_M(coef_table)
        coef_table, constant = apply_cardinality_penalty(coef_table, n_variables, k, M)

    # ── §2: Terms list ────────────────────────────────────────────────────
    terms = coef_table_to_terms(coef_table)

    # ── §3: Classifier feature columns ───────────────────────────────────
    features = compute_instance_features(coef_table, n_variables)

    # ── §4: Reproducibility hash ──────────────────────────────────────────
    # Delegated entirely to benchmarks.hash. The input dict shape MUST match
    # compute_problem_hash's canonicalisation (objective) so that instances generated 
    # here hash identically to those already migrated from JSON.
    rep_hash = compute_problem_hash({
        "terms":    terms,
        "constant": float(constant),
    })

    # ── §5: objective_json blob ───────────────────────────────────────────
    # Exactly the two fields the solver encoder needs at runtime. n_variables
    # is NOT stored here: it is already the typed num_variables column, and
    # duplicating it would be redundant. Encoders read N from that column.
    objective_json = json.dumps(
        {
            "terms":    terms,
            "constant": float(constant),
        },
        separators=(",", ":"),
        ensure_ascii=True,
    )

    # ── §6: Caller-context fields ─────────────────────────────────────────
    instance_type = generator_meta.get("instance_type", "synthetic")
    if instance_type not in _PROBLEM_CLASS:
        raise ValueError(
            f"unknown instance_type {instance_type!r}; "
            f"expected one of {sorted(_PROBLEM_CLASS)}"
        )
    base_notes = generator_meta.get("notes", "")
    notes      = f"{base_notes}; cardinality_k={k}" if base_notes else f"cardinality_k={k}"

    # ── §7: Assemble SQL row (keys == INSTANCE_COLUMNS) ───────────────────
    sql_row: dict[str, Any] = {
        "problem_hash":           rep_hash,
        "problem_schema_version": PROBLEM_SCHEMA_VERSION,
        "created_at":             datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "num_variables":          n_variables,
        "max_degree":             features["max_degree"],
        "density":                features["density"],
        "dynamic_range_ratio":    features["dynamic_range_ratio"],
        "coeff_dist":             _COEFF_DIST[instance_type],
        "num_terms":              features["num_terms"],
        "problem_class":          _PROBLEM_CLASS[instance_type],
        "constraint_ratio":       round(k / n_variables, 6) if k is not None else 0.0,
        "objective_json":         objective_json,
    }

    # ── §8: Registry record (run manifest) ────────────────────────
    registry_record: dict[str, Any] = {
        "instance_id":  "inst_" + rep_hash[:8],
        "problem_hash": rep_hash,
        "N":            n_variables,
        "kvariant":     kvariant,
        "seed":         generator_meta.get("seed"),
        "notes":        notes,
    }

    return sql_row, registry_record