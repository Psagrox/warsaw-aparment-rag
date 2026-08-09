# Warsaw Apartments RAG Pipeline & Query Agent

A RAG (Retrieval-Augmented Generation) pipeline for scraping, vectorizing, and querying Warsaw apartment listings.

---

## 🛠️ Quick Start / Setup from Scratch

Follow these steps to clone and set up the repository from scratch:

### 1. Prerequisites
- **Python 3.9+** installed
- **Git** installed
- Account API keys for:
  - **OpenAI** (for embeddings and Facebook post parsing)
  - **Supabase** (PostgreSQL database instance with vector search)
  - **Apify** *(Optional, required for Facebook Groups scraper)*: Free account token from [Apify Console](https://console.apify.com/account/integrations)

### 2. Clone the Repository
```bash
git clone https://github.com/YOUR_USERNAME/warsaw-aparment-rag.git
cd warsaw-aparment-rag
```

### 3. Create & Activate a Virtual Environment

- **Windows (PowerShell / Command Prompt):**
  ```powershell
  python -m venv venv
  .\venv\Scripts\activate
  ```
- **macOS / Linux:**
  ```bash
  python3 -m venv venv
  source venv/bin/activate
  ```

### 4. Install Dependencies
```bash
pip install -r requirements.txt
```

> 💡 **For Facebook Scraper:** Install the optional `apify-client` package:
> ```bash
> pip install apify-client
> ```

### 5. Environment Configuration
Create a `.env` file in the project root directory and define the following variables:

```env
SUPABASE_URL=https://your-supabase-project.supabase.co
SUPABASE_KEY=your-supabase-api-key
OPENAI_API_KEY=your-openai-api-key

# Facebook Groups Scraper (Apify Integration):
APIFY_API_TOKEN=your-apify-api-token
# Optional: comma-separated custom Facebook group URLs (overrides defaults):
# FB_GROUP_URLS=https://www.facebook.com/groups/mieszkania.na.sprzedaz.warszawa/,https://www.facebook.com/groups/mieszkaniawarszawasprzedam/

# Optional (if using Google Sheets export feature):
GOOGLE_SHEET_ID=your_google_sheet_id
GOOGLE_CREDENTIALS_FILE=credentials.json
```

---

## 🚀 How to Run the Commands

Commands must be executed from the **project root directory**.

> 💡 **Note on spelling:** Ensure you type `python` (not `pyhton`).

---

## 1. ETL Pipeline / Scraper (`src/main.py`)

The ETL script scrapes active real estate portals concurrently, performs delta loading against Supabase (to avoid re-vectorizing existing listings), generates 1536-dimensional embeddings with OpenAI, and updates the database.

### CLI Flags & Arguments (`src/main.py`)

| Flag / Option | Short | Description | Example |
|---|---|---|---|
| `--pages` | `-p` | Number of search result pages to scrape per portal (default: `1`). | `python -m src.main -p 3` |
| `--portals` | | Only scrape specific portal(s). Options: `otodom`, `olx`, `adresowo`, `nieruchomosci-online` (or `no`), `morizon`, `freedom`, `facebook`. | `python -m src.main --portals otodom facebook` |
| `--exclude-portal` | `--exclude` | Exclude specific portal scraper(s). | `python -m src.main --exclude morizon freedom facebook` |

### Basic Execution Command
```bash
python -m src.main
# OR
python src/main.py
```

### Examples (`src/main.py`)
```bash
# Scrape 2 pages from Otodom and OLX only
python -m src.main --pages 2 --portals otodom olx

# Scrape Facebook Groups scraper only
python -m src.main --portals facebook

# Scrape 5 pages across all portals except Morizon, Freedom, and Facebook
python -m src.main -p 5 --exclude morizon freedom facebook
```

---

## 📘 Facebook Groups Scraper Setup & Details

The **Facebook Scraper** fetches real estate listing posts from public/private Warsaw real estate Facebook groups using Apify and uses OpenAI (GPT-4o-mini) to extract structured offer metadata (price, sqm, number of rooms, district, dates).

### Prerequisites & Setup
1. **Get an Apify API Token:**
   - Sign up for a free account on [Apify](https://apify.com/).
   - Navigate to **Settings > Integrations** (or [console.apify.com/account/integrations](https://console.apify.com/account/integrations)) and copy your API Token.
2. **Add to `.env`:**
   ```env
   APIFY_API_TOKEN=apify_api_...
   ```
3. **Install `apify-client`:**
   ```bash
   pip install apify-client
   ```

### Running the Facebook Scraper
To run only the Facebook Groups scraper:
```bash
python -m src.main --portals facebook
```

### Customizing Facebook Groups
By default, the scraper target pre-configured Warsaw apartment sale groups:
- `https://www.facebook.com/groups/mieszkania.na.sprzedaz.warszawa/`
- `https://www.facebook.com/groups/mieszkaniawarszawasprzedam/`
- `https://www.facebook.com/groups/837545249614246/`
- `https://www.facebook.com/groups/633977609988127/`
- `https://www.facebook.com/groups/1692547524296119/`

To scrape custom groups, set `FB_GROUP_URLS` in `.env` as a comma-separated list:
```env
FB_GROUP_URLS=https://www.facebook.com/groups/group1/,https://www.facebook.com/groups/group2/
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
| `--portals` | | Filter search to specific portal(s) (`otodom`, `olx`, `adresowo`, `nieruchomosci-online`, `morizon`, `freedom`, `facebook`). | `python -m src.query_agent --portals otodom facebook` |
| `--exclude-portal` | `--exclude` | Exclude specific portal(s) from search results. | `python -m src.query_agent --exclude freedom facebook` |
| `--exclude-districts` | `--exclude-district` | Exclude specific district(s) (defaults: `białołęka`, `rembertów`). | `python -m src.query_agent --exclude-districts wola ursus` |
| `--include-all-districts` | | Disable default district filtering and include all Warsaw districts. | `python -m src.query_agent --include-all-districts` |
| `--no-cache` | | Bypass local disk cache (`.cache/query_agent_cache.json`) and query Supabase fresh. | `python -m src.query_agent --no-cache` |
| `--export-sheets` | `--gsheet` | Export/append matching apartment rows directly into a Google Sheet. | `python -m src.query_agent --export-sheets` |
| `--sheet-id` | | Google Sheet ID to append results to (defaults to `GOOGLE_SHEET_ID` from `.env`). | `python -m src.query_agent --export-sheets --sheet-id <YOUR_SHEET_ID>` |

### Examples (`src/query_agent.py`)
```bash
# Query new listings added in the last 2 days from Otodom and Facebook
python -m src.query_agent --new --days 2 --portals otodom facebook

# Fetch page 2 (results 21-40) including all Warsaw districts
python -m src.query_agent --page 2 --limit 20 --include-all-districts

# Run fresh query bypassing local cache and export to Google Sheets
python -m src.query_agent --no-cache --export-sheets
```

