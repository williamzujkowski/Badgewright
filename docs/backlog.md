# Badgewright backlog

This repo has no GitHub remote yet, so this file is the canonical issue tracker.
When a remote exists, each unchecked item becomes a GitHub issue and this file
becomes an index. Items marked ✅ are done in the current scaffold.

## Legend

- `[x]` done · `[ ]` open · **NEW** = surfaced by the 2026-07-04 review fan-out
  (fact-check / security / optimizer subagents), not in the original plan.

---

## Epic 0 — Safety, scope, repository foundation

- [x] **0.1** Repository skeleton — package layout, `pyproject.toml`, ruff/pytest,
  CLI entry point, base config. `sbo --help`, `pytest`, `ruff check` all pass.
- [x] **0.2** Safety-boundary ADR + static gate — `docs/adr/0001-safety-boundary.md`;
  runtime `assert_safe_request` guard; AST-based CI gate
  (`tests/unit/test_no_mutating_http.py`) forbidding mutating verbs / egress bypass.
- [x] **0.3** Source-provenance model — `SourceRecord` with kind/url/time/parser/hash/TTL.
- [x] **0.4** Provenance mandatory — SourceRecord requires url or file_name. See #0.4. at the persistence layer —
  `source_url`/`fetched_at` non-null so no un-attributed page can be cached.

## Epic 1 — Data model & storage

- [x] **1.1** Core domain models — `SteamApp`, `BadgeSet`, `Card`,
  `UserBadgeProgress`, `UserCardInventory`, `MarketItem`, `PriceSnapshot`,
  `PurchaseCandidate` + `Money`/`parse_steam_price` (pydantic, validated, tested).
  `OptimizationRun`/`PurchasePlan` deferred until the optimizer consumes them (YAGNI).
- [x] **1.2** SQLite persistence — stdlib sqlite3 `db.Store`: migration runner,
  current-state upserts, append-only price history, source-hash dedup, provenance
  round-trip. See #3.
- [x] **1.3** No-secrets schema invariant + test (models + DDL). See #1.3. + test — assert no field
  named `steamLoginSecure`/`sessionid`/`shared_secret`/`identity_secret` exists in
  any model or table.

## Epic 2 — Steam identity & user-data ingestion

- [x] **2.1** SteamID input — SteamID64 / profile URL / vanity (resolved via profile
  XML through SafeClient); hostile vanity rejected pre-network. See #4.
- [x] **2.2 (REJECTED-BY-DESIGN)** OpenID login helper — superseded by the hard
  no-login-path boundary. Identity is resolved from public data only (2.1, profile XML
  via SafeClient); the tool never opens an auth flow of any kind.
- [x] **2.3** Inventory ingestion — 753/6 parser (join assets<->descriptions, dedup,
  tag-based foil), SafeClient paginated fetch, 403->PrivateInventoryError, file
  fallback; discovered cards feed pricing. See #6.
- [x] **2.4** Badge-progress ingestion (`sbo badges import`; GetBadges API + file;
  env-var key never persisted; URL redaction). See #53. — orig: — level 0–5 per game, foil status, exclude
  maxed; start from manual/exported HTML if live parsing is fragile.
- [x] **2.5 (REJECTED-BY-DESIGN, security)** OpenID cookie-jar isolation test — no
  OpenID flow exists to isolate (2.2). The underlying property holds generically and is
  stronger: `SafeClient` clears cookies on every request (`sources/http_client.py`),
  asserted by `tests/unit/test_http_client.py::test_no_cookies_persisted`.

## Epic 3 — Card-set catalog

- [x] **3.1** Import `steam-badges-db` from file and URL (via SafeClient); normalize
  appid/name/size; provenance; lenient on malformed entries; size-capped. See #5.
- [x] **3.2** Card-name discovery (`sbo cards discover`): market-search enumeration of a
  game's full card list; fail-closed reconciliation vs catalog set_size; foil-filtered;
  manual-import fallback; unknown cards stay explicit. See #47.

## Epic 4 — Market data collection & caching

- [x] **4.1** `priceoverview` fetcher — via SafeClient; parse localized lowest/median
  into Money + volume; persist PriceSnapshot with TTL; reuse fresh cache; graceful on
  missing/failed lookups; 429 surfaced. `sbo prices refresh`. See #32.
- [x] **4.2 (REJECTED-BY-DESIGN)** Market listing-page price-history parser — Steam's
  SSR migration removed the embedded price-history JS from static HTML; unlike the
  surviving `GetGooValue(...)` literal, there is nothing left to scrape. Covered
  instead by `priceoverview` + the append-only `price_snapshot` store (4.4). See #33.
- [x] **4.3** Rate-limit & politeness layer — per-host min-interval spacing + jitter,
  jittered backoff on transport errors, 429 raised as `RateLimited` with no retry,
  TTL cache reuse, `offline` default, explicit bulk-refresh commands, polite sweep
  floor. See `sources/http_client.py`, `test_http_client.py`. — orig: respect
  `Retry-After`/429 backoff; cache TTLs; `--offline` default; bulk refresh is an
  explicit command; **never retry past a rate-limit block or captcha — stop and
  surface it**.
