"""benchmarks/verify_corpus.py

Integrity check for the instances corpus: re-derive each instance's
``problem_hash`` from its stored row and confirm it still equals the PK.

This enforces the invariant the schema already assumes — ``problem_hash`` is a
content address of the polynomial (see ``benchmarks/hash.compute_problem_hash``
and ``data/encoding/instance_builder`` §4). The supported generator path cannot
break it (instances are written ``INSERT OR IGNORE`` and never ``UPDATE``-d), so
a mismatch means the row was altered out-of-band (a direct
``UPDATE instances SET objective_json=...``) or is corrupt.

    python -m main.benchmarks.verify_corpus [--db data/hubobench.db]

Exit code 0 if every instance verifies, 1 if any mismatch is found.

This is an EXPLICIT integrity command, deliberately NOT an always-on per-load
check: re-hashing every instance is kept off the solver hot path. Call it in CI,
before a scoring run, or after any manual DB surgery.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass

from main.benchmarks.hash import compute_problem_hash

# Constants the generator folds into the hash input but does NOT store per row
# (instance_builder.py §4: n_variables lives in its own column; sense and the
# variable domain are fixed for HUBOBench). These MUST match instance_builder
# exactly or every row would mis-verify.
_SENSE = "minimize"
_VARIABLE_DOMAIN = "binary_01"

DEFAULT_DB = "data/hubobench.db"


def recompute_problem_hash(num_variables: int, objective_json: str | bytes) -> str:
    """Re-derive an instance's problem_hash from its stored columns.

    Reconstructs the canonical hash-input dict exactly as instance_builder §4
    builds it (objective + parameters incl. n_variables), then delegates to
    compute_problem_hash. Term order is irrelevant — compute_problem_hash
    re-sorts canonically.
    """
    obj = json.loads(objective_json)
    return compute_problem_hash({
        "objective": {
            "sense":    _SENSE,
            "constant": float(obj.get("constant", 0.0)),
            "terms":    obj["terms"],
        },
        "parameters": {
            "n_variables":     int(num_variables),
            "variable_domain": _VARIABLE_DOMAIN,
        },
    })


@dataclass(frozen=True)
class Mismatch:
    """One instance whose stored PK disagrees with a fresh hash of its content."""
    problem_hash: str   # the stored PK
    computed:     str   # what the stored content actually hashes to


def verify_corpus(conn: sqlite3.Connection) -> tuple[int, list[Mismatch]]:
    """Verify every instance row. Returns (n_checked, mismatches)."""
    rows = conn.execute(
        "SELECT problem_hash, num_variables, objective_json FROM instances"
    ).fetchall()
    mismatches: list[Mismatch] = []
    for stored_hash, num_variables, objective_json in rows:
        computed = recompute_problem_hash(num_variables, objective_json)
        if computed != stored_hash:
            mismatches.append(Mismatch(problem_hash=str(stored_hash), computed=computed))
    return len(rows), mismatches


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify the instances corpus: every problem_hash must equal a fresh hash of its content."
    )
    parser.add_argument(
        "--db", default=DEFAULT_DB, help=f"Path to hubobench.db. Default: {DEFAULT_DB}"
    )
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    try:
        n, mismatches = verify_corpus(conn)
    finally:
        conn.close()

    if not mismatches:
        print(f"[verify] OK — {n} instance(s) verified; every problem_hash matches its content.")
        return

    print(f"[verify] FAIL — {len(mismatches)}/{n} instance(s) do NOT match their content hash:")
    for m in mismatches:
        print(f"  stored {m.problem_hash}  !=  computed {m.computed}")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
