# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/) and [SemVer](https://semver.org/)
(pre-1.0: minor = new capability, patch = fixes).

## [Unreleased]

## [1.4.2] - 2026-07-08

### Fixed

- **card-gem-arbitrage produced nothing against live Steam** (#133): the goo/gem-value
  source required the listing `/render/` endpoint to return JSON, but Steam migrated market
  listing pages to server-rendered HTML (the change behind #86), so every goo fetch failed
  and the whole feature was silently non-functional. The `GetGooValue(...)` call is still in
  the HTML, so it's now matched in the response text (robust to both HTML and the old JSON).
  Found and fixed via live end-to-end validation.

## [1.4.1] - 2026-07-07

### Fixed

- **plan-cheapest candidate selection was fooled by lone cheap cards** (#81): a game with
  one $0.03 card estimated at $0.15 actually cost $7.26 to finish (48×). Selection now
  ranks games with more of their set priced (stronger evidence) ahead of single-card
  gambles, via a non-destructive two-tier gate — so an all-singleton field is unchanged and
  the fix only bites when better-evidenced candidates exist. `--budget` is now spent on
  games likelier to actually be cheap.

## [1.4.0] - 2026-07-07

### Added

- **`sbo optimize --auto-fetch [--max-games N]`** (#69): one command now closes the
  "cheapest way to level *my* account" loop. It discovers + prices the games you're
  actually involved with — you own ≥1 card, or have partial (1–4) badge progress — then
  re-plans, instead of reporting "N badges need discovery/pricing" and leaving you to run
  `cards discover` + `prices refresh` per game. Bounded to your relevant games (not the
  ~15k-game catalog), most-owned-first, capped by `--max-games`, rate-polite,
  429-hard-stop, skip-on-error. Opt-in — offline stays the default.

## [1.3.2] - 2026-07-07

Hardening & maintenance. No new features.

### Changed

- **Read-only boundary narrowed to `GET` only** (#31): `ALLOWED_METHODS` was
  `{GET, HEAD, OPTIONS}` but `SafeClient` only ever GETs, so HEAD/OPTIONS were an
  advertised-but-unreachable allowance. The allowlist, code, tests, ADR, and `sbo safety`
  output now all say exactly `GET` (least privilege).
- The version now lives in exactly one place — `pyproject.toml`. `__version__` and the
  `User-Agent` are derived from the installed package metadata (#40), so a release is a
  one-line bump and the User-Agent can no longer drift from the real version. CI now runs
  on Python 3.12/3.13/**3.14** and the Docker image ships on 3.14 (#123).

### Fixed

- `is_host_allowed` no longer over-blocks a valid trailing-dot FQDN like `steamcommunity.com.`
  (#30); the normalization only strips terminal dots, so suffix spoofs stay rejected.
- Model string fields (`market_hash_name`, app name) now reject whitespace-only input via a
  shared `NonBlankStr` type — `min_length=1` alone accepted a lone space (#27).

## [1.3.1] - 2026-07-07

### Fixed

- **Loose gems were silently dropped from inventory** (#112): validated against a real
  inventory, loose gems carry `market_hash_name="753-Gems"` and are non-marketable, but the
  classifier gated the gems branch on the name being absent — so real gem stashes were never
  retained or valued. Now matched on `type="Steam Gems"` / `item_class_7`. This makes the gem
  inventory valuation shipped in 1.2.0 actually work for held gems.

## [1.3.0] - 2026-07-07

Card→gem arbitrage: flag foil cards that are cheaper to buy than the gems they yield.
Strictly read-only and human-executed.

### Added

- **`sbo market card-gem-arbitrage`** (#118): for a card with a cached goo (gem) value, its
  market lowest ask, and a cached Sack-of-Gems price, compares the card's cost to the gems
  it would yield and flags the ones that are cheaper. Foil-first (normals yield sub-cent
  gems); flags on the **net** realizable gem value (after the ~15% sale fee, matching
  booster-arbitrage) so it never labels a fee-losing trade a profit; confidence capped at
  LOW. Offline scans cached data; `--online --confirm` refreshes the Sack price + fetches
  goo values for foil cards that have a cached price (bounded, rate-polite, 429-hard-stop).
- **Read-only goo-value source + cache** (#116): a card's "goo" value is read via two
  anonymous GETs — scrape `item_type` from the card's public market-listing render JSON,
  then the `ajaxgetgoovalueforitemtype` endpoint (both pass the safety boundary; no auth).
  Cached in a new `card_goo_value` table (migration v4); re-runs skip cached cards unless
  forced. Card names are fully path-encoded and a name tripping the safety boundary skips
  the card rather than crashing.

## [1.2.0] - 2026-07-07

Value your whole inventory — cards **and** non-card goods — and make the booster-arbitrage
signal actually usable. Still strictly read-only and human-executed.

### Added

- **Non-card inventory holdings** (#97): `sbo inventory import` now retains booster packs,
  the Sack of Gems, loose gems, and other marketable community items (a new
  `user_item_holding` table + `ItemKind`), instead of dropping every non-card.
- **`sbo inventory value` now values gems and goods** (#97): gems are marked to the
  Sack-of-Gems price via the gem layer; booster packs / sacks / other items at their own
  cached lowest ask (flagged unpriced, never zeroed). `sbo report inventory-value` gains a
  `kind` column. Offline, descriptive-only.

### Fixed

- **booster-arbitrage `ARB` flag was unreachable** (#108): resale-demand liquidity needs
  24h volume, but a sweep only yields asks, so the "confirmed-liquid" flag never fired.
  `sbo market booster-arbitrage` now enriches each candidate's cards via priceoverview
  (bounded, rate-polite, 429-hard-stop) so the flag is reachable; a misleading test that
  injected impossible volume is fixed.
- **booster-arbitrage set completeness** (#109): a game with fewer discovered cards than
  its catalog set size is now skipped (its EV would be biased low), matching the sibling
  modules.
- **booster-arbitrage skew check** (#110) is now integer math (no float in the
  Decimal-strict module); documented why its liquidity bar is stricter than cheapest-badges.

### Changed

- `Store.latest_price` gains a currency filter (already shipped in 1.1.0's changed section;
  now also used by the non-card valuation path so a stray other-currency fetch can't mask a
  usable price).

## [1.1.0] - 2026-07-06

Arbitrage & inventory valuation (epic #94): value Steam gems in real money, value your own
held cards against the live market, and flag Booster Packs that look cheaper than their card
contents — all strictly read-only and human-executed (Badgewright never buys, sells, crafts,
or trades). Docs now also name Augmented Steam / Steam Card Exchange as alternatives.

### Added

- **`sbo market gems [--set-size N] [--online --confirm]`** (#95): value gems in real money
  via the Sack of Gems (a normal appid-753 market item, priced through the existing guarded
  layer — no new egress, no schema change) and show the gem cost to craft a booster
  (`~6000 / set_size`). Gross (buy) and net-of-fee (sell) per-gem figures; reads the cached
  price, `--online --confirm` refreshes it. Currency-aware so a stray other-currency fetch
  can't mask a usable price.
- **`sbo inventory value [--top N]`** (#97): value your held cards at the current market
  floor with a portfolio total (a floor over the priced subset; unpriced holdings flagged,
  never zeroed). Offline; seed prices with `sbo prices refresh --online`.
- **`sbo market booster-arbitrage --online --confirm`** (#98): for the cheapest fully-priced
  games, fetch the Booster Pack price (one guarded search on item-class 5) and compare it to
  the expected resale value of its 3 cards. Bounded (`--max-games`), rate-polite,
  429-hard-stop. Honest by construction: the estimate is framed as an optimistic ceiling on a
  high-variance draw (confidence capped at LOW), resale liquidity uses 24h demand not
  ask-depth, and a skew signal fires when one card dominates the set. Research, not advice.
- **`sbo report inventory-value --out <path.csv|.html>`** (#99): export the inventory
  valuation as formula-injection-safe CSV or static inert HTML (every Steam-sourced field
  escaped, validated inert before write), with the priced-floor total and an injectable
  timestamp.

### Changed

- `Store.latest_price` gained an optional `currency=` filter (newest snapshot whose lowest
  ask is in that currency), reused by the gem layer and inventory valuation so a stray
  other-currency fetch never masks a usable price.

## [1.0.0] - 2026-07-05

First stable release. Badgewright is feature-complete: find the cheapest Steam badges to
make — across your own games or the whole market — with a bounded, opt-in, rate-polite
pipeline that reads only public data and never touches your account. Documented end-to-end
with a verified command reference, hardened (read-only boundary enforced in code, no stored
credentials, non-root Docker), and covered by 373 tests.

## [0.11.0] - 2026-07-05

Shareable output: export the cheapest-badges ranking as a spreadsheet-ready CSV or a
static, inert HTML page for human review.

### Added

- **`sbo report cheapest-badges --out <path.csv|.html>`** (#70): export the cheapest-badges
  ranking (rank, game, appid, cards, total cost, cost-per-XP, confidence, buyable, bottleneck
  %, notes, as-of timestamp) as a spreadsheet-ready CSV or a static, inert HTML page for
  human review/sharing. Reuses the hardened report infra — CSV cells get formula-injection
  neutralization; the HTML escapes every Steam-sourced field and is validated inert (no
  scripts/handlers/active links) before writing. Offline; the timestamp is injectable so
  output is deterministic.

## [0.10.0] - 2026-07-05

Liquidity precision: confirm a cheap badge is really buyable with real 24h volume, and
never present a price a buyer couldn't fill.

### Added

- **Top-K liquidity enrichment** for `sbo market cheapest-badges` (Epic #71 #74, reshaped):
  `--enrich-top K` (opt-in; needs `--online` + `--confirm`) re-prices the top K candidates
  via priceoverview to confirm liquidity with **real 24h volume** — so a badge that looks
  cheap-and-liquid on stale search listings but has ~0 actual volume is correctly demoted.
  Bounded to those candidates, rate-polite, hard-stops on rate-limit; the cost basis stays
  the current lowest ask (enrichment never presents a price a buyer couldn't fill).

### Note

- Real order-book depth via `itemordershistogram` (#4.5, and the histogram half of #74) is
  **infeasible** for this read-only tool: Steam moved the market listing page to SSR JS
  bundles, so a card's `item_nameid` is no longer in the static HTML (would need browser JS
  execution, outside the no-automation boundary). See #86. Liquidity signals remain the
  conservative order-book proxy + priceoverview volume + search listings.

## [0.9.0] - 2026-07-05

Data-layer correctness: the live market is now authoritative for how many cards a badge
needs, so a stale catalog no longer drops or mis-costs badges.

### Fixed

- **Set-size authority** (#79): a *provably complete* market discovery that finds more
  normal cards than the (stale) steam-badges-db catalog now corrects the stored badge
  `set_size` UPWARD to market truth — the catalog is only a floor, never lowered on a market
  undercount. Fixes badges that were dropped on a catalog/market count mismatch (e.g. The
  Amber Throne: catalog 6, market 7). Every downstream consumer (discovery completeness,
  cheapest-badges ranking, candidate selection) now uses the corrected size.
- Card discovery pagination now advances by the actual page size the endpoint returns
  (~10/page, not the requested 100), so games with more than ~10 cards are fully enumerated
  instead of silently truncated.

## [0.8.0] - 2026-07-05

Targeted completion: `sbo market plan-cheapest` finishes the most promising cheap games
into a real ranked list of cheapest badges, on a small bounded request budget.

### Changed

- `select_candidate_games` (plan-cheapest) now estimates unpriced slots at a conservative
  **75th-percentile** proxy instead of the median, so a game where many cards are verified
  cheap ranks ahead of a single-cheap-card gamble — a better predictor of which games are
  actually cheap to complete (#81). Prioritization only; never affects reported costs.

### Added

- **`sbo market plan-cheapest`** (Epic #71 #77/#69): turns the sweep machinery into a usable
  answer. From cached cheap prices (seed with `sbo market sweep`) it picks the games
  cheapest to FINISH — ranked by estimated completion cost (known prices + a median proxy
  for unpriced slots, so one cheap card can't fake a cheap set) — then discovers + prices
  just those `--max-games` sets and ranks the cheapest badges. Off by default (needs
  `--online` and `--confirm`), rate-polite, bounded, hard-stops on rate-limit. Reuses the
  existing per-game discovery + pricing.

### Changed

- `rank_cheapest_badges` now trusts the market card list: it ranks a set when the discovered
  cards (>= the catalog count) are all priced, costing ALL of them (conservative) and
  flagging any catalog/market count mismatch — instead of dropping the badge on an exact
  mismatch (#79).

## [0.7.0] - 2026-07-05

Whole-catalog cheapest badges (Epic #71): rank the cheapest badges to make across all of
Steam — not just games you own — from a bounded, opt-in, rate-polite market price sweep.

### Added

- **Bulk market sweep** (`sbo market sweep`, `sources.market_sweep`, Epic #71 #73): the
  whole-catalog price source for cheapest-badges. Pages Steam's market search endpoint
  **cheapest-first**, capturing each card's lowest ask + ask-side depth + foil, so the
  cheapest-badges ranking works across all of Steam, not just games you pulled in by hand.
  Fenced hard, every constraint tested: **off by default** (needs BOTH `--online` and
  `--confirm`); rate-polite (~1 req/5s floor + jitter); **hard-stops** on 429/Cloudflare
  (progress saved, no retry storm); **resumable** via a cursor file; **bounded** by
  `--max-pages` with optional `--until-sets` early-exit — it can never sweep the whole
  ~184k-card market by accident. Reads public listings only; never trades.

### Added

- **Cheapest-badges ranking** (`analytics.rank_cheapest_badges`, `sbo market cheapest-badges`,
  Epic #71 Tier 2): ranks the cheapest badges to make from scratch (full single-set cost =
  sum of each card's lowest ask), cost-per-XP, from cached prices. **Liquidity-gated** — a
  set whose cards have fewer than N listings (ask-side depth) is flagged and can never rank
  ahead of a liquid one, so a single-listing "cheap" badge can't masquerade as cheapest.
  Flags one-card-dominates bottlenecks. Source-agnostic (works on discovery+pricing today,
  the bulk market sweep next). Research only.
- The market search parser now captures per-card `sell_price` (lowest ask) and
  `sell_listings` (ask depth); `PriceSnapshot` gains a nullable `listings` field (schema v2).

## [0.6.0] - 2026-07-05

Privacy hardening and two correctness fixes that live testing on a real account
surfaced — the price fetcher and catalog default both now work against real Steam data.

### Fixed

- Price fetching now queries the Community Market under **appid 753** (where trading
  cards are actually listed) instead of the game's appid, which returned `success:true`
  with no price — so `prices refresh` reported every real card "unavailable" and the
  optimizer couldn't cost real inventories. Found and fixed via live testing on a real
  account (#66); verified live (0 → 7/10 cards priced).

### Added

- **`sbo delete-all`** (#8.4): purge all local data — the SQLite database **and its
  `-wal`/`-shm`/`-journal` sidecars** (a leftover value in the WAL would be a silent
  privacy leak) — with a confirmation prompt (`--yes` to skip). Completeness-tested: no
  imported data survives anywhere in the data dir afterward.

### Security

- **No-stored-secrets invariant** (#1.3): a regression test asserts no domain model field
  or schema column is named like a Steam credential/session secret (`steamLoginSecure`,
  `sessionid`, `shared_secret`, `identity_secret`, ...). Credentials are structurally
  un-storable.
- **Provenance is mandatory** (#0.4): a `SourceRecord` now requires a `url` or a
  `file_name` — no un-attributed datum can be constructed.
- **Egress audit** (#9.5): a test asserts no extra network library (`requests`,
  `aiohttp`, ...) is installed; the only sanctioned client is httpx via SafeClient.

### Fixed

- Corrected the default steam-badges-db catalog URL — it pointed at `master/badges.json`
  (404); the file is at `main/data/badges.json`. `sbo catalog import --online` now works
  (verified live: 15,024 apps imported). Found by live end-to-end validation.

## [0.5.0] - 2026-07-05

Correctness on the money path and easy distribution: multi-copy purchase costs are now
modeled (no more under-budgeting) and reconcile across the plan and reports, and the tool
ships as a hardened, non-root container.

### Added

- **Docker packaging** (#9.2): a multi-stage `Dockerfile` (digest-pinned `python:3.13-slim`)
  running as a **non-root** numeric UID (10001), with local data in a mounted volume and
  **no credentials or user data baked into the image**. Ships a `.dockerignore` (excludes
  `.git`/`.venv`/`*.sqlite3`/`.env`/secrets), a README "Run with Docker" section with a
  hardened `docker run` command (read-only rootfs, `no-new-privileges`, `cap-drop ALL`),
  a CI build + fixture-demo smoke test, and Dependabot base-image updates.

### Fixed

- README status refreshed — it described the tool as an early stub; it is now
  feature-complete for the core workflow.

### Changed

- **Order-book-depth cost model** (#15): the cost calculator no longer prices k copies of
  a card at `k * lowest` (an under-budget, since `lowest` is a single-unit ask). Extra
  copies are now priced at a conservative book-walk proxy — the median transacted price,
  **capped at 2x lowest** so a spiky median can't wildly over-estimate — or a documented
  +15% inflation when no median is cached. The estimate never undershoots `k * lowest`
  and is monotonic in quantity; it stays labeled "modeled, not order-book-measured" with
  capped confidence. Real depth via itemordershistogram remains a later precision upgrade
  (#4.5). Replaces the interim floor note from #55.

## [0.4.0] - 2026-07-05

Accuracy and research depth: plans now use your **real badge levels**, the cost model is
**honest about multi-copy under-budgeting**, and market research gains **historical
anomaly detection** — all still local, read-only, and never trading advice.

### Added

- **Historical price-anomaly detection** (`analytics.anomalies`, `sbo market anomalies`,
  Epic 6.3): flags cards with a sudden drop (vs trailing mean), a mean-reversion low
  outlier (z-score), or a stale median far above the live lowest — from the append-only
  price history. Fail-closed: a card with < 5 same-currency history points is skipped
  ("insufficient history"), currency-consistent, and every result is coarse (never HIGH)
  confidence with an explicit caveat. Research only; executes nothing.

### Added

- **Badge-progress ingestion** (`sources.badge_progress`, `sbo badges import`, Epic 2.4):
  imports the user's real per-game badge levels so plans use them instead of assuming
  level 0 (which overstated cost and XP). Primary source is the official Steam Web API
  `GetBadges` (stable JSON) with the user's key from `SBO_STEAM_API_KEY` — a read-only
  key, **never persisted** to the DB, provenance, logs, or errors; the guarded client
  now redacts `key=`/`token=` from any URL it reports. Saved-JSON manual import is the
  offline fallback. Foil badges out of scope; unknown levels keep the assume-0 fallback.

### Changed

- `SafeClient` redacts sensitive query params (`key`, `token`, `secret`, ...) from all
  error messages so a 403/timeout can't leak a secret into a traceback.

## [0.3.0] - 2026-07-05

Makes the optimizer broadly useful and adds market research. **Card-name discovery**
means badges become costable instead of stuck "incomplete", and **market intelligence**
surfaces liquidity-weighted price-weakness signals — still entirely local, read-only,
and never trading advice.

### Added

- **Market intelligence** (`analytics.market_scan`, `sbo market scan-weakness` /
  `scan-sets`, Story 4 / Epic 6): liquidity-weighted price-weakness research over
  cached snapshots. Per-card `ask_vs_median_gap` (not "spread" — priceoverview has no
  buy orders), volume adequacy, staleness, and volatility (only with >= 5 history
  points, else "insufficient history" — never fabricated). Low-volume cards are flagged
  LOW-CONFIDENCE and can never top the ranking on a gap alone. Set-level: single-set
  cost and one-card-dominates (bottleneck) signals. Currency-consistent; labeled
  research, never trading advice; executes nothing.

### Added

- **Card-name discovery** (`sources.card_discovery`, `sbo cards discover`, Epic 3.2):
  enumerates a game's full trading-card list from the Steam market search endpoint
  (read-only) so the optimizer can mark badges **complete** instead of "incomplete"
  (previously only user-owned cards were known). Fail-closed reconciliation — only a
  discovered-count == catalog set_size marks a set fully known; more/fewer stays
  incomplete with a note, never overriding the catalog or inventing a name. Foils
  excluded from the set (stored flagged); manual-import fallback for blocked users.

## [0.2.0] - 2026-07-05

The **MVP optimizer**: ask "what's the cheapest way to gain levels?" and get a ranked,
explainable, manually-actionable plan you can export — entirely from local data, with a
structurally enforced no-automation boundary.

### Added

- **Purchase-plan reports** (`sbo report purchase-plan --format csv|html --out FILE`,
  Epic 7): export the optimizer plan for manual review. CSV has machine-readable columns
  and defends against spreadsheet formula injection (a leading `= + - @ |`, even after
  whitespace, is single-quote-prefixed). HTML is a self-contained **inert** document —
  no scripts, no event handlers, no active URL schemes, strict CSP, every value
  HTML-escaped, links only to informational market pages — enforced in code by
  `assert_inert_html` before the file is written (fail closed), not just by tests.

### Added

- **Greedy optimizer + `sbo optimize`** (Epic 5.2): ranks complete badges by
  cost-per-XP and fills to a `--budget` and/or an account-`--target-level` (via the
  Steam XP step function), explaining chosen vs skipped, surfacing ready-to-craft and
  incomplete badges separately, and printing a read-only disclaimer. The core
  "cheapest way to gain N levels" answer, working end-to-end from local data.
- `config.account_xp_between` — Steam account-level XP math (100 * ceil(level/10)).
- Cost-to-complete calculator (`optimize.compute_costs`, Epic 5.1): per-badge cost to
  reach a target level from cached catalog + inventory + prices. Handles current level
  (crafts_needed = target - level), surfaces ready-to-craft badges, and — fail-closed —
  marks a badge **incomplete** (never fabricates a cost) when card names are unknown or
  any needed card is unpriced/unmarketable. Emits PurchaseCandidates and a coarse
  High/Medium/Low confidence signal. The optimizer will rank only complete badges.

## [0.1.0] - 2026-07-05

First tagged release: the complete local, read-only **data layer** — every input the
optimizer needs, with a structurally enforced no-automation boundary. Import a
catalog, resolve a SteamID, ingest an inventory, and cache market prices; the
optimizer and reports build on this next.

### Added — Milestone 2 (data layer)

- Inventory ingestion (`sources.steam_inventory`, `sbo inventory import`): parses the
  753/6 trading-card inventory (joins assets<->descriptions, sums duplicates, derives
  foil status from the structural `cardborder` tag — locale-independent), skips and
  counts malformed entries, fails loud only on a broken envelope. Fetches a public
  inventory via SafeClient with bounded pagination (HTTP 403 -> PrivateInventoryError
  with a manual-import hint) or imports a saved JSON file. Discovered cards feed the
  price fetcher. Adds `SafeClient` HTTPStatusError carrying the status code.

- SteamID resolution (`sources.steamid`, `sbo steamid`): accepts a raw SteamID64,
  a profile URL, or a vanity name (resolved via the public profile XML through
  SafeClient — no API key, no login). Hostile vanity input is rejected before any
  request.

- Market price fetcher (`sources.steam_market`, `sbo prices refresh`): fetches the
  unofficial `priceoverview` via SafeClient, parses localized lowest/median into
  `Money` + volume, persists a `PriceSnapshot` with TTL, reuses fresh cached prices,
  degrades gracefully on missing/failed lookups, and surfaces HTTP 429.
- Guarded read-only HTTP client (`sources.SafeClient`): the single httpx egress
  point — validates method+URL via the safety guard before any socket, exposes only
  read verbs, keeps no cookie jar, sends an honest User-Agent, surfaces HTTP 429
  instead of hammering, and retries only transient transport errors.
- steam-badges-db catalog import (`sbo catalog import --file … | --online`) and
  `sbo catalog list`: parses `badges.json` into `SteamApp`+`BadgeSet`, persists with
  provenance, tolerates malformed entries, and guards against oversized input.
- Local SQLite persistence (`db.Store`, stdlib `sqlite3`, no ORM): forward-only
  migration runner, current-state upserts for catalog/inventory/badge progress,
  append-only price history with source-hash dedup, and provenance round-tripping.
- `mypy --strict` on `src/` is now a CI gate.
- Core pydantic domain models (`SteamApp`, `BadgeSet`, `Card`, `UserBadgeProgress`,
  `UserCardInventory`, `MarketItem`, `PriceSnapshot`, `PurchaseCandidate`) with strict
  validation and a `Money` value object.
- `parse_steam_price` — robust parser for Steam's localized price display strings
  (US/EU grouping, comma decimals, currency suffixes) into integer cents.
- Repo hygiene: CONTRIBUTING guide (Conventional Commits + SemVer policy), PR/issue
  templates, and a dependency-free PR-title validation workflow.

### Added — Milestone 1 (Safe skeleton)

- Python 3.12+ package skeleton, `pyproject.toml` (hatchling), ruff + pytest config.
- Typer CLI (`sbo` / `steam-badge-optimizer`) with the full command surface wired;
  unimplemented commands fail loudly with their target milestone.
- **Structural read-only safety boundary** (`safety.py`): method + host allowlists
  and a forbidden-action-route tripwire, with release-blocking regression tests and
  an AST-based CI gate against mutating HTTP verbs / egress bypass.
- `SourceRecord` provenance model and runtime configuration with verified Steam
  constants (community appid 753 / context 6, 100 XP per craft, currency ids).
- Documentation: safety ADR-0001, data-sources, optimizer-model, market-model, and a
  milestone-ordered backlog incorporating a fact-check / security / optimizer review.
- CI (GitHub Actions) across Python 3.12–3.13 with an isolated safety gate;
  Dependabot for pip and Actions; MIT license; security policy.
