# AGENTS.md — Badgewright

Guidance for AI coding agents (Claude Code, Codex, Cursor, Aider, Cline, and
friends) working in this repo. Self-contained — no required redirect to other files.

**About this project.** Badgewright (`steam-badge-optimizer`) is a **local-first,
read-only** tool that helps a Steam user level their account cheaply by reading
badge/card/inventory/market data, modeling the cheapest purchase path to a target
level or budget, and producing a **human-reviewable purchase plan**. It optimizes
decisions; it never operates the Steam account.

## The one rule that overrides everything: read / calculate / export only

Badgewright must stay on the read / calculate / export side of the line. This is not
a preference — it is the reason the project can exist without violating Steam's
Subscriber Agreement (§4.C bans scripts/bots/macros/automation interacting with
Steam). Read [`docs/adr/0001-safety-boundary.md`](./docs/adr/0001-safety-boundary.md)
before touching anything network-facing.

**Allowed:** read public Steam data; identify by SteamID resolved from public data
(**no login path at all** — never credentials); import user files/snapshots; fetch
market price/listing metadata with
aggressive caching + conservative rate limiting; generate CSV/HTML/checklist reports
and market **links**; open Steam pages for manual review.

**Disallowed — never implement, even if asked casually:** storing a Steam password
or Steam Guard secret; automated login; any POST/PUT/PATCH/DELETE that could buy,
sell, craft, trade, list, cancel, confirm, or accept; JS that clicks Steam UI;
"one-click buy all"; automated crafting or buy-order repricing; market sniping;
account farming/idling/playtime-simulation/card-drop farming; any bypass of rate
limits, Cloudflare, captchas, Steam Guard, or market/trade holds.

**How the boundary is enforced (do not weaken these):**

1. All network egress goes through the guarded client;
   `steam_badge_optimizer.safety.assert_safe_request` allows only `GET`/`HEAD`/
   `OPTIONS` to an allowlisted set of read-only hosts and trips on known action
   routes. There is deliberately no override for the method allowlist.
2. `tests/unit/test_no_mutating_http.py` is an AST gate that fails the build on any
   `.post/.put/.patch/.delete` call or `requests`/`aiohttp`/`socket` import.
3. No schema field for a Steam session/secret may exist.

If a task seems to require crossing this line, **stop and say so** — do not
implement a workaround. Adding a new *read* host is fine but is a deliberate,
reviewed edit to `ALLOWED_HOSTS` + the ADR.

## Prime directive

```
correctness > simplicity > performance > cleverness
```

Explicit error handling, observable state changes, no silent failures. A wrong
purchase plan costs the user real money — correctness of the cost/XP math is the
highest bar (see [`docs/optimizer-model.md`](./docs/optimizer-model.md)).

## Development disciplines (non-negotiable)

- **Red/Green TDD** — failing test first, then minimum code to pass, then refactor.
  The safety and optimizer paths especially: never production code without a test.
- **Climb the reuse ladder before writing code** — stop at the first rung that holds:

  ```
  1. Does this need to exist?   -> no: skip it (YAGNI)
  2. Already in this codebase?  -> reuse it, don't rewrite
  3. Stdlib does it?            -> use it
  4. Native platform feature?   -> use it
  5. Installed dependency?      -> use it
  6. One line?                  -> one line
  7. Only then: the minimum that works
  ```

  The ladder runs *after* you understand the problem, not instead of it: read the code
  the change touches and trace the real flow first. A small diff in the wrong place is
  a second bug. This is a small local tool — no speculative abstractions, no "just in
  case" plugin layers, and rung 5 is a real constraint here: a **new** dependency
  widens the egress surface and must clear the safety boundary (see below). The plan is
  milestone-ordered in [`docs/backlog.md`](./docs/backlog.md) — work it top-down.
