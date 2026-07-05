# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/) and [SemVer](https://semver.org/)
(pre-1.0: minor = new capability, patch = fixes).

## [Unreleased]

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
