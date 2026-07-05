# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); the project uses semantic
versioning once it reaches 1.0.

## [Unreleased]

### Added — Milestone 2 (in progress)

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
