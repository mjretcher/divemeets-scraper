from __future__ import annotations
"""
scraper/sources/meets.py
Discovers NCAA D1 and USA Diving meets on DiveMeets by scanning sequential
meet numbers via meetinfoext.php — the only reliable discovery method since
DiveMeets blocks automated access to their meet list pages.

Strategy:
  1. Scan known meet number ranges (empirically determined)
  2. For each meet page that loads, check if it's NCAA or USA Diving
  3. Filter by year (2018-present)
  4. Skip all others (high school, AAU, club, etc.)

Meet page URL:
  https://secure.meetcontrol.com/divemeets/system/meetinfoext.php?meetnum=XXXXX
"""
import re
import logging
from datetime import datetime, timezone
from bs4 import BeautifulSoup

from scraper.http_client import get_html, BASE_URL

logger = logging.getLogger(__name__)

# ── Year / org filters ────────────────────────────────────────────────────────

START_YEAR = 2018
CURRENT_YEAR = datetime.now().year

# Meet number ranges by approximate year (based on DiveMeets sequential IDs)
# These ranges were determined empirically — meet numbers are assigned sequentially
# as meets are created in the system. Ranges overlap slightly for safety.
MEET_NUM_RANGES = {
    2018: (4500,  5500),
    2019: (5500,  6500),
    2020: (6500,  7200),
    2021: (7200,  8000),
    2022: (8000,  9000),
    2023: (9000, 10200),
    2024: (10200,11500),
    2025: (11500,12800),
    2026: (12800,14000),
}

# ── Meet classification keywords ─────────────────────────────────────────────

NCAA_KEYWORDS = [
    "national collegiate athletic association",
    "ncaa",
    "division i",
    "div i",
    "div. i",
    "conference championship",
    "big ten", "big 12", "pac-12", "pac 12", "acc ",
    "sec ", "american athletic", "mountain west", "ivy league",
    "colonial athletic", "mid-american", "c-usa", "conference usa",
    "horizon league", "patriot league", "atlantic 10", "sun belt",
    "big east", "big west", "southern conference", "southland",
    "zone qualifier", "ncaa zone", "zone a", "zone b", "zone c",
    "zone d", "zone e",
]

USA_KEYWORDS = [
    "usa diving",
    "us diving",
    "u.s. diving",
    "zone championship",
    "zone qualifier",
    "junior nationals",
    "senior nationals",
    "national championships",
    "grand prix",
    "us open diving",
    "u.s. open",
    "olympic trials",
    "world trials",
    "phillips 66",
    "speedo",
    "winter nationals",
    "summer nationals",
]

EXCLUDE_KEYWORDS = [
    "high school", " hs ", "aau ", "ymca", "age group",
    "intrasquad", "jv ", "junior varsity", "club meet",
    "summer league", "nvsl", "cif ", "piaa", "ihsa",
    "community college",
]


def _classify_meet(name: str, org_text: str = "") -> str | None:
    """Returns 'ncaa', 'usa', or None (skip)."""
    combined = (name + " " + org_text).lower()

    if any(kw in combined for kw in EXCLUDE_KEYWORDS):
        return None
    if any(kw in combined for kw in NCAA_KEYWORDS):
        return "ncaa"
    if any(kw in combined for kw in USA_KEYWORDS):
        return "usa"
    return None


def _parse_date(text: str) -> str | None:
    if not text:
        return None
    text = text.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%B %d, %Y",
                "%b %d, %Y", "%m-%d-%Y", "%b. %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    # Try partial like "Mar 13, 2024"
    m = re.search(r"(\w+\.?\s+\d{1,2},?\s+\d{4})", text)
    if m:
        for fmt in ("%B %d, %Y", "%b %d, %Y", "%b. %d, %Y"):
            try:
                return datetime.strptime(m.group(1).replace(",", ""), fmt.replace(",", "")).date().isoformat()
            except ValueError:
                continue
    return None


def _extract_year_from_dates(start_date: str | None, name: str) -> int | None:
    """Extract year from parsed date or meet name."""
    if start_date:
        try:
            return int(start_date[:4])
        except (ValueError, TypeError):
            pass
    # Try to find 4-digit year in name
    m = re.search(r"\b(20\d{2})\b", name)
    if m:
        return int(m.group(1))
    return None


