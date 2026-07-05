"""Badgewright command-line interface.

The command surface mirrors the plan's sketch (``sbo init``, ``catalog import``,
``inventory import``, ``optimize``, ``report``, ``market ...``). Only the pieces
implemented in the current milestone do real work; the rest are registered stubs
that fail loudly with the milestone they belong to, so the wiring is complete and
discoverable (``--help`` lists everything) without pretending to be finished.

Nothing in this CLI can operate a Steam account — see :mod:`steam_badge_optimizer.safety`.
"""

from __future__ import annotations

import typer

from . import __version__
from .config import (
    MAX_NORMAL_BADGE_LEVEL,
    STEAM_CARDS_CONTEXTID,
    STEAM_COMMUNITY_APPID,
    XP_PER_BADGE_LEVEL,
    Settings,
)
from .safety import ALLOWED_HOSTS, ALLOWED_METHODS

app = typer.Typer(
    name="steam-badge-optimizer",
    help="Badgewright: a local, read-only Steam badge optimizer. It plans; it never automates.",
    no_args_is_help=True,
    add_completion=False,
)
catalog_app = typer.Typer(help="Import and inspect the trading-card catalog.")
inventory_app = typer.Typer(help="Import your card inventory (file or public read).")
badges_app = typer.Typer(help="Import your badge progress.")
prices_app = typer.Typer(help="Refresh cached market prices (network is opt-in).")
report_app = typer.Typer(help="Export purchase plans (CSV / HTML).")
market_app = typer.Typer(help="Market-intelligence research (never trades).")
app.add_typer(catalog_app, name="catalog")
app.add_typer(inventory_app, name="inventory")
app.add_typer(badges_app, name="badges")
app.add_typer(prices_app, name="prices")
app.add_typer(report_app, name="report")
app.add_typer(market_app, name="market")


def _not_yet(feature: str, milestone: str) -> None:
    typer.secho(
        f"'{feature}' is not implemented yet (planned for {milestone}).",
        fg=typer.colors.YELLOW,
    )
    raise typer.Exit(code=2)


@app.command()
def version() -> None:
    """Print the Badgewright version."""
    typer.echo(f"steam-badge-optimizer {__version__}")


@app.command("safety")
def safety_boundary() -> None:
    """Print the read-only safety boundary this tool enforces."""
    typer.secho("Badgewright is a local analytics & planning tool.", bold=True)
    typer.echo("It does NOT buy, sell, trade, craft, list, idle, or automate any Steam action.")
    typer.echo(f"  Permitted HTTP methods : {', '.join(sorted(ALLOWED_METHODS))}")
    typer.echo(f"  Read-only hosts        : {', '.join(sorted(ALLOWED_HOSTS))}")
    typer.echo("  See docs/adr/0001-safety-boundary.md for the full rationale.")


@app.command("steamid")
def steamid_resolve(
    value: str = typer.Argument(..., help="SteamID64, profile URL, or vanity name."),
    online: bool = typer.Option(False, help="Allow a network lookup for vanity names."),
) -> None:
    """Resolve a SteamID64 from an id, profile URL, or vanity name (read-only)."""
    from .sources.http_client import SafeClient
    from .sources.steamid import SteamIdError, parse_offline, resolve_steamid

    offline = parse_offline(value)
    if offline is not None:
        typer.echo(str(offline))
        return
    if not online:
        typer.secho(
            "That looks like a vanity name; resolving it needs the network (--online).",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=2)
    try:
        with SafeClient() as client:
            typer.echo(str(resolve_steamid(value, client)))
    except SteamIdError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc


@app.command()
def init(
    data_dir: str | None = typer.Option(None, help="Override the local data directory."),
) -> None:
    """Create the local data directory (default storage is local SQLite)."""
    settings = Settings.resolve(data_dir=data_dir)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    typer.secho(
        f"Initialized Badgewright data directory: {settings.data_dir}", fg=typer.colors.GREEN
    )
    typer.echo(f"  Database (created on first import): {settings.db_path}")
    typer.echo(
        f"  Community appid/context for cards : {STEAM_COMMUNITY_APPID}/{STEAM_CARDS_CONTEXTID}"
    )
    typer.echo(
        f"  XP per badge level / max level    : {XP_PER_BADGE_LEVEL} / {MAX_NORMAL_BADGE_LEVEL}"
    )


