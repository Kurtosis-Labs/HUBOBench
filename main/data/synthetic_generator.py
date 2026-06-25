"""Synthetic HUBO instance generator for HUBOBench boundary-stress testing.

Generates canonical problem instances with fully controlled parameters:
    n_variables   — number of binary decision variables
    max_degree    — polynomial degree (1–5; must be ≤ 5 for Dirac-3)
    density       — fraction of possible monomials that are non-zero, per degree
    dynamic_range — max|c| / min|c| coefficient spread ratio

Coefficients are drawn log-uniformly across the full requested range with
random signs. No domain-specific structure is embedded.

Instances write directly to hubobench.db (instances table) conforming to
problem_schema.md v0.3.0. The table is assumed to already exist (created out
of band from schema.sql); this module owns no DDL. No JSON files are produced.

Usage — programmatic:
    from synthetic_generator import generate_instance
    sql_row, record = generate_instance(
        n_variables=30, max_degree=3,
        density=0.3, dynamic_range=200.0, seed=42,
    )

Usage — CLI (single instance):
    python synthetic_generator.py --n 30 --degree 3 --density 0.3 --dr 200 --seed 42

Usage — CLI (batch sweep):
    python synthetic_generator.py --batch
    python synthetic_generator.py --batch --seeds 0 1 2 3 4
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from itertools import combinations
from pathlib import Path

import numpy as np

from main.data.config import (
    EPS_COEF,
    REPO_ROOT,
)
from main.data.encoding.instance_builder import (
    assemble_instance,
    insert_instance,
)

HUBOBENCH_DB = REPO_ROOT / "data" / "hubobench.db"

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Named zones for batch generation (Customisable)
# ─────────────────────────────────────────────────────────────────────────────

DR_ZONES: dict[str, float] = {
    "clean":           10.0,
    "dirac_boundary":  200.0,
    "gurobi_warning":  1e6,
    "gurobi_degraded": 1e9,
}

N_BY_DEGREE: dict[int, list[int]] = {
    3: [10, 19, 30, 39, 50, 75, 99, 120],
    4: [10, 19, 30, 39],
}

BATCH_DEGREES   = [3, 4]
BATCH_DENSITIES = [0.2, 0.5, 1.0]


# ─────────────────────────────────────────────────────────────────────────────
# Core coefficient generation
# ─────────────────────────────────────────────────────────────────────────────

def _generate_terms(
    n_variables: int,
    max_degree: int,
    density: float | dict[str, float],
    dynamic_range: float,
    rng: np.random.Generator,
    sign_balance: float = 0.5,
) -> list[dict]:
    """Generate a random HUBO term list with controlled density and dynamic range."""
    if isinstance(density, float):
        density_map = {d: density for d in range(1, max_degree + 1)}
    else:
        density_map = {int(k): float(v) for k, v in density.items()}

    all_terms: list[tuple[tuple[int, ...], float]] = []

    for d in range(1, max_degree + 1):
        d_density = density_map.get(d, 0.0)
        if d_density <= 0.0:
            continue

        all_monomials = list(combinations(range(n_variables), d))
        n_possible    = len(all_monomials)
        n_select      = min(max(1, round(d_density * n_possible)), n_possible)

        chosen_idx = rng.choice(n_possible, size=n_select, replace=False)
        chosen     = [all_monomials[i] for i in sorted(chosen_idx)]

        if not chosen:
            continue

        n = len(chosen)
        if n == 1:
            mags = np.array([1.0])
        else:
            raw = rng.uniform(0.0, 1.0, n)
            raw[np.argmin(raw)] = 0.0
            raw[np.argmax(raw)] = 1.0
            mags = np.exp(raw * np.log(max(dynamic_range, 1.0 + 1e-9)))

        signs = rng.choice([-1.0, 1.0], size=n, p=[sign_balance, 1.0 - sign_balance])
        coefs = mags * signs

        for vars_tuple, coef in zip(chosen, coefs):
            if abs(coef) > EPS_COEF:
                all_terms.append((vars_tuple, float(coef)))

    all_terms.sort(key=lambda x: (len(x[0]), x[0]))
    return [{"vars": list(vt), "coef": c} for vt, c in all_terms]


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def generate_instance(
    n_variables: int,
    max_degree: int,
    density: float | dict[str, float],
    dynamic_range: float,
    seed: int = 0,
    kvariant: str = "kfree",
    sign_balance: float = 0.5,
) -> tuple[dict, dict]:
    """Generate one synthetic HUBOBench instance.

    Returns (sql_row, registry_record).
    sql_row maps directly to hubobench.db instances table via INSERT_SQL.
    Caller is responsible for the DB write.

    Args:
        n_variables:   number of binary variables.
        max_degree:    highest term degree in the polynomial (1–5).
        density:       float (uniform across degrees) or dict[str, float]
                       (per-degree, e.g. {"1": 0.8, "3": 0.2}).
        dynamic_range: target max|c| / min|c| ratio for base polynomial.
        seed:          random seed for full reproducibility.
        kvariant:      "kfree" | "khalf" | "kquarter"
        sign_balance:  fraction of terms with negative coefficients (default 0.5).
    """
    if n_variables < 1:
        raise ValueError(f"n_variables must be ≥ 1, got {n_variables}")
    if not 1 <= max_degree <= 5:
        raise ValueError(f"max_degree must be 1–5, got {max_degree}")
    if dynamic_range < 1.0:
        raise ValueError(f"dynamic_range must be ≥ 1.0, got {dynamic_range}")

    rng   = np.random.default_rng(seed=seed)
    terms = _generate_terms(n_variables, max_degree, density, dynamic_range, rng, sign_balance)
    coef_table = {tuple(t["vars"]): t["coef"] for t in terms}

    if isinstance(density, float):
        density_note = str(density)
    else:
        density_note = ";".join(f"d{k}={v}" for k, v in sorted(density.items()))

    sql_row, base_record = assemble_instance(
        coef_table=coef_table,
        n_variables=n_variables,
        kvariant=kvariant,
        generator_meta={
            "instance_type": "synthetic",
            "seed":          seed,
            "notes": (
                f"n={n_variables}; degree={max_degree}; "
                f"density={density_note}; dynamic_range={dynamic_range}"
            ),
        },
    )

    registry_record = {
        **base_record,
        "max_degree":    max_degree,
        "density":       density if isinstance(density, float) else dict(density),
        "dynamic_range": dynamic_range,
        "dr_zone":       None,   # populated by generate_batch
    }

    return sql_row, registry_record


# ─────────────────────────────────────────────────────────────────────────────
# Batch generation
# ─────────────────────────────────────────────────────────────────────────────

def generate_batch(
    db_path: Path | None = None,
    degrees: list[int] | None = None,
    dr_zones: list[str] | None = None,
    densities: list[float] | None = None,
    seeds: list[int] | None = None,
    kvariant: str = "kfree",
) -> list[dict]:
    """Generate a benchmark sweep of synthetic instances, writing to hubobench.db.

    Sweeps over: N values (per degree), dynamic range zones, densities, seeds.
    Returns a list of registry_record dicts for all generated instances.
    """
    db_path   = db_path   or HUBOBENCH_DB
    degrees   = degrees   or BATCH_DEGREES
    dr_zones  = dr_zones  or list(DR_ZONES.keys())
    densities = densities or BATCH_DENSITIES
    seeds     = seeds     or [0]

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL;")

    registry: list[dict] = []

    with conn:
        for degree in degrees:
            n_values = N_BY_DEGREE.get(degree, [10, 19, 30])
            for n in n_values:
                for zone_name in dr_zones:
                    dr = DR_ZONES[zone_name]
                    for density in densities:
                        for seed in seeds:
                            try:
                                sql_row, record = generate_instance(
                                    n_variables=n,
                                    max_degree=degree,
                                    density=density,
                                    dynamic_range=dr,
                                    seed=seed,
                                    kvariant=kvariant,
                                )
                            except Exception as exc:
                                log.warning(
                                    "Skipped n=%d deg=%d dr=%s density=%.1f seed=%d: %s",
                                    n, degree, zone_name, density, seed, exc,
                                )
                                continue

                            insert_instance(conn, sql_row)
                            record["dr_zone"] = zone_name
                            registry.append(record)

                            log.info(
                                "  N=%d deg=%d zone=%-18s density=%.1f seed=%d → %s",
                                n, degree, zone_name, density, seed,
                                record["instance_id"],
                            )

    log.info("Batch complete: %d instances written to %s", len(registry), db_path)
    conn.close()
    return registry


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Synthetic HUBO instance generator for HUBOBench"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--batch", action="store_true",
                      help="Run full benchmark sweep")

    parser.add_argument("--n",       type=int,   default=10)
    parser.add_argument("--degree",  type=int,   default=3)
    parser.add_argument("--density", type=float, default=0.5)
    parser.add_argument("--dr",      type=float, default=10.0)
    parser.add_argument("--seed",    type=int,   default=0)
    parser.add_argument("--kvariant", choices=["kfree", "khalf", "kquarter"],
                        default="kfree")

    parser.add_argument("--degrees",   type=int,   nargs="+", default=None)
    parser.add_argument("--dr-zones",  nargs="+",  choices=list(DR_ZONES.keys()), default=None)
    parser.add_argument("--densities", type=float, nargs="+", default=None)
    parser.add_argument("--seeds",     type=int,   nargs="+", default=None)
    parser.add_argument("--db",        type=Path,  default=None,
                        help=f"Path to hubobench.db. Default: {HUBOBENCH_DB}")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    args = _parse_args()

    if args.batch:
        generate_batch(
            db_path=args.db,
            degrees=args.degrees,
            dr_zones=args.dr_zones,
            densities=args.densities,
            seeds=args.seeds,
            kvariant=args.kvariant,
        )
    else:
        sql_row, record = generate_instance(
            n_variables=args.n,
            max_degree=args.degree,
            density=args.density,
            dynamic_range=args.dr,
            seed=args.seed,
            kvariant=args.kvariant,
        )
        db_path = args.db or HUBOBENCH_DB
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode = WAL;")
        with conn:
            insert_instance(conn, sql_row)
        conn.close()

        log.info("Instance written to %s", db_path)
        log.info("  instance_id=%s  n=%d  degree=%d  density=%.2f  dr=%.1e  seed=%d",
                 record["instance_id"], args.n, args.degree,
                 args.density, args.dr, args.seed)
        log.info("  dynamic_range_ratio=%.4g  num_terms=%d",
                 sql_row["dynamic_range_ratio"], sql_row["num_terms"])


if __name__ == "__main__":
    main()