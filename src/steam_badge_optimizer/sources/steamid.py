"""Resolve a user's SteamID64 from the forms a person actually has on hand.

Accepts a raw SteamID64, a full profile URL (``/profiles/<id64>`` or ``/id/<vanity>``),
or a bare vanity name. The first two resolve offline; a vanity requires one read-only
lookup of the public profile XML (``steamcommunity.com/id/<vanity>?xml=1``) via the
guarded :class:`SafeClient` — no API key, no login.

Only a SteamID64 is ever produced; nothing here touches credentials.
"""

from __future__ import annotations

import re
from urllib.parse import quote

from .http_client import FetchError, SafeClient

__all__ = ["SteamIdError", "parse_offline", "resolve_steamid"]

# A SteamID64 for an individual account is 76561197960265728 + accountid: a 17-digit
# number beginning 7656119.
STEAMID64_BASE = 76561197960265728
_STEAMID64_RE = re.compile(r"^7656119\d{10}$")
_PROFILES_URL_RE = re.compile(r"/profiles/(7656119\d{10})\b")
_VANITY_URL_RE = re.compile(r"/id/([^/?#\s]+)")
# Steam vanity names are restricted to this charset; validating it also keeps hostile
# input (path traversal, injected route fragments) out of the request path.
_VANITY_RE = re.compile(r"^[A-Za-z0-9_-]{2,64}$")
_VANITY_XML_RE = re.compile(rb"<steamID64>(\d{17})</steamID64>")


class SteamIdError(ValueError):
    """The input could not be resolved to a valid SteamID64."""


def _validate_id64(value: int) -> int:
    if not (STEAMID64_BASE <= value <= STEAMID64_BASE + 2**32):
        raise SteamIdError(f"{value} is not a valid individual SteamID64")
    return value


def parse_offline(value: str) -> int | None:
    """Return a SteamID64 if *value* resolves without a network call, else ``None``.

    ``None`` means "looks like a vanity" — use :func:`resolve_steamid` with a client.
    """
    text = value.strip()
    match = _PROFILES_URL_RE.search(text)
    if match:
        return _validate_id64(int(match.group(1)))
    if _STEAMID64_RE.match(text):
        return _validate_id64(int(text))
    return None


def _extract_vanity(value: str) -> str:
    text = value.strip()
    match = _VANITY_URL_RE.search(text)
    vanity = match.group(1) if match else text
    if not _VANITY_RE.match(vanity):
        raise SteamIdError(f"not a valid vanity name: {vanity!r}")
    return vanity


def resolve_steamid(value: str, client: SafeClient | None = None) -> int:
    """Resolve *value* to a SteamID64, fetching the vanity profile XML if needed.

    Raises :class:`SteamIdError` for unresolvable input, or if a vanity is supplied
    without a client to look it up.
    """
    offline = parse_offline(value)
    if offline is not None:
        return offline

    vanity = _extract_vanity(value)
    if client is None:
        raise SteamIdError(f"{vanity!r} looks like a vanity name; a client is needed to resolve it")

    url = f"https://steamcommunity.com/id/{quote(vanity, safe='')}"
    try:
        resp = client.get(url, params={"xml": 1}, max_bytes=1024 * 1024)
    except FetchError as exc:
        raise SteamIdError(f"could not fetch profile for {vanity!r}: {exc}") from exc

    match = _VANITY_XML_RE.search(resp.content)
    if not match:
        raise SteamIdError(f"no SteamID64 found for vanity {vanity!r} (does the profile exist?)")
    return _validate_id64(int(match.group(1)))
