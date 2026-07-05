"""Doc-drift guard: the README must mention every CLI command (so docs can't silently
drift from the real command surface — a merge condition from the docs-pass vote)."""

from __future__ import annotations

from pathlib import Path

import typer

from steam_badge_optimizer.cli import app

README = (Path(__file__).resolve().parents[2] / "README.md").read_text()


def _command_name(info: object) -> str:
    name = getattr(info, "name", None)
    if name:
        return str(name)
    cb = getattr(info, "callback", None)  # fall back to the function name
    return cb.__name__.replace("_", "-") if cb else ""


def _walk(t: typer.Typer, prefix: str = "") -> list[str]:
    """All leaf command paths, e.g. 'market sweep', 'report cheapest-badges'."""
    leaves: list[str] = []
    for cmd in t.registered_commands:
        name = _command_name(cmd)
        if name:
            leaves.append(f"{prefix}{name}".strip())
    for group in t.registered_groups:
        gname = getattr(group, "name", "") or ""
        sub = group.typer_instance
        if sub is not None:
            leaves.extend(_walk(sub, prefix=f"{prefix}{gname} "))
    return leaves


def test_readme_mentions_every_command() -> None:
    leaves = _walk(app)
    assert len(leaves) > 15, "command introspection found too few commands — check the walker"
    # Every command's leaf name (last token) must appear in the README so a rename/removal
    # or a new undocumented command trips this guard.
    missing = [cmd for cmd in leaves if cmd.split()[-1] not in README]
    assert not missing, f"README.md does not mention these CLI commands: {missing}"
