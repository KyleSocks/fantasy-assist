"""Starlette's TestClient sends Host: testserver; allow it like a real
deployment allows its hostnames (src/api/security.py)."""
import os

os.environ["FA_ALLOWED_HOSTS"] = ",".join(
    filter(None, [os.environ.get("FA_ALLOWED_HOSTS", ""), "testserver"]))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_live_matchups(monkeypatch):
    """Refresh tests fake the Yahoo pages they use; the matchup pull (an
    optional extra) must never reach the network from a test."""
    import src.inseason.yahoo_public as yp

    def offline(*a, **k):
        raise ValueError("offline in tests")
    monkeypatch.setattr(yp, "fetch_matchups", offline)