- [x] **4.4 (optimizer)** Local price-history store — append-only `price_snapshot`
  table, unique per `(appid, market_hash_name, fetched_at)`, read via
  `Store.price_history()`. Unblocked 6.3.
- [x] **4.5 (REJECTED-BY-DESIGN, optimizer)** `itemordershistogram` integration —
  infeasible read-only: `item_nameid` is absent from SSR HTML entirely, so reaching it
  would require executing page JS, outside the no-automation boundary. 6.1 shipped
  without it, using an ask-vs-median gap instead of a true spread. See #86.
- [x] **4.6** Order-book depth / multi-unit price walk — conservative offline model
  (median-capped, never undershoots, modeled-not-measured). See #15. This is the
  permanent answer, not a placeholder — real measured depth (4.5) was ruled infeasible
  read-only. — orig: — `lowest_price`
  is 1-unit; buying k copies underestimates cost. Model depth or inflation factor.
  **Highest-impact correctness issue for the optimizer.** Blocks 5.1 accuracy.

## Epic 5 — Badge-cost optimizer

- [x] **5.1** Cost-to-complete calculator (`optimize.compute_costs`): per-badge cost to
  reach a target level; `crafts_needed = target - current_level`; duplicates subtracted;
  excludes L5/foil; ready-to-craft surfaced; incomplete-badge fail-closed (no fabricated
  cost); confidence signal. See #38. (Accuracy refined later by order-book depth #15.)
- [x] **5.2** Greedy optimizer — rank complete badges by cost-per-XP, fill to budget/
  target-level (account XP step function), explain chosen/skipped, `sbo optimize`. See #39.
- [x] **5.3 (SHELVED — not building, optimizer)** ILP engine — greedy is provably exact
  under uniform 100-XP-per-craft (see `optimizer-model.md`). Only warranted once value
  becomes non-uniform (per-vendor caps, foil-XP, completion bonus); none of those has
  landed or been proposed. Formulation stays documented, unbuilt. See #24.
- [ ] **5.4 (optimizer)** Mid-band XP overshoot flag — the band math itself shipped
  (`config.py` `account_xp_between`, `sbo optimize --current-level/--target-level`).
  Remaining: note when a plan's last craft lands past a band boundary, so the user can
  see XP that bought no level. See #18.
- [x] **5.5 (optimizer)** Ready-to-craft free-XP surfacing — `ready_to_craft` on the
  cost report; fully-owned sets cost 0 and sort first; surfaced separately by the CLI.
- [x] **5.6 (optimizer)** Unmarketable/delisted-card gating — an unmarketable needed
  card marks the badge uncostable rather than zero-costing or crashing.
- [x] **5.7 (optimizer)** XP-per-craft as verified config constant —
  `XP_PER_BADGE_LEVEL = 100` imported everywhere, no phantom level-5 bonus (pinned by a
  test asserting 5 crafts == 500 XP). Sourced from documented Steam behaviour rather
  than a live-account probe, which this read-only tool cannot perform.
- [ ] **5.8 (NEW, optimizer)** Confidence-weighted pessimistic ranking — formalize the
  liquidity-risk score feeding plan order (hard gates first, then risk-adjusted sort).

## Epic 6 — Market intelligence & arbitrage research

- [x] **6.1** Price-weakness scoring (`sbo market scan-weakness`; liquidity-weighted). See Epic 6. — ask-vs-median gap, recent drop, volume adequacy,
  staleness, volatility, ask-vs-median (not "spread"), set-completion impact; explain
  each; flag low-volume as risky.
- [x] **6.2** Set-level mispricing (`sbo market scan-sets`; card-dominance/bottleneck). See Epic 6. — Σ card prices vs set utility; cheapest full sets;
  "avoid" sets with one overpriced bottleneck card; partial-set opportunities.
- [x] **6.3** Historical anomaly detection (`sbo market anomalies`; sudden-drop/mean-
  reversion/stale-median; fail-closed on thin history; research-only). See Epic 6. — drops/volume spikes/mean reversion/stale-
  median-vs-live-lowest; type + confidence + caveats; no trading action. (Depends on 4.4.)
