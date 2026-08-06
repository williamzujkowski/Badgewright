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


def _documented(command: str) -> bool:
    """True if the README shows this command being invoked, under either binary name."""
    return any(f"{binary} {command}" in README for binary in ("sbo", "badgewright"))


def test_readme_mentions_every_command() -> None:
    leaves = _walk(app)
    assert len(leaves) > 15, "command introspection found too few commands — check the walker"
    # Match the FULL invocation, not the leaf token. A bare last-token search passes on
    # coincidence: "list" is satisfied by the words "allowlist"/"listing" elsewhere in the
    # prose, so `catalog list` could vanish from the docs entirely and this stayed green.
    missing = [cmd for cmd in leaves if not _documented(cmd)]
    assert not missing, f"README.md does not document these CLI commands: {missing}"
