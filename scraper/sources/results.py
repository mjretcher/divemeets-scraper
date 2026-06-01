"""
scraper/sources/results.py
Scrapes per-diver results for each event.
Captures: place, total score, prelim/semifinal/final splits, team, diver info.

Results page URLs:
  https://secure.meetcontrol.com/divemeets/system/meetresults.php?meetnum=XXX&eventnum=YY
  https://secure.meetcontrol.com/divemeets/system/resultlist.php?meetnum=XXX&eventnum=YY
"""
import re
import json
import logging
from datetime import datetime, timezone
from bs4 import BeautifulSoup

from scraper.http_client import get_html, BASE_URL

logger = logging.getLogger(__name__)


def _safe_float(text: str) -> float | None:
    if not text:
        return None
    try:
        return float(re.sub(r"[^\d.\-]", "", text))
    except (ValueError, TypeError):
        return None


def _safe_int(text: str) -> int | None:
    if not text:
        return None
    try:
        cleaned = re.sub(r"[^\d]", "", text)
        return int(cleaned) if cleaned else None
    except (ValueError, TypeError):
        return None


def _extract_diver_id(href: str) -> str | None:
    """Extract diver profile ID from link href."""
    # Patterns: ?divernum=XXX  or ?diverid=XXX  or profileID=XXX
    for pattern in [r"divernum=(\d+)", r"diverid=(\d+)", r"profileid=(\d+)", r"diverId=(\d+)"]:
        m = re.search(pattern, href, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def fetch_event_results(event: dict) -> tuple[list[dict], list[dict]]:
    """
    Fetch results for one event.
    Returns (results_list, divers_list).
    Both are dicts ready for DB upsert.
    """
    meet_id = event["meet_id"]
    event_id = event["event_id"]
    event_url = event["event_url"]
    meet_num = event["_meet_num"]
    event_num = event["_event_num"]

    logger.info(f"  Fetching results for event {event_id}")

    html = get_html(full_url=event_url)
    if not html:
        logger.warning(f"  No response for event {event_url}")
        return [], []

    soup = BeautifulSoup(html, "lxml")
    results = []
    divers = []
    now = datetime.now(timezone.utc).isoformat()

    # DiveMeets results tables vary in structure by meet type.
    # Common patterns:
    #   Place | Name | Team | Score columns (Total, Prelim, Final, etc.)
    # We handle both formats.

    tables = soup.find_all("table")
    results_table = None

    # Find the main results table — usually the largest table with score data
    for table in tables:
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if any(h in headers for h in ["place", "name", "score", "total"]):
            results_table = table
            break

    if not results_table:
        # Fallback: look for table with diver profile links
        for table in tables:
            if table.find("a", href=re.compile(r"diver|profile", re.I)):
                results_table = table
                break

    if not results_table:
        logger.warning(f"  No results table found for event {event_id}")
        return [], []

    # Parse header row to map column indices
    header_row = results_table.find("tr")
    if not header_row:
        return [], []

    headers = [th.get_text(strip=True).lower() for th in header_row.find_all(["th", "td"])]

    col_map = {}
    for i, h in enumerate(headers):
        if "place" in h or h == "#":
            col_map["place"] = i
        elif "name" in h:
            col_map["name"] = i
        elif "team" in h or "school" in h or "club" in h:
            col_map["team"] = i
        elif "total" in h:
            col_map["total"] = i
        elif "prelim" in h:
            col_map["prelim"] = i
        elif "semi" in h:
            col_map["semifinal"] = i
        elif "final" in h and "semi" not in h:
            col_map["final"] = i
        elif "score" in h and "total" not in col_map:
            col_map["total"] = i

    # Parse data rows
    rows = results_table.find_all("tr")[1:]  # skip header

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 2:
            continue

        cell_texts = [c.get_text(strip=True) for c in cells]

        # Extract diver info from name cell (which usually has a profile link)
        name_idx = col_map.get("name", 1)
        name_cell = cells[name_idx] if name_idx < len(cells) else None
        if not name_cell:
            continue

        full_name = name_cell.get_text(strip=True)
        if not full_name or full_name.lower() in ("name", "diver", "athlete"):
            continue

        # Try to get diver profile link
        diver_link = name_cell.find("a", href=True)
        diver_id = None
        profile_url = None

        if diver_link:
            href = diver_link.get("href", "")
            diver_id = _extract_diver_id(href)
            if diver_id:
                profile_url = f"{BASE_URL}/profile.php?divernum={diver_id}"

        # If no diver ID from link, generate a stable synthetic ID from name
        if not diver_id:
            diver_id = re.sub(r"[^a-z0-9]", "", full_name.lower())[:20]

        # Team
        team_idx = col_map.get("team", 2)
        team = cell_texts[team_idx] if team_idx < len(cell_texts) else None

        # Scores
        total_score = _safe_float(cell_texts[col_map["total"]]) if "total" in col_map and col_map["total"] < len(cell_texts) else None
        prelim_score = _safe_float(cell_texts[col_map["prelim"]]) if "prelim" in col_map and col_map["prelim"] < len(cell_texts) else None
        semi_score = _safe_float(cell_texts[col_map["semifinal"]]) if "semifinal" in col_map and col_map["semifinal"] < len(cell_texts) else None
        final_score = _safe_float(cell_texts[col_map["final"]]) if "final" in col_map and col_map["final"] < len(cell_texts) else None

        # Place
        place_idx = col_map.get("place", 0)
        place = _safe_int(cell_texts[place_idx]) if place_idx < len(cell_texts) else None

        # Qualified flag (often shown as * or "Q")
        qualified = 0
        for text in cell_texts:
            if text in ("Q", "q", "*"):
                qualified = 1
                break

        result_id = f"{event_id}_{diver_id}"

        results.append({
            "result_id": result_id,
            "event_id": event_id,
            "meet_id": meet_id,
            "diver_id": diver_id,
            "team": team,
            "place": place,
            "total_score": total_score,
            "prelim_score": prelim_score,
            "semifinal_score": semi_score,
            "final_score": final_score,
            "qualified": qualified,
            "scraped_at": now,
        })

        # Build minimal diver record (full profile scraped separately)
        name_parts = full_name.rsplit(" ", 1)
        first_name = name_parts[0] if len(name_parts) > 1 else ""
        last_name = name_parts[1] if len(name_parts) > 1 else full_name

        divers.append({
            "diver_id": diver_id,
            "first_name": first_name,
            "last_name": last_name,
            "full_name": full_name,
            "gender": None,   # filled in by profile scraper
            "hometown": None,
            "home_state": None,
            "country": "USA",
            "team": team,
            "coach": None,
            "profile_url": profile_url,
            "scraped_at": now,
        })

    logger.info(f"  → {len(results)} results for event {event_id}")
    return results, divers


def upsert_result(conn, result: dict):
    conn.execute("""
        INSERT INTO results
            (result_id, event_id, meet_id, diver_id, team, place,
             total_score, prelim_score, semifinal_score, final_score,
             qualified, scraped_at)
        VALUES
            (:result_id, :event_id, :meet_id, :diver_id, :team, :place,
             :total_score, :prelim_score, :semifinal_score, :final_score,
             :qualified, :scraped_at)
        ON CONFLICT(result_id) DO UPDATE SET
            team=excluded.team,
            place=excluded.place,
            total_score=excluded.total_score,
            prelim_score=excluded.prelim_score,
            semifinal_score=excluded.semifinal_score,
            final_score=excluded.final_score,
            qualified=excluded.qualified,
            scraped_at=excluded.scraped_at
    """, result)


def upsert_diver_minimal(conn, diver: dict):
    """Insert diver with minimal info — won't overwrite richer profile data."""
    conn.execute("""
        INSERT INTO divers
            (diver_id, first_name, last_name, full_name, gender, hometown,
             home_state, country, team, coach, profile_url, scraped_at)
        VALUES
            (:diver_id, :first_name, :last_name, :full_name, :gender, :hometown,
             :home_state, :country, :team, :coach, :profile_url, :scraped_at)
        ON CONFLICT(diver_id) DO NOTHING
    """, diver)
