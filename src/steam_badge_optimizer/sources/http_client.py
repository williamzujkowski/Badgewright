"""The single sanctioned HTTP egress point: a guarded, read-only client.

Every outbound request in Badgewright goes through :class:`SafeClient`. It:

* validates the method + URL with :func:`safety.assert_safe_request` **before** any
  socket is opened (fail-closed: a disallowed host/method never leaves the process);
* only exposes read verbs — there is no ``post``/``put``/etc. method to call;
* keeps **no cookie jar** across requests (a response ``Set-Cookie`` is discarded), so
  no session token can accumulate or be replayed toward a market action;
* sends an honest, contactable User-Agent — it does not impersonate a browser;
* **surfaces** HTTP 429 as :class:`RateLimited` rather than hammering past it, and
  retries only transient transport errors with bounded backoff;
* enforces a conservative per-call minimum interval (politeness).

This is a thin wrapper over ``httpx`` (reuse, not reinvention).
"""

from __future__ import annotations

import re
import time
from types import TracebackType
from typing import Any

import httpx
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)
from tenacity.wait import wait_base

from ..config import USER_AGENT
from ..safety import assert_safe_request

__all__ = [
    "FetchError",
    "HTTPStatusError",
    "RateLimited",
    "SafeClient",
    "SafeResponse",
    "redact_url",
]

# Query-param names whose values are secrets (e.g. a user's Steam Web API key). We never
# let these appear in an error message, log line, or exception — a 403/timeout must not
# leak the key into a traceback.
_SENSITIVE_PARAM_RE = re.compile(r"(?i)([?&](?:key|token|access_token|secret|password)=)[^&#]*")


def redact_url(url: object) -> str:
    """Return the URL as a string with sensitive query-param values masked."""
    return _SENSITIVE_PARAM_RE.sub(r"\1REDACTED", str(url))


class FetchError(RuntimeError):
    """A read request failed in a way callers should handle (not a safety violation)."""


class HTTPStatusError(FetchError):
    """A non-OK HTTP status (>=400, excluding 429). Carries the status code so callers
    can react to specific cases (e.g. 403 for a private Steam inventory)."""

    def __init__(self, status_code: int, url: str) -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code} fetching {url}")


class RateLimited(FetchError):
    """The server returned HTTP 429. We surface it instead of retrying past it."""

    def __init__(self, url: str, retry_after: float | None) -> None:
        self.retry_after = retry_after
        super().__init__(
            f"rate limited (429) fetching {url}"
            + (f"; server asked to wait {retry_after}s" if retry_after else "")
        )


class SafeResponse:
    """The read-only surface of a response we expose to callers."""

    __slots__ = ("content", "status_code", "url")

    def __init__(self, status_code: int, content: bytes, url: str) -> None:
        self.status_code = status_code
        self.content = content
        self.url = url

    def json(self) -> Any:
        import orjson

        return orjson.loads(self.content)


class SafeClient:
    """A read-only HTTP client. Use as a context manager."""

    def __init__(
        self,
        *,
        timeout_s: float = 20.0,
        min_interval_s: float = 0.0,
        user_agent: str = USER_AGENT,
        max_attempts: int = 3,
        retry_wait: wait_base | None = None,
    ) -> None:
        self._min_interval_s = max(0.0, min_interval_s)
        self._last_request_at = 0.0
        self._max_attempts = max(1, max_attempts)
        self._retry_wait = (
            retry_wait if retry_wait is not None else wait_exponential_jitter(initial=0.5, max=8.0)
        )
        # No cookie persistence; honest UA. Redirects are REFUSED, not followed: a 3xx
        # target would escape the guard that already validated the original URL, so we
        # never chase one (a redirect is returned as-is and treated as a failed fetch).
        self._client = httpx.Client(
            timeout=timeout_s,
            headers={"User-Agent": user_agent},
            follow_redirects=False,
        )

    def __enter__(self) -> SafeClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        max_bytes: int | None = None,
    ) -> SafeResponse:
        """Perform a guarded GET. Raises SafetyViolationError before any network I/O
        for a disallowed method/host/route; RateLimited on 429; FetchError otherwise.

        The exact ``httpx.URL`` that is validated is the one fetched (validate==fetch).
        ``max_bytes`` caps the streamed body so an oversized response cannot OOM the
        process (the body is enforced during download, not after)."""
        request_url = httpx.URL(url, params=params)
        assert_safe_request("GET", str(request_url))

        retryer: Retrying = Retrying(
            stop=stop_after_attempt(self._max_attempts),
            wait=self._retry_wait,
            retry=retry_if_exception_type(httpx.TransportError),
            reraise=True,
        )
        try:
            return retryer(self._do_get, request_url, max_bytes)
        except httpx.TransportError as exc:  # exhausted retries
            raise FetchError(f"transport error fetching {redact_url(url)}: {exc}") from exc

    def _do_get(self, request_url: httpx.URL, max_bytes: int | None) -> SafeResponse:
        self._respect_min_interval()
        try:
            with self._client.stream("GET", request_url) as resp:
                safe_url = redact_url(request_url)
                if resp.status_code == 429:
                    retry_after = resp.headers.get("Retry-After")
                    raise RateLimited(safe_url, float(retry_after) if retry_after else None)
                # Redirects are refused, so a 3xx is an error, not an empty success.
                if 300 <= resp.status_code < 400:
                    raise FetchError(
                        f"HTTP {resp.status_code} fetching {safe_url} (redirects are not followed)"
                    )
                if resp.status_code >= 400:
                    raise HTTPStatusError(resp.status_code, safe_url)
                content = self._read_capped(resp, max_bytes, request_url)
                status, final_url = resp.status_code, str(resp.url)
        finally:
            # Never retain cookies between requests — defense in depth.
            self._client.cookies.clear()
        return SafeResponse(status, content, final_url)

    @staticmethod
    def _read_capped(resp: httpx.Response, max_bytes: int | None, url: httpx.URL) -> bytes:
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_bytes():
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise FetchError(f"response from {redact_url(url)} exceeded {max_bytes}-byte cap")
            chunks.append(chunk)
        return b"".join(chunks)

    def _respect_min_interval(self) -> None:
        if self._min_interval_s <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self._min_interval_s:
            time.sleep(self._min_interval_s - elapsed)
        self._last_request_at = time.monotonic()
