"""Report exports (CSV and inert HTML) for purchase plans and cheapest-badge rankings."""

from .cheapest_report import (
    CheapestBadgeRow,
    build_cheapest_rows,
    render_cheapest_html,
    write_cheapest,
)
from .csv_report import neutralize_formula, write_csv
from .html_report import InertHtmlError, assert_inert_html, render_html, write_html
from .inventory_report import (
    InventoryValueRow,
    build_inventory_rows,
    render_inventory_html,
    write_inventory_value,
)
from .purchase_plan import PlanRow, build_rows

__all__ = [
    "CheapestBadgeRow",
    "InertHtmlError",
    "InventoryValueRow",
    "PlanRow",
    "assert_inert_html",
    "build_cheapest_rows",
    "build_inventory_rows",
    "build_rows",
    "neutralize_formula",
    "render_cheapest_html",
    "render_html",
    "render_inventory_html",
    "write_cheapest",
    "write_csv",
    "write_html",
    "write_inventory_value",
]
