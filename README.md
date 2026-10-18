# A_Chain — AI Supply Chain Monitor

A network model of the AI build-out, from chip tools to applications. It maps who sells to whom, how concentrated each company's revenue is, and how each name is doing on fundamentals, technicals and analyst views.

**Live site:** https://charliebrown390.github.io/A_Chain/

## Status

| Phase | Scope | Status |
|---|---|---|
| 0 | Prototype dashboard (sample data) | Done |
| 1 | Company universe, layer taxonomy, sourced relationship map, SEC customer-concentration pull | Done |
| 2 | Market data: prices, fundamentals, analyst ratings and rating changes, implied vs realised vol, beta, news (Yahoo Finance) | Done |
| 3 | Credit layer: financing links, funding sources, leverage and coverage, debt maturities, off-balance-sheet leases, Merton distance-to-default, stress-test tool | Done |
| 4 | Custom silicon and TPU: 55 names over 10 layers (custom-ASIC layer, MediaTek, Alchip, GUC, Celestica, Ibiden, ASE and others), chip-programme filter and timelines, exposure scores, GPU-vs-custom revenue view, rumoured tag, coverage tests | Done |
| 5 | Screening and scoring across the chain, Taiwan monthly sales | Next |
| 3 | Analytics: fundamentals, technicals, network metrics | Planned |
| 4 | Dashboard wired to live data | Planned |
| 5 | Thesis notes and nightly refresh via GitHub Actions | Planned |

## Layout

- `data/universe.yaml` — the company list and the nine layers (edit by hand)
- `data/edges.yaml` — every relationship, with type, strength, confidence tag and source (edit by hand)
- `data/raw/sec_concentration.json` — customer-concentration and backlog facts pulled from SEC XBRL filings (written by the weekly job)
- `scripts/sec_concentration.py` — the SEC pull; needs the `SEC_USER_AGENT` repository secret
- `scripts/build_site_data.py` — validates the map, merges filing figures into links, writes `site/data/*.json`
- `site/` — the static web page served by GitHub Pages
- `data/theses.yaml` — bull/bear/what-to-watch notes per company (edit by hand)
- `data/programmes.yaml` — chip programmes (TPU, Trainium, MTIA, Maia, OpenAI chip, GPU): generations, users, exposure estimates, and the quarterly GPU-vs-custom revenue table (update by hand after earnings)
- `tests/test_coverage.py` — must-have tickers, sourcing rules and TPU-chain checks; runs before every deploy and data refresh
- `data/funding.yaml` — funding sources, guarantees, backstops, off-balance-sheet vehicles and credit signals per company (edit by hand; every item needs a source)
- `data/history/iv.csv` — daily 30-day implied vol log, so IV percentiles build up over time
- `scripts/market_data.py` — Yahoo Finance pull: `--mode full` (prices, fundamentals, analysts, news), `options` (implied vol), `news`
- `.github/workflows/` — `pages.yml` (build and deploy on every push), `sec-extract.yml` (weekly SEC pull), `market-data.yml` (full refresh after each US close, IV snapshot during US hours, news every 3 hours)

## How links are tagged

| Tag | Meaning |
|---|---|
| D | Disclosed by a party in a filing or official release |
| E | Estimated, or reported by the press but not confirmed by the parties. Also used when a filing gives the % for an anonymised customer and we map it to a name |
| R | Rumoured: the relationship itself is unconfirmed |
| I | Inferred from known product relationships; size not disclosed |

D, E and R links need a source and an `as_of` date. Links can carry `fam` (chip programmes) and `role` (co_designs, manufactures, packages...).

Link types: `supply` (ongoing sales), `contract` (announced multi-year commitment), `capital` (equity, warrants), `financing` (credit support: backstop, guarantee, contract used as loan collateral, rent-back offer, vendor finance).

## Credit measures

- Leverage, coverage, cash flow and days-sales-outstanding: Yahoo Finance quarterly statements (TTM), converted to USD.
- Debt maturities, leases not yet commenced, purchase commitments, customer prepayments: SEC XBRL (company-wide facts only). XBRL guarantee tags are ignored because they proved unreliable; guarantees are curated in `funding.yaml`.
- Distance-to-default: Merton model with equity value, equity vol (30-day implied where available, else 1-year realised), default point = short-term debt + half of long-term debt, 1-year horizon. The implied default probability is risk-neutral.
- Stress test (in the page): shocks one company and passes losses through supply/contract links for three rounds (80% pass-through), capped at each buyer's annual spending; on default it also writes off stakes, triggers backstops and guarantees and leaves receivables unpaid. Unknown revenue shares are assumed from link strength and marked *.

## Editing the map

Edit `data/edges.yaml` on GitHub and commit. The deploy job validates it (unknown company ids, missing sources for D/E links and duplicates all fail the build) and republishes the site.

## Disclaimer

This is an analytical tool, not investment advice. The current page uses illustrative sample data.
