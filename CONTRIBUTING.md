# Contributing to Badgewright

Thanks for helping. Badgewright is a **local-first, read-only** tool — before
anything else, read the one rule that overrides everything.

## The read-only boundary (non-negotiable)

Badgewright plans; it never operates a Steam account. No buying, selling, crafting,
trading, listing, or login automation. This is enforced structurally in
[`src/steam_badge_optimizer/safety.py`](src/steam_badge_optimizer/safety.py) and an
AST CI gate. If a change seems to need a mutating Steam call, **stop** — open an
issue, don't work around the guard. Full rationale:
[`docs/adr/0001-safety-boundary.md`](docs/adr/0001-safety-boundary.md).

## Dev setup

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pre-commit install          # runs ruff + hygiene hooks on commit
ruff check . && ruff format --check . && pytest -q
```

Requires Python 3.12+.

## Conventional Commits

Commit messages and PR titles follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<optional scope>): <description>
```

Allowed **types**: `feat`, `fix`, `docs`, `refactor`, `perf`, `test`, `build`,
`ci`, `chore`, `revert`. Common **scopes**: `safety`, `models`, `cli`, `sources`,
`optimize`, `analytics`, `reports`, `db`, `repo`, `deps`.

- `feat:` and `fix:` are user-facing and must land a CHANGELOG entry.
- A commit that changes the read-only surface must use scope `safety` and be called
  out in the PR description.
- Breaking changes: add `!` (`feat!:`) and a `BREAKING CHANGE:` footer.

The `PR title` CI job validates the PR title against this format.

## Semantic Versioning

The project follows [SemVer](https://semver.org/). While pre-1.0 (`0.y.z`):

- **MINOR** (`0.MINOR.0`) — a new milestone / user-facing capability.
- **PATCH** (`0.y.PATCH`) — fixes and internal changes.
- The public API is not considered stable until `1.0.0`.

Releases are cut from `main` by tagging `vX.Y.Z` (which publishes a GitHub Release);
`CHANGELOG.md` is moved from `[Unreleased]` to the version heading at release time.

## Branches & PRs

- Branch names: `feat/…`, `fix/…`, `chore/…`, `docs/…`.
- One focused change per PR; keep `feat` and unrelated `chore` in separate PRs.
- Every PR must keep CI green: ruff, format, pytest, and the **read-only safety
  gate**. New code follows Red/Green TDD — tests first.
- Track all work in [`docs/backlog.md`](docs/backlog.md) and/or a GitHub issue; link
  the issue in the PR.

## Definition of done

See the self-check list in [`AGENTS.md`](AGENTS.md). The safety gate is a release
blocker, not a nice-to-have.
