"""CSV purchase-plan export with spreadsheet-formula-injection defense.

Steam card names and game titles are attacker-influenceable, so any cell whose first
non-whitespace character is a formula trigger (``= + - @ |``) is prefixed with a single
quote — the OWASP-recommended mitigation. (Quoting a field does *not* stop a spreadsheet
from evaluating ``=cmd()``.) Leading whitespace/tab/CR/LF before a trigger is covered,
since spreadsheets trim before parsing.
"""

from __future__ import annotations

import csv
from dataclasses import fields
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .purchase_plan import PlanRow, build_rows

if TYPE_CHECKING:
    from ..db import Store
    from ..optimize import OptimizationPlan

__all__ = ["neutralize_formula", "write_csv"]

_TRIGGERS = frozenset("=+-@|")
_LEADING_WS = " \t\r\n"


def neutralize_formula(value: str) -> str:
    """Prefix a single quote if the first non-whitespace char is a formula trigger."""
    stripped = value.lstrip(_LEADING_WS)
    if stripped and stripped[0] in _TRIGGERS:
        return "'" + value
    return value


def write_csv(
    plan: OptimizationPlan, store: Store, path: str | Path, *, now: datetime | None = None
) -> int:
    """Write the plan to a CSV file. Returns the number of rows written."""
    rows = build_rows(plan, store, now=now)
    columns = [f.name for f in fields(PlanRow)]
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: neutralize_formula(str(getattr(row, col))) for col in columns})
    return len(rows)
