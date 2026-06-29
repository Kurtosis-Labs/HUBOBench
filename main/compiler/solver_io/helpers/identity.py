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
    """
    Compute the SHA-256 digest of the given bytes.
    
    Parameters:
    	data (bytes): Input data to hash.
    
    Returns:
    	str: The lowercase hexadecimal SHA-256 digest.
    """
    return hashlib.sha256(data).hexdigest()


def _host_fingerprint() -> str:
    """
    Build a host-derived fingerprint for the current runtime environment.
    
    Returns:
    	str: A string in the form ``host:<16-character-hex-digest>``.
    """
    parts = [platform.platform(), platform.machine(), "py" + platform.python_version()]
    return f"host:{_sha256_hex('|'.join(parts).encode())[:16]}"


def capture_environment_digest() -> str:
    """
    Capture the environment digest for the current run.
    
    Returns:
    	str: The container image digest from HUBOBENCH_CONTAINER_DIGEST when set, otherwise a host fingerprint prefixed with ``host:``.
    """
    return os.environ.get(CONTAINER_DIGEST_ENV) or _host_fingerprint()
