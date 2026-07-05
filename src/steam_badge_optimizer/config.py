"""Runtime configuration and well-known Steam constants.

Values are resolved with this precedence: explicit constructor arg > environment
variable (``SBO_*``) > sensible local-first default. Nothing here contacts the
network or stores secrets.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "steam-badge-optimizer"

# --- Well-known Steam constants (see docs/data-sources.md) -------------------

#: Steam community-items appid. Trading cards, backgrounds, emoticons, and badges
#: all live under this appid regardless of which game they belong to.
STEAM_COMMUNITY_APPID = 753

#: Context id for trading cards / community items within appid 753. A frequent
#: wrong assumption is to use the game's own appid+context; card inventory is here.
STEAM_CARDS_CONTEXTID = 6

#: Flat account XP granted per badge level crafted (levels 1-5, and the separate
#: foil badge). Used by the optimizer's cost-per-XP ranking.
XP_PER_BADGE_LEVEL = 100

#: Max craftable levels for a normal (non-foil) game badge.
MAX_NORMAL_BADGE_LEVEL = 5

#: priceoverview currency ids (subset; extend as needed).
CURRENCY_IDS: dict[str, int] = {"USD": 1, "GBP": 2, "EUR": 3, "CHF": 4, "RUB": 5}

#: Honest, contactable User-Agent. We do not impersonate a browser.
USER_AGENT = "Badgewright/0.0.1 (+https://github.com/grenlan/Badgewright; local read-only planner)"


@dataclass(slots=True)
class Settings:
    """Resolved runtime settings for a single invocation."""

    data_dir: Path
    db_path: Path
    currency: str = "USD"
    offline: bool = True
    """Default to offline/cached behavior. Network access is opt-in per command."""
    request_timeout_s: float = 20.0
    min_request_interval_s: float = 3.0
    """Conservative per-host spacing; the politeness layer adds jitter on top."""

    @classmethod
    def resolve(
        cls,
        *,
        data_dir: str | os.PathLike[str] | None = None,
        currency: str | None = None,
        offline: bool | None = None,
    ) -> Settings:
        resolved_dir = Path(
            data_dir or os.environ.get("SBO_DATA_DIR") or user_data_dir(APP_NAME, appauthor=False)
        ).expanduser()
        resolved_currency = (currency or os.environ.get("SBO_CURRENCY") or "USD").upper()
        if resolved_currency not in CURRENCY_IDS:
            raise ValueError(
                f"Unsupported currency {resolved_currency!r}; known: {sorted(CURRENCY_IDS)}"
            )
        env_offline = os.environ.get("SBO_OFFLINE")
        resolved_offline = (
            offline
            if offline is not None
            else (env_offline.lower() in {"1", "true", "yes"} if env_offline else True)
        )
        return cls(
            data_dir=resolved_dir,
            db_path=resolved_dir / "badgewright.sqlite3",
            currency=resolved_currency,
            offline=resolved_offline,
        )

    def currency_id(self) -> int:
        return CURRENCY_IDS[self.currency]
