"""Autouse guard so tests/integration never runs (or hangs) without a real LM Studio server.

See Q7: marker + reachability probe, both gating independently.
"""

import socket

import pytest

LM_STUDIO_HOST = "localhost"
LM_STUDIO_PORT = 1234


def _lm_studio_is_reachable() -> bool:
    try:
        with socket.create_connection((LM_STUDIO_HOST, LM_STUDIO_PORT), timeout=0.5):
            return True
    except OSError:
        return False


@pytest.fixture(autouse=True)
def _require_lm_studio():
    if not _lm_studio_is_reachable():
        pytest.skip(f"LM Studio not reachable at {LM_STUDIO_HOST}:{LM_STUDIO_PORT}")