@catalog_app.command("import")
def catalog_import(
    source: str = typer.Option("steam-badges-db", help="Catalog source name."),
    file: str | None = typer.Option(None, help="Path to a local badges.json (offline)."),
    url: str | None = typer.Option(None, help="Override the catalog URL (needs --online)."),
    online: bool = typer.Option(False, help="Allow a network fetch (default is offline)."),
    data_dir: str | None = typer.Option(None, help="Override the local data directory."),
) -> None:
    """Import the public card-set catalog (appid, name, set size)."""
    from .db import Store
    from .sources import steam_badges_db as sbd
    from .sources.http_client import SafeClient

    if source != "steam-badges-db":
        typer.secho(f"Unknown catalog source {source!r}.", fg=typer.colors.RED)
        raise typer.Exit(code=2)

    settings = Settings.resolve(data_dir=data_dir)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with Store(settings.db_path) as store:
        if file:
            result = sbd.import_from_file(store, file)
        elif online:
            with SafeClient(min_interval_s=settings.min_request_interval_s) as client:
                result = sbd.import_from_url(store, client, url or sbd.DEFAULT_BADGES_URL)
        else:
            typer.secho(
                "Offline by default: pass --file <badges.json>, or --online to fetch.",
                fg=typer.colors.YELLOW,
            )
            raise typer.Exit(code=2)
    typer.secho(
        f"Imported {result.imported} apps ({result.skipped} skipped) into {settings.db_path}.",
        fg=typer.colors.GREEN,
    )


@catalog_app.command("list")
def catalog_list(
    limit: int = typer.Option(20, help="Max rows to show."),
    data_dir: str | None = typer.Option(None, help="Override the local data directory."),
) -> None:
    """List imported apps that have trading-card badge sets."""
    from .db import Store

    settings = Settings.resolve(data_dir=data_dir)
    with Store(settings.db_path) as store:
        apps = store.list_apps()
    if not apps:
        typer.echo("No catalog imported yet. Run: sbo catalog import --file badges.json")
        return
    for app in apps[:limit]:
        typer.echo(f"  {app.appid:>8}  {app.name}")
    if len(apps) > limit:
        typer.echo(f"  ... and {len(apps) - limit} more")


@inventory_app.command("import")
def inventory_import(
    file: str | None = typer.Option(None, help="Path to an exported inventory JSON (offline)."),
    steamid: str | None = typer.Option(None, help="SteamID64 / URL / vanity (needs --online)."),
    online: bool = typer.Option(False, help="Allow a network fetch of a public inventory."),
    max_pages: int = typer.Option(5, help="Max inventory pages to fetch (politeness bound)."),
    data_dir: str | None = typer.Option(None, help="Override the local data directory."),
) -> None:
    """Import your trading-card inventory (appid 753 / context 6)."""
    from .db import Store
    from .sources import steam_inventory as si
    from .sources.http_client import FetchError, SafeClient
    from .sources.steamid import SteamIdError, resolve_steamid

    settings = Settings.resolve(data_dir=data_dir)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with Store(settings.db_path) as store:
        try:
            if file:
                result = si.import_from_file(store, file)
            elif steamid and online:
                with SafeClient(min_interval_s=settings.min_request_interval_s) as client:
                    id64 = resolve_steamid(steamid, client)
                    result = si.import_inventory(store, client, id64, max_pages=max_pages)
            elif steamid and not online:
                typer.secho("Fetching an inventory needs --online.", fg=typer.colors.YELLOW)
                raise typer.Exit(code=2)
            else:
                typer.secho(
                    "Provide --file <inventory.json>, or --steamid <id> --online.",
                    fg=typer.colors.YELLOW,
                )
                raise typer.Exit(code=2)
        except si.PrivateInventoryError as exc:
            typer.secho(str(exc), fg=typer.colors.RED)
            raise typer.Exit(code=1) from exc
        except (si.InventoryParseError, SteamIdError, FetchError) as exc:
            # FetchError covers RateLimited (429) and HTTPStatusError (404/500/etc.).
            typer.secho(f"Import failed: {exc}", fg=typer.colors.RED)
            raise typer.Exit(code=1) from exc
    typer.secho(
        f"Imported {len(result.cards)} cards ({result.skipped} skipped of "
        f"{result.total_assets} assets) into {settings.db_path}.",
        fg=typer.colors.GREEN,
    )
    if result.truncated:
        typer.secho(
            f"Warning: inventory was truncated at {max_pages} pages — some cards are "
            "missing. Re-run with a higher --max-pages, or use --file with an export.",
            fg=typer.colors.YELLOW,
        )


