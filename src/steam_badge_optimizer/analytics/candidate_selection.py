"""Pick which games to targeted-complete for cheapest badges (Epic #71 / #77 / #69).

The global cheapest-first sweep surfaces cheap cards scattered one-each across many games,
so it rarely completes a full *set*. This selects the games most likely to yield a cheap
badge if we spend a little request budget finishing their sets, so a bounded targeted pass
(discover + price just those games) can actually deliver ranked cheapest badges.

Selection avoids the obvious bias — a game can look cheap because the ONE card we happened
to price is cheap while the rest of the set is expensive. So a candidate is ranked by its
ESTIMATED cost to COMPLETE the whole set: the sum of the cards we've already priced plus a
conservative median-of-known-prices proxy for each still-unpriced slot. Ranking by that
(ascending, appid tie-break) is a best-effort estimate over the partial sample we have.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..db import Store

__all__ = ["CandidateGame", "select_candidate_games"]


@dataclass(frozen=True, slots=True)
class CandidateGame:
    appid: int
    set_size: int
    priced_count: int  # cards of this set we already have a price for
    known_cost_cents: int  # sum of those known prices
    est_completion_cents: int  # known + median-proxy * remaining slots (ranking key)


def select_candidate_games(
    store: Store,
    *,
    currency: str = "USD",
    max_games: int = 5,
) -> list[CandidateGame]:
    """Rank incomplete games by estimated cost to complete their set (cheapest first).

    Only games with at least one priced card but not a fully-priced set are candidates
    (a fully-priced set is already rankable by ``rank_cheapest_badges``). Pure function.
    """
    if max_games < 1:
        raise ValueError("max_games must be >= 1")

    games: list[tuple[int, int, list[int]]] = []
    all_known: list[int] = []
    for badge_set in store.list_badge_sets():
        if badge_set.set_size <= 0:
            continue
        known: list[int] = []
        for card in store.cards_for_app(badge_set.appid, include_foil=False):
            snap = store.latest_price(badge_set.appid, card.market_hash_name)
            unit = (snap.lowest or snap.median) if snap else None
            if unit is not None and unit.currency == currency:
                known.append(unit.cents)
        # Candidate = has a cheap signal but the set isn't fully priced yet.
        if known and len(known) < badge_set.set_size:
            games.append((badge_set.appid, badge_set.set_size, known))
            all_known.extend(known)

    if not games:
        return []
    proxy = int(statistics.median(all_known))  # a typical cheap-card price for unpriced slots
    out = [
        CandidateGame(
            appid=appid,
            set_size=set_size,
            priced_count=len(known),
            known_cost_cents=sum(known),
            est_completion_cents=sum(known) + (set_size - len(known)) * proxy,
        )
        for appid, set_size, known in games
    ]
    out.sort(key=lambda c: (c.est_completion_cents, c.appid))
    return out[:max_games]
