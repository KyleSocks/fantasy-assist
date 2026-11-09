"""Starlette's TestClient sends Host: testserver; allow it like a real
deployment allows its hostnames (src/api/security.py)."""
import os

os.environ["FA_ALLOWED_HOSTS"] = ",".join(
    filter(None, [os.environ.get("FA_ALLOWED_HOSTS", ""), "testserver"]))
