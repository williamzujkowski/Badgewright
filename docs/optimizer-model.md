# Optimizer model

Reviewed during planning (2026-07-04). The headline finding: **greedy is provably
optimal for the core problem — ILP is not needed for the MVP** and is demoted to a
shelf spec.

## Problem

Gain Steam account levels as cheaply as possible by buying missing cards to craft
badges. Each craft grants an identical 100 XP, and account level is a step function
of cumulative XP (see [data-sources.md](data-sources.md)).

## Cost-to-complete

For a badge with card set `C` and target level `L`:

```
copies_needed[card] = L               # crafting consumes cards; L crafts need L copies
missing[card]       = max(0, L - owned[card])
badge_cost          = sum(missing[card] * unit_cost[card] for card in C)
```

Edge cases (each a tracked issue where non-trivial):

- **Order-book depth.** `lowest_price` is a single-unit ask; buying `missing[card]`
  copies costs ≥ `missing × lowest`. Model with `itemordershistogram` depth or a
  conservative inflation factor; never assume linear cost at `lowest`.
- **Ready-to-craft = free XP.** An already-owned full set crafts at cost 0 — surface
  these first (cost/XP = 0).
- **Unmarketable / delisted cards.** No market price → cost undefined; **gate out**,
  never treat as 0 or crash.
- **Booster packs / gems / sack-of-gems.** Alternative acquisition paths (a booster
  = 3 random set cards, often cheaper for large sets). Out of MVP scope; model at
  least as an expected-cost comparison flag.
- **Foil** badges: separate badge, excluded by default.
- **Currency** is pinned per run; `priceoverview` is currency-scoped.

## Why greedy is optimal (not just "adequate")

Every craft is worth the **same** 100 XP. So "min cost to reach target XP" and "max
XP under budget" both reduce to: sort all candidate crafts by marginal cost, take
cheapest first. Per-badge marginal cost is **non-decreasing** across successive
crafts (owned duplicates make the early crafts cheaper), so a single merged sorted
list of per-craft marginal costs automatically respects the craft-1-before-craft-2
precedence. This is **not** general knapsack — uniform item value collapses it.

Greedy therefore ships as the MVP optimizer and is expected to be exact.

## When ILP is actually warranted (shelf spec)

Only once item value stops being uniform or constraints stop being separable:
per-vendor purchase caps, foil with different XP, or a genuine completion bonus.
Documented form (for when it's needed):

- Vars: ordered per-craft binaries `y[b,j] ∈ {0,1}` with `y[b,j] ≤ y[b,j-1]`.
- Objective: `min Σ marginalCost[b,j] · y[b,j]`.
- Constraints: `Σ 100·y ≥ targetXP`, `Σ cost ≤ budget`, `Σ_j y[b,j] ≤ maxLevel[b]`.
- Gate predicates (min volume, unit-price cap, max staleness) are applied as
  **inclusion filters before solving**, not as soft penalties.

## Confidence-weighted, pessimistic ranking

Do **not** rank on the point-estimate `lowest`. For low-volume cards substitute the
median (or a high percentile) and inflate by an uncertainty premium from a
confidence score `f(volume, quote age, volatility, lowest/median divergence, book
depth)`. Apply **hard gates first** (min volume, max staleness, max unit price) so
illiquid junk with a fake-cheap lowest never enters ranking; then sort survivors by
risk-adjusted (upper-bound) cost/XP. Surface per-line confidence for the reviewer.

## Target-level optimization

The user asks for **levels**, not XP. Optimize cost-to-reach-target-level over the
XP step function, and flag overshoot waste when a plan lands mid-band (paying for XP
that buys no additional level).
