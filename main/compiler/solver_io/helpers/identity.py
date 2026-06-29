"""Environment digest for solver identity.

A solver_configs row is uniquely identified by (solver_name, config_json,
environment_digest). The environment digest distinguishes runs across different
machines or containers without re-running the corpus when code changes.

Environment: a real digest always — the injected container image digest when
present (HUBOBENCH_CONTAINER_DIGEST), else a host fingerprint derived from
platform, machine, and Python version.
"""

from __future__ import annotations

import hashlib
import os
import platform

CONTAINER_DIGEST_ENV = "HUBOBENCH_CONTAINER_DIGEST"


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _host_fingerprint() -> str:
    parts = [platform.platform(), platform.machine(), "py" + platform.python_version()]
    return f"host:{_sha256_hex('|'.join(parts).encode())[:16]}"


def capture_environment_digest() -> str:
    """Return this run's environment digest.

    Returns the container image digest from HUBOBENCH_CONTAINER_DIGEST if set,
    otherwise a 16-char host fingerprint derived from platform + Python version.
    """
    return os.environ.get(CONTAINER_DIGEST_ENV) or _host_fingerprint()
