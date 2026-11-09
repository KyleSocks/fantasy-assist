"""HTTP GET with bounded retries for the NHL API and Yahoo pages.

A refresh makes ~100 requests (NHL stats pages, schedule weeks, Yahoo
roster/free-agent pages); one transient 503, 429 or dropped connection used
to fail the whole thing. Transport errors, 429 and 5xx are retried with
exponential backoff (honouring Retry-After, capped); anything else -- a
404, a 403, a redirect to a login wall -- is returned/raised at once.
"""
from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}
ATTEMPTS = 4                 # 1 try + 3 retries
BACKOFF_S = 1.0              # 1s, 2s, 4s
MAX_WAIT_S = 30.0            # never sleep longer than this for one retry


def _wait(attempt: int, resp: httpx.Response | None) -> float:
    if resp is not None:
        ra = resp.headers.get("retry-after", "")
        if ra.isdigit():
            return min(float(ra), MAX_WAIT_S)
    return min(BACKOFF_S * 2 ** attempt, MAX_WAIT_S)


def get(client: httpx.Client, url: str, *, attempts: int = ATTEMPTS,
        sleep=time.sleep) -> httpx.Response:
    """client.get(url), retrying transient failures; raises for any final
    non-2xx status (httpx.HTTPStatusError) or transport error."""
    for attempt in range(attempts):
        last = attempt == attempts - 1
        try:
            resp = client.get(url)
        except (httpx.TransportError,) as e:
            if last:
                raise
            w = _wait(attempt, None)
            log.warning("GET %s failed (%s); retry in %.0fs", url, e, w)
            sleep(w)
            continue
        if resp.status_code in RETRY_STATUS and not last:
            w = _wait(attempt, resp)
            log.warning("GET %s -> %d; retry in %.0fs", url, resp.status_code, w)
            sleep(w)
            continue
        resp.raise_for_status()
        return resp
    raise AssertionError("unreachable")  # pragma: no cover
