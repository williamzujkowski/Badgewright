"""Inert HTML purchase-plan export.

The generated file is a **static, inert** document: no scripts, no event handlers, no
active URL schemes, and every interpolated value HTML-escaped (stored-XSS defense). The
invariant is enforced in code — :func:`write_html` runs :func:`assert_inert_html` on the
rendered bytes *before* writing, so a future template edit that reintroduces a script
fails closed rather than silently shipping. Links point only to informational Steam
market *listings* pages (viewing), never to market-action routes.
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ..safety import FORBIDDEN_PATH_FRAGMENTS
from .purchase_plan import PlanRow, build_rows

if TYPE_CHECKING:
    from ..db import Store
    from ..optimize import OptimizationPlan

__all__ = ["InertHtmlError", "assert_inert_html", "render_html", "write_html"]

_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; "
    "base-uri 'none'; form-action 'none'"
)
_SCRIPT_RE = re.compile(r"<\s*script", re.IGNORECASE)
_HANDLER_RE = re.compile(r"<[^>]*\son\w+\s*=", re.IGNORECASE)
_BAD_SCHEME_RE = re.compile(r"(javascript|vbscript|data|steam)\s*:", re.IGNORECASE)
# Matches href/src attribute values only (not text nodes).
_URL_ATTR_RE = re.compile(r'\b(href|src)\s*=\s*"([^"]*)"', re.IGNORECASE)


class InertHtmlError(RuntimeError):
    """The generated HTML violated the inert-document invariant — refused to write."""


def assert_inert_html(document: str) -> None:
    """Raise :class:`InertHtmlError` if the document is not provably inert."""
    if _SCRIPT_RE.search(document):
        raise InertHtmlError("document contains a <script> tag")
    if _HANDLER_RE.search(document):
        raise InertHtmlError("document contains an inline event handler (on*=)")
    if "Content-Security-Policy" not in document:
        raise InertHtmlError("document is missing its Content-Security-Policy meta")
    # Check URL schemes only inside href/src *attribute values*, never in text nodes:
    # an escaped card/game name that merely contains "steam:"/"data:" (e.g. the game
    # title "Portal 2: …") is inert text, not a link, and must not fail the report.
    for match in _URL_ATTR_RE.finditer(document):
        url = match.group(2)
        if _BAD_SCHEME_RE.search(url):
            raise InertHtmlError(f"active/forbidden URL scheme in link: {url!r}")
        if not url.startswith(("https://", "http://", "#")):
            raise InertHtmlError(f"non-http(s) URL attribute: {url!r}")
        lowered = url.lower()
        if any(fragment in lowered for fragment in FORBIDDEN_PATH_FRAGMENTS):
            raise InertHtmlError(f"URL names a forbidden market-action route: {url!r}")


def _row_html(row: PlanRow) -> str:
    e = html.escape
    # The total is a modeled book-walk cost, not unit x qty, so don't imply arithmetic.
    price = f"{row.missing_qty} copies (from {e(row.unit_price)} ea) ~ {e(row.total_price)}"
    link = e(row.market_url, quote=True)
    return (
        "<tr>"
        '<td><input type="checkbox"></td>'
        f"<td>{row.priority}</td>"
        f"<td>{e(row.card)}</td>"
        f"<td>{price}</td>"
        f'<td><a href="{link}" rel="noopener noreferrer nofollow">market</a></td>'
        f"<td>{e(row.price_age)}</td>"
        f"<td>{e(row.confidence)}</td>"
        "</tr>"
    )


def render_html(plan: OptimizationPlan, store: Store, *, now: datetime | None = None) -> str:
    e = html.escape
    rows = build_rows(plan, store, now=now)
    # Group rows by badge (appid + game).
    grouped: dict[tuple[int, str], list[PlanRow]] = {}
    for row in rows:
        grouped.setdefault((row.appid, row.game), []).append(row)

    body = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        f'<meta http-equiv="Content-Security-Policy" content="{_CSP}">',
        "<title>Badgewright purchase plan</title>",
        "<style>body{font-family:system-ui,sans-serif;margin:2rem;max-width:60rem}"
        "table{border-collapse:collapse;width:100%;margin-bottom:2rem}"
        "th,td{border:1px solid #ccc;padding:.3rem .5rem;text-align:left}"
        ".note{color:#a60;font-size:.9rem}</style></head><body>",
        "<h1>Badgewright purchase plan</h1>",
        "<p><strong>This is a research plan.</strong> Buy cards manually in Steam. "
        "Badgewright never buys, sells, trades, or crafts anything.</p>",
        f"<p>Total estimated spend: <strong>{e(plan.total_cost.amount.__format__('.2f'))} "
        f"{e(plan.currency)}</strong> for <strong>+{plan.total_xp} XP</strong>.</p>",
    ]
    if not grouped:
        body.append("<p>No complete, priced badges to plan yet.</p>")
    for (appid, game), group in grouped.items():
        body.append(f"<h2>{e(game)} <small>(appid {appid})</small></h2>")
        note = group[0].notes
        if note:
            body.append(f'<p class="note">{e(note)}</p>')
        body.append(
            "<table><thead><tr><th>done</th><th>#</th><th>card</th><th>price</th>"
            "<th>link</th><th>price age</th><th>confidence</th></tr></thead><tbody>"
        )
        body.extend(_row_html(r) for r in group)
        body.append("</tbody></table>")
    body.append("</body></html>")
    return "\n".join(body)


def write_html(
    plan: OptimizationPlan, store: Store, path: str | Path, *, now: datetime | None = None
) -> None:
    """Render and write the inert HTML report (validated before writing)."""
    document = render_html(plan, store, now=now)
    assert_inert_html(document)  # fail closed if the invariant is ever broken
    Path(path).write_text(document, encoding="utf-8")
