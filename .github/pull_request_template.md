<!-- PR title MUST follow Conventional Commits, e.g. "feat(models): add Card model" -->

## What & why

<!-- What does this change and why. Link the issue: Closes #NN -->

## Safety boundary

- [ ] No mutating HTTP verb, no new egress host without an ADR edit, no secret field.
- [ ] The read-only safety gate (`test_safety.py`, `test_no_mutating_http.py`) passes.
- [ ] If this touches the network/safety surface, I used scope `safety` and explained it above.

## Checklist

- [ ] Tests written first (Red/Green) and cover happy path + edge + error cases.
- [ ] `ruff check .`, `ruff format --check .`, and `pytest -q` are green locally.
- [ ] Provenance attached to any new imported datum; constants live in `config.py`.
- [ ] `CHANGELOG.md` updated for user-facing `feat`/`fix`.
- [ ] Backlog / linked issue updated.
