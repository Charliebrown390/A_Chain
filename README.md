# A_Chain — AI Supply Chain Monitor

A network model of the AI build-out, from chip tools to applications. It maps who sells to whom, how concentrated each company's revenue is, and how each name is doing on fundamentals, technicals and analyst views.

**Live site:** https://charliebrown390.github.io/A_Chain/

## Status

| Phase | Scope | Status |
|---|---|---|
| 0 | Prototype dashboard (sample data) | Done |
| 1 | Company universe, layer taxonomy, sourced relationship map | Next |
| 2 | Data pipeline: SEC EDGAR, yfinance, Taiwan monthly sales | Planned |
| 3 | Analytics: fundamentals, technicals, network metrics | Planned |
| 4 | Dashboard wired to live data | Planned |
| 5 | Thesis notes and nightly refresh via GitHub Actions | Planned |

## Layout

- `site/` — the static web page served by GitHub Pages
- `.github/workflows/` — deployment and (later) the nightly data refresh

## Disclaimer

This is an analytical tool, not investment advice. The current page uses illustrative sample data.