def fetch_meet_page(meet_num: int) -> dict | None:
    """
    Fetch and parse a single meet info page.
    Returns meet dict if it's an NCAA/USA Diving meet in range, else None.
    """
    html = get_html("meetinfoext.php", params={"meetnum": meet_num})
    if not html:
        return None

    # Quick check before full parse — skip if clearly not relevant
    html_lower = html.lower()
    if "meet not found" in html_lower or "no meet" in html_lower:
        return None
    if len(html) < 500:  # empty/error page
        return None

    soup = BeautifulSoup(html, "lxml")
    page_text = soup.get_text(separator=" ", strip=True)
    now = datetime.now(timezone.utc).isoformat()

    # ── Extract meet name ────────────────────────────────────────────────────
    name = None
    for tag in ["h1", "h2", "h3", "title"]:
        el = soup.find(tag)
        if el:
            text = el.get_text(strip=True)
            # Clean up DiveMeets title prefix
            text = re.sub(r"^:+\s*DiveMeets\s*[-–]\s*", "", text, flags=re.I)
            text = re.sub(r"\s*:+$", "", text)
            if text and len(text) > 3 and "divemeets" not in text.lower():
                name = text
                break

    # Fallback: look for bold/strong text at top of page
    if not name:
        for el in soup.find_all(["b", "strong"])[:5]:
            text = el.get_text(strip=True)
            if len(text) > 10 and not text.lower().startswith("important"):
                name = text
                break

    if not name:
        return None

    # ── Classify org ─────────────────────────────────────────────────────────
    # Also check navigation/sidebar which shows which org category this meet is in
    nav_text = ""
    nav = soup.find(["nav", "div"], class_=re.compile(r"nav|menu|sidebar", re.I))
    if nav:
        nav_text = nav.get_text(separator=" ", strip=True)

    org = _classify_meet(name, page_text[:2000])
    if not org:
        return None

    # ── Extract dates ────────────────────────────────────────────────────────
    start_date = end_date = None

    # Date patterns in page text
    date_patterns = [
        r"(\w+\.?\s+\d{1,2},?\s+\d{4})\s*[-–to]+\s*(\w+\.?\s+\d{1,2},?\s+\d{4})",
        r"(\d{1,2}/\d{1,2}/\d{4})\s*[-–]\s*(\d{1,2}/\d{1,2}/\d{4})",
        r"(\w+\.?\s+\d{1,2})\s*[-–]\s*(\d{1,2}),?\s+(\d{4})",  # "Mar 13-16, 2024"
    ]

    for pattern in date_patterns:
        m = re.search(pattern, page_text)
        if m:
            start_date = _parse_date(m.group(1))
            if len(m.groups()) >= 2:
                end_date = _parse_date(m.group(2))
            break

    # Single date fallback
    if not start_date:
        m = re.search(r"(\w+\.?\s+\d{1,2},?\s+\d{4})", page_text)
        if m:
            start_date = _parse_date(m.group(1))

    # ── Year filter ───────────────────────────────────────────────────────────
    year = _extract_year_from_dates(start_date, name)
    if year is None or year < START_YEAR or year > CURRENT_YEAR:
        return None

    # ── Extract location ──────────────────────────────────────────────────────
    city = state = location = None
    loc_match = re.search(r"([A-Za-z\s]+),\s*([A-Z]{2})\b", page_text)
    if loc_match:
        city = loc_match.group(1).strip()
        state = loc_match.group(2)
        location = f"{city}, {state}"

    meet_id = f"{org}_{year}_{meet_num}"
    meet_url = f"{BASE_URL}/meetinfoext.php?meetnum={meet_num}"

    return {
        "meet_id": meet_id,
        "name": name,
        "org": org,
        "year": year,
        "start_date": start_date,
        "end_date": end_date,
        "location": location,
        "city": city,
        "state": state,
        "host_team": None,
        "meet_url": meet_url,
        "scraped_at": now,
        "_meet_num": str(meet_num),
    }


def fetch_meet_list(year: int) -> list[dict]:
    """
    Scan meet numbers for a given year and return qualifying meets.
    Uses the empirical meet number range for that year.
    """
    if year not in MEET_NUM_RANGES:
        logger.warning(f"No meet number range defined for year {year}")
        return []

    start_num, end_num = MEET_NUM_RANGES[year]
    logger.info(f"Scanning meet numbers {start_num}-{end_num} for year {year}")

    meets = []
    consecutive_misses = 0
    MAX_CONSECUTIVE_MISSES = 30  # if 30 in a row return nothing, widen range

    for meet_num in range(start_num, end_num + 1):
        meet = fetch_meet_page(meet_num)

        if meet and meet["year"] == year:
            meets.append(meet)
            consecutive_misses = 0
            logger.debug(f"  ✓ {meet_num}: {meet['name']}")
        else:
            consecutive_misses += 1

        # Log progress every 100 numbers
        if (meet_num - start_num) % 100 == 0:
            logger.info(f"  Progress: {meet_num}/{end_num} — {len(meets)} meets found so far")

    logger.info(f"  Year {year}: found {len(meets)} qualifying meets")
    return meets


def fetch_all_meets() -> dict[tuple, list[dict]]:
    """Fetch meets for all years 2018-present."""
    all_meets: dict[tuple, list[dict]] = {}
    for year in range(START_YEAR, CURRENT_YEAR + 1):
        meets = fetch_meet_list(year)
        for meet in meets:
            key = (meet["org"], meet["year"])
            all_meets.setdefault(key, []).append(meet)
    return all_meets


def upsert_meet(conn, meet: dict):
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
