"""compiler/manifest.py

Experiment manifests — declare *what to run* as data, so agents can drive
HUBOBench from a CLI file instead of editing the solver registry or a solver's
DEFAULT_CONFIG.

Manifest (JSON):
    {
      "notes": "gurobi MIPGap sweep + SA seeds",   // optional, run-level
      "experiments": [
        {"solver": "gurobi_miqp", "config": {"MIPGap": 1e-6}},
        {"solver": "SA_OpenJij",  "config": {"num_sweeps": 2000, "seed": 7}},
        {"solver": "SA_OpenJij"}                    // no override -> DEFAULT_CONFIG
      ]
    }

Each experiment's ``config`` is *overrides* merged onto that solver's
DEFAULT_CONFIG. Override keys must exist in DEFAULT_CONFIG (typos are rejected).
Because config is part of the content-addressed solver identity, two experiments
with the same solver but different configs fork distinct solver_config_ids —
results never pool across them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any


@dataclass(frozen=True)
class Experiment:
    """One thing to run: a solver and its fully-resolved (merged) config."""
    solver: str
    config: dict[str, Any]


def load_manifest(
    path: str | Path, registry: dict[str, ModuleType]
) -> tuple[list[Experiment], str | None]:
    """Parse and validate a JSON experiment manifest against ``registry``.

    Returns ``(experiments, notes)``.

    Raises ValueError on any problem: malformed JSON/shape, an unknown solver
    name, or a config override key absent from the solver's DEFAULT_CONFIG.
    """
    raw = json.loads(Path(path).read_text())
    if not isinstance(raw, dict) or "experiments" not in raw:
        raise ValueError("manifest must be a JSON object with an 'experiments' list")

    notes = raw.get("notes")
    raw_exps = raw["experiments"]
    if not isinstance(raw_exps, list) or not raw_exps:
        raise ValueError("manifest 'experiments' must be a non-empty list")

    experiments: list[Experiment] = []
    for i, entry in enumerate(raw_exps):
        if not isinstance(entry, dict) or "solver" not in entry:
            raise ValueError(f"experiment[{i}] must be an object with a 'solver' key")

        solver = entry["solver"]
        if solver not in registry:
            raise ValueError(
                f"experiment[{i}]: unknown solver {solver!r}; "
                f"valid: {sorted(registry)}"
            )

        overrides = entry.get("config", {})
        if not isinstance(overrides, dict):
            raise ValueError(f"experiment[{i}] ({solver}): 'config' must be an object")

        default = registry[solver].DEFAULT_CONFIG
        unknown = set(overrides) - set(default)
        if unknown:
            raise ValueError(
                f"experiment[{i}] ({solver}): unknown config key(s) {sorted(unknown)}; "
                f"valid: {sorted(default)}"
            )

        experiments.append(Experiment(solver=solver, config={**default, **overrides}))

    return experiments, notes
