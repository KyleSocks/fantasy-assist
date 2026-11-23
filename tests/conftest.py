"""Starlette's TestClient sends Host: testserver; allow it like a real
deployment allows its hostnames (src/api/security.py)."""
import os

os.environ["FA_ALLOWED_HOSTS"] = ",".join(
    filter(None, [os.environ.get("FA_ALLOWED_HOSTS", ""), "testserver"]))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_app_settings(monkeypatch, tmp_path):
    """Tests run on config/inseason.yaml alone: never a settings file left
    on this machine by the app's gear button."""
    import src.inseason.settings as st
    import src.inseason.teams as teams
    monkeypatch.setattr(st, "SETTINGS_PATH", tmp_path / "settings.yaml")
    monkeypatch.setattr(teams, "INJURY_RETURNS_PATH", tmp_path / "injury_returns.json")


@pytest.fixture(autouse=True)
def _no_live_matchups(monkeypatch):
    """Refresh tests fake the Yahoo pages they use; the matchup pull (an
    optional extra) must never reach the network from a test."""
    import src.inseason.yahoo_public as yp

    def offline(*a, **k):
        raise ValueError("offline in tests")
    monkeypatch.setattr(yp, "fetch_matchups", offline)
