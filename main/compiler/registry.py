"""compiler/registry.py

Dynamic solver discovery.

Instead of a hand-maintained dict, the solver registry is built at runtime by
scanning ``compiler/solvers/run_*.py`` for modules that expose the run-wrapper
contract. Drop a new ``run_<solver>.py`` into that package and it is picked up
automatically — no edit to any registry. Removing a file unregisters it.

Run-wrapper contract (a module is a solver iff it exposes all of these):
    SOLVER_NAME             str   — key stored in solver_configs.solver_name
    LIMITS_DOSSIER_VERSION  str
    DEFAULT_CONFIG          dict  — the solver's default knobs
    run(conn, problem_hash, run_id, solver_config_id, config) -> status
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
from types import ModuleType

from main.compiler import solvers as _solvers_pkg

_REQUIRED = ("SOLVER_NAME", "LIMITS_DOSSIER_VERSION", "DEFAULT_CONFIG", "run")


def discover_solvers() -> dict[str, ModuleType]:
    """Discover solver run modules under ``compiler/solvers/``.

    Returns ``{SOLVER_NAME: module}`` for every ``run_*.py`` that satisfies the
    run-wrapper contract, in deterministic (module-name-sorted) order.

    A ``run_*.py`` that is missing part of the contract is skipped with a
    warning (it may be a scaffold). A duplicate ``SOLVER_NAME`` is a hard error
    — two modules claiming one identity would silently shadow each other.
    """
    registry: dict[str, ModuleType] = {}
    mod_names = sorted(
        info.name
        for info in pkgutil.iter_modules(_solvers_pkg.__path__)
        if info.name.startswith("run_")
    )
    for mod_name in mod_names:
        module = importlib.import_module(f"{_solvers_pkg.__name__}.{mod_name}")
        missing = [attr for attr in _REQUIRED if not hasattr(module, attr)]
        if missing:
            print(
                f"[registry] skipping {mod_name}: missing {missing}",
                file=sys.stderr,
            )
            continue
        solver_name = module.SOLVER_NAME
        if solver_name in registry:
            raise ValueError(
                f"duplicate SOLVER_NAME {solver_name!r}: defined by both "
                f"{registry[solver_name].__name__} and {module.__name__}"
            )
        registry[solver_name] = module
    return registry
