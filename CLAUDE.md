# A_Chain project notes

Purpose: analyse the AI supply chain (tools → chip design → foundry → memory → networking → servers → power/DCs → compute providers → models/apps) to identify investment candidates.

Conventions
- Static site in `site/`, deployed to GitHub Pages by `.github/workflows/pages.yml`. No server-side code.
- Data will live as JSON under `site/data/`, written by Python scripts run nightly in GitHub Actions.
- Free data sources only unless a paid API is critically required: SEC EDGAR XBRL, yfinance, company IR pages, Taiwan monthly sales.
- Every relationship edge carries a confidence tag: D (disclosed in filings), E (estimated), I (inferred), plus a source note.
- Keep sample/illustrative data clearly labelled on the page until replaced by live data.
- Universe: core ~40 names across 9 layers.
