## V75 / Alko-adapteri
Lue README_V75.md. Alko-tuontitavat: `alko_file` (CSV/XLSX) ja `alko_mirror` (epävirallinen verkkohinnasto). Älä väitä, että virallinen live-rajapinta tai koko verkkokeruu olisi vahvistettu; älä ohita robots.txt-/HTTP-estoja. Alkon tuotenumero EI ole EAN. Älä julkaise raakahavaintoja suoraan tuotteiksi. Adapteri: `tools/harvester_desktop/alko_v75.py`, testit `tests/test_alko_v75.py`.

# Claude Code project context

Follow `AGENTS.md` and `DEVELOPER_GUIDE.md`. This is a Python 3.10+ offline-first browser-based desktop app; start with `python run.py`. The new UI runs on `127.0.0.1:8765`, uses `tools/harvester_desktop/` backend and `harvester_ui/` frontend. Source snapshots are imported into the legacy SQLite observation registry; published Kaljappi products are a separate, protected dataset. Do not claim a whole retailer inventory was scraped unless a complete source-specific batch actually succeeds.

When asked for fixes, reproduce, make minimal changes, add a test, run unittest and node tests. Scraping must honor robots.txt, access restrictions and server rate limits, never use CAPTCHA/account bypass. The user prefers simple, privacy-friendly, account-free software that also works without AI.

V74 adds the durable sequential queue in `tools/harvester_desktop/workflow_v74.py`, the 12-source `source_catalog_v74.json`, and source inventory spreadsheets/CSVs. Do not mark category listing URLs as fully crawlable: all are `requires_adapter` until proven otherwise. Scheduler only runs while local UI is running. Target exports are **review-only**, never publication.
