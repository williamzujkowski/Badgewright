"""Ingest a user's Steam trading-card inventory (appid 753, context 6).

Card inventory lives under the Steam *community* app (753/6), not the game's own
appid. The endpoint splits ``assets`` (asset ids + amounts) from ``descriptions``
(metadata), joined on ``(classid, instanceid)``.

Design (per the approving vote):

* :func:`parse_inventory_json` is a pure, offline, unit-testable join+filter. It keeps
  only trading cards, sums duplicate copies per market hash name, and derives foil
  status from the **structural ``cardborder`` tag** (not the localized type string,
  which breaks on non-English inventories — the type is only a fallback). Individual
  malformed/unattributable entries are **skipped and counted**, never fatal; only a
  broken JSON envelope fails loudly.
* :func:`fetch_inventory` pages through the endpoint via the guarded SafeClient,
  bounded by ``max_pages``; a private inventory (HTTP 403) raises
  :class:`PrivateInventoryError` pointing the user at manual import.
* :func:`import_from_file` is the manual fallback for private profiles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import orjson

from ..models import Card, ItemKind, UserCardInventory, UserItemHolding
from ..models.provenance import SourceKind, SourceRecord
from .http_client import HTTPStatusError, SafeClient

if TYPE_CHECKING:
    from ..db import Store

__all__ = [
    "InventoryParseError",
    "InventoryResult",
    "ParsedCard",
    "PrivateInventoryError",
    "fetch_inventory",
    "import_from_file",
    "import_inventory",
    "parse_inventory_json",
]

INVENTORY_URL = "https://steamcommunity.com/inventory/{steamid}/753/6"
PARSER_VERSION = "1"
INVENTORY_TTL_SECONDS = 6 * 3600
MAX_BYTES = 64 * 1024 * 1024
MAX_ASSETS = 200_000  # resource guard against an absurd aggregate
STEAMID64_MIN = 76561197960265728

STEAM_COMMUNITY_APPID = 753
SACK_OF_GEMS_HASH = "753-Sack of Gems"
#: Loose gems' market_hash_name (verified against a real inventory, #112): they DO carry
#: one, "753-Gems", and are non-marketable with the asset amount = gem count.
LOOSE_GEMS_HASH = "753-Gems"


class InventoryParseError(ValueError):
    """The inventory JSON envelope could not be parsed (bad JSON / wrong shape)."""


class PrivateInventoryError(RuntimeError):
    """The inventory is private (HTTP 403). Suggest making it public or manual import."""

    def __init__(self, steamid64: int) -> None:
        super().__init__(
            f"inventory for {steamid64} is private or unavailable (HTTP 403). "
            "Make it public in Steam privacy settings, or export it and use "
            "`sbo inventory import --file <inventory.json>`."
        )


@dataclass(frozen=True, slots=True)
class ParsedCard:
    inventory: UserCardInventory
    card: Card


@dataclass(frozen=True, slots=True)
class InventoryResult:
    cards: list[ParsedCard]
    skipped: int
    total_assets: int
    truncated: bool = False
    """True if a paged fetch hit max_pages with more items remaining (partial result)."""
    holdings: list[UserItemHolding] = field(default_factory=list)
    """Non-card community items retained (booster packs, gems, sacks, other)."""


def _tags_by_category(desc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for tag in desc.get("tags", []) or []:
        if isinstance(tag, dict) and isinstance(tag.get("category"), str):
            out[tag["category"]] = tag
    return out


def _is_trading_card(desc: dict[str, Any], tags: dict[str, dict[str, Any]]) -> bool:
    # A card border tag is only ever present on trading cards — strongest signal.
    if "cardborder" in tags:
        return True
    item_class = tags.get("item_class")
    if item_class:
        blob = (
            str(item_class.get("internal_name", "")) + str(item_class.get("localized_tag_name", ""))
        ).lower()
        if "trading_card" in blob or "trading card" in blob:
            return True
    return "trading card" in str(desc.get("type", "")).lower()


def _is_foil(desc: dict[str, Any], tags: dict[str, dict[str, Any]]) -> bool:
    border = tags.get("cardborder")
    if border is not None:
        # cardborder_0 == normal, cardborder_1 == foil. Anything non-zero is foil.
        return str(border.get("internal_name", "")).strip() not in ("cardborder_0", "")
    return "foil" in str(desc.get("type", "")).lower()


def _classify_holding(
    desc: dict[str, Any], tags: dict[str, dict[str, Any]]
) -> tuple[ItemKind, int, str] | None:
    """Classify a NON-card 753/6 item into (kind, appid, market_hash_name), or None to skip.

    Recognizes the Sack of Gems, loose gems, booster packs (item class 5 / "Booster Pack"
    name), and otherwise retains any marketable item as OTHER (backgrounds, emoticons, ...).
    Non-marketable, unclassifiable items are skipped. Detection uses structural tags where
    possible; the loose-gems shape is best-effort (validated against a real inventory in a
    follow-up) and falls back safely to skip.
    """
    name = desc.get("market_hash_name")
    name = name if isinstance(name, str) and name else None
    item_class = tags.get("item_class") or {}
    internal = str(item_class.get("internal_name", "")).lower()
    type_str = str(desc.get("type", "")).lower()

    if name is not None and name.casefold() == SACK_OF_GEMS_HASH.casefold():
        return (ItemKind.SACK_OF_GEMS, STEAM_COMMUNITY_APPID, name)
    if name is not None and (
        name.casefold().endswith("booster pack") or internal == "item_class_5"
    ):
        appid = _game_appid(desc, name)
        return (ItemKind.BOOSTER_PACK, appid, name) if appid is not None else None
    # Loose gems (verified #112): type "Steam Gems" / item_class_7, non-marketable, and
    # they DO carry market_hash_name "753-Gems" — so match on type/class, not name absence
    # (the Sack is already returned above, so it can't be misrouted here).
    if "steam gems" in type_str or internal == "item_class_7":
        return (ItemKind.GEMS, STEAM_COMMUNITY_APPID, LOOSE_GEMS_HASH)
    if name is not None and bool(desc.get("marketable", 0)):
        appid = _game_appid(desc, name)
        return (ItemKind.OTHER, appid, name) if appid is not None else None
    return None


def _game_appid(desc: dict[str, Any], market_hash_name: str) -> int | None:
    # The card's game appid (e.g. 440), not the community appid 753.
    fee_app = desc.get("market_fee_app")
    if isinstance(fee_app, int):
        return fee_app
    if isinstance(fee_app, str) and fee_app.isdigit():
        return int(fee_app)
    prefix = market_hash_name.split("-", 1)[0]
    return int(prefix) if prefix.isdigit() else None


def parse_inventory_json(raw: bytes) -> InventoryResult:
    """Parse inventory JSON into deduplicated per-card copies. Fails loudly only on a
    broken envelope; skips (and counts) individual malformed/unattributable cards."""
    try:
        data = orjson.loads(raw)
    except orjson.JSONDecodeError as exc:
        raise InventoryParseError(f"invalid inventory JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise InventoryParseError(f"expected a JSON object, got {type(data).__name__}")

    assets = data.get("assets") or []
    descriptions = data.get("descriptions") or []
    if not isinstance(assets, list) or not isinstance(descriptions, list):
        raise InventoryParseError("assets/descriptions must be lists")
    if len(assets) > MAX_ASSETS:
        raise InventoryParseError(f"too many assets ({len(assets)} > {MAX_ASSETS})")

    index: dict[tuple[str, str], dict[str, Any]] = {}
    for desc in descriptions:
        if isinstance(desc, dict):
            key = (str(desc.get("classid")), str(desc.get("instanceid")))
            index[key] = desc

    # Aggregate copies by (appid, market_hash_name): cards in `agg`, non-card holdings
    # (booster packs, gems, sacks, other) in `holdings_agg`.
    agg: dict[tuple[int, str], list[Any]] = {}
    holdings_agg: dict[tuple[int, str], list[Any]] = {}  # (appid, mhn) -> [quantity, kind]
    skipped = 0
    for asset in assets:
        if not isinstance(asset, dict):
            skipped += 1
            continue
        desc = index.get((str(asset.get("classid")), str(asset.get("instanceid"))))
        if desc is None:
            skipped += 1
            continue
        try:
            amount = max(0, int(asset.get("amount", "1")))
        except (TypeError, ValueError):
            amount = 1
        tags = _tags_by_category(desc)
        if not _is_trading_card(desc, tags):
            # A non-card community item: retain it as a typed holding if we can classify
            # it (booster pack, gems, sack, or a marketable "other"); otherwise ignore it
            # (backgrounds/emoticons that aren't marketable, etc.) — not an error.
            classified = _classify_holding(desc, tags)
            if classified is not None:
                kind, h_appid, h_name = classified
                h_entry = holdings_agg.setdefault((h_appid, h_name), [0, kind])
                h_entry[0] += amount
            continue
        mhn = desc.get("market_hash_name")
        if not isinstance(mhn, str) or not mhn:
            skipped += 1
            continue
        appid = _game_appid(desc, mhn)
        if appid is None:
            skipped += 1
            continue
        entry = agg.setdefault((appid, mhn), [0, desc, tags])
        entry[0] += amount

    cards: list[ParsedCard] = []
    for (appid, mhn), (quantity, desc, tags) in agg.items():
        is_foil = _is_foil(desc, tags)
        inv = UserCardInventory(
            appid=appid, market_hash_name=mhn, quantity=quantity, is_foil=is_foil
        )
        card = Card(
            appid=appid,
            market_hash_name=mhn,
            card_name=(str(desc.get("market_name")) or None) if desc.get("market_name") else None,
            is_foil=is_foil,
            marketable=bool(desc.get("marketable", 1)),
            tradable=bool(desc.get("tradable", 1)),
        )
        cards.append(ParsedCard(inventory=inv, card=card))
    holdings = [
        UserItemHolding(appid=appid, market_hash_name=mhn, kind=kind, quantity=quantity)
        for (appid, mhn), (quantity, kind) in holdings_agg.items()
    ]
    return InventoryResult(
        cards=cards,
        skipped=skipped,
        total_assets=len(assets),
        truncated=bool(data.get("_truncated", False)),
        holdings=holdings,
    )


def _validate_steamid64(steamid64: int) -> int:
    if not (STEAMID64_MIN <= steamid64 <= STEAMID64_MIN + 2**32):
        raise ValueError(f"{steamid64} is not a valid individual SteamID64")
    return steamid64


def fetch_inventory(
    client: SafeClient,
    steamid64: int,
    *,
    max_pages: int = 5,
    page_count: int = 2000,
    language: str = "english",
) -> bytes:
    """Fetch and aggregate all inventory pages into one JSON document (bytes).

    Raises :class:`PrivateInventoryError` on HTTP 403. ``max_pages`` bounds politeness.
    """
    _validate_steamid64(steamid64)
    url = INVENTORY_URL.format(steamid=steamid64)
    all_assets: list[Any] = []
    all_descriptions: list[Any] = []
    start_assetid: str | None = None
    truncated = False
    try:
        for _ in range(max_pages):
            params: dict[str, Any] = {"l": language, "count": page_count}
            if start_assetid is not None:
                params["start_assetid"] = start_assetid
            resp = client.get(url, params=params, max_bytes=MAX_BYTES)
            page = resp.json()
            if not isinstance(page, dict):
                raise InventoryParseError("inventory page was not a JSON object")
            all_assets.extend(page.get("assets") or [])
            all_descriptions.extend(page.get("descriptions") or [])
            if not page.get("more_items"):
                break
            next_cursor = page.get("last_assetid")
            if not next_cursor:
                break  # more_items claimed but no cursor — stop rather than loop on "None"
            start_assetid = str(next_cursor)
        else:
            # Loop exhausted max_pages while more items remained: the result is partial.
            truncated = True
    except HTTPStatusError as exc:
        if exc.status_code == 403:
            raise PrivateInventoryError(steamid64) from exc
        raise
    return orjson.dumps(
        {"assets": all_assets, "descriptions": all_descriptions, "_truncated": truncated}
    )


def _persist(store: Store, result: InventoryResult, source: SourceRecord) -> None:
    for parsed in result.cards:
        store.upsert_card(parsed.card)
        store.upsert_inventory(parsed.inventory)
    for holding in result.holdings:
        store.upsert_item_holding(holding)
    store.record_source(source)


def import_from_file(store: Store, path: str | Path) -> InventoryResult:
    """Import inventory from a saved JSON file (the manual fallback)."""
    file_path = Path(path)
    if not file_path.is_file():
        raise InventoryParseError(f"not a file: {file_path}")
    if file_path.stat().st_size > MAX_BYTES:
        raise InventoryParseError(f"file exceeds size cap ({file_path.stat().st_size} bytes)")
    raw = file_path.read_bytes()
    result = parse_inventory_json(raw)
    source = SourceRecord(
        kind=SourceKind.STEAM_INVENTORY,
        file_name=file_path.name,
        fetched_at=datetime.now(UTC),
        parser_version=PARSER_VERSION,
        raw_sha256=SourceRecord.sha256_of(raw),
        cache_ttl_seconds=INVENTORY_TTL_SECONDS,
    )
    _persist(store, result, source)
    return result


def import_inventory(
    store: Store, client: SafeClient, steamid64: int, **kwargs: Any
) -> InventoryResult:
    """Fetch a public inventory via SafeClient and persist it."""
    raw = fetch_inventory(client, steamid64, **kwargs)
    result = parse_inventory_json(raw)
    source = SourceRecord(
        kind=SourceKind.STEAM_INVENTORY,
        url=INVENTORY_URL.format(steamid=steamid64),
        fetched_at=datetime.now(UTC),
        parser_version=PARSER_VERSION,
        raw_sha256=SourceRecord.sha256_of(raw),
        cache_ttl_seconds=INVENTORY_TTL_SECONDS,
    )
    _persist(store, result, source)
    return result
