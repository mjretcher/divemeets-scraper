from __future__ import annotations
"""
scraper/sources/standings.py
Scrapes team standings from a meet.
DiveMeets shows team scores on the meet summary/results page.

URL pattern:
  https://secure.meetcontrol.com/divemeets/system/meet.php?meetnum=XXXXX
  (same meet page — standings are usually in a separate table or section)
"""
import re
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


def fetch_team_standings(meet: dict) -> list[dict]:
    """
    Fetch team standings from a meet page.
    Returns list of standing dicts.
    """
    meet_id = meet["meet_id"]
    meet_num = meet.get("_meet_num") or re.search(r"meetnum=(\d+)", meet.get("meet_url", ""))
    if not meet_num:
        return []
    if hasattr(meet_num, "group"):
        meet_num = meet_num.group(1)

    logger.info(f"Fetching team standings for meet {meet_id}")

    # Try dedicated team scores page first
    html = get_html("meetteamscores.php", params={"meetnum": meet_num})
    if not html or "<table" not in html.lower():
        # Fall back to main meet page
        html = get_html("meet.php", params={"meetnum": meet_num})

    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    standings = []
    now = datetime.now(timezone.utc).isoformat()

    # Find tables that look like team standings
    # Usually: Place | Team | Points
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        all_text = table.get_text(strip=True).lower()

        # Check if this looks like a team standings table
        has_team_col = any("team" in h or "school" in h or "club" in h for h in headers)
        has_score_col = any("point" in h or "score" in h or "total" in h for h in headers)

        if not (has_team_col or "team" in all_text):
            continue

        col_place = None
        col_team = None
        col_points = None

        for i, h in enumerate(headers):
            if "place" in h or h == "#":
                col_place = i
            elif "team" in h or "school" in h or "club" in h:
                col_team = i
            elif "point" in h or "score" in h or "total" in h:
                col_points = i

        if col_team is None:
            continue

        rows = table.find_all("tr")[1:]  # skip header
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue

            cell_texts = [c.get_text(strip=True) for c in cells]

            team_name = cell_texts[col_team] if col_team < len(cell_texts) else None
            if not team_name or len(team_name) < 2:
                continue

            place = _safe_int(cell_texts[col_place]) if col_place is not None and col_place < len(cell_texts) else None
            total = _safe_float(cell_texts[col_points]) if col_points is not None and col_points < len(cell_texts) else None

            standing_id = f"{meet_id}_{re.sub(r'[^a-z0-9]', '', team_name.lower())}"

            standings.append({
                "standing_id": standing_id,
                "meet_id": meet_id,
                "team_name": team_name,
                "place": place,
                "total_points": total,
                "scraped_at": now,
            })

        if standings:
            break  # stop at first valid table

    logger.info(f"  → {len(standings)} team standings for meet {meet_id}")
    return standings


def upsert_standing(conn, standing: dict):
    conn.execute("""
        INSERT INTO team_standings
            (standing_id, meet_id, team_name, place, total_points, scraped_at)
        VALUES
            (:standing_id, :meet_id, :team_name, :place, :total_points, :scraped_at)
        ON CONFLICT(standing_id) DO UPDATE SET
            place=excluded.place,
            total_points=excluded.total_points,
            scraped_at=excluded.scraped_at
    """, standing)
