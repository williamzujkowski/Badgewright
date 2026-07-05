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
cards_app = typer.Typer(help="Discover the full card list for a game's badge set.")
app.add_typer(catalog_app, name="catalog")
app.add_typer(inventory_app, name="inventory")
app.add_typer(badges_app, name="badges")
app.add_typer(prices_app, name="prices")
app.add_typer(report_app, name="report")
app.add_typer(market_app, name="market")
app.add_typer(cards_app, name="cards")


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
    budget: float | None = typer.Option(None, help="Spend cap for the plan (e.g. 50 = $50)."),
    current_level: int | None = typer.Option(None, help="Your current Steam account level."),
    target_level: int | None = typer.Option(None, help="Desired Steam account level."),
    badge_level: int = typer.Option(5, help="Target level for each game badge (1-5)."),
    data_dir: str | None = typer.Option(None, help="Override the local data directory."),
) -> None:
    """Compute the cheapest badge-completion plan (greedy by cost-per-XP)."""
    from decimal import ROUND_HALF_UP, Decimal

    from .config import MAX_NORMAL_BADGE_LEVEL, account_xp_between
    from .db import Store
    from .models import Money
    from .optimize import build_plan, compute_costs

    if not (1 <= badge_level <= MAX_NORMAL_BADGE_LEVEL):
        typer.secho(f"--badge-level must be 1..{MAX_NORMAL_BADGE_LEVEL}.", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    if budget is not None and budget < 0:
        typer.secho("--budget must be >= 0.", fg=typer.colors.RED)
        raise typer.Exit(code=2)

    settings = Settings.resolve(data_dir=data_dir, currency=None)
    currency = settings.currency
    target_xp: int | None = None
    if target_level is not None:
        if current_level is None:
            typer.secho("--target-level also needs --current-level.", fg=typer.colors.RED)
            raise typer.Exit(code=2)
        target_xp = account_xp_between(current_level, target_level)
    elif current_level is not None:
        typer.secho(
            "--current-level is only used with --target-level; ignoring it.",
            fg=typer.colors.YELLOW,
        )

    # Exact currency conversion (avoid binary float drift on e.g. 1.005).
    money_budget = (
        Money(int((Decimal(str(budget)) * 100).to_integral_value(ROUND_HALF_UP)), currency)
        if budget is not None
        else None
    )
    with Store(settings.db_path) as store:
        report = compute_costs(store, target_level=badge_level, currency=currency)
        plan = build_plan(report, budget=money_budget, target_xp=target_xp)

    if (
        not plan.chosen
        and not plan.ready_to_craft
        and not plan.incomplete
        and not plan.skipped_over_budget
    ):
        typer.echo("No badges to plan yet. Import a catalog, inventory, and prices first.")
        return

    chosen_appids = {b.appid for b in plan.chosen}
    free_now = [b for b in plan.ready_to_craft if b.appid not in chosen_appids]
    if free_now:
        typer.secho("Ready to craft now (you own a full set — free):", bold=True)
        for b in free_now:
            typer.echo(f"  appid {b.appid}: craft the next level now, no purchase")
    typer.secho(f"\nRecommended purchases (cheapest cost-per-XP, {currency}):", bold=True)
    if not plan.chosen:
        typer.echo("  (none fit the constraints)")
    for b in plan.chosen:
        cost = b.known_cost.amount if b.known_cost else 0
        ready = " — own a full set; next level free, rest priced" if b.ready_to_craft else ""
        typer.echo(
            f"  appid {b.appid}: {b.crafts_needed} level(s), +{b.expected_xp} XP, "
            f"~{cost:.2f} {currency} [{b.confidence.value}]{ready}"
        )
    typer.secho(f"\nTotal: {plan.total_cost.amount:.2f} {currency}, +{plan.total_xp} XP", bold=True)
    if plan.skipped_over_budget:
        typer.secho(
            f"\n{len(plan.skipped_over_budget)} badge(s) skipped — over budget:",
            fg=typer.colors.YELLOW,
        )
        for b in plan.skipped_over_budget:
            cost = b.known_cost.amount if b.known_cost else 0
            typer.echo(f"  appid {b.appid}: ~{cost:.2f} {currency} for +{b.expected_xp} XP")
    if plan.budget is not None and plan.budget_remaining is not None:
        typer.echo(f"Budget remaining: {plan.budget_remaining.amount:.2f} {currency}")
        typer.secho(
            "(Greedy by cost-per-XP; near a tight budget the plan may be slightly sub-optimal.)",
            dim=True,
        )
    if target_xp is not None:
        status = "reached" if plan.target_reached else "NOT reached"
        typer.echo(f"XP target {target_xp} {status}.")
    for note in plan.notes:
        typer.secho(note, fg=typer.colors.YELLOW)
    if plan.incomplete:
        typer.secho(
            f"\n{len(plan.incomplete)} badge(s) need card discovery/pricing before they "
            "can be costed (run inventory import + prices refresh).",
            fg=typer.colors.YELLOW,
        )
    typer.secho(
        "\nThis is a research plan. Buy cards manually in Steam; Badgewright never trades.",
        dim=True,
    )


@report_app.command("purchase-plan")
def report_purchase_plan(
    fmt: str = typer.Option("html", "--format", help="Output format: html or csv."),
    out: str = typer.Option(..., help="Output file path."),
    budget: float | None = typer.Option(None, help="Spend cap for the plan."),
    badge_level: int = typer.Option(5, help="Target level for each game badge (1-5)."),
    data_dir: str | None = typer.Option(None, help="Override the local data directory."),
) -> None:
    """Export the purchase plan for manual review (inert; no scripts, no actions)."""
    from decimal import ROUND_HALF_UP, Decimal

    from .config import MAX_NORMAL_BADGE_LEVEL
    from .db import Store
    from .models import Money
    from .optimize import build_plan, compute_costs
    from .reports import write_csv, write_html

    fmt = fmt.lower()
    if fmt not in {"csv", "html"}:
        typer.secho("--format must be 'csv' or 'html'.", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    if not (1 <= badge_level <= MAX_NORMAL_BADGE_LEVEL):
        typer.secho(f"--badge-level must be 1..{MAX_NORMAL_BADGE_LEVEL}.", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    if budget is not None and budget < 0:
        typer.secho("--budget must be >= 0.", fg=typer.colors.RED)
        raise typer.Exit(code=2)

    settings = Settings.resolve(data_dir=data_dir, currency=None)
    money_budget = (
        Money(int((Decimal(str(budget)) * 100).to_integral_value(ROUND_HALF_UP)), settings.currency)
        if budget is not None
        else None
    )
    with Store(settings.db_path) as store:
        report = compute_costs(store, target_level=badge_level, currency=settings.currency)
        plan = build_plan(report, budget=money_budget)
        if fmt == "csv":
            count = write_csv(plan, store, out)
        else:
            write_html(plan, store, out)
            count = len(plan.chosen)
    typer.secho(f"Wrote {fmt.upper()} plan ({count} rows) to {out}.", fg=typer.colors.GREEN)
    typer.secho("Open it for manual review; buy cards yourself in Steam.", dim=True)


@cards_app.command("discover")
def cards_discover(
    appid: int | None = typer.Option(None, help="Discover one app's cards."),
    all_apps: bool = typer.Option(False, "--all", help="Discover for all catalog badge sets."),
    online: bool = typer.Option(False, help="Allow network fetches (required)."),
    max_pages: int = typer.Option(5, help="Max search pages per app (politeness)."),
    data_dir: str | None = typer.Option(None, help="Override the local data directory."),
) -> None:
    """Enumerate a game's full trading-card list so its badge can be costed."""
    from .db import Store
    from .sources import card_discovery as cd
    from .sources.http_client import FetchError, RateLimited, SafeClient

    if not online:
        typer.secho("Card discovery needs the network: pass --online.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=2)
    if (appid is not None) == all_apps:  # exactly one of --appid / --all
        typer.secho("Provide exactly one of --appid or --all.", fg=typer.colors.RED)
        raise typer.Exit(code=2)

    settings = Settings.resolve(data_dir=data_dir)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with Store(settings.db_path) as store:
        sizes = {b.appid: b.set_size for b in store.list_badge_sets()}
        if all_apps:
            targets: list[int] = list(sizes)
        else:
            assert appid is not None  # guaranteed by the exactly-one check above
            if appid not in sizes:
                typer.secho(
                    f"appid {appid} has no badge set in the catalog; import the catalog first.",
                    fg=typer.colors.RED,
                )
                raise typer.Exit(code=2)
            targets = [appid]
        complete = partial = 0
        with SafeClient(min_interval_s=settings.min_request_interval_s) as client:
            try:
                for app_id in targets:
                    result = cd.import_cards(
                        store, client, app_id, sizes[app_id], max_pages=max_pages
                    )
                    complete += int(result.complete)
                    partial += int(not result.complete)
            except RateLimited as exc:
                typer.secho(f"Stopped (rate limited): {exc}", fg=typer.colors.RED)
                raise typer.Exit(code=1) from exc
            except (cd.CardDiscoveryError, FetchError) as exc:
                typer.secho(f"Discovery failed: {exc}", fg=typer.colors.RED)
                raise typer.Exit(code=1) from exc
    typer.secho(
        f"Discovered card lists: {complete} complete, {partial} partial/incomplete.",
        fg=typer.colors.GREEN,
    )


@market_app.command("scan-weakness")
def market_scan_weakness(
    top: int = typer.Option(20, help="Number of results."),
    min_volume: int = typer.Option(5, help="Volume below this is flagged low-confidence."),
    data_dir: str | None = typer.Option(None, help="Override the local data directory."),
) -> None:
    """Research: rank cards by liquidity-weighted price-weakness signals. Never trades."""
    from .analytics import scan_weakness
    from .db import Store

    if top <= 0:
        typer.secho("--top must be positive.", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    if min_volume < 1:
        typer.secho("--min-volume must be >= 1.", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    settings = Settings.resolve(data_dir=data_dir, currency=None)
    with Store(settings.db_path) as store:
        rows = scan_weakness(store, currency=settings.currency, min_volume=min_volume, top=top)
    if not rows:
        typer.echo("No priced cards to scan yet. Run: sbo prices refresh --online")
        return
    typer.secho(f"Price-weakness research ({settings.currency}) — NOT trading advice:", bold=True)
    for r in rows:
        low = f"{r.lowest.amount:.2f}" if r.lowest else "n/a"
        typer.echo(
            f"  {r.appid} {r.market_hash_name}: lowest {low}, vol {r.volume or 0}, "
            f"score {r.score:.2f} [{r.confidence.value}] — {'; '.join(r.signals) or 'ok'}"
        )
    typer.secho("\nResearch only. Buy/sell decisions are yours, made manually in Steam.", dim=True)


@market_app.command("scan-sets")
def market_scan_sets(
    sort: str = typer.Option("cheapest", help="Sort: cheapest or dominance."),
    data_dir: str | None = typer.Option(None, help="Override the local data directory."),
) -> None:
    """Research: set-level cost and one-card-dominates signals. Never trades."""
    from .analytics import scan_sets
    from .db import Store

    if sort not in {"cheapest", "dominance"}:
        typer.secho("--sort must be 'cheapest' or 'dominance'.", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    settings = Settings.resolve(data_dir=data_dir, currency=None)
    with Store(settings.db_path) as store:
        sets = scan_sets(store, currency=settings.currency)
    complete = [s for s in sets if s.complete and s.total_cost is not None]
    if sort == "dominance":
        complete.sort(key=lambda s: s.card_dominance or 0, reverse=True)
    else:
        complete.sort(key=lambda s: s.total_cost.cents if s.total_cost else 0)
    if not complete:
        typer.echo("No fully-known, priced sets yet. Run: sbo cards discover + prices refresh.")
        return
    typer.secho(f"Set-level research ({settings.currency}) — NOT trading advice:", bold=True)
    for s in complete:
        total = f"{s.total_cost.amount:.2f}" if s.total_cost else "n/a"
        dom = f"{s.card_dominance * 100:.0f}%" if s.card_dominance is not None else "n/a"
        typer.echo(
            f"  appid {s.appid}: full set {total}, top card {dom} of cost"
            f"{' — ' + '; '.join(s.signals) if s.signals else ''}"
        )
    typer.secho("\nResearch only.", dim=True)


if __name__ == "__main__":  # pragma: no cover
    app()
