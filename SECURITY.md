# Security policy

## The project's security posture is its whole design

Badgewright is a **local, read-only** tool. Its central security property is that it
**cannot operate a Steam account** — it never buys, sells, crafts, trades, lists,
logs in with credentials, or automates any Steam action. This is enforced
structurally (see [`docs/adr/0001-safety-boundary.md`](docs/adr/0001-safety-boundary.md)):

- A choke-point HTTP guard (`steam_badge_optimizer.safety`) permits only
  `GET`/`HEAD`/`OPTIONS` to an allowlisted set of read-only hosts and trips on known
  action routes.
- An AST-based CI gate rejects any mutating HTTP verb or egress-bypassing import.
- No Steam password or Steam Guard secret is ever collected or stored; identity uses
  Steam OpenID (SteamID only).
- User data stays local (SQLite); no telemetry, no hosted backend.

If you believe a change or dependency could let the tool cross that boundary, treat
it as a security vulnerability.

## Reporting a vulnerability

Please report privately via GitHub's **Report a vulnerability** (Security tab →
Advisories) rather than opening a public issue. Include reproduction steps and the
affected version/commit. We aim to acknowledge within a few days.

Out of scope: issues that require the user to deliberately modify the safety module
or disable the CI gate.

## Supported versions

Pre-1.0: only the latest `main` is supported.
