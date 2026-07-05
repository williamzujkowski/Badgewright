# Data sources & provenance

Every datum Badgewright stores carries a `SourceRecord`
(`steam_badge_optimizer.models.provenance`): source kind, URL/file, retrieval time,
parser version, raw-bytes SHA-256, and cache TTL. `source_url` and `fetched_at` are
NOT NULL in persistence — nothing un-attributed may be cached.

The facts below were verified during planning (2026-07-04). Steam mechanics drift;
treat the numeric constants as **config**, not gospel, and re-verify against a live
account before trusting a plan.

## Steam OpenID (identity only)

- Endpoint: `https://steamcommunity.com/openid/login` (OpenID 2.0).
- On success the user returns with `openid.claimed_id =
  https://steamcommunity.com/openid/id/<SteamID64>`; strip the trailing digits.
- The relying party never sees the password or Steam Guard — auth happens entirely
  on Steam's domain.
- **Always verify the signature** via `check_authentication` before trusting
  `claimed_id`; skipping it lets an attacker forge any SteamID.
- We keep only the validated SteamID64 and discard the response (no cookie kept).

## Market price — `priceoverview`

- `https://steamcommunity.com/market/priceoverview/?appid=&currency=&market_hash_name=`
- `currency` is a numeric id (1=USD, 2=GBP, 3=EUR, ...). See `config.CURRENCY_IDS`.
- Returns `{success, lowest_price, median_price, volume}`. `median_price`/`volume`
  may be **absent** for thin items; `*_price` are **localized strings**
  (`"$0.03"`, `"1,23€"`) — parse per currency, never `float()` naively.
- **`lowest_price` is a 1-unit ask.** Buying k copies of a thin card walks the order
  book, so real cost ≥ k × lowest. This is the biggest silent cost error — see the
  optimizer model.
- No buy-order / bid data here → a true bid/ask **spread is not computable** from
  `priceoverview` alone (needs `market/itemordershistogram` keyed by `item_nameid`).
- Rate limits are **undocumented**; the "~20 req/min" figure is community lore.
  Cache aggressively (10+ min), serialize, back off hard on HTTP 429 (429s can
  escalate to IP/temporary throttles).

## Inventory — community items

- `https://steamcommunity.com/inventory/{steamid64}/{appid}/{contextid}`.
- **Trading cards use `appid=753`, `contextid=6`** — *not* the game's own appid.
  This is the single most common wrong assumption. (`config.STEAM_COMMUNITY_APPID`
  / `config.STEAM_CARDS_CONTEXTID`.)
- Cursor pagination: `?count=&start_assetid=`, with `more_items`/`last_assetid` in
  the response (~5000/page). Response splits `assets` from `descriptions`; join on
  `classid`/`instanceid`.
- Private/friends-only inventories return **HTTP 403** — handle explicitly and
  suggest manual import; do not treat as an empty inventory.

## Card-set catalog — `steam-badges-db`

- `github.com/nolddor/steam-badges-db` — `data/badges.json` (+ `.min.json`, `.slim.json`)
  on the `main` branch,
  updated **hourly**. Shape: `{"220": {"name": "Half-Life 2", "size": 8}}` where
  `size` = number of cards in the set.
- Served via `raw.githubusercontent.com` (on the read allowlist).
- Cross-check candidates: SteamDB badges, SteamCardExchange, steamsets.com.

## Account XP / level math (the optimizer core)

- Crafting one badge level grants a **flat 100 account XP**
  (`config.XP_PER_BADGE_LEVEL`).
- A normal game badge crafts **5 times** (levels 1–5) → 500 XP max
  (`config.MAX_NORMAL_BADGE_LEVEL`). **There is no extra level-5 completion bonus** —
  it is 5 × 100.
- The **foil badge is a separate badge**, level 1 only (~100 XP); excluded by
  default (thin market).
- Account level is a **step function**: levels 1–10 cost 100 XP each, 11–20 cost 200
  each, i.e. per-level cost `= 100 × band`, band = `ceil(level / 10)`. Optimize
  cost-to-reach-**level**, and flag mid-band overshoot waste.

## Steam ToS

- Subscriber Agreement §4.C prohibits scripts/bots/macros/automation interacting
  with Steam, and automating any Marketplace process. Read/calculate/export-only
  keeps us clearly on the safe side. See [ADR-0001](adr/0001-safety-boundary.md).
