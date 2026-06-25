"""Content-addressed solver (candidate) identity.

A candidate's identity is a SHA-256 over its provenance, so any material change
forks a new identity and results are never silently pooled under one
solver_config_id. Components (the clean 4-component design — see issue #8):

    solver_name · source_commit · config · environment_digest · dep_lock_digest

Decisions baked in:
  - Anchor: solver_configs.UNIQUE(solver_identity_hash).
  - Environment: a real digest ALWAYS — the injected container image digest when
    present, else a host fingerprint. No "none" sentinel.
  - Dirty tree: refuse to run with uncommitted *tracked* changes (so there is no
    separate patch field). Override with HUBOBENCH_ALLOW_DIRTY=1 for dev, which
    marks the commit "+dirty" so the identity still differs from a clean build.
  - No solver_version: dep_lock_digest supersedes it; "which solver" stays
    readable via solver_name.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
from pathlib import Path
from typing import Any

# main/compiler/solver_io/helpers/identity.py -> repo root is parents[4].
REPO_ROOT = Path(__file__).resolve().parents[4]
CONTAINER_DIGEST_ENV = "HUBOBENCH_CONTAINER_DIGEST"
ALLOW_DIRTY_ENV = "HUBOBENCH_ALLOW_DIRTY"


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO_ROOT, text=True).strip()


def _tree_is_dirty() -> bool:
    """True if there are uncommitted changes to *tracked* files (staged or not).

    Untracked / gitignored files (the DB, .lavish artifacts, …) do not count.
    """
    out = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    ).stdout
    return bool(out.strip())


def _host_fingerprint() -> str:
    parts = [platform.platform(), platform.machine(), "py" + platform.python_version()]
    return "host:" + _sha256_hex("|".join(parts).encode())[:16]


def capture_provenance() -> dict[str, str]:
    """Capture this run's provenance: source_commit, environment_digest, dep_lock_digest.

    Raises RuntimeError if the working tree has uncommitted tracked changes,
    unless HUBOBENCH_ALLOW_DIRTY is set (then source_commit is marked '+dirty').
    """
    commit = _git("rev-parse", "HEAD")
    if _tree_is_dirty():
        if os.environ.get(ALLOW_DIRTY_ENV):
            commit = f"{commit}+dirty"
        else:
            raise RuntimeError(
                "Refusing to run with uncommitted tracked changes: a candidate's "
                "identity must pin a real commit. Commit (or stash) your changes, or "
                f"set {ALLOW_DIRTY_ENV}=1 to override (the run is then marked '+dirty')."
            )

    lock = REPO_ROOT / "uv.lock"
    dep_lock_digest = (
        "lock:" + _sha256_hex(lock.read_bytes())[:16] if lock.exists() else "lock:none"
    )
    environment_digest = os.environ.get(CONTAINER_DIGEST_ENV) or _host_fingerprint()

    return {
        "source_commit": commit,
        "environment_digest": environment_digest,
        "dep_lock_digest": dep_lock_digest,
    }


def compute_solver_identity_hash(
    solver_name: str, config: dict[str, Any], provenance: dict[str, str]
) -> str:
    """SHA-256 over the canonical identity tuple. 64-char lowercase hex."""
    canonical = {
        "solver_name":        solver_name,
        "source_commit":      provenance["source_commit"],
        "environment_digest": provenance["environment_digest"],
        "dep_lock_digest":    provenance["dep_lock_digest"],
        "config":             config,
    }
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return _sha256_hex(blob)