- **Fix the root cause, not the symptom** — a report names a symptom. Grep every caller
  of the function you touch and fix the shared function once; one guard there is a
  smaller diff than one per caller, and patching only the path the report names leaves
  a sibling caller broken. The `(Foil Trading Card)` bug (#138) was exactly this: the
  visible symptom was one wrong set size, the cause was one detector used everywhere.
- **DRY** — single authoritative representation. Extract at the third occurrence.
- **Never lazy about** — the safety boundary, input validation at trust boundaries,
  provenance, error handling that prevents data loss, and the cost/XP math. Shortest
  working diff wins *everywhere else*. A deliberate simplification with a known ceiling
  (naive heuristic, O(n²) scan, single-currency assumption) gets a `ponytail:` comment
  naming the ceiling and the upgrade path — not silence.
- **Lazy code without its check is unfinished** — non-trivial logic leaves behind the
  smallest thing that fails if the logic breaks. For a claim that closes an issue or
  backs a safety property, prove the test fails without the fix before trusting it.
- **Provenance always** — every imported datum carries a `SourceRecord`
  (source/url/time/parser/hash/TTL). No un-attributed data enters the store.
- **Constants, not magic numbers** — Steam mechanics drift (XP per craft, contextids,
  currency ids). Keep them in `config.py` and cite the source in `docs/data-sources.md`.

The reuse ladder, the root-cause rule, the "never lazy about" carve-outs and the
`ponytail:` marker are adapted from [ponytail](https://github.com/DietrichGebert/ponytail)
(MIT). Its rules are followed here as written into this file — deliberately **not**
vendored as files or hooks: this is a Python project with a strict egress boundary, and
importing a multi-agent JS plugin to enforce minimalism would be the exact over-build the
ladder rejects. For the always-on version in your own client:
`/plugin marketplace add DietrichGebert/ponytail` then `/plugin install ponytail@ponytail`.
Where ponytail and the safety boundary ever disagree, **the boundary wins** — the one rule
at the top of this file overrides everything.

## Default working mode — fan out, verify, then implement

For any non-trivial work (3+ steps, architecture, security-sensitive, or anything
you'd want a second opinion on):

1. **Fan out read-only subagents for breadth and independence.** When answering means
   sweeping many files/sources, or when independent perspectives help (a fact-check, a
   security review, an optimizer-model review), launch them concurrently in waves of
   3–4 and keep the conclusions, not the file dumps. The 2026-07-04 scaffold review
   used exactly this and it caught real errors (no L5 completion bonus; greedy is
   optimal so ILP is demoted; `lowest_price` order-book-depth trap; cards live at
   appid 753/context 6, not the game appid).
2. **Verify claims before acting.** An adversarial verify/review pass before you
   consider something done catches what a solo pass misses — especially for the
   safety boundary and any cost math.
3. **Plan, then implement** the smallest correct increment; update the backlog
   checkbox when it lands.

Skip the ceremony for trivial fixes (typo, one-line bug, doc tweak) or when the user
says "just do it."

## Track all work — deferring is fine, untracked is not

Every identified piece of work — including work you're explicitly deferring or that
is blocked on something else — goes in [`docs/backlog.md`](./docs/backlog.md) (the
canonical tracker until a GitHub remote exists; then each open item becomes an
issue). A bug you notice outside your current scope, a dependency-blocked follow-up,
a scope cut during planning — file it the moment you name it. Code TODOs and prose
mentions are not tracking. Record the unblock trigger for blocked items.

## Error handling

State uncertain actions before taking them: **DOING / EXPECT / IF YES / IF NO**, then
close the loop with the actual result. On failure: (1) report the raw error, (2) your
theory of the cause, (3) one proposed next action, (4) expected outcome, (5) wait.
Never silently retry, guess past a failure, or — for anything network-facing — retry
past a rate-limit block or captcha.

## Self-check before "done"

- [ ] TDD/DRY held and the reuse ladder was climbed (no new dependency or abstraction
      that a lower rung already covered); tests cover happy path + edge + error cases.
- [ ] Any test backing a safety property or closing an issue was shown to FAIL without
      the fix — a green test that cannot fail is worse than no test.
- [ ] **Safety boundary intact** — no mutating verb, no new egress host without an ADR
  edit, no secret field, AST gate green.
- [ ] Wiring complete — new CLI commands registered in the Typer app; new models
  exported and validated.
- [ ] Provenance attached to any new imported datum; constants in `config.py`.
- [ ] `ruff check` and `pytest` green; CLI still runs (`sbo --help`, `sbo safety`).
- [ ] Discoveries logged to the backlog.

## Project map

```
src/steam_badge_optimizer/
  cli.py            # Typer CLI — full command surface wired; stubs fail loudly by milestone
  config.py         # Settings + well-known Steam constants (appid 753/ctx 6, XP=100, currencies)
  safety.py         # THE read-only boundary: assert_safe_request + allowlists
  models/           # pydantic domain models (provenance.py = SourceRecord)
  sources/          # steamid, badge_progress, steam_badges_db, steam_inventory, steam_market, market_sweep, booster_market, goo_value, card_discovery, http_client
  normalize/        # cards, badges, inventory, prices
  optimize/         # greedy (primary), ilp (shelf spec), scoring, constraints
  analytics/        # spreads, volatility, liquidity, arbitrage, anomalies (research only)
  reports/          # purchase_plan, html (inert), csv, markdown
  db/               # schema, migrations
tests/              # unit / integration / fixtures — safety tests are release-blocking
docs/               # adr/, data-sources.md, optimizer-model.md, market-model.md, backlog.md
```

## Key docs

- [Safety boundary (ADR-0001)](./docs/adr/0001-safety-boundary.md) — read first.
- [Data sources & provenance](./docs/data-sources.md) — verified Steam facts + gotchas.
- [Optimizer model](./docs/optimizer-model.md) — why greedy is optimal; cost model.
- [Market model](./docs/market-model.md) — what's computable vs not; statistical hygiene.
- [Backlog](./docs/backlog.md) — the canonical, milestone-ordered issue tracker.

## Commands

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
ruff check . && pytest -q
sbo --help          # full command surface
sbo safety          # print the read-only boundary
```
