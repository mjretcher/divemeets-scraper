"""
scraper/sources/events.py
Scrapes events within a meet from DiveMeets.
Handles prelims, semifinals, finals as separate event rows.

Meet page URL:
  https://secure.meetcontrol.com/divemeets/system/meet.php?meetnum=XXXXX

Event results page:
  https://secure.meetcontrol.com/divemeets/system/meetresults.php?meetnum=XXXXX&eventnum=YY
  or
  https://secure.meetcontrol.com/divemeets/system/resultlist.php?meetnum=XXXXX&eventnum=YY
"""
import re
import logging
from datetime import datetime, timezone
from bs4 import BeautifulSoup

from scraper.http_client import get_html, BASE_URL

logger = logging.getLogger(__name__)


# ── Parsing helpers ───────────────────────────────────────────────────────────

_GENDER_MAP = {
    "women": "F", "woman": "F", "girls": "F", "girl": "F",
    "men": "M", "man": "M", "boys": "M", "boy": "M",
    "mixed": "Mixed", "open": "Mixed",
}

_BOARD_MAP = {
    "1 meter": "1m", "1m": "1m", "1-meter": "1m",
    "3 meter": "3m", "3m": "3m", "3-meter": "3m",
    "platform": "platform", "10m": "platform", "10 meter": "platform",
    "tower": "platform", "5m": "5m", "7.5m": "7.5m",
}

_DIVISION_MAP = {
    "prelim": "prelim", "preliminary": "prelim", "preliminaries": "prelim",
    "semi": "semifinal", "semifinal": "semifinal", "semifinals": "semifinal",
    "final": "final", "finals": "final",
}


def _parse_event_name(raw: str):
    """
    Parse event name like "Women's 3-Meter Springboard Finals"
    Returns (gender, board_height, division, num_dives, clean_name)
    """
    raw_lower = raw.lower()

    gender = None
    for kw, val in _GENDER_MAP.items():
        if kw in raw_lower:
            gender = val
            break

    board_height = None
    for kw, val in _BOARD_MAP.items():
        if kw in raw_lower:
            board_height = val
            break

    division = None
    for kw, val in _DIVISION_MAP.items():
        if kw in raw_lower:
            division = val
            break

    # Num dives: look for "6 dives" or "11 dives" pattern
    num_dives = None
    nd_match = re.search(r"(\d+)\s*dives?", raw_lower)
    if nd_match:
        num_dives = int(nd_match.group(1))

    return gender, board_height, division, num_dives, raw.strip()


def fetch_meet_events(meet_id: str, meet_num: str) -> list[dict]:
    """
    Fetch all events for a meet.
    Returns list of event dicts.
    """
    logger.info(f"Fetching events for meet {meet_id} (meetnum={meet_num})")

    html = get_html("meet.php", params={"meetnum": meet_num})
    if not html:
        logger.warning(f"No response for meet {meet_num}")
        return []

    soup = BeautifulSoup(html, "lxml")
    events = []
    now = datetime.now(timezone.utc).isoformat()

    # DiveMeets meet page lists events as links to results pages
    # Pattern: meetresults.php?meetnum=XXX&eventnum=YY
    # or:      resultlist.php?meetnum=XXX&eventnum=YY
    seen_event_nums = set()

    for link in soup.find_all("a", href=re.compile(r"(meetresults|resultlist)\.php")):
        href = link.get("href", "")

        event_num_match = re.search(r"eventnum=(\d+)", href)
        if not event_num_match:
            continue

        event_num = event_num_match.group(1)
        if event_num in seen_event_nums:
            continue
        seen_event_nums.add(event_num)

        raw_name = link.get_text(strip=True)
        if not raw_name:
            continue

        gender, board_height, division, num_dives, clean_name = _parse_event_name(raw_name)

        event_id = f"{meet_id}_evt{event_num}"

        # Build event results URL
        if "resultlist.php" in href:
            event_url = f"{BASE_URL}/resultlist.php?meetnum={meet_num}&eventnum={event_num}"
        else:
            event_url = f"{BASE_URL}/meetresults.php?meetnum={meet_num}&eventnum={event_num}"

        events.append({
            "event_id": event_id,
            "meet_id": meet_id,
            "event_num": int(event_num),
            "name": clean_name,
            "gender": gender,
            "board_height": board_height,
            "division": division,
            "num_dives": num_dives,
            "event_url": event_url,
            "scraped_at": now,
            "_meet_num": meet_num,
            "_event_num": event_num,
        })

    logger.info(f"  Found {len(events)} events for meet {meet_id}")
    return events


def upsert_event(conn, event: dict):
    """Insert or update an event record."""
    conn.execute("""
        INSERT INTO events
            (event_id, meet_id, event_num, name, gender, board_height,
             division, num_dives, event_url, scraped_at)
        VALUES
            (:event_id, :meet_id, :event_num, :name, :gender, :board_height,
             :division, :num_dives, :event_url, :scraped_at)
        ON CONFLICT(event_id) DO UPDATE SET
            name=excluded.name,
            gender=excluded.gender,
            board_height=excluded.board_height,
            division=excluded.division,
            num_dives=excluded.num_dives,
            scraped_at=excluded.scraped_at
    """, event)
