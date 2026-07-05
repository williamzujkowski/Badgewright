# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/) and [SemVer](https://semver.org/)
(pre-1.0: minor = new capability, patch = fixes).

## [Unreleased]

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
