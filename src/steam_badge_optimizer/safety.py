"""Structural enforcement of Badgewright's read-only safety boundary.

Badgewright is a *local analytics and planning* tool. It reads public Steam
data, calculates, and exports human-reviewable plans. It must **never** operate
a Steam account: no purchasing, selling, crafting, trading, buy/sell orders,
listing, cancelling, or any UI automation.

This module makes that boundary *structural* rather than merely documented.
Every outbound HTTP request the app makes MUST pass through :func:`assert_safe_request`
(and in practice through :class:`SafeClient`, the only sanctioned HTTP client).

Two independent checks, defense-in-depth:

1. **Method allowlist** — only the side-effect-free GET verb is permitted (#31: the
   tool only GETs, so the allowlist states exactly what is reachable). A
   POST/PUT/PATCH/DELETE — or even HEAD/OPTIONS — to Steam is refused unconditionally.
2. **Host allowlist** — requests may only target a small set of read endpoints.
3. **Forbidden-path tripwire** — even a GET whose URL names a known state-mutating
   Steam action route (buy, sell, craft, trade, ...) is refused, so an accidental
   or malicious reintroduction of an action call fails closed.

If you are here because a legitimate new *read* source needs a new host, add it
to :data:`ALLOWED_HOSTS` and note it in ``docs/adr/0001-safety-boundary.md``.
There is deliberately no supported way to relax the method allowlist.
"""

from __future__ import annotations

from urllib.parse import urlsplit

__all__ = [
    "ALLOWED_HOSTS",
    "ALLOWED_METHODS",
    "FORBIDDEN_PATH_FRAGMENTS",
    "SafetyViolationError",
    "assert_safe_request",
    "is_host_allowed",
]


class SafetyViolationError(RuntimeError):
    """Raised when a request would cross the read-only safety boundary.

    This is a hard failure by design: the tool would rather refuse to run than
    perform an action against a Steam account.
    """


#: Only side-effect-free HTTP methods are ever permitted. This is the single most
#: important line in the project — mutating verbs against Steam are how one buys,
#: sells, crafts, trades, or lists. There is intentionally no override. Narrowed to
#: exactly GET (#31): the tool only ever GETs (SafeClient exposes no other verb), so the
#: allowlist states precisely what's reachable — least privilege, no advertised-but-dead
#: allowance. HEAD/OPTIONS are safe verbs; re-add them here explicitly if a real need ever
#: arises (e.g. a content-length probe).
ALLOWED_METHODS: frozenset[str] = frozenset({"GET"})

#: Read-only Steam (and catalog) hosts the tool is permitted to contact. Kept
#: deliberately small; adding a host is a reviewed change (see the safety ADR).
ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "steamcommunity.com",
        "api.steampowered.com",
        "store.steampowered.com",
        # steam-badges-db catalog is served from GitHub raw.
        "raw.githubusercontent.com",
    }
)

#: URL substrings that name known state-mutating Steam action routes. Even under
#: an allowed method + host, a URL containing one of these fails closed. This is a
#: tripwire, not the primary control (the method allowlist is) — it exists to make
#: an accidental reintroduction of an action endpoint loud and immediate.
FORBIDDEN_PATH_FRAGMENTS: frozenset[str] = frozenset(
    {
        "/market/buylisting",
        "/market/sellitem",
        "/market/createbuyorder",
        "/market/cancelbuyorder",
        "/market/removelisting",
        "/market/confirm",
        "buyorder",
        "sellorder",
        "sellitem",
        "buylisting",
        "createbuyorder",
        "cancelbuyorder",
        "removelisting",
        "goodwrap",  # gem/booster craft confirmation
        "ajaxcraftbadge",
        "craftbadge",
        "gcgotocard",
        "consumeitem",
        # Trade surfaces. Path-anchored with a leading slash so a card *name* containing
        # "trade" (e.g. "Trade Federation") in a query string can't false-positive a
        # legitimate price fetch — only the /tradeoffer(s)/ path matches.
        "/tradeoffer",
        "/trade/",
    }
)


def is_host_allowed(host: str) -> bool:
    """Return True if *host* (or a subdomain of an allowed host) is permitted."""
    # Normalize a trailing dot: "steamcommunity.com." is a valid absolute FQDN that
    # resolves identically, so it must match (#30). rstrip only removes terminal dots, so
    # an embedded label like "steamcommunity.com.evil.com" is unaffected and still rejected.
    host = host.lower().strip().rstrip(".")
    if not host:
        return False
    return any(host == allowed or host.endswith("." + allowed) for allowed in ALLOWED_HOSTS)


def assert_safe_request(method: str, url: str) -> None:
    """Validate an outbound request against the read-only boundary.

    Raises :class:`SafetyViolationError` if the request uses a mutating method,
    targets a non-allowlisted host, uses a non-HTTP(S) scheme, or names a known
    Steam action route. Returns ``None`` on success.
    """
    normalized_method = method.upper().strip()
    if normalized_method not in ALLOWED_METHODS:
        raise SafetyViolationError(
            f"HTTP method {method!r} is not permitted. Badgewright only makes "
            f"read-only requests ({', '.join(sorted(ALLOWED_METHODS))}). Mutating "
            "verbs are how one buys/sells/crafts/trades — refused by design."
        )

    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        raise SafetyViolationError(
            f"URL scheme {parts.scheme!r} is not permitted (url={url!r}); only http/https."
        )

    if not is_host_allowed(parts.hostname or ""):
        raise SafetyViolationError(
            f"Host {parts.hostname!r} is not on the read-only allowlist "
            f"({', '.join(sorted(ALLOWED_HOSTS))}). Add it to ALLOWED_HOSTS via a "
            "reviewed change if it is a legitimate read source (see safety ADR)."
        )

    lowered = url.lower()
    for fragment in FORBIDDEN_PATH_FRAGMENTS:
        if fragment in lowered:
            raise SafetyViolationError(
                f"URL {url!r} names a forbidden Steam action route ({fragment!r}). "
                "Badgewright never buys, sells, crafts, trades, or lists."
            )
