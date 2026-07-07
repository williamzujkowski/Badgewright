# Badgewright

**A local-first, read-only Steam trading-card badge optimizer and market-intelligence tool.**

Badgewright finds the **cheapest Steam badges to make** — across your own games or the
whole market — by reading public card/inventory/market data, modeling the cost to complete
each badge, and producing a **human-reviewable plan**. You then buy the cards yourself,
manually, inside Steam. It never touches your account.

## What it does — and what it will never do

> This tool is a local analytics and planning tool. It does **not** buy, sell,
> trade, craft, idle games, fake gameplay, manipulate the Steam UI, submit
> marketplace actions, or automate Steam account activity. It generates reports and
> links for manual review only. You are responsible for complying with Steam's terms
> and for making any marketplace decisions manually inside Steam.

This boundary is not just a promise — it is **structurally enforced** in code. Every
outbound request goes through `steam_badge_optimizer.safety`, which permits only
side-effect-free HTTP methods (`GET`/`HEAD`/`OPTIONS`) to a small allowlist of read-only
Steam hosts, and trips a hard failure on any URL that names a known action route (buy,
sell, craft, trade, list, cancel). A CI gate parses the source and fails the build if a
mutating HTTP verb or an egress path outside the guarded client is ever introduced. See
[`docs/adr/0001-safety-boundary.md`](docs/adr/0001-safety-boundary.md).

Steam's Subscriber Agreement (§4.C) prohibits scripts, bots, macros, and other
non-human-controlled systems from interacting with Steam. Badgewright stays firmly
on the read / calculate / export side of that line.

## Install

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
sbo --help
sbo safety      # print the read-only boundary this tool enforces
sbo init        # create the local data directory
```

Requires Python 3.12+.

## Quickstart: cheapest badges across all of Steam

Find the cheapest badges to make from scratch. Networked steps are **opt-in** (`--online`),
rate-polite, and bounded — nothing runs against Steam by default.

```bash
sbo catalog import --online                 # the card-set catalog (game -> #cards)

# Bulk price sweep of the whole card market, CHEAPEST-FIRST. Off by default: needs both
# --online and --confirm. Rate-polite (~1 req/5s), resumable (re-run to continue),
# bounded by --max-pages so it can never crawl the whole market by accident.
sbo market sweep --online --confirm --max-pages 30

# Rank the cheapest badges from the cached prices (offline). Liquidity-gated: a badge whose
# cards you can't actually buy won't rank as "cheapest".
sbo market cheapest-badges --top 20

# The cheapest cards scatter across many games, so a sweep alone rarely completes a full
# set. plan-cheapest picks the games cheapest to FINISH and completes just those (opt-in,
# bounded by --max-games), then re-ranks.
sbo market plan-cheapest --online --confirm --max-games 5

# Confirm the top candidates' liquidity with real 24h volume (opt-in, bounded to top K).
sbo market cheapest-badges --top 20 --enrich-top 5 --online --confirm

