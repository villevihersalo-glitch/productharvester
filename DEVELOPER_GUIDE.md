## V75 / Alko-adapteri
Lue README_V75.md. Alko-tuontitavat: `alko_file` (CSV/XLSX) ja `alko_mirror` (epävirallinen verkkohinnasto). Älä väitä, että virallinen live-rajapinta tai koko verkkokeruu olisi vahvistettu; älä ohita robots.txt-/HTTP-estoja. Alkon tuotenumero EI ole EAN. Älä julkaise raakahavaintoja suoraan tuotteiksi. Adapteri: `tools/harvester_desktop/alko_v75.py`, testit `tests/test_alko_v75.py`.

# Product Harvester — developer guide

## Architecture

- `run.py`: standalone local browser dashboard entry point (`127.0.0.1` only).
- `harvester_ui/dashboard.{html,css,js}`: minimal client UI, no build system, no CDN.
- `tools/harvester_desktop/server.py`: HTTP endpoints, local file upload, CSV export; AJAX token and origin checks.
- `tools/harvester_desktop/engine.py`: background worker, crawl/import modes, robots gate, read-only source parsing, durable run summary.
- `tools/catalog_harvester/harvester.py`: reusable SQLite observations and EAN canonicalization; old Kaljappi V69 engine.
- `tools/catalog_harvester/source_ingest_v70.py`: retailer-specific CSV/JSON mappings; source configs.
- `tools/catalog_harvester/source_runs_v71.py`: older source-target run tracker.
- `data/catalog_registry_v69.sqlite`: existing historical observation data (do not replace).
- `data/products.json`: 351 published Kaljappi drinks (separate from raw observations).
- `workspace/snapshots/`: locally captured batches, no automatic publication.

## Data flow

`public downloadable export / selected product URLs / sitemap / Shopify JSON` → `raw normalized observations` → `dedupe / status` → `SQLite` → `review CSV` → *(later optional manually approved Kaljappi import)*.

Retain original source URL, original product identifier and evidence; never fabricate EAN or flavor. Raw observations may include out-of-scope categories in generic mode; Kaljappi publication is limited to Finnish-market beers, ciders, long drinks, and hard seltzers. A package-size variation is not a distinct Kaljappi drink. Current sales in Finland must be checked at publish time.

## Adding an adapter

Prefer a source's documented API or export. Where authorized, implement a collector that yields normalized rows (`name`, `brand`, `ean`, `category`, `abv`, `product_url`, `source_product_id`, `units`, `size_ml`). Keep source-specific network parsing in a separate module and import it from `engine.py`; do not hardcode undocumented selectors into the common registry. Add a test with local, redacted fixture data; document paging, coverage and rights. Honor robots.txt, terms, rate limiting and errors. Do not bypass bot protection or invent coverage.

## Bug reports and agent-assisted changes

1. Copy the last error and relevant lines from the dashboard (do not send passwords or private data).
2. Open the project folder in Codex or Claude Code.
3. Ask it to read `AGENTS.md`/`CLAUDE.md`, reproduce the issue, add a regression test and fix the narrow cause.
4. Run all Python tests and JS tests. Compare `data/products.json` count and SQLite `catalog_products` before and after.
5. Changes to collection must not quietly publish or delete Kaljappi products.

## Known limitations

- Python stdlib fetcher does not run client-side JavaScript. Some retailers expose no schema.org Product data in returned HTML; those pages yield 0 until a legitimate source adapter or allowed export is available.
- The engine does not guarantee complete enumeration of all products in a store or market. URL filter, max pages, sitemap coverage and accessibility matter.
- The current `import_file` deduplicates an identical source snapshot by checksum; it deliberately preserves distinct observations across sources and versions.
- Shopify's public `/products.json` may not be available and `page` pagination support varies by store. Stop once an empty or repeating page is reached.
- No browser-rendered login sessions, authenticated API, scraper proxy, CAPTCHA solving or evasion.
- The UI runs only while the terminal process is open, and jobs do not survive a process restart. Completed batches are durable.

### V74 architecture
`workflow_v74.QueueManager` seeds local source entries in `catalog_sources`, schedules due `queue_jobs` serially using existing `HarvesterDesktop.start()` and persists recurrence in SQLite. The local HTTP handlers provide `/api/status`, `/api/queue/add`, `/api/queue/import`, `/api/queue/run`, `/api/queue/toggle`, `/api/queue/remove`, `/api/inventory.csv`, `/api/sources.csv`, `/api/export/target?target=...`. Run-specific files go in `workspace/runs/` and `workspace/snapshots/`. Exported CSVs require manual review. Category pages are metadata until a source-specific allowed adapter exists.
