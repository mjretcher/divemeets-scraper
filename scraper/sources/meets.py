"""
scraper/sources/meets.py
Scrapes the DiveMeets meet listing pages to discover all meets.
Filters for: NCAA (D1) and USA Diving, years 2018-present.

DiveMeets meet list URL:
  https://secure.meetcontrol.com/divemeets/system/meetlist.php
  Params: ?seasonid=YYYY  (season year)

Meet detail page:
  https://secure.meetcontrol.com/divemeets/system/meet.php?meetnum=XXXXX
"""
import re
import logging
from datetime import datetime, timezone
from bs4 import BeautifulSoup

from scraper.http_client import get_html, BASE_URL

logger = logging.getLogger(__name__)

# ── Filter constants ──────────────────────────────────────────────────────────

START_YEAR = 2018
CURRENT_YEAR = datetime.now().year

# Keywords that indicate an NCAA D1 meet
NCAA_KEYWORDS = [
    "ncaa", "division i", "div i", "div. i", "d1", "d-1",
    "conference", "big ten", "big 12", "pac-12", "pac 12", "acc ",
    "sec ", "american athletic", "mountain west", "ivy league",
    "colonial", "mid-american", "c-usa", "conference usa",
]

# Keywords that indicate a USA Diving meet
USA_KEYWORDS = [
    "usa diving", "us diving", "u.s. diving", "zone", "zones",
    "junior nationals", "senior nationals", "nationals", "grand prix",
    "us open", "u.s. open", "olympic trials", "world trials",
    "phillips 66", "speedo",
]

# Hard excludes — high school, club, rec meets we don't want
EXCLUDE_KEYWORDS = [
    "high school", "hs ", " hs ", "aau ", "ymca", "age group",
    "intrasquad", "invitational jv",
]


def _classify_meet(name: str) -> str | None:
    """
    Returns 'ncaa', 'usa', or None (skip).
    NCAA takes priority if both match.
    """
    name_lower = name.lower()

    if any(kw in name_lower for kw in EXCLUDE_KEYWORDS):
        return None

    if any(kw in name_lower for kw in NCAA_KEYWORDS):
        return "ncaa"

    if any(kw in name_lower for kw in USA_KEYWORDS):
        return "usa"

    return None


def _parse_date(text: str) -> str | None:
    """Try to parse various date formats DiveMeets uses."""
    if not text:
        return None
    text = text.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text  # return raw if unparseable


def fetch_meet_list(year: int) -> list[dict]:
    """
    Fetch all meets for a given season year.
    Returns list of normalized meet dicts.
    """
    logger.info(f"Fetching meet list for year {year}")
    html = get_html("meetlist.php", params={"seasonid": year})
    if not html:
        logger.warning(f"No response for year {year}")
        return []

    soup = BeautifulSoup(html, "lxml")
    meets = []
    now = datetime.now(timezone.utc).isoformat()

    # DiveMeets meet list: table rows with meet links
    # Each row typically: Meet Name | Dates | Location | Host
    for row in soup.select("table tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue

        # Find the meet link — href contains meetnum=
        link = row.find("a", href=re.compile(r"meet\.php\?meetnum=\d+"))
        if not link:
            # Also check for meetinfo.php pattern
            link = row.find("a", href=re.compile(r"meetnum=\d+"))
        if not link:
            continue

        name = link.get_text(strip=True)
        if not name:
            continue

        org = _classify_meet(name)
        if not org:
            continue

        # Extract meet ID from URL
        href = link.get("href", "")
        meet_id_match = re.search(r"meetnum=(\d+)", href)
        if not meet_id_match:
            continue
        meet_num = meet_id_match.group(1)
        meet_id = f"{org}_{year}_{meet_num}"

        # Parse remaining cells for dates/location
        cell_texts = [c.get_text(strip=True) for c in cells]

        start_date = end_date = None
        location = city = state = host_team = ""

        # Try to find date range in cells — DiveMeets format varies by year
        for text in cell_texts:
            # Date range like "01/15/2024 - 01/17/2024" or "Jan 15-17, 2024"
            date_range = re.search(
                r"(\d{1,2}/\d{1,2}/\d{4})\s*[-–]\s*(\d{1,2}/\d{1,2}/\d{4})", text
            )
            if date_range:
                start_date = _parse_date(date_range.group(1))
                end_date = _parse_date(date_range.group(2))
                continue

            single_date = re.search(r"\d{1,2}/\d{1,2}/\d{4}", text)
            if single_date and not start_date:
                start_date = _parse_date(single_date.group())
                continue

            # Location: "City, ST" pattern
            loc_match = re.search(r"([A-Za-z\s]+),\s*([A-Z]{2})", text)
            if loc_match and not city:
                city = loc_match.group(1).strip()
                state = loc_match.group(2).strip()
                location = f"{city}, {state}"

        meet_url = f"{BASE_URL}/meet.php?meetnum={meet_num}"

        meets.append({
            "meet_id": meet_id,
            "name": name,
            "org": org,
            "year": year,
            "start_date": start_date,
            "end_date": end_date,
            "location": location or None,
            "city": city or None,
            "state": state or None,
            "host_team": host_team or None,
            "meet_url": meet_url,
            "scraped_at": now,
            "_meet_num": meet_num,  # internal use for event scraping
        })

    logger.info(f"  Found {len(meets)} qualifying meets for {year}")
    return meets


def fetch_all_meets() -> dict[tuple, list[dict]]:
    """
    Fetch meets for all years 2018-present.
    Returns dict keyed by (org, year) → list of meets.
    """
    all_meets: dict[tuple, list[dict]] = {}

    for year in range(START_YEAR, CURRENT_YEAR + 1):
        meets = fetch_meet_list(year)
        for meet in meets:
            key = (meet["org"], meet["year"])
            all_meets.setdefault(key, []).append(meet)

    return all_meets


def upsert_meet(conn, meet: dict):
    """Insert or update a meet record."""
    conn.execute("""
        INSERT INTO meets
            (meet_id, name, org, year, start_date, end_date, location,
             city, state, host_team, meet_url, scraped_at)
        VALUES
            (:meet_id, :name, :org, :year, :start_date, :end_date, :location,
             :city, :state, :host_team, :meet_url, :scraped_at)
        ON CONFLICT(meet_id) DO UPDATE SET
            name=excluded.name,
            start_date=excluded.start_date,
            end_date=excluded.end_date,
            location=excluded.location,
            city=excluded.city,
            state=excluded.state,
            host_team=excluded.host_team,
            scraped_at=excluded.scraped_at
    """, meet)
