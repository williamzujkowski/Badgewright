# Badgewright

**A local-first, read-only Steam trading-card badge optimizer and market-intelligence tool.**

Badgewright helps you level your Steam account as cheaply as possible by reading
your badge/card/inventory state and market prices, modeling the cheapest path to a
target level or budget, and producing a **human-reviewable purchase plan**. You then
buy the cards yourself, manually, inside Steam.

## What it does — and what it will never do

> This tool is a local analytics and planning tool. It does **not** buy, sell,
> trade, craft, idle games, fake gameplay, manipulate the Steam UI, submit
> marketplace actions, or automate Steam account activity. It generates reports and
> links for manual review only. You are responsible for complying with Steam's terms
> and for making any marketplace decisions manually inside Steam.

This boundary is not just a promise — it is **structurally enforced** in code. Every
outbound request goes through `steam_badge_optimizer.safety`, which permits only
side-effect-free HTTP methods (`GET`/`HEAD`) to a small allowlist of read-only Steam
hosts, and trips a hard failure on any URL that names a known action route (buy,
sell, craft, trade, list, cancel). See [`docs/adr/0001-safety-boundary.md`](docs/adr/0001-safety-boundary.md).

Steam's Subscriber Agreement (§4.C) prohibits scripts, bots, macros, and other
non-human-controlled systems from interacting with Steam. Badgewright stays firmly
on the read / calculate / export side of that line.

## Status

Feature-complete for the core workflow. The full chain works end-to-end, all local and
read-only:

- Import the card-set **catalog** (`catalog import`), resolve a **SteamID** (`steamid`),
  ingest your **inventory** (`inventory import`) and **badge levels** (`badges import`),
  discover a game's full **card list** (`cards discover`), and cache **prices**
  (`prices refresh`).
- **Optimize** the cheapest path to a target level or budget (`optimize`), with a
  conservative multi-copy cost model, and **export** a purchase plan (`report
  purchase-plan --format csv|html`).
- **Market research** (`market scan-weakness`, `scan-sets`, `anomalies`) — liquidity-
  weighted, labeled research, never advice.

Run `sbo --help` for the full map; the backlog and remaining ideas live in
[`docs/backlog.md`](docs/backlog.md).

## Install (development)

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
sbo --help
sbo safety      # print the read-only boundary
sbo init        # create the local data directory
```

Requires Python 3.12+.

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
- Mount your own input files **read-only** (`:ro`) and ensure they're readable.
- The hardened flags (`--read-only`, `--cap-drop ALL`, `--security-opt no-new-privileges`)
  are safe because the app only ever writes to the mounted `/data` volume.

## Privacy

Your Steam data stays on your machine. Default storage is a local SQLite database;
there is no telemetry, no hosted backend, no remote analytics, and no Steam
credential is ever collected (identity uses Steam's own OpenID flow, which returns
only your SteamID). A future `delete-all` command purges all local data.

## Documentation

- [Safety boundary (ADR-0001)](docs/adr/0001-safety-boundary.md)
- [Data sources & provenance](docs/data-sources.md)
- [Optimizer model](docs/optimizer-model.md)
- [Market model](docs/market-model.md)
- [Backlog / issues](docs/backlog.md)

## License

MIT.
