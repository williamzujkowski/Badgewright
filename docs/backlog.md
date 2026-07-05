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
- [ ] **2.2** OpenID login helper — identity only; **verify signature via
  `check_authentication`**; discard response, keep only SteamID64; isolated ephemeral
  cookie context wiped after extraction (see 2.5).
- [x] **2.3** Inventory ingestion — 753/6 parser (join assets<->descriptions, dedup,
  tag-based foil), SafeClient paginated fetch, 403->PrivateInventoryError, file
  fallback; discovered cards feed pricing. See #6.
- [x] **2.4** Badge-progress ingestion (`sbo badges import`; GetBadges API + file;
  env-var key never persisted; URL redaction). See #53. — orig: — level 0–5 per game, foil status, exclude
  maxed; start from manual/exported HTML if live parsing is fragile.
- [ ] **2.5 (NEW, security)** OpenID cookie-jar isolation test — assert no Steam
  session cookie ever reaches the fetch client.

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
- [ ] **4.2** Market listing-page parser — extract embedded price-history where
  available; store HTML hash + parser version; structured diagnostics on failure.
- [ ] **4.3** Rate-limit & politeness layer — per-host token bucket + jitter; respect
  `Retry-After`/429 backoff; cache TTLs; `--offline` default; bulk refresh is an
  explicit command; **never retry past a rate-limit block or captcha — stop and
  surface it**.
- [ ] **4.4 (NEW, optimizer)** Local price-history store — precondition for any
  volatility/staleness metric (snapshots alone can't yield it). Blocks 6.3.
- [ ] **4.5 (NEW, optimizer)** `itemordershistogram` integration — buy-side depth &
  true spread; needs an `item_nameid` scrape. Blocks a real "spread" metric in 6.1.
- [x] **4.6** Order-book depth / multi-unit price walk — conservative offline model
  (median-capped, never undershoots, modeled-not-measured). See #15. Real depth: #4.5. — orig: — `lowest_price`
  is 1-unit; buying k copies underestimates cost. Model depth or inflation factor.
  **Highest-impact correctness issue for the optimizer.** Blocks 5.1 accuracy.

## Epic 5 — Badge-cost optimizer

- [x] **5.1** Cost-to-complete calculator (`optimize.compute_costs`): per-badge cost to
  reach a target level; `crafts_needed = target - current_level`; duplicates subtracted;
  excludes L5/foil; ready-to-craft surfaced; incomplete-badge fail-closed (no fabricated
  cost); confidence signal. See #38. (Accuracy refined later by order-book depth #15.)
- [x] **5.2** Greedy optimizer — rank complete badges by cost-per-XP, fill to budget/
  target-level (account XP step function), explain chosen/skipped, `sbo optimize`. See #39.
- [ ] **5.3 (DEMOTED → shelf spec, optimizer)** ILP engine — only warranted once value
  is non-uniform (per-vendor caps, foil-XP, completion bonus). Keep the formulation
  documented; do **not** build for MVP. Was Epic 5.3 "must-have"; review found greedy
  exact.
- [ ] **5.4 (NEW, optimizer)** Account-level step-function optimizer — optimize
  cost-to-**target-level** over the XP bands; flag mid-band overshoot waste.
- [ ] **5.5 (NEW, optimizer)** Ready-to-craft free-XP surfacing — detect already-owned
  full sets, rank at cost 0.
- [ ] **5.6 (NEW, optimizer)** Unmarketable/delisted-card gating — exclude cards with
  no market listing rather than zero-costing or crashing.
- [ ] **5.7 (NEW, optimizer)** XP-per-craft as verified config constant — no hardcoded
  100, no phantom level-5 completion bonus; verify against a live account.
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
- [ ] **6.4 (NEW, optimizer)** Booster-pack / gems expected-cost path — alternative
  acquisition, often cheaper for large sets; compare as expected cost.

## Epic 7 — Reports & purchase workflow

- [ ] **7.1** CLI summary `plan` — total spend, expected XP, budget remaining,
  confidence, warnings.
- [x] **7.2** CSV export (formula-injection-safe). See Epic 7. — priority/appid/game/levels/card/qty/unit+total price/
  market_hash_name/URL/price age/confidence/notes; machine-readable numerics.
- [x] **7.3** HTML purchase planner (inert, CSP, escaped, checkboxes). See Epic 7. — group by badge, manual checkboxes, market links,
  copy-text-only. Works offline.
- [ ] **7.4** Manual batch sizing — batch by spend/badge/card count; smaller first
  batches for low-confidence data; regenerate after re-import.
- [x] **7.5** Inert-report invariant — assert_inert_html gate (no script/on*=/steam:/js:/data:,
  CSP required, http(s)-only hrefs, no forbidden market routes) + XSS/escaping tests. See #10.

## Epic 8 — Testing, fixtures, validation

- [ ] **8.1** Golden fixtures — sanitized catalog/inventory/badge/priceoverview/
  price-history; documented provenance; no real user data committed.
- [ ] **8.2** Optimizer correctness tests — exact cases, duplicates, max-level, stale
  prices, illiquid cards, budget & target-level constraints; outputs explainable/valid.
- [x] **8.3** Safety-regression tests (partial) — AST gate for mutating verbs / egress
  bypass done; extend with allowlisted-host/method assertions and forbidden-route
  fixtures as sources land.
- [x] **8.4** `sbo delete-all` (DB + WAL/SHM sidecars) + completeness test. See #8.4. — VACUUM/recreate DB +
  purge exported reports; verify no recoverable SteamID or cached token remains.
- [ ] **8.5 (NEW, security)** Cached-HTML sanitization-on-write — strip scripts/
  handlers/session tokens before persistence; test with a token-laden fixture.

## Epic 9 — Packaging & DX

- [ ] **9.1** Install & CLI docs — `uv`/`pipx`; example commands; dry-run; offline;
  troubleshooting.
- [x] **9.2** Dockerized execution (multi-stage, non-root UID 10001, digest-pinned base,
  named-volume data, hardened run, CI smoke test). See #9.2. — orig: — mount local data dir; non-root; no baked creds;
  SQLite persists in volume.
- [ ] **9.3** First-release checklist — versioning, changelog, release workflow,
  security policy.
- [ ] **9.4 (NEW, security)** Runtime egress allowlist (firewall/sandbox backstop) —
  static gates miss `eval`/native/dependency code; enforce host + GET at the process
  boundary in the Docker image.
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
- [ ] **#74** Tier-3 top-K depth enrichment.
