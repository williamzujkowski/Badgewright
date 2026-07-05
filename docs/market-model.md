# Market model

Market analytics are **research**, labeled as such — never instructions to trade,
and Badgewright never executes trades. Reviewed 2026-07-04.

## What is computable from `priceoverview` (single snapshot)

- **Ask-vs-median-sale gap** — `lowest_price` vs `median_price`. (Call it this, *not*
  "spread": `priceoverview` has no buy orders, so it is not a bid/ask spread.)
- **Set-level mispricing** — Σ card prices vs set utility / booster-pack cost.
- **Card imbalance** — `max(card_cost) / total_set_cost`; a genuinely useful
  execution-risk flag ("one card dominates the cost / is the bottleneck").
- **Low-volume staleness proxy** — small/absent `volume` ⇒ unreliable quote.

## What is NOT computable from a single snapshot

- **Volatility** — needs a local price-history store (snapshots over time) or the
  auth-gated `pricehistory`. Any variance metric from one snapshot is meaningless.
- **True bid/ask spread & buy-side depth** — require
  `market/itemordershistogram?item_nameid=...`, which needs a separate `item_nameid`
  scrape. Tracked as its own issue.

## Statistical hygiene

- Weight every metric by liquidity. A lowest/median gap on a card with tiny 24h
  volume is noise, not opportunity.
- Never trust `median_price` for thin items.
- Flag low-volume anomalies as **risky**, not as recommendations.

## Confidence propagation

The same confidence score used by the optimizer (volume, quote age, volatility,
lowest/median divergence, book depth) tags every market-research row so the human
can discount illiquid findings. Anomaly reports state anomaly type, confidence, and
caveats — and produce **no** trading action.

## Data prerequisites

Volatility/history metrics depend on a **local price-history store** that is
populated over repeated conservative refreshes. Until enough history exists,
history-dependent metrics report "insufficient data," never a fabricated number.
