# Warsaw Apartments RAG Pipeline & Query Agent

A RAG (Retrieval-Augmented Generation) pipeline for scraping, vectorizing, and querying Warsaw apartment listings.

---

## 🚀 How to Run the Commands

Commands must be executed from the **project root directory** (`c:\Users\walja\Desktop\warsaw-aparment-rag`).

> 💡 **Note on spelling:** Ensure you type `python` (not `pyhton`).

---

## 1. ETL Pipeline / Scraper (`src/main.py`)

The ETL script scrapes active real estate portals concurrently, performs delta loading against Supabase (to avoid re-vectorizing existing listings), generates 1536-dimensional embeddings with OpenAI, and updates the database.

### Basic Execution Command
```bash
python -m src.main
# OR
python src/main.py
```

### CLI Flags & Arguments (`src/main.py`)

| Flag / Option | Short | Description | Example |
|---|---|---|---|
| `--pages` | `-p` | Number of search result pages to scrape per portal (default: `1`). | `python -m src.main -p 3` |
| `--portals` | | Only scrape specific portal(s). Options: `otodom`, `olx`, `adresowo`, `nieruchomosci-online` (or `no`), `morizon`, `freedom`. | `python -m src.main --portals otodom olx` |
| `--exclude-portal` | `--exclude` | Exclude specific portal scraper(s). | `python -m src.main --exclude morizon freedom` |

### Examples (`src/main.py`)
```bash
# Scrape 2 pages from Otodom and OLX only
python -m src.main --pages 2 --portals otodom olx

# Scrape 5 pages across all portals except Morizon and Freedom
python -m src.main -p 5 --exclude morizon freedom
```

---

## 2. Query Agent (`src/query_agent.py`)

The query agent retrieves top apartment recommendations from Supabase vector search using natural language embeddings, filtered by criteria (district exclusions, date filters, price/area bounds, portal filters, and caching).

### Basic Execution Command
```bash
python -m src.query_agent
# OR
python src/query_agent.py
```

### CLI Flags & Arguments (`src/query_agent.py`)

| Flag / Option | Short | Description | Example |
|---|---|---|---|
| `--new` | | Show ONLY new apartments posted within recent days. | `python -m src.query_agent --new` |
| `--days` | | Number of days to classify an apartment as new when using `--new` (default: `3`). | `python -m src.query_agent --new --days 5` |
| `--limit` | | Number of results to display per page (default: `20`). | `python -m src.query_agent --limit 50` |
| `--offset` | | Number of initial results to skip for pagination. | `python -m src.query_agent --offset 20 --limit 20` |
| `--page` | | Page number for pagination (e.g. page 2 with limit 20 sets offset to 20). | `python -m src.query_agent --page 2 --limit 20` |
| `--full` | `--all` | Print the FULL list of matching apartments without page limit. | `python -m src.query_agent --full` |
| `--portals` | | Filter search to specific portal(s) (`otodom`, `olx`, `adresowo`, `nieruchomosci-online`, `morizon`, `freedom`). | `python -m src.query_agent --portals otodom olx` |
| `--exclude-portal` | `--exclude` | Exclude specific portal(s) from search results. | `python -m src.query_agent --exclude freedom` |
| `--exclude-districts` | `--exclude-district` | Exclude specific district(s) (defaults: `białołęka`, `rembertów`). | `python -m src.query_agent --exclude-districts wola ursus` |
| `--include-all-districts` | | Disable default district filtering and include all Warsaw districts. | `python -m src.query_agent --include-all-districts` |
| `--no-cache` | | Bypass local disk cache (`.cache/query_agent_cache.json`) and query Supabase fresh. | `python -m src.query_agent --no-cache` |
| `--export-sheets` | `--gsheet` | Export/append matching apartment rows directly into a Google Sheet. | `python -m src.query_agent --export-sheets` |
| `--sheet-id` | | Google Sheet ID to append results to (defaults to `GOOGLE_SHEET_ID` from `.env`). | `python -m src.query_agent --export-sheets --sheet-id <YOUR_SHEET_ID>` |

### Examples (`src/query_agent.py`)
```bash
# Query new listings added in the last 2 days from Otodom only
python -m src.query_agent --new --days 2 --portals otodom

# Fetch page 2 (results 21-40) including all Warsaw districts
python -m src.query_agent --page 2 --limit 20 --include-all-districts

# Run fresh query bypassing local cache and export to Google Sheets
python -m src.query_agent --no-cache --export-sheets
```
