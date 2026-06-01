"""
scraper/sources/dive_sheets.py
Scrapes individual dive-level data for each diver in each event.
Captures: dive code, name, position, DD, judge scores, net score, total points.

Dive sheet URL (per diver per event):
  https://secure.meetcontrol.com/divemeets/system/divesheet.php?meetnum=XXX&eventnum=YY&divernum=ZZZ
  or embedded in the results page as an expandable section.
"""
import re
import json
import logging
from datetime import datetime, timezone
from bs4 import BeautifulSoup

from scraper.http_client import get_html, BASE_URL

logger = logging.getLogger(__name__)

# DiveMeets dive code format: e.g. 6245D, 107B, 5152D, 201C
# Position letters: A=tuck, B=pike, C=straight, D=free
_POSITION_MAP = {"A": "tuck", "B": "pike", "C": "straight", "D": "free"}

_DD_PATTERN = re.compile(r"\b(\d\.\d)\b")


def _parse_judge_scores(text: str) -> tuple[list[float], float | None]:
    """
    Parse judge score string like "7.5 8.0 7.5 8.5 7.0" or "7.5/8.0/7.5"
    Returns (scores_list, net_score_after_drop).
    DiveMeets typically drops highest and lowest judges.
    """
    nums = re.findall(r"\b(\d+\.?\d*)\b", text)
    scores = []
    for n in nums:
        try:
            f = float(n)
            if 0 <= f <= 10:
                scores.append(f)
        except ValueError:
            continue

    if not scores:
        return [], None

    # Drop high and low if 5+ judges
    if len(scores) >= 5:
        net = sum(sorted(scores)[1:-1])
    else:
        net = sum(scores)

    return scores, round(net, 2)


def _safe_float(text: str) -> float | None:
    if not text:
        return None
    try:
        return float(re.sub(r"[^\d.\-]", "", text))
    except (ValueError, TypeError):
        return None


def fetch_dive_sheet(result: dict, event: dict) -> list[dict]:
    """
    Fetch the dive sheet for one diver in one event.
    result dict must have: result_id, diver_id, event_id
    event dict must have: _meet_num, _event_num
    Returns list of dive dicts.
    """
    diver_id = result["diver_id"]
    result_id = result["result_id"]
    event_id = result["event_id"]
    meet_num = event["_meet_num"]
    event_num = event["_event_num"]

    # Only fetch if diver has a real DiveMeets numeric ID (not synthetic)
    if not diver_id.isdigit():
        return []

    url = (f"{BASE_URL}/divesheet.php"
           f"?meetnum={meet_num}&eventnum={event_num}&divernum={diver_id}")

    logger.debug(f"    Fetching dive sheet: {url}")
    html = get_html(full_url=url)
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    dives = []
    now = datetime.now(timezone.utc).isoformat()

    # DiveMeets dive sheets typically have separate tables per round
    # (prelim, semifinal, final) or one combined table.
    # Look for tables with dive code columns.

    current_round = "final"  # default if not labeled

    for table in soup.find_all("table"):
        # Detect round from preceding heading
        prev = table.find_previous(["h1", "h2", "h3", "h4", "b", "strong"])
        if prev:
            prev_text = prev.get_text(strip=True).lower()
            if "prelim" in prev_text:
                current_round = "prelim"
            elif "semi" in prev_text:
                current_round = "semifinal"
            elif "final" in prev_text:
                current_round = "final"

        rows = table.find_all("tr")
        dive_num = 0

        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue

            cell_texts = [c.get_text(strip=True) for c in cells]

            # Dive code cell: usually matches pattern like 107B, 6245D, 5253B
            dive_code = None
            for text in cell_texts:
                if re.match(r"^\d{3,4}[A-D]$", text):
                    dive_code = text
                    break

            if not dive_code:
                # Sometimes code and name are in same cell
                for text in cell_texts:
                    m = re.search(r"\b(\d{3,4}[A-D])\b", text)
                    if m:
                        dive_code = m.group(1)
                        break

            if not dive_code:
                continue

            dive_num += 1

            # Position from last letter of dive code
            position_letter = dive_code[-1] if dive_code else None
            position = _POSITION_MAP.get(position_letter, position_letter)

            # DD — find float like "2.4" or "3.2" in cells
            dd = None
            for text in cell_texts:
                m = _DD_PATTERN.search(text)
                if m:
                    val = float(m.group(1))
                    if 1.0 <= val <= 4.5:  # valid DD range
                        dd = val
                        break

            # Judge scores — cells with score-like values
            # Usually the cells after the DD cell
            score_texts = []
            for text in cell_texts:
                nums = re.findall(r"\b(\d+\.?\d*)\b", text)
                for n in nums:
                    try:
                        f = float(n)
                        if 0 <= f <= 10 and text != str(dd):
                            score_texts.append(str(f))
                    except ValueError:
                        pass

            judge_scores, net_score = _parse_judge_scores(" ".join(score_texts))

            # Total points = net_score * dd
            total_points = round(net_score * dd, 2) if (net_score and dd) else None

            # Try to get dive name from a cell that's not just numbers or code
            dive_name = None
            for text in cell_texts:
                if (text != dive_code and
                        not re.match(r"^[\d\s.]+$", text) and
                        len(text) > 5 and
                        text not in ("DD", "Score", "Net")):
                    dive_name = text
                    break

            dive_id = f"{result_id}_{current_round}_{dive_num}"

            dives.append({
                "dive_id": dive_id,
                "result_id": result_id,
                "event_id": event_id,
                "diver_id": diver_id,
                "round": current_round,
                "dive_num": dive_num,
                "dive_code": dive_code,
                "dive_name": dive_name,
                "position": position,
                "dd": dd,
                "judge_scores": json.dumps(judge_scores) if judge_scores else None,
                "net_score": net_score,
                "total_points": total_points,
                "scraped_at": now,
            })

    logger.debug(f"    → {len(dives)} dives for diver {diver_id} event {event_id}")
    return dives


def upsert_dive(conn, dive: dict):
    conn.execute("""
        INSERT INTO dive_sheets
            (dive_id, result_id, event_id, diver_id, round, dive_num,
             dive_code, dive_name, position, dd, judge_scores,
             net_score, total_points, scraped_at)
        VALUES
            (:dive_id, :result_id, :event_id, :diver_id, :round, :dive_num,
             :dive_code, :dive_name, :position, :dd, :judge_scores,
             :net_score, :total_points, :scraped_at)
        ON CONFLICT(dive_id) DO UPDATE SET
            dive_name=excluded.dive_name,
            dd=excluded.dd,
            judge_scores=excluded.judge_scores,
            net_score=excluded.net_score,
            total_points=excluded.total_points,
            scraped_at=excluded.scraped_at
    """, dive)
