# HyperTarget Incentive Report Generator

Standalone web app that generates draft tax incentive reports from a company website and Georgia addresses.

## What it does

- Accepts company name, website, and one or more addresses.
- If no address is entered, attempts to auto-detect addresses from website content.
- Scrapes website text and extracts signals (industry, expansion, property).
- Geocodes addresses and maps county -> Georgia Job Tax Credit tier.
- Uses the Georgia DCA ArcGIS webmap layers for spatial checks when available:
  - Tier
  - Military Zone
  - LDCT
  - Opportunity Zone
- Override map source with `ARCGIS_VIEWER_URL` if needed.
- Includes a designation explorer (`/designation-explorer`) that overlays ArcGIS designations on Google Maps and lists businesses in selected zones.
- Produces a report with:
  - Location tier grid
  - Industry profile (typical software + equipment)
  - Opportunity matrix for:
    - Georgia Retraining Tax Credit
    - Federal R&D Credit
    - Georgia R&D Credit
    - Cost Segregation
    - Georgia Investment Tax Credit
    - MERP (self-insured reimbursement strategy)
- Saves every report as JSON and serves an HTML report page.
- Exports reports to Microsoft Word (`.docx`) from the report page.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

To use the map explorer business search, set:

```bash
export GOOGLE_MAPS_API_KEY="your-key"
```

Optional ArcGIS source override:

```bash
export ARCGIS_VIEWER_URL="https://experience.arcgis.com/experience/e655a4ebd5e94cdd9a731822f59d2097"
```

## API

`POST /api/report/generate`

```json
{
  "company_name": "Ace Electric",
  "website": "https://example.com",
  "addresses": [{"raw": "123 Main St, Atlanta, GA 30303"}],
  "notes": "New facility announced in 2025"
}
```

Response:

```json
{
  "report_id": "abc123def456",
  "report_url": "/reports/abc123def456"
}
```

Word export:

- Browser: open `/reports/{report_id}` and click **Download Microsoft Word (.docx)**.
- API path: `GET /reports/{report_id}/download/docx`

## Data notes

- County tiers are read from `app/data/ga_county_tiers.json`.
- Refresh this file each year from the Georgia DCA county ranking memo.
- If you have a CSV source, place it at `app/data/ga_county_tiers_source.csv` with columns `county,tier` and run:

```bash
python scripts/import_ga_tiers.py
```

## Important

This tool generates a draft for analyst review and does not provide legal or tax advice.
