"""Export the cheapest-badges ranking to CSV / inert HTML (#70).

Reuses the existing report hardening: CSV cells get formula-injection neutralization and the
HTML document is validated inert (no scripts/handlers/active links) before writing. Game
names and signals are Steam-sourced (attacker-influenceable), so every interpolated field is
escaped. The ``as_of`` timestamp is injected, not read from the wall clock, so output is
deterministic and diffable.
"""

from __future__ import annotations

import csv
import html
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .csv_report import neutralize_formula
from .html_report import _CSP, assert_inert_html

if TYPE_CHECKING:
    from ..analytics import BadgeSetCost

__all__ = ["CheapestBadgeRow", "build_cheapest_rows", "render_cheapest_html", "write_cheapest"]


@dataclass(frozen=True, slots=True)
class CheapestBadgeRow:
    rank: int
    game: str
    appid: int
    cards: int
    total_cost: str
    currency: str
    cost_per_xp: str
    confidence: str
    buyable: str  # "yes" / "thin" — human-legible liquidity verdict
    bottleneck_pct: str
    notes: str
    as_of: str


def build_cheapest_rows(
    badges: list[BadgeSetCost],
    names: dict[int, str],
    *,
    currency: str,
    now: datetime,
) -> list[CheapestBadgeRow]:
    as_of = now.isoformat(timespec="seconds")
    rows: list[CheapestBadgeRow] = []
    for i, b in enumerate(badges, start=1):
        rows.append(
            CheapestBadgeRow(
                rank=i,
                game=names.get(b.appid, f"App {b.appid}"),
                appid=b.appid,
                cards=b.set_size,
                total_cost=f"{b.total_cost.amount:.2f}",
                currency=currency,
                # currency per XP (a set craft = XP_PER_BADGE_LEVEL); informational, sortable.
                cost_per_xp=f"{b.cost_per_xp_cents / 100:.4f}",
                confidence=b.confidence.value,
                buyable="yes" if b.liquid else "thin",
                bottleneck_pct=(
                    f"{b.bottleneck_fraction * 100:.0f}"
                    if b.bottleneck_fraction is not None
                    else ""
                ),
                notes="; ".join(b.signals),
                as_of=as_of,
            )
        )
    return rows


def _write_csv(rows: list[CheapestBadgeRow], path: Path) -> None:
    columns = [f.name for f in fields(CheapestBadgeRow)]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {col: neutralize_formula(str(value)) for col, value in asdict(row).items()}
            )


def render_cheapest_html(rows: list[CheapestBadgeRow], *, currency: str) -> str:
    e = html.escape
    as_of = e(rows[0].as_of) if rows else ""
    body = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        f'<meta http-equiv="Content-Security-Policy" content="{_CSP}">',
        "<title>Badgewright cheapest badges</title>",
        "<style>body{font-family:system-ui,sans-serif;margin:2rem;max-width:60rem}"
        "table{border-collapse:collapse;width:100%}"
        "th,td{border:1px solid #ccc;padding:.3rem .5rem;text-align:left}"
        ".thin{color:#a60}</style></head><body>",
        "<h1>Cheapest badges to make</h1>",
        "<p><strong>This is research, not trading advice.</strong> Buy cards manually in "
        "Steam. Badgewright never buys, sells, trades, or crafts anything.</p>",
        f"<p>Ranked by set cost in {e(currency)}. As of {as_of}.</p>",
    ]
    if not rows:
        body.append("<p>No fully-known, priced badges to rank yet.</p>")
    else:
        body.append(
            "<table><thead><tr><th>#</th><th>game</th><th>appid</th><th>cards</th>"
            f"<th>cost ({e(currency)})</th><th>confidence</th><th>buyable</th>"
            "<th>bottleneck %</th><th>notes</th></tr></thead><tbody>"
        )
        for r in rows:
            cls = ' class="thin"' if r.buyable != "yes" else ""
            body.append(
                f"<tr{cls}><td>{r.rank}</td><td>{e(r.game)}</td><td>{r.appid}</td>"
                f"<td>{r.cards}</td><td>{e(r.total_cost)}</td><td>{e(r.confidence)}</td>"
                f"<td>{e(r.buyable)}</td><td>{e(r.bottleneck_pct)}</td>"
                f"<td>{e(r.notes)}</td></tr>"
            )
        body.append("</tbody></table>")
    body.append("</body></html>")
    document = "\n".join(body)
    # Self-assert inertness: this is a public API, so a caller that writes the returned
    # string directly (not via write_cheapest) still gets the fail-closed guarantee.
    assert_inert_html(document)
    return document


def write_cheapest(
    badges: list[BadgeSetCost],
    names: dict[int, str],
    path: str | Path,
    *,
    currency: str,
    now: datetime,
) -> int:
    """Write the ranking to CSV or inert HTML (by extension). Returns rows written."""
    dest = Path(path)
    rows = build_cheapest_rows(badges, names, currency=currency, now=now)
    suffix = dest.suffix.lower()
    if suffix == ".csv":
        _write_csv(rows, dest)
    elif suffix in (".html", ".htm"):
        document = render_cheapest_html(rows, currency=currency)
        assert_inert_html(document)  # fail closed if the inert invariant is ever broken
        dest.write_text(document, encoding="utf-8")
    else:
        raise ValueError(f"unsupported export extension {suffix!r}; use .csv or .html")
    return len(rows)
