"""HTTP hardening for a no-login LAN app.

- Cross-site write guard: any other website open in your browser could
  otherwise POST /api/inseason/refresh or PUT the lists (a "simple" POST
  needs no CORS preflight). Writes whose Sec-Fetch-Site says cross-site,
  or whose Origin doesn't match the Host, get 403. Same-origin pages and
  non-browser clients (curl from the refresh timer, the CLI) send neither
  header mismatch, so they pass.
- Security headers on every response: no framing, no MIME sniffing, a
  strict same-origin content policy (scripts only from /static).
"""
from __future__ import annotations

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
        if why:
            response = JSONResponse({"detail": f"Forbidden: {why}"},
                                    status_code=403)
        else:
            response = await call_next(request)
        for k, v in HEADERS.items():
            response.headers.setdefault(k, v)
        return response
