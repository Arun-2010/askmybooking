from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from airline_agent.schemas import PolicySnapshot  # noqa: E402


@pytest.fixture(autouse=True)
def no_gemini_pacing(monkeypatch):
    """Request pacing protects real free-tier quotas; tests use fake clients."""
    from airline_agent.agent import gemini_loop

    monkeypatch.setattr(gemini_loop, "MIN_INTERVAL_S", 0)
    # Tests pick their provider explicitly; don't inherit the developer's .env choice.
    monkeypatch.setenv("AGENT_PROVIDER", "anthropic")


@pytest.fixture
def seed() -> list[dict]:
    return json.loads((ROOT / "mock_site" / "seed_bookings.json").read_text(encoding="utf-8"))["bookings"]


@pytest.fixture
def pinned_snapshots() -> dict[str, PolicySnapshot]:
    data = json.loads((ROOT / "eval" / "fixtures" / "snapshots.json").read_text(encoding="utf-8"))["snapshots"]
    return {d["snapshot_id"]: PolicySnapshot(**d) for d in data}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def mock_site_url():
    """Start the TestAir mock site in a subprocess for browser tests."""
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "mock_site.app:app", "--port", str(port), "--log-level", "warning"],
        cwd=str(ROOT), env={**os.environ},
    )
    url = f"http://127.0.0.1:{port}"
    import httpx

    for _ in range(50):
        try:
            if httpx.get(url + "/healthz", timeout=1).status_code == 200:
                break
        except Exception:
            time.sleep(0.2)
    yield url
    proc.terminate()
    proc.wait(timeout=10)
