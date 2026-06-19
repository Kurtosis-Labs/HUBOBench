"""compiler/solver_io/helpers/instance_loader.py

Shared instance loader for HUBOBench solvers.

This is the single place that knows how to turn a row of the hubobench.db
`instances` table into the fields a solver_io encode_problem needs.

The instances table stores the polynomial as objective_json = {terms, constant}
(n_variables is NOT in the blob; it is the typed num_variables column). This
loader reconstructs the pieces the encoders ask for:

    load_instance(conn, problem_hash) -> LoadedInstance(
        problem_hash:  str,
        num_variables: int,
        max_degree:    int,
        objective:     {"terms": [...], "constant": float},
    )

Encoders take objective + num_variables (+ max_degree where the device cares).
Decoders take objective + num_variables.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LoadedInstance:
    """The fields a solver_io module needs, reconstructed from one SQL row."""
    problem_hash:  str
    num_variables: int
    max_degree:    int
    objective:     dict[str, Any]   # {"terms": [...], "constant": float}


# Columns pulled for encode/decode. objective_json is parsed; num_variables
# and max_degree come from the typed columns (the blob no longer carries N).
_SELECT_INSTANCE = """
    SELECT problem_hash, num_variables, max_degree, objective_json
    FROM instances
    WHERE problem_hash = ?
"""


def load_instance(conn: sqlite3.Connection, problem_hash: str) -> LoadedInstance:
    """Fetch one instance and reconstruct the solver-facing fields.

    Args:
        conn:         open connection to hubobench.db.
        problem_hash: the 64-char PK of the instance.

    Returns:
        LoadedInstance with objective parsed to {terms, constant}.

    Raises:
        KeyError: if no instance row exists for problem_hash.
        ValueError: if objective_json is malformed or missing terms.
    """
    row = conn.execute(_SELECT_INSTANCE, (problem_hash,)).fetchone()
    if row is None:
        raise KeyError(f"no instance row for problem_hash={problem_hash!r}")

    ph, num_variables, max_degree, objective_json = row

    try:
        objective = json.loads(objective_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"objective_json for {problem_hash!r} is not valid JSON: {exc}"
        ) from exc

    if "terms" not in objective:
        raise ValueError(
            f"objective_json for {problem_hash!r} has no 'terms' key"
        )
    objective.setdefault("constant", 0.0)

    return LoadedInstance(
        problem_hash=str(ph),
        num_variables=int(num_variables),
        max_degree=int(max_degree),
        objective=objective,
    )