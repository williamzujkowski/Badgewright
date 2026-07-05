"""Static safety-regression gate (Issues 0.2 / 8.3).

Walks the source AST and fails if anyone introduces a mutating HTTP verb call
(`.post/.put/.patch/.delete`) or a direct `requests`/`urllib` egress. This is the
AST-based gate the security review recommended over naive keyword grep: it ignores
comments and docstrings, and it catches the actual dangerous construct (a call),
not a substring. The runtime `assert_safe_request` guard is the primary control;
this test makes an accidental reintroduction fail in CI before it can ship.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "steam_badge_optimizer"

# Verbs that mutate server state. There is no legitimate reason for Badgewright to
# call any of these against a network client.
FORBIDDEN_HTTP_VERBS = {"post", "put", "patch", "delete"}

# Modules whose direct use would bypass the SafeClient choke point.
FORBIDDEN_IMPORT_ROOTS = {"requests", "urllib3", "aiohttp", "socket"}


def _python_files() -> list[Path]:
    return sorted(SRC_ROOT.rglob("*.py"))


def test_source_tree_exists() -> None:
    assert _python_files(), f"no source files found under {SRC_ROOT}"


def test_no_mutating_http_verb_calls() -> None:
    offenders: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr.lower() in FORBIDDEN_HTTP_VERBS
            ):
                offenders.append(f"{path.name}:{node.lineno} -> .{node.func.attr}(...)")
    assert not offenders, (
        f"Mutating HTTP verb call(s) found — Badgewright is read-only. Refused: {offenders}"
    )


def test_no_bypass_of_safe_http_client() -> None:
    offenders: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            roots: set[str] = set()
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots = {node.module.split(".")[0]}
            for forbidden in roots & FORBIDDEN_IMPORT_ROOTS:
                offenders.append(f"{path.name}:{node.lineno} -> import {forbidden}")
    assert not offenders, (
        "Import that bypasses the SafeClient egress choke point. "
        f"Route all network reads through the guarded client. Found: {offenders}"
    )