# Export the ranking for review/sharing — spreadsheet CSV or a static, inert HTML page.
sbo report cheapest-badges --out cheapest.html
```

## Quickstart: cheapest way to level *your* account

Reads only **public** data — your profile/inventory must be public. No login, ever.

```bash
sbo catalog import --online
sbo inventory import --steamid <your-vanity-or-SteamID64> --online   # your owned cards
sbo optimize --budget 5                                              # cheapest plan under $5
sbo report purchase-plan --format html --out plan.html              # export it for review
```

## Command reference

Run `sbo <command> --help` for the exact flags. Networked commands are always opt-in
(`--online`); the bulk sweep and targeted completion additionally require `--confirm`.

| Command | What it does |
|---|---|
| `sbo version` | Print the Badgewright version. |
| `sbo safety` | Print the enforced read-only boundary. |
| `sbo init` | Create the local data directory. |
| `sbo catalog import [--online\|--file F]` | Import the card-set catalog (game → set size). |
| `sbo steamid <id\|url\|vanity> [--online]` | Resolve a SteamID64 (no login). |
| `sbo inventory import --steamid X [--online]` | Ingest your public card inventory. |
| `sbo inventory value [--top N]` | Value your held cards at the current market (offline; seed prices first). |
| `sbo badges import [--online]` | Ingest your public badge levels. |
| `sbo cards discover --appid N [--online]` | Enumerate a game's full card list. |
| `sbo prices refresh [--online]` | Cache market prices (priceoverview). |
| `sbo market sweep --online --confirm` | Bounded, resumable, cheapest-first bulk price sweep. |
| `sbo market cheapest-badges [--enrich-top K]` | Rank cheapest badges to make (liquidity-gated). |
| `sbo market plan-cheapest --online --confirm` | Complete the cheapest candidate games, then rank. |
| `sbo market gems [--set-size N] [--online --confirm]` | Value gems in real money (Sack of Gems) + booster gem cost; reads cached price, `--online --confirm` refreshes. |
| `sbo market booster-arbitrage --online --confirm` | Flag Booster Packs cheaper than their card contents (research; modeled EV). |
| `sbo market card-gem-arbitrage [--online --confirm]` | Flag cards cheaper than the gems they yield (research; foils). |
| `sbo market scan-sets / scan-weakness / anomalies` | Market-intelligence research (never advice). |
| `sbo optimize [--budget B] [--badge-level L]` | Cheapest plan to a target level/budget (greedy). |
| `sbo report purchase-plan --out F` | Export a purchase plan (CSV / inert HTML). |
| `sbo report cheapest-badges --out F` | Export the cheapest-badges ranking (CSV / inert HTML). |
| `sbo report inventory-value --out F` | Export your inventory valuation (CSV / inert HTML). |
| `sbo delete-all [--yes]` | Purge all local data (DB + journal sidecars). |

## Run with Docker

The image ships only the code — **no credentials and no user data are baked in**, and it
runs as a **non-root** user. Your local SQLite database lives in a Docker volume, not in
the image.

```bash
docker build -t badgewright .
docker volume create badgewright-data          # Docker owns it, so the non-root user can write

# Import the bundled sample catalog, then plan — run hardened:
docker run --rm --read-only --tmpfs /tmp --security-opt no-new-privileges --cap-drop ALL \
  -v badgewright-data:/data \
  -v "$PWD/tests/fixtures/badges.json:/in/badges.json:ro" \
  badgewright catalog import --file /in/badges.json

docker run --rm --read-only --tmpfs /tmp --security-opt no-new-privileges --cap-drop ALL \
  -v badgewright-data:/data badgewright catalog list
```

Notes:

- Use a **named volume** (as above) so the container's non-root user (UID 10001) can
  write; a host bind-mount would need matching ownership.
- Mount your own input files **read-only** (`:ro`) and make sure they're readable by the
  container user, e.g. `chmod +r badges.json` (a restrictive umask can leave a file
  unreadable to UID 10001, giving `Permission denied`).
- The hardened flags (`--read-only`, `--cap-drop ALL`, `--security-opt no-new-privileges`)
  are safe because the app only ever writes to the mounted `/data` volume.

## Privacy

Your Steam data stays on your machine. Default storage is a local SQLite database; there is
no telemetry, no hosted backend, and no remote analytics. **No Steam credential is ever
collected** — there is no login path at all. Identity is just a SteamID (resolved from an
id, profile URL, or vanity name via public data), and reading your inventory/badges needs
only that your Steam profile be public. An optional Steam **Web API key** (for badge levels)
is read from the `SBO_STEAM_API_KEY` environment variable and is never written to disk.
Run `sbo delete-all` to purge every local file (database plus its journal sidecars).

## Documentation

- [Safety boundary (ADR-0001)](docs/adr/0001-safety-boundary.md)
- [Data sources & provenance](docs/data-sources.md)
- [Optimizer model](docs/optimizer-model.md)
- [Market model](docs/market-model.md)
- [Backlog / issues](docs/backlog.md)

## Alternatives

Badgewright is local-first and read-only by design. If you want live in-browser overlays
of gem/card/booster values instead, [Augmented Steam](https://augmentedsteam.com/) and
[Steam Card Exchange](https://www.steamcardexchange.net/) cover similar ground (they run
in the browser against live Steam pages rather than as an auditable, guarded-GET CLI).

## License

MIT.
