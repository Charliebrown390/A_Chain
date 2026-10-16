# A_Chain — AI Supply Chain Monitor

A network model of the AI build-out, from chip tools to applications. It maps who sells to whom, how concentrated each company's revenue is, and how each name is doing on fundamentals, technicals and analyst views.

**Live site:** https://charliebrown390.github.io/A_Chain/

## Status

| Phase | Scope | Status |
|---|---|---|
| 0 | Prototype dashboard (sample data) | Done |
| 1 | Company universe, layer taxonomy, sourced relationship map, SEC customer-concentration pull | Done |
| 2 | Market data: prices, fundamentals, analyst ratings and rating changes, implied vs realised vol, beta, news (Yahoo Finance) | Done |
| 3 | Analytics: screening and scoring across the chain, Taiwan monthly sales | Next |
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
- `data/history/iv.csv` — daily 30-day implied vol log, so IV percentiles build up over time
- `scripts/market_data.py` — Yahoo Finance pull: `--mode full` (prices, fundamentals, analysts, news), `options` (implied vol), `news`
- `.github/workflows/` — `pages.yml` (build and deploy on every push), `sec-extract.yml` (weekly SEC pull), `market-data.yml` (full refresh after each US close, IV snapshot during US hours, news every 3 hours)

## How links are tagged

| Tag | Meaning |
|---|---|
| D | Disclosed by a party in a filing or official release |
| E | Estimated, or reported by the press but not confirmed by the parties. Also used when a filing gives the % for an anonymised customer and we map it to a name |
| I | Inferred from known product relationships; size not disclosed |

Link types: `supply` (ongoing sales), `contract` (announced multi-year commitment), `capital` (equity, warrants, financing backstops).

## Editing the map

Edit `data/edges.yaml` on GitHub and commit. The deploy job validates it (unknown company ids, missing sources for D/E links and duplicates all fail the build) and republishes the site.

## Disclaimer

This is an analytical tool, not investment advice. The current page uses illustrative sample data.