- [ ] **6.4 (optimizer)** Booster-pack / gems expected-cost path — alternative
  acquisition, often cheaper for large sets; compare as expected cost. Now a wiring
  job, not a subsystem: `gem_economy.booster_crafting_cost_gems()` and
  `sources/booster_market.py` already exist (built for Epic #94); what is missing is an
  alternative-cost lane inside `optimize/cost.py`. See #23.

## Epic 7 — Reports & purchase workflow

- [x] **7.1** CLI plan summary — shipped as `sbo optimize` (not `plan`): total spend,
  expected XP, budget remaining, per-badge confidence, warnings and notes. Pinned by
  `tests/unit/test_cli_optimize.py::test_plan_never_exceeds_budget`.
- [x] **7.2** CSV export (formula-injection-safe). See Epic 7. — priority/appid/game/levels/card/qty/unit+total price/
  market_hash_name/URL/price age/confidence/notes; machine-readable numerics.
- [x] **7.3** HTML purchase planner (inert, CSP, escaped, checkboxes). See Epic 7. — group by badge, manual checkboxes, market links,
  copy-text-only. Works offline.
- [ ] **7.4** Manual batch sizing — batch by spend/badge/card count; smaller first
  batches for low-confidence data; regenerate after re-import.
- [x] **7.5** Inert-report invariant — assert_inert_html gate (no script/on*=/steam:/js:/data:,
  CSP required, http(s)-only hrefs, no forbidden market routes) + XSS/escaping tests. See #10.

## Epic 8 — Testing, fixtures, validation

- [ ] **8.1 (partial)** Golden fixtures — catalog/inventory/badge/card-discovery
  fixtures exist under `tests/fixtures/` and are all synthetic. Remaining: committed
  `priceoverview` and price-history fixtures (currently built inline via respx), plus a
  provenance note in `tests/fixtures/` recording how each was constructed.
- [x] **8.2** Optimizer correctness tests — exact-case math, duplicates, max-level,
  illiquid/low-volume, budget and target-level constraints, explainability
  (`test_cost.py`, `test_greedy.py`). Residual gap: stale-price handling is exercised
  at the model layer, not through `compute_costs`.
- [x] **8.3** Safety-regression tests (partial) — AST gate for mutating verbs / egress
  bypass done; extend with allowlisted-host/method assertions and forbidden-route
  fixtures as sources land.
- [x] **8.4** `sbo delete-all` (DB + WAL/SHM sidecars) + completeness test. See #8.4. — VACUUM/recreate DB +
  purge exported reports; verify no recoverable SteamID or cached token remains.
- [x] **8.5 (REJECTED-BY-DESIGN, security)** Cached-HTML sanitization-on-write — the
  premise does not hold: no Steam response body is ever persisted. `source_record`
  stores a `raw_sha256` hash and metadata only, never bytes. Pinned by
  `tests/unit/test_privacy_hardening.py::TestNoRawBodyPersistence`. See #12.

## Epic 9 — Packaging & DX

- [ ] **9.1 (partial)** Install & CLI docs — `uv` install, quickstarts, offline
  defaults and a full command-reference table are in the README. Remaining: a `pipx`
  install path and a dedicated troubleshooting section.
- [x] **9.2** Dockerized execution (multi-stage, non-root UID 10001, digest-pinned base,
  named-volume data, hardened run, CI smoke test). See #9.2. — orig: — mount local data dir; non-root; no baked creds;
  SQLite persists in volume.
- [x] **9.3** First-release checklist — SemVer + conventional commits documented in
  `CONTRIBUTING.md`, populated `CHANGELOG.md`, `SECURITY.md`, and a tag-driven release
  flow exercised across 20 tagged releases, v0.1.0 through v1.4.2.
- [ ] **9.4 (security)** Runtime egress backstop — static gates miss
  `eval`/native/dependency code. **Not** in-container iptables: that needs `NET_ADMIN`,
  contradicting the image's drop-all-capabilities hardening. Ship instead as an
  external layer — an example compose file with an egress-only proxy restricted to the
  allowlisted hosts, documented as untestable reference material. See #13.
- [x] **9.5** Dependency egress audit (no extra network libs installed). See #9.5. — pin deps; CI check that
  no dependency introduces a non-GET Steam path or network-capable transitive.

## Cross-cutting inputs

- Untrusted-input hardening (security review): path-traversal-safe imports (reject
  absolute/`..`/symlinks, basename-only), JSON/zip size caps + schema validation, no
  user-supplied URL ever reaches the fetch client (host allowlist already blocks
  SSRF; also reject IP-literal/link-local). Fold into 2.3/3.1/7.x as those land.

## Epic #71 — whole-catalog cheapest badges

- [x] **#72** Tier-2 set-cost aggregation + `sbo market cheapest-badges` (liquidity-gated,
  cost-per-XP, bottleneck flag; captures sell_price+sell_listings; PriceSnapshot.listings).
- [x] **#73** Bounded opt-in cheapest-first market sweep (`sbo market sweep`; default-off,
  429-hard-stop, resumable cursor, --max-pages cap, --until-sets early-exit).
- [x] **#74** Tier-3 top-K liquidity enrichment — shipped reshaped as `--enrich-top K`
  on `sbo market cheapest-badges`: re-prices the top K candidates via `priceoverview`
  for real 24h volume, cost basis stays the lowest ask. True order-book depth was ruled
  infeasible separately (4.5).
