## V75 / Alko-adapteri
Lue README_V75.md. Alko-tuontitavat: `alko_file` (CSV/XLSX) ja `alko_mirror` (epävirallinen verkkohinnasto). Älä väitä, että virallinen live-rajapinta tai koko verkkokeruu olisi vahvistettu; älä ohita robots.txt-/HTTP-estoja. Alkon tuotenumero EI ole EAN. Älä julkaise raakahavaintoja suoraan tuotteiksi. Adapteri: `tools/harvester_desktop/alko_v75.py`, testit `tests/test_alko_v75.py`.

# Repository instructions for Codex and other coding agents

This is **Kaljappi V73 + Product Harvester Desktop**, a Python standard-library local UI and catalogue collection engine, designed to be reusable for coffee/Shopify and other catalogues.

Priorities: preserve data and existing functionality, source evidence accuracy, no invented tasting notes/EANs, respect source terms/robots/rate limits, and reliable user-visible progress.

Read `README_V73.md` and `DEVELOPER_GUIDE.md` before edits. Start at `tools/harvester_desktop/engine.py` for harvesting or `server.py` and `harvester_ui/` for the dashboard. Import sources from `tools/catalog_harvester/`.

Do not overwrite `data/catalog_registry_v69.sqlite` or `data/products.json`; V72 contains 351 published drinks and 809 historical observations. No automatic product publication. Treat URL/evidence fields as source assertions, not guarantees of current stock. Avoid changing canonical drink category or flavor without reliable product-specific evidence. Avoid extracting/saving consumer account data.

Regression commands:

```
python -m unittest discover -s tests -p 'test_*.py' -q
node --test tests/test_taste_search.cjs tests/test_ui_smoke.cjs
```

Add local fixtures for new source adapters and document their terms/limitations. Keep all secrets out of Git. UI should work without installing Python packages, AI API keys, npm or frameworks.

### V74: queue & catalog roadmap
- `tools/harvester_desktop/workflow_v74.py`: source catalogue, durable scheduler, review-only CSV exports.
- `data/source_catalog_v74.csv` and `data/historical_urls_v74.csv`: reference datasets. The category-page rows with `requires_adapter` are NOT runnable until a verified adapter/export path exists.
- Queue execution remains serial and operates only while the local program is running. Do not imply background Windows service scheduling.
- Preserve source provenance and user review gate. Avoid modifying `data/products.json` from scraper code.
- Run `python -m unittest discover -s tests -p 'test_*.py' -q` before distribution.
