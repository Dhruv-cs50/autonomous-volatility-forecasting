"""The one place in the package that makes an HTTP request.

Every fetcher takes a ``Transport`` rather than calling out itself, so the whole ingestion layer
is exercisable from committed fixtures with no network access. ``default_transport`` is the only
implementation that touches the wire, and no test uses it.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.parse
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


# Hosts that must NOT receive a browser User-Agent. FRED tarpits such requests: measured, the
# same URL returns 168,568 bytes in 1.8s with no User-Agent or a curl-like one, and times out
# after 21s with a browser one. Nasdaq is the opposite -- it refuses a request without one. So
# the User-Agent cannot be a single global, and a source that wants neither default gets an
# explicit transport.
_NO_BROWSER_AGENT: frozenset[str] = frozenset({"fred.stlouisfed.org"})


def _headers_for(url: str) -> dict[str, str]:
    accept = "application/json, text/csv, */*"
    host = urllib.parse.urlsplit(url).hostname or ""
    if host in _NO_BROWSER_AGENT:
        # Deliberately no User-Agent at all rather than a fake one: urllib's own default is what
        # the host answers quickly, and inventing a curl string to dodge a filter is the kind of
        # thing that breaks silently when the filter changes.
        return {"Accept": accept}
    return {"User-Agent": _USER_AGENT, "Accept": accept}


def default_transport(url: str) -> bytes:
    """Fetch ``url`` and return its body, retrying a transient failure a few times.

    Retries exist because these are public endpoints with no service guarantee, not because a
    failure is expected. A refusal that repeats is raised rather than swallowed: ingestion that
    silently returns nothing would look identical to a vendor with no data.
    """
    request = urllib.request.Request(  # noqa: S310 - https URLs only, built by the fetchers
        url, headers=_headers_for(url)
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
