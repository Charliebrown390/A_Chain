# Keeping the hand-maintained data current

Most of the site refreshes itself. This page covers the few datasets that free sources don't publish in a machine-readable form, and how to update them.

## What updates itself

| Data | Source | When |
|---|---|---|
| Prices, technicals, fundamentals, analyst ratings | Yahoo Finance | Weekdays after the US close |
| ROIC, cost of capital, capital intensity, inventory days | Yahoo Finance statements | Weekdays after the US close |
| Credit metrics, distance-to-default | Yahoo Finance | Weekdays after the US close |
| Treasury curve, term premium, credit spreads | FRED (no key needed) | Weekdays after the US close |
| Implied volatility | Option quotes via Yahoo | During US trading hours |
| Headlines | Yahoo Finance feeds | Every 3 hours |
| Customer concentration, backlog, maturities, leases | SEC XBRL filings | Weekly (Mondays) |

## What needs a person (or a Claude chat)

| File | What | How often | Where the numbers come from |
|---|---|---|---|
| `data/bottlenecks.yaml` | GPU rental rates and term curve | Monthly | Silicon Data blog (silicondata.com/blog); CME B200 rental futures, code GPU2 |
| | DRAM contract price change | Quarterly | TrendForce press centre (trendforce.com/presscenter) |
| | Data-centre vacancy, preleasing, construction, absorption, rents | Twice a year (Feb–Mar, Aug–Sep) | CBRE North America Data Center Trends; JLL North America Data Center Report |
| | CoWoS / packaging allocation | When revised | SemiAnalysis, TrendForce, DIGITIMES (often via press coverage) |
| `data/compute_econ.yaml` | GPU rates by generation, on-demand list prices, price-of-intelligence studies | Monthly | Silicon Data; provider price pages; Epoch AI, Artificial Analysis, academic studies |
| `data/capital.yaml` | Large AI bond deals, marker bond prices, context | When a deal prices or a bond moves | Press coverage (Reuters, Bloomberg, FT), company 8-Ks, FINRA bond lookups |
| `data/programmes.yaml` | GPU-vs-custom quarterly revenue table, TPU/Trainium timelines, exposure | After each earnings season | Company earnings releases and calls |
| `data/funding.yaml` | Funding sources, guarantees, off-balance-sheet vehicles | When announced | 8-Ks, 10-Qs, press |
| `data/edges.yaml`, `data/theses.yaml` | Relationships and thesis notes | Any time | Your own research |

Each indicator in `bottlenecks.yaml` carries its own `update` note and a `fresh_days` limit; the page marks it **stale** when it is overdue.

## Three ways to update

1. **Ask Claude.** In any Claude chat, on any device: *"In my GitHub repo Charliebrown390/A_Chain, add the latest DRAM contract price data to data/bottlenecks.yaml."* Claude finds the source, edits the file, runs the checks and pushes. This is the easiest route.
2. **Edit on github.com.** Open the file, click the pencil icon, add a line, commit. The deploy runs the checks; if something is wrong (missing source, wrong date order) the build fails and the live site keeps the previous version. You get an email saying so.
3. **A scheduled Claude task (set up).** "A_Chain monthly data update" runs on the 8th of each month at about 09:00 Singapore time. It checks every hand-maintained indicator for staleness or newer figures, adds only dated and sourced points, runs the build and tests, and pushes; if nothing new is found it makes no commit. You get a push notification and email with what changed and what it couldn't find. Pause, edit or run it early from your scheduled tasks list in Claude.

## Formats

Add a point to a bottleneck series (newest last, ISO date, period end for quarterly data):

```yaml
    series:
      - ["2026-09-30", 15.5]
      - ["2026-12-31", 8.0]      # new
    sources:
      - {src: "TrendForce, Q4 2026 DRAM outlook", url: "https://..."}
```

Update the B200 term curve:

```yaml
    curve:
      date: "2026-10-07"
      points: [["Spot", 5.70], ["3m", 5.68], ["6m", 5.55], ["12m", 5.30]]
```

Add a quarter to the GPU-vs-custom table in `programmes.yaml` (USD billions, calendar quarter):

```yaml
    - {q: "CQ3 2026", NVDA: 104.0, AMD: 7.5, AVGO: 21.7, MRVL: 2.5, notes: "NVDA FQ3'27; AVGO FQ4'26; ..."}
```

Add a bond deal to `capital.yaml`:

```yaml
  - {date: "2026-11", issuer: META, usd_bn: 25.0, note: "Senior notes, 5-40 years", src: "Reuters", url: "https://..."}
```

Every row needs a `url`. Dates are ISO (`YYYY-MM-DD`, or `YYYY-MM` for deals).
