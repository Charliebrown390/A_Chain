# A_Chain project notes

Purpose: analyse the AI supply chain (tools → chip design → foundry → memory → networking → servers → power/DCs → compute providers → models/apps) to identify investment candidates.

Conventions
- Static site in `site/`, deployed to GitHub Pages by `.github/workflows/pages.yml`. No server-side code.
- Curated inputs: `data/universe.yaml`, `data/edges.yaml`. Pulled data: `data/raw/`. Compiled JSON for the page: `site/data/` (built by `scripts/build_site_data.py`, which also validates).
- SEC requests need the `SEC_USER_AGENT` secret ("Name email"); SEC rejects GitHub noreply addresses. The sandbox shell cannot reach SEC, so run SEC pulls via GitHub Actions.
- Free data sources only unless a paid API is critically required: SEC EDGAR XBRL, yfinance, company IR pages, Taiwan monthly sales.
- Every relationship edge carries a confidence tag: D (disclosed in filings), E (estimated), I (inferred), plus a source note.
- Market data comes from Yahoo Finance via yfinance (works from the sandbox too). Implied vol is computed from option mid-quotes in `--mode options`, which must run during US trading hours; Yahoo's own IV field is unreliable outside them.
- Thesis notes: `data/theses.yaml`. Funding/credit support: `data/funding.yaml` (sources required). Do not display XBRL GuaranteeObligationsMaximumExposure (mis-tagged values seen, e.g. NVIDIA). The page is a single file, `site/index.html`.
- Universe: 55 names across 10 layers (layer 3 = custom ASIC partners). Names under review go in `watchlist` in universe.yaml, not on the map. China A-shares stay on the watchlist unless the user says otherwise.
- Confidence tags: D, E, R (rumoured), I. D/E/R need url (or xbrl) and as_of. Chip programmes live in `data/programmes.yaml`; tag links with `fam`.
- `python -m pytest -q tests` must pass; it runs in the deploy and refresh workflows.
