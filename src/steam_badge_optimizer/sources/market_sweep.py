"""Bounded, opt-in, cheapest-first market price sweep (Epic #71 / #73).

Pages Steam's Community Market search endpoint over the whole trading-card catalog
(appid 753), **sorted cheapest-first**, capturing each card's lowest ask + ask-side depth
+ foil status, and persisting them so the cheapest-badges ranking can work catalog-wide
instead of only for games the user pulled in by hand.

This is the one place in Badgewright that fetches at scale, so it is fenced hard — every
constraint below is an *enforced, tested* invariant, not a comment:

* **Bounded.** ``max_pages`` is a hard cap (small default). It can NEVER walk the whole
  ~184k-card market by accident. Optional early-exit once enough cheap sets are complete.
* **Cheapest-first.** ``sort_column=price&sort_dir=asc`` — cheap badges are made of cheap
  cards, so the useful data is at the front; you rarely need many pages.
* **Rate-polite.** Fetches go through SafeClient's min-interval; the loop adds jitter.
* **Hard stop on rate-limit.** On 429 / Cloudflare / any HTTP error it STOPS (progress is
  already persisted) and reports — it never retries past a block.
* **Resumable.** A cursor file records the next ``start`` index, so an interrupted run
  resumes rather than restarting. Cursor/cache paths derive only from the data dir (no
  user-controlled path components).

Read-only: it reads public listing metadata and buys/crafts/lists nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

import orjson

from ..models import Card, MarketItem, Money, PriceSnapshot
from ..models.provenance import SourceKind, SourceRecord
from .card_discovery import SEARCH_URL, _non_negative_int, parse_search_results
from .http_client import FetchError, RateLimited, SafeClient

if TYPE_CHECKING:
    from ..db import Store

__all__ = ["DEFAULT_MAX_PAGES", "PAGE_SIZE", "StopReason", "SweepResult", "sweep_cheapest"]

PAGE_SIZE = 100
DEFAULT_MAX_PAGES = 20  # hard cap: 20 pages x 100 = 2000 cheapest listings, not ~184k
PARSER_VERSION = "1"
TTL_SECONDS = 12 * 3600
MAX_PAGE_BYTES = 4 * 1024 * 1024


class StopReason(StrEnum):
    END_OF_MARKET = "end_of_market"  # paged past total_count — sweep is complete
    MAX_PAGES = "max_pages"  # hit the page cap (more remains; resume to continue)
    EARLY_EXIT = "early_exit"  # found enough complete cheap sets
    RATE_LIMITED = "rate_limited"  # 429/Cloudflare/HTTP error — stopped, resume later


@dataclass(frozen=True, slots=True)
class SweepResult:
    pages_fetched: int
    cards_priced: int
    resumed_from: int
    next_cursor: int | None  # None means the sweep reached the end of the market
    stop_reason: StopReason
    complete_sets: int


def _cursor_path(data_dir: Path) -> Path:
    # Fixed filename under the (already-trusted) data dir — no user-controlled components.
    return data_dir / "sweep_cursor.json"


def _load_cursor(data_dir: Path) -> int:
    path = _cursor_path(data_dir)
    if not path.is_file():
        return 0
    try:
        data = orjson.loads(path.read_bytes())
    except (orjson.JSONDecodeError, OSError):
        return 0
    start = data.get("start", 0) if isinstance(data, dict) else 0
    # `type(start) is int` rejects bool (True/False are ints) and any non-int garbage.
    return start if type(start) is int and start >= 0 else 0


def _save_cursor(data_dir: Path, start: int, total_count: int) -> None:
    _cursor_path(data_dir).write_bytes(orjson.dumps({"start": start, "total_count": total_count}))


def _clear_cursor(data_dir: Path) -> None:
    _cursor_path(data_dir).unlink(missing_ok=True)


def _game_appid(market_hash_name: str) -> int | None:
    # Card market hash names are "<gameappid>-<CardName>"; the prefix is the game.
    prefix = market_hash_name.split("-", 1)[0]
    return int(prefix) if prefix.isdigit() else None


def _total_count(raw: bytes) -> int:
    try:
        data = orjson.loads(raw)
    except orjson.JSONDecodeError:
        return 0
    total = data.get("total_count") if isinstance(data, dict) else None
    return int(total) if isinstance(total, int) and total >= 0 else 0


def _raw_result_count(raw: bytes) -> int:
    """How many listings the server actually returned on this page (its real page size)."""
    try:
        data = orjson.loads(raw)
    except orjson.JSONDecodeError:
        return 0
    results = data.get("results") if isinstance(data, dict) else None
    return len(results) if isinstance(results, list) else 0


def sweep_cheapest(
    store: Store,
    client: SafeClient,
    data_dir: Path,
    *,
    currency: str = "USD",
    max_pages: int = DEFAULT_MAX_PAGES,
    page_size: int = PAGE_SIZE,
    stop_after_complete_sets: int | None = None,
    jitter_s: float = 0.0,
    clock: Callable[[], datetime] | None = None,
    sleep: Callable[[float], None] | None = None,
    rng: Callable[[], float] | None = None,
) -> SweepResult:
    """Page the cheapest-first card market, persisting prices; bounded and resumable."""
    if max_pages < 1:
        raise ValueError("max_pages must be >= 1")
    now = clock or (lambda: datetime.now(UTC))
    do_sleep = sleep or __import__("time").sleep
    jitter = rng or __import__("random").random

    data_dir.mkdir(parents=True, exist_ok=True)
    start = resumed_from = _load_cursor(data_dir)
    pages = priced = 0
    total_count = 0
    reason = StopReason.MAX_PAGES
    next_cursor: int | None = start

    for _ in range(max_pages):
        params = {
            "norender": 1,
            "l": "english",
            "appid": 753,
            "count": page_size,
            "start": start,
            "category_753_item_class[]": "tag_item_class_2",
            "sort_column": "price",
            "sort_dir": "asc",
        }
        try:
            resp = client.get(SEARCH_URL, params=params, max_bytes=MAX_PAGE_BYTES)
        except (RateLimited, FetchError):
            # Hard stop: progress is persisted in the cursor; surface it, never retry past.
            reason = StopReason.RATE_LIMITED
            next_cursor = start
            break

        raw = resp.content
        total_count = _total_count(raw) or total_count
        fetched_at = now()
        source = SourceRecord(
            kind=SourceKind.STEAM_MARKET_SEARCH,
            url=SEARCH_URL,
            fetched_at=fetched_at,
            parser_version=PARSER_VERSION,
            raw_sha256=SourceRecord.sha256_of(raw),
            cache_ttl_seconds=TTL_SECONDS,
            http_status=resp.status_code,
        )
        page_cards = parse_search_results(raw)
        for card in page_cards:
            appid = _game_appid(card.market_hash_name)
            price = _non_negative_int(card.sell_price_cents)
            if appid is None or price is None:
                continue  # unattributable or unpriced listing — skip, don't guess
            store.upsert_card(
                Card(appid=appid, market_hash_name=card.market_hash_name, is_foil=card.is_foil)
            )
            store.add_price_snapshot(
                PriceSnapshot(
                    item=MarketItem(appid=appid, market_hash_name=card.market_hash_name),
                    lowest=Money(price, currency),
                    listings=card.listings,
                    source=source,
                )
            )
            priced += 1

        pages += 1
        # Advance by what the server ACTUALLY returned, not what we asked for: search/render
        # caps a page at ~10 results regardless of `count`, so advancing by page_size would
        # skip most of the market. This keeps pagination contiguous at any real page size.
        raw_count = _raw_result_count(raw)
        start += raw_count if raw_count > 0 else page_size
        _save_cursor(data_dir, start, total_count)

        if raw_count == 0 or (total_count and start >= total_count):
            reason = StopReason.END_OF_MARKET
            next_cursor = None
            _clear_cursor(data_dir)  # sweep finished — start fresh next time
            break

        if stop_after_complete_sets is not None:
            complete = _count_complete_sets(store, currency)
            if complete >= stop_after_complete_sets:
                reason = StopReason.EARLY_EXIT
                next_cursor = start
                break

        if jitter_s > 0:
            do_sleep(jitter() * jitter_s)
    else:
        next_cursor = start  # loop ran the full max_pages without an END/EARLY break

    return SweepResult(
        pages_fetched=pages,
        cards_priced=priced,
        resumed_from=resumed_from,
        next_cursor=next_cursor,
        stop_reason=reason,
        complete_sets=_count_complete_sets(store, currency),
    )


def _count_complete_sets(store: Store, currency: str) -> int:
    """How many badge sets are now fully known + priced in the requested currency."""
    complete = 0
    for badge_set in store.list_badge_sets():
        cards = store.cards_for_app(badge_set.appid, include_foil=False)
        if len(cards) != badge_set.set_size or badge_set.set_size == 0:
            continue
        if all(
            (snap := store.latest_price(badge_set.appid, c.market_hash_name)) is not None
            and (snap.lowest or snap.median) is not None
            and (snap.lowest or snap.median).currency == currency  # type: ignore[union-attr]
            for c in cards
        ):
            complete += 1
    return complete
