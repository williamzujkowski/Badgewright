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

Evidence gate (#81): a live case picked a 1-priced-card game estimated at $0.15 that
actually cost $7.26 to finish — one cheap card is weak evidence about a set. So candidates
are ranked in two tiers: those with at least ``min_priced_fraction`` of the set priced
(more evidence) come first, then the sparser ones — each tier still cheapest-first. This is
*non-destructive*: sparse candidates are down-ranked, never excluded, so when every game is
a lone-cheap-card singleton (the common post-sweep state) the tiers are uniform and the
order is unchanged. Note: priced-fraction is evidence *coverage*, not *strength* — a game
can clear the gate on several non-cheap cards; it is a cheap, honest heuristic, not a proof.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..db import Store

__all__ = ["CandidateGame", "select_candidate_games"]


#: Default evidence gate: prefer candidates with at least this fraction of the set priced.
DEFAULT_MIN_PRICED_FRACTION = 0.34  # ~ need >= 1/3 of the set priced to be "well-evidenced"


@dataclass(frozen=True, slots=True)
class CandidateGame:
    appid: int
    set_size: int
    priced_count: int  # cards of this set we already have a price for
    known_cost_cents: int  # sum of those known prices
    est_completion_cents: int  # known + median-proxy * remaining slots (ranking key)
    priced_fraction: float  # priced_count / set_size (evidence COVERAGE, not strength)


def select_candidate_games(
    store: Store,
    *,
    currency: str = "USD",
    max_games: int = 5,
    min_priced_fraction: float = DEFAULT_MIN_PRICED_FRACTION,
) -> list[CandidateGame]:
    """Rank incomplete games by estimated cost to complete their set (cheapest first).

    Only games with at least one priced card but not a fully-priced set are candidates
    (a fully-priced set is already rankable by ``rank_cheapest_badges``). Candidates with at
    least ``min_priced_fraction`` of the set priced rank ahead of sparser ones (evidence
    gate, #81), each tier cheapest-first; sparse candidates are down-ranked, never excluded.
    Pure function.
    """
    if max_games < 1:
        raise ValueError("max_games must be >= 1")
    if not 0.0 <= min_priced_fraction <= 1.0:
        raise ValueError("min_priced_fraction must be within [0, 1]")

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
            priced_fraction=len(known) / set_size,
        )
        for appid, set_size, known in games
    ]
    # Two-tier evidence gate (#81): well-evidenced candidates (priced_fraction >= threshold)
    # sort first, then sparser ones — each tier cheapest-first. Non-destructive: an
    # all-singleton field is one uniform tier, so the order degrades to (est_cost, appid).
    out.sort(
        key=lambda c: (c.priced_fraction < min_priced_fraction, c.est_completion_cents, c.appid)
    )
    return out[:max_games]


def _p75(values: list[int]) -> int:
    """75th-percentile (nearest-rank) of a non-empty list of ints."""
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(0.75 * (len(ordered) - 1) + 0.5))
    return ordered[idx]