@badges_app.command("import")
def badges_import(file: str = typer.Option(..., help="Path to exported badge progress.")) -> None:
    """Import badge progress (level 0-5 per game, foil status)."""
    _not_yet("badges import", "Milestone 2")


@prices_app.command("refresh")
def prices_refresh(
    appid: int | None = typer.Option(None, help="Price a single item (with --name)."),
    name: str | None = typer.Option(None, help="Market hash name (with --appid)."),
    limit: int = typer.Option(200, help="Max known cards to refresh."),
    force: bool = typer.Option(False, help="Refetch even if a cached price is still fresh."),
    online: bool = typer.Option(False, help="Allow network fetches (required)."),
    data_dir: str | None = typer.Option(None, help="Override the local data directory."),
) -> None:
    """Refresh cached market prices (opt-in network; caches with TTL; surfaces 429)."""
    from .db import Store
    from .models import MarketItem
    from .sources import steam_market as sm
    from .sources.http_client import RateLimited, SafeClient

    settings = Settings.resolve(data_dir=data_dir)
    if not online:
        typer.secho("Refreshing prices needs the network: pass --online.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=2)
    if (appid is None) ^ (name is None):
        typer.secho("Provide both --appid and --name, or neither.", fg=typer.colors.RED)
        raise typer.Exit(code=2)

    with Store(settings.db_path) as store:
        items = (
            [MarketItem(appid=appid, market_hash_name=name)]
            if appid is not None and name is not None
            else store.list_card_items()[:limit]
        )
        if not items:
            typer.echo("No cards known yet — import inventory or discover card names first.")
            return
        with SafeClient(min_interval_s=settings.min_request_interval_s) as client:
            try:
                result = sm.refresh_prices(store, client, items, settings.currency, force=force)
            except RateLimited as exc:
                typer.secho(f"Stopped: {exc}", fg=typer.colors.RED)
                raise typer.Exit(code=1) from exc
    typer.secho(
        f"Prices: {result.fetched} fetched, {result.skipped_cached} cached, "
        f"{result.failed} unavailable.",
        fg=typer.colors.GREEN,
    )


@app.command()
def optimize(
    budget: float | None = typer.Option(None, help="Spend cap for the plan."),
    current_level: int | None = typer.Option(None, help="Your current Steam level."),
    target_level: int | None = typer.Option(None, help="Desired Steam level."),
    exclude_foil: bool = typer.Option(True, help="Exclude foil badges (thin markets)."),
) -> None:
    """Compute the cheapest badge-completion plan (greedy; provably optimal for uniform XP)."""
    _not_yet("optimize", "Milestone 4")


@report_app.command("purchase-plan")
def report_purchase_plan(
    fmt: str = typer.Option("html", "--format", help="Output format: html or csv."),
    out: str = typer.Option(..., help="Output file path."),
) -> None:
    """Export the purchase plan for manual review (inert; no scripts, no actions)."""
    _not_yet("report purchase-plan", "Milestone 4")


@market_app.command("scan-weakness")
def market_scan_weakness(top: int = typer.Option(50, help="Number of results.")) -> None:
    """Research: rank cards by price-weakness signals. Never trades."""
    _not_yet("market scan-weakness", "Milestone 5")


if __name__ == "__main__":  # pragma: no cover
    app()
