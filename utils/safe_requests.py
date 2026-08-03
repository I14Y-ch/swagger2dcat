"""
SSRF-safe wrapper around requests.get.

Strategy (defense in depth):
- Validate the target URL resolves to a *public* IP address (blocks localhost,
  RFC1918, link-local, multicast, IPv6 ULA, etc.). Uses is_safe_public_url()
  from async_http, which performs DNS resolution before returning.
- Refuse redirects by default (allow_redirects=False). Following a redirect
  would re-open the SSRF window on the second hop, since the redirect target
  is not validated. If a 3xx is returned, raise ValueError so callers can
  surface a clear error to the user asking for the direct target URL.
- Enforce a default timeout of 10 seconds.

Callers that need retry logic can pass their own requests.Session via the
`session` argument; validation is performed the same way.
"""
from __future__ import annotations

import requests

from .async_http import is_safe_public_url


def check_safe_url(url: str) -> None:
    """Raise ValueError if the URL is not safe for outbound requests."""
    ok, reason = is_safe_public_url(url)
    if not ok:
        raise ValueError(f"Refused unsafe URL: {reason}")


def safe_get(url: str, session=None, **kwargs):
    """
    Perform a GET against `url` after validating it is a public URL.

    Args:
        url: Target URL. Must resolve to a public IP address.
        session: Optional requests.Session to reuse (e.g., for retries).
        **kwargs: Extra arguments forwarded to requests.get / session.get.
                  Defaults: timeout=10, allow_redirects=False.

    Raises:
        ValueError: If the URL is unsafe or if the response is a redirect.
    """
    check_safe_url(url)
    kwargs.setdefault("timeout", 10)
    kwargs.setdefault("allow_redirects", False)

    getter = session.get if session is not None else requests.get
    resp = getter(url, **kwargs)

    if 300 <= resp.status_code < 400:
        raise ValueError(
            f"URL returned redirect ({resp.status_code}) which is not supported for "
            "security reasons. Please provide the direct target URL."
        )
    return resp
