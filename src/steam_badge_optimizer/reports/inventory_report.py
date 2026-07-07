"""Export the inventory valuation to CSV / inert HTML (#99).

Mirrors the cheapest-badges report hardening: CSV cells get formula-injection
neutralization and the HTML document is validated inert before writing. Game names and
card hash-names are Steam-sourced (attacker-influenceable), so every interpolated field is
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
    from ..analytics import InventoryValuation

__all__ = [
    "InventoryValueRow",
    "build_inventory_rows",
    "render_inventory_html",
    "write_inventory_value",
]


@dataclass(frozen=True, slots=True)
class InventoryValueRow:
    rank: int
    game: str
    appid: int
    kind: str  # "card" or booster_pack / gems / sack_of_gems / other
    card: str  # market_hash_name
    quantity: int
    foil: str  # "yes" / ""
    unit_price: str  # "" when unpriced
    line_value: str  # "" when unpriced
    currency: str
    notes: str
    as_of: str


def build_inventory_rows(
    valuation: InventoryValuation,
    names: dict[int, str],
    *,
    now: datetime,
) -> list[InventoryValueRow]:
    as_of = now.isoformat(timespec="seconds")
    rows: list[InventoryValueRow] = []
    for i, h in enumerate(valuation.holdings, start=1):
        rows.append(
            InventoryValueRow(
                rank=i,
                game=names.get(h.appid, f"App {h.appid}"),
                appid=h.appid,
                kind=h.kind,
                card=h.market_hash_name,
                quantity=h.quantity,
                foil="yes" if h.is_foil else "",
                unit_price=f"{h.unit_price.amount:.2f}" if h.unit_price is not None else "",
                line_value=f"{h.line_value.amount:.2f}" if h.line_value is not None else "",
                currency=valuation.currency,
                notes="; ".join(h.signals),
                as_of=as_of,
            )
        )
    return rows


def _write_csv(rows: list[InventoryValueRow], path: Path) -> None:
    columns = [f.name for f in fields(InventoryValueRow)]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {col: neutralize_formula(str(value)) for col, value in asdict(row).items()}
            )


def render_inventory_html(
    valuation: InventoryValuation,
    rows: list[InventoryValueRow],
    *,
    currency: str,
) -> str:
    e = html.escape
    as_of = e(rows[0].as_of) if rows else ""
    total = f"{valuation.total_value.amount:.2f}"
    body = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        f'<meta http-equiv="Content-Security-Policy" content="{_CSP}">',
        "<title>Badgewright inventory value</title>",
        "<style>body{font-family:system-ui,sans-serif;margin:2rem;max-width:60rem}"
        "table{border-collapse:collapse;width:100%}"
        "th,td{border:1px solid #ccc;padding:.3rem .5rem;text-align:left}"
        ".unpriced{color:#a60}</style></head><body>",
        "<h1>Inventory value</h1>",
        "<p><strong>This is research, not trading advice.</strong> Values are current market "
        "floors; sell/hold decisions are yours, made manually in Steam.</p>",
        f"<p>Total (priced floor): <strong>{e(total)} {e(currency)}</strong> — "
        f"{valuation.priced_count} holding(s) priced, {valuation.unpriced_count} unpriced. "
        f"As of {as_of}.</p>",
    ]
    if not rows:
        body.append("<p>No holdings to value yet.</p>")
    else:
        body.append(
            "<table><thead><tr><th>#</th><th>game</th><th>appid</th><th>kind</th><th>item</th>"
            "<th>qty</th><th>foil</th>"
            f"<th>unit ({e(currency)})</th><th>value ({e(currency)})</th>"
            "<th>notes</th></tr></thead><tbody>"
        )
        for r in rows:
            cls = ' class="unpriced"' if not r.line_value else ""
            body.append(
                f"<tr{cls}><td>{r.rank}</td><td>{e(r.game)}</td><td>{r.appid}</td>"
                f"<td>{e(r.kind)}</td><td>{e(r.card)}</td><td>{r.quantity}</td><td>{e(r.foil)}</td>"
                f"<td>{e(r.unit_price)}</td><td>{e(r.line_value)}</td>"
                f"<td>{e(r.notes)}</td></tr>"
            )
        body.append("</tbody></table>")
    body.append("</body></html>")
    document = "\n".join(body)
    # Self-assert inertness so a caller writing the returned string directly still gets the
    # fail-closed guarantee.
    assert_inert_html(document)
    return document


def write_inventory_value(
    valuation: InventoryValuation,
    names: dict[int, str],
    path: str | Path,
    *,
    now: datetime,
) -> int:
    """Write the inventory valuation to CSV or inert HTML (by extension). Returns rows written."""
    dest = Path(path)
    rows = build_inventory_rows(valuation, names, now=now)
    suffix = dest.suffix.lower()
    if suffix == ".csv":
        _write_csv(rows, dest)
    elif suffix in (".html", ".htm"):
        document = render_inventory_html(valuation, rows, currency=valuation.currency)
        assert_inert_html(document)  # fail closed if the inert invariant is ever broken
        dest.write_text(document, encoding="utf-8")
    else:
        raise ValueError(f"unsupported export extension {suffix!r}; use .csv or .html")
    return len(rows)
