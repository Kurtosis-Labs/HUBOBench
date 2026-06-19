"""Central configuration for HUBOBench.

Constants shared across the HUBOBench instance-generation and benchmarking
modules. HUBOBench generates synthetic HUBO instances with fully controlled
structure (degree, density, dynamic range) and stores them as canonical rows
in hubobench.db. No instances depend on any external data source.

Run-level values (seeds, N sweeps, output DB path) are passed as CLI
arguments to synthetic_generator.py and override the defaults defined in
that module.
"""

from __future__ import annotations

import sys
from pathlib import Path

# ── Repo layout ──────────────────────────────────────────────────────────────
# DATA_ROOT is the package root (the directory containing this file). The
# default database lives in a sibling data/ directory at the repo root.

DATA_ROOT = Path(__file__).parent.resolve()
_REPO_ROOT = DATA_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

DEFAULT_DB_PATH = _REPO_ROOT / "data" / "hubobench.db"

# ── Cardinality variants ──────────────────────────────────────────────────────
# Generic "select exactly k of N" constraint variants. kfree applies no
# cardinality constraint; khalf and kquarter target k = N//2 and N//4.

CARDINALITY_VARIANTS = ["kfree", "khalf", "kquarter"]

# ── Encoding ──────────────────────────────────────────────────────────────────

EPS_COEF             = 1e-15   # drop terms with |coef| < this
CARDINALITY_M_FACTOR = 10.0    # cardinality penalty M = factor × max|coef in base objective|

# ── Schema / generator versioning ─────────────────────────────────────────────

SCHEMA_VERSION = "0.3.0"
GENERATOR_NAME = "hubo_bench"