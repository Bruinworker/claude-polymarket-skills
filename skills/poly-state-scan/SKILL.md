---
name: poly-state-scan
description: Arrow-Debreu state-space consistency scanner for a Polymarket soccer game. Reads the exact-score grid as state prices, replicates every bundle market (moneyline, totals, spreads, team totals, BTTS, extra time) as a sum of states, and flags bundles priced outside their replication interval — the cheapest-expression / dutch-book finder. Outputs a console table + two charts (implied score-distribution heatmap, bundle-vs-replication intervals). Read-only, no keys. Trigger on - state scan, consistency scan, scan this game for mispricing, dutch book scan, arrow-debreu scan, implied score distribution, cheapest replication, 状态空间扫描, 一致性扫描, 这场球哪里错价, 隐含比分分布, 最便宜的表达.
---

# poly-state-scan

Scan one Polymarket soccer game for cross-market pricing inconsistencies using
the exact-score grid as an Arrow-Debreu state space.

## Run

```bash
python3 ~/.claude/skills/poly-state-scan/scan.py <query> [--out DIR] [--no-chart]
```

`<query>` accepts, in order of preference:
1. an event slug of any of the game's events (e.g. `fifwc-prt-hrv-2026-07-02`)
2. a numeric gamma `gameId`
3. free-text team search (`Portugal Croatia`) — resolves via public-search

`--out DIR` puts the two PNGs there (default cwd). Use the session scratchpad
for throwaway runs.

## Method

1. Pull all events sharing the game's `gameId` from the Gamma API
   (`/events?game_id=...`) — soccer games split markets across ~6 events
   (main, exact-score, halftime, second-half, first-to-score, more-markets).
2. State space = the exact-score grid (16 scores + "Any Other Score"). State
   price π_i = YES mid from the book (bid/ask mid, falling back to last trade),
   then normalized so Σπ = 1 — raw Σπ (reported separately) is grid vig, and
   without normalization wide bundles inherit the whole skew as phantom edge.
3. Every FT-derivable bundle gets a replication price = Σ π over its component
   states. The "Other" bucket (some side ≥ 4 goals) is mixed for most bundles,
   so replication is an interval [lo, hi]: lo excludes Other, hi includes it.
   Exception: total O/U lines below 4 get Other assigned to Over exactly
   (any Other score implies total ≥ 4). In knockout games, Extra Time YES is
   replicated as the draw states (ET ⇔ level after 90').
4. Flag when market mid falls outside [lo, hi] by more than half the bundle's
   own bid-ask spread (min 1¢). The signed edge is distance beyond the interval.
5. Halftime / second-half / first-to-score / penalty / team-to-advance markets
   need the (HT, FT) joint distribution and are listed as not replicable —
   don't force them through the FT state space.

## Reading the output

- `Σπ` far from 1.000 = overall vig/skew in the grid itself (typically +1 to
  +3¢ rich pre-game, tails overpriced — favorite-longshot bias).
- A bundle ABOVE its interval = expensive vs the grid → the same exposure is
  cheaper built from exact-score legs (or the bundle is a sell).
- A bundle BELOW its interval = cheap vs the grid → cheapest expression of
  that view is the bundle itself, not the states.
- Flags are mid-vs-mid signals, NOT executable arbs: check both books' depth
  and fees before acting. On a live game prices move faster than the scan.
- Closed games print degenerate 0/1 prices — the scan is meaningless there.

## Caveats

- Assumes FT-score markets settle on regulation 90' (Polymarket soccer rules).
- `bestBid/bestAsk` from gamma can be stale on thin books; `src` falls back to
  last-trade, which widens real uncertainty beyond the printed tolerance.
- Requires `certifi` + `matplotlib` (both present on this machine).
