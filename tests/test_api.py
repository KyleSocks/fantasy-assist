"""Tests for src/api/routes.py — build_board payload and HTTP endpoints.

Only checks that need no ingested data (unknown league, static assets);
tests against the real database were removed because its numbers change
with every ingest.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.api.routes import app, build_board


# ─── Fixtures ───


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


# ─── build_board: unknown league -> 404 ───


def test_unknown_league_raises_404():
    with pytest.raises(HTTPException) as excinfo:
        build_board("nope")
    exc = excinfo.value
    assert exc.status_code == 404
    assert "nope" in exc.detail


# ─── HTTP layer (TestClient) ───


def test_http_unknown_league_404(client):
    res = client.get("/api/board", params={"league": "nope"})
    assert res.status_code == 404


def test_http_index_serves_board_html(client):
    res = client.get("/")
    assert res.status_code == 200
    assert b"/static/board.js" in res.content


def test_http_static_board_js(client):
    res = client.get("/static/board.js")
    assert res.status_code == 200
    # sanity: the client bundle served is the real board.js (has draft logic)
    assert b"drafted" in res.content


# ─── HTTP layer: /api/player/{id} ──────────────────────────────────


def test_http_index_serves_detail_scripts(client):
    res = client.get("/")
    assert res.status_code == 200
    assert b"/static/vendor/uPlot.iife.min.js" in res.content
    assert b"/static/detail.js" in res.content


def test_http_static_detail_js(client):
    res = client.get("/static/detail.js")
    assert res.status_code == 200
    assert b"uPlot" in res.content
