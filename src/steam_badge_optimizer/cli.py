"""Badgewright command-line interface.

The command surface mirrors the plan's sketch (``sbo init``, ``catalog import``,
``inventory import``, ``optimize``, ``report``, ``market ...``). Only the pieces
implemented in the current milestone do real work; the rest are registered stubs
that fail loudly with the milestone they belong to, so the wiring is complete and
discoverable (``--help`` lists everything) without pretending to be finished.

Nothing in this CLI can operate a Steam account — see :mod:`steam_badge_optimizer.safety`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from .analytics import BadgeSetCost
    from .db import Store

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


@app.command("delete-all")
def delete_all(
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
    data_dir: str | None = typer.Option(None, help="Override the local data directory."),
) -> None:
    """Delete ALL local Badgewright data (the SQLite database and its journal files)."""
    from pathlib import Path

    settings = Settings.resolve(data_dir=data_dir)
    db = settings.db_path
    # The database plus any SQLite journal/WAL sidecars — a leftover SteamID or price in
    # the -wal file would be a silent privacy failure, so purge them too.
    targets = [Path(f"{db}{suffix}") for suffix in ("", "-wal", "-shm", "-journal")]
    present = [p for p in targets if p.is_file()]

    if not present:
        typer.echo(f"No local data to delete at {settings.data_dir}.")
        return
    if not yes:
        typer.secho(
            f"This will permanently delete all local Badgewright data in {settings.data_dir}:",
            fg=typer.colors.YELLOW,
        )
        for p in present:
            typer.echo(f"  {p}")
        if not typer.confirm("Continue?"):
            typer.echo("Aborted.")
            raise typer.Exit(code=1)

    for p in present:
        p.unlink()
    typer.secho(f"Deleted {len(present)} file(s). Your local data is gone.", fg=typer.colors.GREEN)
    typer.secho(
        "Note: purchase-plan reports you exported with `report --out <path>` are not "
        "tracked here — delete those files yourself if you want them gone.",
        dim=True,
    )


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
    if result.holdings:
        typer.echo(
            f"Also retained {len(result.holdings)} non-card holding(s) "
            "(booster packs, gems, sacks, other)."
        )
    if result.truncated:
        typer.secho(
            f"Warning: inventory was truncated at {max_pages} pages — some cards are "
            "missing. Re-run with a higher --max-pages, or use --file with an export.",
            fg=typer.colors.YELLOW,
        )


@inventory_app.command("value")
def inventory_value(
    top: int = typer.Option(20, help="How many holdings to list (totals cover everything)."),
    data_dir: str | None = typer.Option(None, help="Override the local data directory."),
) -> None:
    """Value your held cards + gems/boosters/other at the current market (research only).

    Offline: uses cached prices. Seed them first with `sbo prices refresh --online` (and
    `sbo market gems --online --confirm` for the gem price). Values each holding at its
    latest lowest ask (gems marked to the Sack-of-Gems price); unpriced holdings are
    flagged, and the total is a floor over the priced ones.
    """
    if top < 1:
        typer.secho("--top must be >= 1.", fg=typer.colors.RED)
        raise typer.Exit(code=2)

    from .analytics import value_inventory
    from .db import Store

    settings = Settings.resolve(data_dir=data_dir, currency=None)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with Store(settings.db_path) as store:
        names = {a.appid: a.name for a in store.list_apps()}
        valuation = value_inventory(store, currency=settings.currency, top=top)

    if not valuation.holdings:
        typer.echo(
            "No inventory to value. Import it first:\n"
            "  sbo inventory import --steamid <you> --online"
        )
        raise typer.Exit(code=1)

    typer.secho(
        f"Inventory value (priced floor): {valuation.total_value.amount:.2f} {valuation.currency}",
        bold=True,
    )
    typer.echo(
        f"  {valuation.priced_count} holding(s) priced, "
        f"{valuation.unpriced_count} unpriced"
        + ("  — seed prices with `sbo prices refresh --online`" if valuation.unpriced_count else "")
    )
    for h in valuation.holdings:
        label = names.get(h.appid, f"App {h.appid}") if h.kind == "card" else h.market_hash_name
        foil = " (foil)" if h.is_foil else ""
        tag = "" if h.kind == "card" else f" [{h.kind}]"
        if h.line_value is not None:
            at = f" @ {h.unit_price.amount:.2f}" if h.unit_price is not None else ""
            typer.echo(f"  {h.line_value.amount:>8.2f}  {h.quantity:>5}x{at}  {label}{foil}{tag}")
        else:
            typer.echo(
                f"  {'—':>8}  {h.quantity:>5}x  {label}{foil}{tag}  [{'; '.join(h.signals)}]"
            )
    typer.secho(
        "\nResearch only. Values are current market floors; sell/hold decisions are yours.",
        dim=True,
    )


@badges_app.command("import")
def badges_import(
    file: str | None = typer.Option(None, help="Path to a saved GetBadges JSON (offline)."),
    steamid: str | None = typer.Option(None, help="SteamID64 / URL / vanity (needs --online)."),
    online: bool = typer.Option(False, help="Fetch via the Steam Web API (needs a key)."),
    data_dir: str | None = typer.Option(None, help="Override the local data directory."),
) -> None:
    """Import per-game badge levels so plans use your real levels, not assumed 0."""
    from .db import Store
    from .sources import badge_progress as bp
    from .sources.http_client import FetchError, SafeClient
    from .sources.steamid import SteamIdError, resolve_steamid

    settings = Settings.resolve(data_dir=data_dir)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with Store(settings.db_path) as store:
        try:
            if file:
                result = bp.import_from_file(store, file)
            elif steamid and online:
                api_key = bp.api_key_from_env()
                if not api_key:
                    raise bp.MissingApiKeyError()
                with SafeClient(min_interval_s=settings.min_request_interval_s) as client:
                    id64 = resolve_steamid(steamid, client)
                    result = bp.import_from_api(store, client, id64, api_key)
            elif steamid and not online:
                typer.secho("Fetching badge levels needs --online.", fg=typer.colors.YELLOW)
                raise typer.Exit(code=2)
            else:
                typer.secho(
                    "Provide --file <getbadges.json>, or --steamid <id> --online.",
                    fg=typer.colors.YELLOW,
                )
                raise typer.Exit(code=2)
        except bp.MissingApiKeyError as exc:
            typer.secho(str(exc), fg=typer.colors.RED)
            raise typer.Exit(code=1) from exc
        except (bp.BadgeProgressError, SteamIdError, FetchError) as exc:
            typer.secho(f"Import failed: {exc}", fg=typer.colors.RED)
            raise typer.Exit(code=1) from exc
    typer.secho(
        f"Imported badge levels for {result.imported} game(s) into {settings.db_path}.",
        fg=typer.colors.GREEN,
    )


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


@report_app.command("cheapest-badges")
def report_cheapest_badges(
    out: str = typer.Option(..., help="Output file path (.csv or .html)."),
    top: int = typer.Option(50, help="How many cheapest badges to export."),
    min_listings: int = typer.Option(2, help="Min asks/volume per card to count as liquid."),
    data_dir: str | None = typer.Option(None, help="Override the local data directory."),
) -> None:
    """Export the cheapest-badges ranking to CSV or inert HTML for review. Never trades."""
    from datetime import UTC, datetime

    from .analytics import rank_cheapest_badges
    from .db import Store
    from .reports import write_cheapest

    if top <= 0:
        typer.secho("--top must be positive.", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    if not out.lower().endswith((".csv", ".html", ".htm")):
        typer.secho("--out must end in .csv or .html.", fg=typer.colors.RED)
        raise typer.Exit(code=2)

    settings = Settings.resolve(data_dir=data_dir, currency=None)
    with Store(settings.db_path) as store:
        badges = rank_cheapest_badges(
            store, currency=settings.currency, min_listings=min_listings, top=top
        )
        names = {a.appid: a.name for a in store.list_apps()}
    count = write_cheapest(badges, names, out, currency=settings.currency, now=datetime.now(UTC))
    if count == 0:
        typer.secho(
            "Wrote an empty report — no fully-known, priced badges yet "
            "(run a sweep / plan-cheapest first).",
            fg=typer.colors.YELLOW,
        )
    else:
        typer.secho(f"Wrote {count} cheapest badge(s) to {out}.", fg=typer.colors.GREEN)
    typer.secho("Research only. Buy cards manually in Steam.", dim=True)


@report_app.command("inventory-value")
def report_inventory_value(
    out: str = typer.Option(..., help="Output file path (.csv or .html)."),
    top: int = typer.Option(200, help="How many holdings to export (total covers everything)."),
    data_dir: str | None = typer.Option(None, help="Override the local data directory."),
) -> None:
    """Export your inventory valuation to CSV or inert HTML for review. Never trades."""
    from datetime import UTC, datetime

    from .analytics import value_inventory
    from .db import Store
    from .reports import write_inventory_value

    if top <= 0:
        typer.secho("--top must be positive.", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    if not out.lower().endswith((".csv", ".html", ".htm")):
        typer.secho("--out must end in .csv or .html.", fg=typer.colors.RED)
        raise typer.Exit(code=2)

    settings = Settings.resolve(data_dir=data_dir, currency=None)
    with Store(settings.db_path) as store:
        valuation = value_inventory(store, currency=settings.currency, top=top)
        names = {a.appid: a.name for a in store.list_apps()}
    count = write_inventory_value(valuation, names, out, now=datetime.now(UTC))
    if count == 0:
        typer.secho(
            "Wrote an empty report — no held cards to value (import your inventory first).",
            fg=typer.colors.YELLOW,
        )
    else:
        typer.secho(
            f"Wrote {count} holding(s), total {valuation.total_value.amount:.2f} "
            f"{valuation.currency} (priced floor), to {out}.",
            fg=typer.colors.GREEN,
        )
    typer.secho("Research only. Sell/hold decisions are yours, made manually in Steam.", dim=True)


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


@market_app.command("anomalies")
def market_anomalies(
    top: int = typer.Option(20, help="Number of results."),
    data_dir: str | None = typer.Option(None, help="Override the local data directory."),
) -> None:
    """Research: flag unusual price movements from stored history. Never trades."""
    from .analytics import detect_anomalies
    from .db import Store

    if top <= 0:
        typer.secho("--top must be positive.", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    settings = Settings.resolve(data_dir=data_dir, currency=None)
    with Store(settings.db_path) as store:
        anomalies = detect_anomalies(store, currency=settings.currency, top=top)
    if not anomalies:
        typer.echo(
            "No anomalies (or not enough price history). Refresh prices over time to "
            "build history: sbo prices refresh --online"
        )
        return
    typer.secho(f"Price anomalies ({settings.currency}) — NOT trading advice:", bold=True)
    for a in anomalies:
        typer.echo(
            f"  {a.appid} {a.market_hash_name}: {a.kind.value} "
            f"(latest {a.latest.amount:.2f} vs {a.reference.amount:.2f}) "
            f"[{a.confidence.value}] — {a.caveat}"
        )
    typer.secho("\nResearch only. Anomalies are speculative; verify before any action.", dim=True)


@market_app.command("cheapest-badges")
def market_cheapest_badges(
    top: int = typer.Option(20, help="Number of results."),
    min_listings: int = typer.Option(2, help="Min asks per card to count a set as liquid."),
    enrich_top: int = typer.Option(
        0, help="Re-price this many top candidates via priceoverview for real 24h volume."
    ),
    online: bool = typer.Option(False, help="Allow network (required with --confirm to enrich)."),
    confirm: bool = typer.Option(False, "--confirm", help="Acknowledge the enrichment fetch."),
    data_dir: str | None = typer.Option(None, help="Override the local data directory."),
) -> None:
    """Rank the cheapest badges to make from scratch, from cached prices. Never trades.

    ``--enrich-top K`` (opt-in, needs --online + --confirm) re-prices the top K candidates
    via priceoverview to confirm liquidity with real 24h volume — bounded and rate-polite.
    """
    from .analytics import rank_cheapest_badges
    from .db import Store

    if top <= 0:
        typer.secho("--top must be positive.", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    if enrich_top < 0:
        typer.secho("--enrich-top must be >= 0.", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    if enrich_top > 0 and not (online and confirm):
        typer.secho(
            "--enrich-top fetches from Steam, so it needs BOTH --online and --confirm.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=2)

    settings = Settings.resolve(data_dir=data_dir, currency=None)
    with Store(settings.db_path) as store:
        ranked = rank_cheapest_badges(
            store, currency=settings.currency, min_listings=min_listings, top=top
        )
        if enrich_top > 0 and ranked:
            _enrich_candidates(store, ranked[:enrich_top], settings)
            ranked = rank_cheapest_badges(
                store, currency=settings.currency, min_listings=min_listings, top=top
            )
        names = {a.appid: a.name for a in store.list_apps()}
    if not ranked:
        typer.echo(
            "No fully-known, priced badge sets yet. Discover + price cards first "
            "(cards discover / prices refresh), or run a bulk market sweep."
        )
        return
    typer.secho(f"Cheapest badges to make ({settings.currency}) — NOT trading advice:", bold=True)
    for b in ranked:
        game = names.get(b.appid, f"App {b.appid}")
        liq = "" if b.liquid else "  ⚠ thin"
        typer.echo(
            f"  {b.total_cost.amount:>7.2f}  {game} (appid {b.appid}, {b.set_size} cards) "
            f"[{b.confidence.value}]{liq}" + (f" — {'; '.join(b.signals)}" if b.signals else "")
        )
    typer.secho("\nResearch only. Buy cards manually in Steam.", dim=True)


SWEEP_MIN_INTERVAL_S = 4.5  # polite floor for the bulk sweep (~1 req / 4-5s) — never faster


def _enrich_candidates(store: Store, badges: list[BadgeSetCost], settings: Settings) -> None:
    """Re-price the given badges' cards via priceoverview to add real 24h volume/median.

    Bounded (only these badges' cards), rate-polite, hard-stops on rate-limit. The cost
    basis stays the current lowest ask — enrichment only adds a truer liquidity signal
    (volume) and never presents a price a buyer couldn't fill.
    """
    from .models import MarketItem
    from .sources.http_client import RateLimited, SafeClient
    from .sources.steam_market import refresh_prices

    items = [
        MarketItem(appid=b.appid, market_hash_name=card.market_hash_name)
        for b in badges
        for card in store.cards_for_app(b.appid, include_foil=False)
    ]
    if not items:
        return
    interval = max(settings.min_request_interval_s, SWEEP_MIN_INTERVAL_S)
    typer.echo(f"Enriching {len(badges)} candidate(s) with 24h volume via priceoverview...")
    with SafeClient(min_interval_s=interval) as client:
        try:
            refresh_prices(store, client, items, settings.currency, force=True)
        except RateLimited:
            typer.secho("Steam rate-limited the enrichment; showing what we have.", fg="yellow")


@market_app.command("sweep")
def market_sweep_cmd(
    online: bool = typer.Option(False, help="Allow network access (required, with --confirm)."),
    confirm: bool = typer.Option(
        False, "--confirm", help="Acknowledge this fetches many pages from Steam."
    ),
    max_pages: int = typer.Option(20, help="Hard cap on pages fetched (100 cards/page)."),
    until_sets: int | None = typer.Option(
        None, help="Stop early once this many badge sets are fully priced."
    ),
    data_dir: str | None = typer.Option(None, help="Override the local data directory."),
) -> None:
    """Bounded, opt-in bulk price sweep (cheapest-first). Reads public listings; never trades.

    Off by default: you must pass BOTH --online and --confirm. It is rate-polite,
    resumable (re-run to continue), and stops hard if Steam rate-limits it.
    """
    if max_pages < 1:
        typer.secho("--max-pages must be >= 1.", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    if not (online and confirm):
        typer.secho(
            "The bulk market sweep fetches many pages from Steam, so it is OFF by default.",
            fg=typer.colors.YELLOW,
        )
        typer.echo(
            "Re-run with BOTH --online and --confirm to proceed. It is cheapest-first, "
            "rate-polite (~1 req/5s), bounded by --max-pages, and resumable."
        )
        raise typer.Exit(code=2)

    from .db import Store
    from .sources.http_client import SafeClient
    from .sources.market_sweep import StopReason, sweep_cheapest

    settings = Settings.resolve(data_dir=data_dir, currency=None)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    interval = max(settings.min_request_interval_s, SWEEP_MIN_INTERVAL_S)
    typer.echo(f"Sweeping cheapest-first at ~1 req/{interval:.0f}s (Ctrl-C is safe).")
    with (
        Store(settings.db_path) as store,
        SafeClient(min_interval_s=interval) as client,
    ):
        result = sweep_cheapest(
            store,
            client,
            settings.data_dir,
            currency=settings.currency,
            max_pages=max_pages,
            stop_after_complete_sets=until_sets,
            jitter_s=1.0,
        )
    typer.secho(
        f"Swept {result.pages_fetched} page(s); priced {result.cards_priced} card(s); "
        f"{result.complete_sets} badge set(s) now fully priced.",
        fg=typer.colors.GREEN,
    )
    typer.echo(f"Stopped: {result.stop_reason.value}.")
    if result.stop_reason is StopReason.RATE_LIMITED:
        typer.secho(
            "Steam rate-limited the sweep; progress is saved. Wait a while, then re-run to resume.",
            fg=typer.colors.YELLOW,
        )
    elif result.next_cursor is not None:
        typer.echo("More remains — re-run `sbo market sweep --online --confirm` to resume.")
    typer.secho("Then rank results with: sbo market cheapest-badges", dim=True)


@market_app.command("plan-cheapest")
def market_plan_cheapest(
    online: bool = typer.Option(False, help="Allow network access (required, with --confirm)."),
    confirm: bool = typer.Option(
        False, "--confirm", help="Acknowledge this fetches from Steam for a few games."
    ),
    max_games: int = typer.Option(5, help="How many candidate games to complete (bounded)."),
    top: int = typer.Option(15, help="How many cheapest badges to show."),
    min_listings: int = typer.Option(2, help="Min asks per card to count a set as liquid."),
    data_dir: str | None = typer.Option(None, help="Override the local data directory."),
) -> None:
    """Complete the most promising cheap candidate games, then rank cheapest badges.

    Off by default (needs --online and --confirm). Uses cached prices (seed them with
    `sbo market sweep`) to pick the games cheapest to finish, then discovers + prices just
    those sets — a small, bounded request budget. Reads public data; never trades.
    """
    if max_games < 1:
        typer.secho("--max-games must be >= 1.", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    if not (online and confirm):
        typer.secho("This fetches from Steam, so it is OFF by default.", fg=typer.colors.YELLOW)
        typer.echo(
            "Re-run with BOTH --online and --confirm. It completes only the --max-games "
            "cheapest candidate games (seed prices first with `sbo market sweep`)."
        )
        raise typer.Exit(code=2)

    from .analytics import rank_cheapest_badges, select_candidate_games
    from .db import Store
    from .models import MarketItem
    from .sources.card_discovery import CardDiscoveryError, import_cards
    from .sources.http_client import FetchError, RateLimited, SafeClient
    from .sources.steam_market import refresh_prices

    settings = Settings.resolve(data_dir=data_dir, currency=None)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    interval = max(settings.min_request_interval_s, SWEEP_MIN_INTERVAL_S)
    with Store(settings.db_path) as store:
        candidates = select_candidate_games(store, currency=settings.currency, max_games=max_games)
        if not candidates:
            typer.echo(
                "No candidate games with cached cheap prices. Seed some first:\n"
                "  sbo market sweep --online --confirm"
            )
            return
        set_sizes = {c.appid: c.set_size for c in candidates}
        names = {a.appid: a.name for a in store.list_apps()}
        typer.secho(
            f"Completing {len(candidates)} candidate game(s), cheapest-to-finish:", bold=True
        )
        for c in candidates:
            game = names.get(c.appid, f"App {c.appid}")
            typer.echo(
                f"  appid {c.appid} {game}: {c.priced_count}/{c.set_size} priced, "
                f"est ~{c.est_completion_cents / 100:.2f} {settings.currency} to complete"
            )

        with SafeClient(min_interval_s=interval) as client:
            for c in candidates:
                try:
                    import_cards(store, client, c.appid, set_sizes[c.appid])
                    items = [
                        MarketItem(appid=c.appid, market_hash_name=card.market_hash_name)
                        for card in store.cards_for_app(c.appid, include_foil=False)
                    ]
                    refresh_prices(store, client, items, settings.currency)
                except RateLimited:
                    typer.secho(
                        "Steam rate-limited us; stopping. Re-run later to continue.",
                        fg=typer.colors.YELLOW,
                    )
                    break
                except (CardDiscoveryError, FetchError) as exc:
                    # A delisted game (404), a transient blip, or a parse failure on one
                    # game shouldn't abort the whole pass — skip it and keep going.
                    typer.secho(f"  skipped appid {c.appid}: {exc}", fg=typer.colors.YELLOW)
                    continue

        ranked = rank_cheapest_badges(
            store, currency=settings.currency, min_listings=min_listings, top=top
        )
    if not ranked:
        typer.echo("No sets were completed this pass. Try a larger --max-games or sweep more.")
        return
    typer.secho(f"\nCheapest badges to make ({settings.currency}) — NOT trading advice:", bold=True)
    for b in ranked:
        game = names.get(b.appid, f"App {b.appid}")
        liq = "" if b.liquid else "  thin"
        typer.echo(
            f"  {b.total_cost.amount:>7.2f}  {game} (appid {b.appid}, {b.set_size} cards) "
            f"[{b.confidence.value}]{liq}"
        )
    typer.secho("\nResearch only. Buy cards manually in Steam.", dim=True)


@market_app.command("gems")
def market_gems_cmd(
    online: bool = typer.Option(False, help="Allow network access (with --confirm) to refresh."),
    confirm: bool = typer.Option(
        False, "--confirm", help="Acknowledge this fetches the Sack of Gems price from Steam."
    ),
    set_size: int | None = typer.Option(
        None, "--set-size", help="Also show the gem cost to craft a booster for an N-card set."
    ),
    data_dir: str | None = typer.Option(None, help="Override the local data directory."),
) -> None:
    """Value Steam gems in real money (via the Sack of Gems) + the gem cost to craft a booster.

    Reads the cached Sack-of-Gems price by default; pass BOTH --online and --confirm to
    refresh it. Reads public market data; never buys, sells, or crafts.
    """
    if set_size is not None and set_size < 1:
        typer.secho("--set-size must be >= 1.", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    if online and not confirm:
        typer.secho("Refreshing the gem price fetches from Steam, so it needs --confirm too.")
        raise typer.Exit(code=2)

    from .analytics import (
        booster_crafting_cost_gems,
        gem_value,
        gems_to_money,
        latest_sack_price,
        refresh_sack_price,
    )
    from .db import Store

    settings = Settings.resolve(data_dir=data_dir, currency=None)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with Store(settings.db_path) as store:
        if online and confirm:
            from .sources.http_client import RateLimited, SafeClient

            try:
                with SafeClient(min_interval_s=settings.min_request_interval_s) as client:
                    snap = refresh_sack_price(store, client, currency=settings.currency)
            except RateLimited:
                typer.secho(
                    "Steam rate-limited us; using the cached price.", fg=typer.colors.YELLOW
                )
                snap = latest_sack_price(store, currency=settings.currency)
        else:
            snap = latest_sack_price(store, currency=settings.currency)

        if snap is None or snap.lowest is None:
            typer.secho(
                f"No cached Sack-of-Gems price in {settings.currency}. Fetch it with:\n"
                "  sbo market gems --online --confirm",
                fg=typer.colors.YELLOW,
            )
            raise typer.Exit(code=1)

        value = gem_value(snap.lowest)
        per_1k = gems_to_money(1000, value)
        typer.secho(
            f"Sack of Gems (1000 gems): {snap.lowest.amount:.2f} {value.currency}", bold=True
        )
        typer.echo(
            f"  1 gem ≈ {value.cents_per_gem:.5f}¢ to buy; "
            f"1000 gems ≈ {per_1k.amount:.2f} {value.currency}"
        )
        sizes = [set_size] if set_size is not None else [5, 6, 8, 10, 15]
        typer.secho("Booster crafting cost (gems ≈ money to buy those gems):", bold=True)
        for n in sizes:
            gems = booster_crafting_cost_gems(n)
            typer.echo(
                f"  {n:>2}-card set: {gems:>4} gems ≈ "
                f"{gems_to_money(gems, value).amount:.2f} {value.currency}"
            )
    typer.secho(
        "\nResearch only. Gem/booster decisions are yours, made manually in Steam.", dim=True
    )


@market_app.command("booster-arbitrage")
def market_booster_arbitrage(
    online: bool = typer.Option(False, help="Allow network access (required, with --confirm)."),
    confirm: bool = typer.Option(
        False, "--confirm", help="Acknowledge this fetches booster prices from Steam."
    ),
    max_games: int = typer.Option(10, help="How many fully-priced games to check (bounded)."),
    min_listings: int = typer.Option(5, help="Min pack asks / card 24h sales to call it liquid."),
    data_dir: str | None = typer.Option(None, help="Override the local data directory."),
) -> None:
    """Flag Booster Packs cheaper than their card contents (research only; never trades).

    Off by default (needs --online and --confirm). Uses cached card floors (seed with
    `sbo market sweep` / `plan-cheapest`) to pick the cheapest fully-priced games, fetches
    just those games' booster prices (bounded by --max-games, rate-polite, 429-hard-stop),
    and reports where the pack looks cheaper than reselling its 3 cards.

    Note: this samples the CHEAPEST-badge games (the cheap tail), so margins are small and
    it won't surface high-value arbitrage in expensive-card sets. The estimate is an
    optimistic ceiling on a high-variance 3-card draw — never a guaranteed profit. It also
    refreshes each candidate's card 24h volumes (priceoverview) so resale demand — and thus
    the "ARB" (confirmed-liquid) flag — is determinable; a sweep alone only gives asks.
    """
    if max_games < 1:
        typer.secho("--max-games must be >= 1.", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    if not (online and confirm):
        typer.secho("This fetches from Steam, so it is OFF by default.", fg=typer.colors.YELLOW)
        typer.echo(
            "Re-run with BOTH --online and --confirm. It checks only the --max-games cheapest "
            "fully-priced games (seed card prices first with `sbo market sweep`)."
        )
        raise typer.Exit(code=2)

    from .analytics import rank_cheapest_badges, scan_booster_arbitrage
    from .db import Store
    from .models import MarketItem
    from .sources.booster_market import BoosterQuote, fetch_booster_price
    from .sources.http_client import FetchError, RateLimited, SafeClient
    from .sources.steam_market import refresh_prices

    settings = Settings.resolve(data_dir=data_dir, currency=None)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    interval = max(settings.min_request_interval_s, SWEEP_MIN_INTERVAL_S)
    with Store(settings.db_path) as store:
        candidates = rank_cheapest_badges(store, currency=settings.currency, top=max_games)
        if not candidates:
            typer.echo(
                "No fully-priced games cached. Seed card prices first:\n"
                "  sbo market sweep --online --confirm"
            )
            return
        names = {a.appid: a.name for a in store.list_apps()}
        quotes: dict[int, BoosterQuote] = {}
        typer.echo(
            f"Refreshing card volumes + booster prices for {len(candidates)} game(s) "
            f"at ~1 req/{interval:.0f}s."
        )
        with SafeClient(min_interval_s=interval) as client:
            for b in candidates:
                try:
                    # Enrich cards that have no 24h volume yet (priceoverview gives volume;
                    # sweep/search only give asks). Skip cards already enriched so repeat
                    # runs stay cheap. Then fetch the booster price.
                    need_volume = [
                        MarketItem(appid=b.appid, market_hash_name=c.market_hash_name)
                        for c in store.cards_for_app(b.appid, include_foil=False)
                        if not any(
                            s.volume is not None
                            for s in store.price_history(b.appid, c.market_hash_name)
                        )
                    ]
                    if need_volume:
                        refresh_prices(store, client, need_volume, settings.currency, force=True)
                    quote = fetch_booster_price(client, b.appid, settings.currency)
                except RateLimited:
                    typer.secho(
                        "Steam rate-limited us; stopping. Re-run later to continue.",
                        fg=typer.colors.YELLOW,
                    )
                    break
                except FetchError as exc:
                    typer.secho(f"  skipped appid {b.appid}: {exc}", fg=typer.colors.YELLOW)
                    continue
                if quote is not None:
                    quotes[b.appid] = quote

        results = scan_booster_arbitrage(
            store, quotes, currency=settings.currency, min_listings=min_listings, top=max_games
        )

    if not results:
        typer.echo("No booster prices found for the checked games (packs may be unlisted).")
        return
    typer.secho(
        f"\nBooster-vs-contents ({settings.currency}) — modeled, NOT trading advice:", bold=True
    )
    for r in results:
        game = names.get(r.appid, f"App {r.appid}")
        flag = "ARB" if (r.liquid and r.profitable) else ("thin" if r.profitable else "   ")
        typer.echo(
            f"  margin {r.margin_cents / 100:>+7.2f}  pack {r.booster_cost.amount:.2f} vs "
            f"contents≈{r.contents_ev_net.amount:.2f}  {game} [{r.confidence.value}] {flag}"
        )
    typer.secho(
        "\nResearch only. EV of 3 random cards, resale net of fee — high variance. "
        "Buy/unpack/sell manually in Steam.",
        dim=True,
    )


if __name__ == "__main__":  # pragma: no cover
    app()
