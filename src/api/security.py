"""HTTP hardening for a no-login LAN app.

- Cross-site write guard: any other website open in your browser could
  otherwise POST /api/inseason/refresh or PUT the lists (a "simple" POST
  needs no CORS preflight). Writes whose Sec-Fetch-Site says cross-site,
  or whose Origin doesn't match the Host, get 403. Same-origin pages and
  non-browser clients (curl from the refresh timer, the CLI) send neither
  header mismatch, so they pass.
- Host allowlist (DNS rebinding): a hostile page can point its own
  hostname at 192.168.10.69, after which the browser treats it as
  same-origin -- Origin and Host both say evil.example:8420, so the guard
  above passes and the page can read your lists and PUT/POST freely. The
  Host header is the one thing such a page can't fake, so only IP literals,
  localhost and the names in FA_ALLOWED_HOSTS (comma-separated, * wildcards,
  set in docker-compose.yml) are served; anything else gets 400.
- Security headers on every response: no framing, no MIME sniffing, a
  strict same-origin content policy (scripts only from /static).
"""
from __future__ import annotations

import ipaddress
import os
from fnmatch import fnmatchcase
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# style-src needs 'unsafe-inline': templates set style= attributes and the
# uPlot charts size themselves with inline styles. No inline scripts exist.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
       "base-uri 'none'; form-action 'self'; object-src 'none'")

HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": CSP,
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


BASE_HOSTS = ("localhost",)


def allowed_hosts() -> tuple[str, ...]:
    extra = os.environ.get("FA_ALLOWED_HOSTS", "")
    return BASE_HOSTS + tuple(h.strip().lower() for h in extra.split(",")
                              if h.strip())


def _hostname(host_header: str) -> str:
    """Host header minus the port: "a:8420" -> "a", "[::1]:80" -> "::1"."""
    h = host_header.strip().lower()
    if h.startswith("["):
        return h[1:h.find("]")] if "]" in h else h
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


def host_allowed(host_header: str, patterns: tuple[str, ...] | None = None) -> bool:
    name = _hostname(host_header).rstrip(".")
    if not name:
        return False
    try:
        ipaddress.ip_address(name)
        return True       # rebinding always needs a hostname, never an IP
    except ValueError:
        pass
    return any(fnmatchcase(name, p)
               for p in (allowed_hosts() if patterns is None else patterns))


def cross_site_write(request: Request) -> str | None:
    """Why a write request looks cross-site, or None if it's fine."""
    if request.method not in WRITE_METHODS:
        return None
    site = request.headers.get("sec-fetch-site")
    if site == "cross-site":
        return "cross-site request"
    origin = request.headers.get("origin")
    if origin and origin != "null":
        host = request.headers.get("host", "")
        if urlsplit(origin).netloc.lower() != host.lower():
            return f"origin {origin} does not match host {host}"
    elif origin == "null":
        return "opaque origin"
    return None


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        why = cross_site_write(request)
        if not host_allowed(request.headers.get("host", "")):
            response = JSONResponse(
                {"detail": "Invalid host header (add it to FA_ALLOWED_HOSTS)"},
                status_code=400)
        elif why:
            response = JSONResponse({"detail": f"Forbidden: {why}"},
                                    status_code=403)
        else:
            response = await call_next(request)
        for k, v in HEADERS.items():
            response.headers.setdefault(k, v)
        return response
