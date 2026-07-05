"""Pick which games to targeted-complete for cheapest badges (Epic #71 / #77 / #69).

The global cheapest-first sweep surfaces cheap cards scattered one-each across many games,
so it rarely completes a full *set*. This selects the games most likely to yield a cheap
badge if we spend a little request budget finishing their sets, so a bounded targeted pass
(discover + price just those games) can actually deliver ranked cheapest badges.

Selection avoids the obvious bias — a game can look cheap because the ONE card we happened
to price is cheap while the rest of the set is expensive. So a candidate is ranked by its
ESTIMATED cost to COMPLETE the whole set: the sum of the cards we've already priced plus a
**conservative (75th-percentile) proxy** for each still-unpriced slot. Charging unpriced
slots a pessimistic price means a game where we've verified MANY cards are cheap estimates
lower than one where a single cheap card hides an unknown (possibly expensive) remainder —
so evidence is rewarded and thin single-card gambles sink. Best-effort over a partial sample.
"""

from __future__ import annotations

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
    # Conservative proxy for unpriced slots: the 75th percentile of known prices. Pricier
    # than the median, so a game must have EVIDENCE (many priced-cheap cards) to estimate
    # low — a lone cheap card can't make the whole set look cheap.
    proxy = _p75(all_known)
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


def _p75(values: list[int]) -> int:
    """75th-percentile (nearest-rank) of a non-empty list of ints."""
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(0.75 * (len(ordered) - 1) + 0.5))
    return ordered[idx]
