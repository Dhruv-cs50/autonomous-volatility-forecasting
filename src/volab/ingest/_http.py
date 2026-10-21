"""The one place in the package that makes an HTTP request.

Every fetcher takes a ``Transport`` rather than calling out itself, so the whole ingestion layer
is exercisable from committed fixtures with no network access. ``default_transport`` is the only
implementation that touches the wire, and no test uses it.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from collections.abc import Callable

Transport = Callable[[str], bytes]

# Nasdaq's historical endpoint refuses a request without a browser User-Agent.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_TIMEOUT_SECONDS = 30.0
_ATTEMPTS = 3
_BACKOFF_SECONDS = 2.0


def default_transport(url: str) -> bytes:
    """Fetch ``url`` and return its body, retrying a transient failure a few times.

    Retries exist because these are public endpoints with no service guarantee, not because a
    failure is expected. A refusal that repeats is raised rather than swallowed: ingestion that
    silently returns nothing would look identical to a vendor with no data.
    """
    request = urllib.request.Request(  # noqa: S310 - https URLs only, built by the fetchers
        url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json, text/csv, */*"}
    )
    last: Exception | None = None
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310
                body: bytes = response.read()
                return body
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            if attempt < _ATTEMPTS:
                time.sleep(_BACKOFF_SECONDS * attempt)
    raise RuntimeError(f"fetching {url} failed after {_ATTEMPTS} attempts: {last}")
