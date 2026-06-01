# DiveMeets Scraper

Automated scraper for [DiveMeets.com](https://secure.meetcontrol.com/divemeets/system/) — collects NCAA Division I and USA Diving meet data from 2018 to present and stores it in analytics-ready SQLite databases.

---

## What it collects

| Entity | What's captured |
|--------|----------------|
| **Meets** | Name, org, dates, location, host team |
| **Events** | Gender, board height (1m/3m/platform), division (prelim/semi/final) |
| **Results** | Place, total score, prelim/semifinal/final splits, qualified flag |
| **Diver profiles** | Full name, team, hometown, state, coach, gender |
| **Dive sheets** | Dive code, name, position (tuck/pike/straight/free), DD, judge scores, net score, total points |
| **Team standings** | Team name, place, total points per meet |

---

## Database structure

One SQLite file per org per year:

```
data/
  ncaa_2018.db
  ncaa_2019.db
  ...
  ncaa_2025.db
  usa_2018.db
  ...
  usa_2025.db
```

Each DB has these tables:

```
meets → events → results → dive_sheets
                         → divers
meets → team_standings
scrape_log  (tracks what's been scraped — enables resuming)
```

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/divemeets-scraper.git
cd divemeets-scraper
pip install -r requirements.txt
python setup.py
```

### 2. First run — historical backfill

This scrapes all 7+ years of data. It takes several hours. Run it in the background or overnight:

```bash
# All years, all orgs, skip dive sheets for speed (add them later)
python -m scraper.main --no-dive-sheets

# Or just one year to test
python -m scraper.main --year 2024 --org ncaa
```

### 3. Dive sheets (optional — adds significant data volume)

```bash
python -m scraper.main --phase 5 --year 2024
```

---

## Running manually

```bash
# Full run — current year only
python -m scraper.main --year 2025

# Specific org and year
python -m scraper.main --org ncaa --year 2024

# Specific phase only
python -m scraper.main --phase 3 --year 2024   # results only
python -m scraper.main --phase 4               # diver profiles only

# Fastest mode (no dive sheets)
python -m scraper.main --no-dive-sheets
```

**Phases:**
1. Meet discovery
2. Event discovery (runs with phase 1)
3. Results & scores
4. Diver profiles
5. Dive sheets
6. Team standings

---

## Exporting to CSV

The scraper stores everything in SQLite. Export to CSV when you need Excel, Google Sheets, or pandas:

```bash
# Export all tables for NCAA 2024
python -m export.export_csv --org ncaa --year 2024

# Export only results and events
python -m export.export_csv --org ncaa --year 2024 --tables results events

# Export everything across all years
python -m export.export_csv --all

# Export all data for one diver (searches by name, all years/orgs)
python -m export.export_csv --diver "Emma Smith"

# Custom output directory
python -m export.export_csv --org usa --year 2023 --out-dir /path/to/my/analytics
```

Files land in `exports/org_year/table.csv` — flat, human-readable, ready to open in Excel.

---

## Using with pandas

```python
import sqlite3
import pandas as pd

# Connect to a specific DB
conn = sqlite3.connect("data/ncaa_2024.db")

# Load results with full context
df = pd.read_sql("""
    SELECT
        m.name AS meet, m.start_date,
        e.name AS event, e.board_height, e.division,
        d.full_name AS diver, r.team,
        r.place, r.total_score
    FROM results r
    JOIN meets m ON r.meet_id = m.meet_id
    JOIN events e ON r.event_id = e.event_id
    JOIN divers d ON r.diver_id = d.diver_id
    ORDER BY m.start_date, e.event_num, r.place
""", conn)

# Top scores on 3m springboard finals
top_3m = df[
    (df["board_height"] == "3m") &
    (df["division"] == "final")
].sort_values("total_score", ascending=False).head(20)

# Diver career results
diver = df[df["diver"].str.contains("Smith", case=False)]
```

---

## GitHub Actions — automatic weekly run

The scraper runs every **Monday at 6:00 AM UTC** automatically.

Results are committed back to the repo as updated `.db` files.

### Manual trigger

Go to **Actions → DiveMeets Scraper → Run workflow** and optionally set:
- Year (blank = current + previous year)
- Org (both / ncaa / usa)
- Phase (blank = all phases)
- Skip dive sheets (for faster runs)

### GitHub Secrets required

None required — DiveMeets is a public site. No API keys needed.

### Git LFS (recommended for large DBs)

After 2-3 years of data, your DBs will be 100MB+. Set up Git LFS:

```bash
git lfs install
git lfs track "*.db"
git add .gitattributes
git commit -m "chore: track .db files with Git LFS"
```

Then enable LFS in your GitHub repository settings.

---

## Data volume estimates

| Scope | Meets | Events | Results | Dive Sheets |
|-------|-------|--------|---------|-------------|
| NCAA D1 per year | ~50-80 | ~400-600 | ~8,000-15,000 | ~80,000-150,000 |
| USA Diving per year | ~40-60 | ~300-500 | ~6,000-12,000 | ~60,000-120,000 |
| 7 years combined | ~700-1,000 | ~5,000-8,000 | ~100,000-190,000 | ~1M-2M |

**Estimated DB sizes:** ~5-15MB per org per year without dive sheets; ~50-150MB with dive sheets.

---

## Rate limiting & robots.txt

The scraper:
- Waits **1.5 seconds between every request**
- Identifies itself with a descriptive User-Agent
- Skips already-scraped entities (fully resumable)
- Respects DiveMeets server availability (retries with exponential backoff)

DiveMeets is a public results platform — this scraper collects only publicly available competition data.

---

## File structure

```
divemeets-scraper/
├── scraper/
│   ├── main.py              # Orchestrator — run this
│   ├── http_client.py       # Rate-limited HTTP session
│   └── sources/
│       ├── meets.py         # Phase 1: meet discovery
│       ├── events.py        # Phase 2: event discovery
│       ├── results.py       # Phase 3: results & scores
│       ├── divers.py        # Phase 4: diver profiles
│       ├── dive_sheets.py   # Phase 5: individual dives
│       └── standings.py     # Phase 6: team standings
├── storage/
│   └── db.py                # Schema + DB connection manager
├── export/
│   └── export_csv.py        # On-demand CSV exporter
├── data/                    # SQLite databases (git-ignored or LFS)
│   ├── ncaa_2024.db
│   └── usa_2024.db
├── exports/                 # CSV exports (git-ignored)
├── setup.py                 # Setup validator
├── requirements.txt
└── .github/
    └── workflows/
        └── scraper.yml      # Weekly GitHub Actions run
```
