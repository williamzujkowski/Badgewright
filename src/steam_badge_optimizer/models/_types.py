"""Shared Pydantic field types for the domain models."""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator


def _reject_blank(value: str) -> str:
    # `Field(min_length=1)` counts characters, so a whitespace-only " " slips through and
    # could form a garbage market query / cache key. Reject anything that is blank once
    # stripped (the value itself is NOT stripped — exact market hash names are preserved).
    if not value.strip():
        raise ValueError("must not be blank or whitespace-only")
    return value


#: A string that must contain at least one non-whitespace character. Stricter than
#: ``min_length=1`` (which accepts " "); the value is validated, not modified.
NonBlankStr = Annotated[str, AfterValidator(_reject_blank)]
