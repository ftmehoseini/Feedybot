"""Shared HTTP plumbing for provider adapters.

One client per process, reused across requests: TLS handshakes are a meaningful share of
per-turn latency when every turn makes three API calls.
"""

from __future__ import annotations

import httpx

#: Generous ceiling. The *real* deadline is the per-stage `asyncio.timeout` in the
#: pipeline; this only stops a socket hanging forever if that were ever bypassed.
_CLIENT_TIMEOUT = httpx.Timeout(60.0, connect=10.0)

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    """The process-wide async HTTP client, created on first use."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=_CLIENT_TIMEOUT)
    return _client


async def close_client() -> None:
    """Close the shared client. Called from the app's shutdown hook."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def describe_http_error(exc: Exception) -> str:
    """A log-safe description of an HTTP failure.

    Response bodies from AI providers can echo request content, so only the status line
    and a short prefix are kept — never headers (which carry the API key) and never a
    full body.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        body = exc.response.text[:200].replace("\n", " ")
        return f"HTTP {exc.response.status_code}: {body}"
    if isinstance(exc, httpx.TimeoutException):
        return "transport timeout"
    if isinstance(exc, httpx.HTTPError):
        return f"{type(exc).__name__}: {exc}"
    return f"{type(exc).__name__}: {exc}"
