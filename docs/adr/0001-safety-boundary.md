# ADR-0001: Read / calculate / export only — the safety boundary

- **Status:** Accepted
- **Date:** 2026-07-04
- **Deciders:** Project owner

## Context

Badgewright helps a user level their Steam account cheaply by planning card
purchases. The obvious temptation is to also *perform* those purchases (and
crafting, buy orders, trades) automatically. Doing so would put the project on the
wrong side of Steam's Subscriber Agreement, which (§4.C) prohibits "scripts, bots,
macros, or other non-human-controlled systems ('Automation') to interact with
Content and Services on Steam," and bars automating any Marketplace process. Valve
may restrict or terminate accounts for such automation.

A reasonable reviewer must be able to look at this project and see a **local
analytics assistant**, not a market bot.

## Decision

Badgewright stays entirely on the **read / calculate / export** side of the line.

**Allowed:** read public Steam data; identify the user via Steam OpenID (SteamID
only, never credentials); import user-provided files/snapshots; fetch market
listing/price metadata with aggressive caching and conservative rate limiting;
generate checklists, CSV/HTML reports, and market links; open Steam pages for
**manual** review.

**Disallowed:** storing any Steam password or Steam Guard secret; automated login;
any POST/PUT/PATCH/DELETE that could create, cancel, accept, confirm, buy, sell,
craft, trade, or list anything; JavaScript that clicks Steam UI; "one-click buy
all"; automated crafting or buy-order repricing; market sniping; account farming,
idling, playtime simulation, achievement manipulation, or card-drop farming; any
attempt to bypass rate limits, Cloudflare, captchas, Steam Guard, or market/trade
holds.

## Enforcement — structural, not just documented

1. **Choke-point HTTP client.** All network egress goes through the guarded client;
   `steam_badge_optimizer.safety.assert_safe_request` validates every request:
   - **Method allowlist** — only `GET`/`HEAD`/`OPTIONS`. Mutating verbs are how one
     buys/sells/crafts/trades; there is deliberately **no** override to relax this.
   - **Host allowlist** — a small set of read-only Steam/catalog hosts. Adding a
     host is a reviewed change recorded here.
   - **Forbidden-route tripwire** — any URL naming a known action route
     (`sellitem`, `createbuyorder`, `ajaxcraftbadge`, ...) fails closed even under
     an allowed method.
2. **AST-based CI gate** (`tests/unit/test_no_mutating_http.py`) — walks the source
   AST and fails the build if a `.post/.put/.patch/.delete` call or a
   `requests`/`aiohttp`/`socket` import (bypassing the choke point) is introduced.
   Chosen over keyword grep because it ignores comments/docstrings and matches the
   actual dangerous construct.
3. **No secrets by construction** — OpenID yields only a SteamID64; no schema field
   for `steamLoginSecure`, `sessionid`, `shared_secret`, or `identity_secret` may
   exist (asserted by test). The OpenID response is discarded after ID extraction;
   no Steam session cookie is persisted or reused.

## Consequences

- Some conveniences are permanently off the table (auto-buy, auto-craft). That is
  the point.
- New read sources require a reviewed allowlist edit here — small, deliberate cost.
- If a future contributor needs a mutating call, the build fails and this ADR must
  be revisited explicitly; it cannot happen by accident.

## References

- Steam Subscriber Agreement §4.C — <https://store.steampowered.com/subscriber_agreement/>
- Steam OpenID (identity only) — <https://partner.steamgames.com/doc/features/auth>
