"""
scraper/sources/divers.py
Scrapes full diver profiles from DiveMeets.
Enriches the minimal diver records created during results scraping.

Profile URL:
  https://secure.meetcontrol.com/divemeets/system/profile.php?divernum=XXXXX
"""
import re
import logging
from datetime import datetime, timezone
from bs4 import BeautifulSoup

from scraper.http_client import get_html, BASE_URL

logger = logging.getLogger(__name__)


_US_STATES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA",
    "KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
    "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT",
    "VA","WA","WV","WI","WY","DC",
}


def _extract_state(text: str) -> str | None:
    """Find a 2-letter US state abbreviation in a text string."""
    if not text:
        return None
    for part in text.split(","):
        part = part.strip()
        if part.upper() in _US_STATES:
            return part.upper()
    m = re.search(r"\b([A-Z]{2})\b", text)
    if m and m.group(1) in _US_STATES:
        return m.group(1)
    return None


def fetch_diver_profile(diver_id: str, profile_url: str) -> dict | None:
    """
    Fetch and parse a diver's full profile page.
    Returns enriched diver dict or None.
    """
    if not profile_url:
        return None

    logger.debug(f"  Fetching profile for diver {diver_id}")
    html = get_html(full_url=profile_url)
    if not html:
        return None

    soup = BeautifulSoup(html, "lxml")
    now = datetime.now(timezone.utc).isoformat()

    profile: dict = {
        "diver_id": diver_id,
        "first_name": None,
        "last_name": None,
        "full_name": None,
        "gender": None,
        "hometown": None,
        "home_state": None,
        "country": "USA",
        "team": None,
        "coach": None,
        "profile_url": profile_url,
        "scraped_at": now,
    }

    # Name: usually in <h1> or <h2> or a prominent heading
    for tag in ["h1", "h2", "h3"]:
        el = soup.find(tag)
        if el:
            name = el.get_text(strip=True)
            if name and len(name) > 2 and not name.lower().startswith("meet"):
                profile["full_name"] = name
                parts = name.split()
                if len(parts) >= 2:
                    profile["first_name"] = " ".join(parts[:-1])
                    profile["last_name"] = parts[-1]
                break

    # DiveMeets profile pages typically have a definition list or table
    # with fields like: Team, Coach, Hometown, Age, Gender, etc.

    # Try definition list pattern (dl/dt/dd)
    dl = soup.find("dl")
    if dl:
        terms = dl.find_all("dt")
        defs = dl.find_all("dd")
        for term, defn in zip(terms, defs):
            key = term.get_text(strip=True).lower().rstrip(":")
            val = defn.get_text(strip=True)
            _map_profile_field(profile, key, val)

    # Try table-based profile layout
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) >= 2:
            key = cells[0].get_text(strip=True).lower().rstrip(":")
            val = cells[1].get_text(strip=True)
            if val and key:
                _map_profile_field(profile, key, val)

    # Gender inference from page text if still missing
    if not profile["gender"]:
        page_text = soup.get_text()
        if re.search(r"\bwomen'?s?\b|\bfemale\b", page_text, re.I):
            profile["gender"] = "F"
        elif re.search(r"\bmen'?s?\b|\bmale\b", page_text, re.I):
            profile["gender"] = "M"

    # State from hometown
    if profile["hometown"] and not profile["home_state"]:
        profile["home_state"] = _extract_state(profile["hometown"])

    # Validate we got a name
    if not profile["full_name"]:
        logger.debug(f"  Could not parse name for diver {diver_id}")
        return None

    return profile


def _map_profile_field(profile: dict, key: str, val: str):
    """Map a parsed key-value pair to the profile dict."""
    if not val or val == "-":
        return

    if "team" in key or "school" in key or "club" in key or "college" in key:
        profile["team"] = val
    elif "coach" in key:
        profile["coach"] = val
    elif "hometown" in key or "home town" in key or "city" in key:
        profile["hometown"] = val
        profile["home_state"] = _extract_state(val)
    elif "state" in key:
        profile["home_state"] = val
    elif "country" in key:
        profile["country"] = val
    elif "gender" in key or "sex" in key:
        v = val.lower()
        if v in ("f", "female", "w", "women", "woman"):
            profile["gender"] = "F"
        elif v in ("m", "male", "men", "man"):
            profile["gender"] = "M"
    elif "name" in key and not profile["full_name"]:
        profile["full_name"] = val
        parts = val.split()
        if len(parts) >= 2:
            profile["first_name"] = " ".join(parts[:-1])
            profile["last_name"] = parts[-1]


def get_unscraped_diver_ids(conn) -> list[tuple[str, str | None]]:
    """
    Return (diver_id, profile_url) for divers not yet in scrape_log.
    Skips divers with no profile_url (synthetic IDs with no DiveMeets page).
    """
    rows = conn.execute("""
        SELECT d.diver_id, d.profile_url
        FROM divers d
        LEFT JOIN scrape_log sl
            ON sl.entity_type = 'diver' AND sl.entity_id = d.diver_id
        WHERE sl.id IS NULL
          AND d.profile_url IS NOT NULL
        ORDER BY d.diver_id
    """).fetchall()
    return [(r["diver_id"], r["profile_url"]) for r in rows]


def upsert_diver_full(conn, profile: dict):
    """Update diver with full profile data."""
    conn.execute("""
        INSERT INTO divers
            (diver_id, first_name, last_name, full_name, gender, hometown,
             home_state, country, team, coach, profile_url, scraped_at)
        VALUES
            (:diver_id, :first_name, :last_name, :full_name, :gender, :hometown,
             :home_state, :country, :team, :coach, :profile_url, :scraped_at)
        ON CONFLICT(diver_id) DO UPDATE SET
            first_name=excluded.first_name,
            last_name=excluded.last_name,
            full_name=excluded.full_name,
            gender=excluded.gender,
            hometown=excluded.hometown,
            home_state=excluded.home_state,
            country=excluded.country,
            team=excluded.team,
            coach=excluded.coach,
            scraped_at=excluded.scraped_at
    """, profile)
