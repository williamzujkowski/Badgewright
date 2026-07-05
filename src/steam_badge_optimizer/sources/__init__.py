"""Data sources: the guarded HTTP client and per-source importers.

All network egress goes through :class:`~steam_badge_optimizer.sources.http_client.SafeClient`.
"""

from .http_client import FetchError, RateLimited, SafeClient, SafeResponse

__all__ = ["FetchError", "RateLimited", "SafeClient", "SafeResponse"]
