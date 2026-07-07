"""Badgewright — a local-first, read-only Steam badge optimizer.

This package plans; it never operates a Steam account. See :mod:`steam_badge_optimizer.safety`
for the structural read-only boundary and ``docs/adr/0001-safety-boundary.md`` for the rationale.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    #: Single source of truth is pyproject.toml; read it from the installed metadata so
    #: the version lives in exactly one place (no hand-synced constants). See #40.
    __version__ = version("steam-badge-optimizer")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0+unknown"
