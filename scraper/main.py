from __future__ import annotations
"""
scraper/main.py
Main orchestrator for the DiveMeets scraper.
Runs all scrapers in priority order:
  1. Meets
  2. Events (prelims/semis/finals)
  3. Results & scores
  4. Diver profiles
  5. Dive sheets
  6. Team standings

Fully resumable: skips already-scraped entities via scrape_log.
One DB per org per year: ncaa_2024.db, usa_2024.db, etc.
"""
import logging
import argparse
from datetime import datetime

from storage.db import get_conn, already_scraped, log_scraped
from scraper.sources import meets as meets_scraper
from scraper.sources import events as events_scraper
from scraper.sources import results as results_scraper
from scraper.sources import divers as divers_scraper
from scraper.sources import dive_sheets as dives_scraper
from scraper.sources import standings as standings_scraper

# ── Logging setup ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")

# ── Config ────────────────────────────────────────────────────────────────────

START_YEAR = 2018
CURRENT_YEAR = datetime.now().year

# Max diver profiles and dive sheets per run (to control runtime & politeness)
# Set to None to scrape everything (good for initial historical backfill)
MAX_DIVER_PROFILES_PER_RUN = 500
MAX_DIVE_SHEETS_PER_RUN = 200


# ── Helpers ───────────────────────────────────────────────────────────────────

def _filter_internal_keys(d: dict) -> dict:
    """Remove internal _ keys before DB upsert."""
    return {k: v for k, v in d.items() if not k.startswith("_")}


# ── Phase 1 + 2: Meets & Events ───────────────────────────────────────────────

def run_meets_and_events(year: int = None, org: str = None):
    """
    Discover meets and their events.
    Writes to the appropriate org/year DB.
    """
    logger.info("=" * 60)
    logger.info("PHASE 1+2: Meets & Events")
    logger.info("=" * 60)

    years = [year] if year else range(START_YEAR, CURRENT_YEAR + 1)
    orgs_filter = [org] if org else None

    for y in years:
        meets = meets_scraper.fetch_meet_list(y)

        for meet in meets:
            if orgs_filter and meet["org"] not in orgs_filter:
                continue

            with get_conn(meet["org"], meet["year"]) as conn:
                # Skip if already fully scraped (events done)
                if already_scraped(conn, "meet_events", meet["meet_id"]):
                    logger.debug(f"Skipping meet {meet['meet_id']} (already scraped)")
                    continue

                # Upsert meet
                meets_scraper.upsert_meet(conn, _filter_internal_keys(meet))

                # Fetch events
                meet_num = meet.get("_meet_num")
                if not meet_num:
                    continue

                events = events_scraper.fetch_meet_events(meet["meet_id"], meet_num)
                for event in events:
                    event["_meet_num"] = meet_num  # carry through
                    events_scraper.upsert_event(conn, _filter_internal_keys(event))

                # Store events list for later phases
                # We use scrape_log to mark meets with events done
                log_scraped(conn, "meet_events", meet["meet_id"])
                logger.info(f"  ✓ Meet {meet['meet_id']} — {len(events)} events")


# ── Phase 3: Results & Scores ─────────────────────────────────────────────────

def run_results(year: int = None, org: str = None):
    """Scrape per-diver results for all events."""
    logger.info("=" * 60)
    logger.info("PHASE 3: Results & Scores")
    logger.info("=" * 60)

    years = [year] if year else range(START_YEAR, CURRENT_YEAR + 1)
    orgs_filter = [org.lower()] if org else ["ncaa", "usa"]

    for y in years:
        for o in orgs_filter:
            with get_conn(o, y) as conn:
                # Get all events in this DB that haven't had results scraped
                events = conn.execute("""
                    SELECT e.*, m.org
                    FROM events e
                    JOIN meets m ON e.meet_id = m.meet_id
                    LEFT JOIN scrape_log sl
                        ON sl.entity_type = 'event_results' AND sl.entity_id = e.event_id
                    WHERE sl.id IS NULL
                    ORDER BY e.event_id
                """).fetchall()

                if not events:
                    continue

                logger.info(f"  {o.upper()} {y}: {len(events)} events to scrape results for")

                for event_row in events:
                    event = dict(event_row)
                    meet_num = _get_meet_num_from_url(event.get("event_url", ""))
                    event_num = str(event.get("event_num", ""))
                    if not meet_num:
                        continue

                    event["_meet_num"] = meet_num
                    event["_event_num"] = event_num

                    res_list, divers_list = results_scraper.fetch_event_results(event)

                    for result in res_list:
                        results_scraper.upsert_result(conn, result)
                    for diver in divers_list:
                        results_scraper.upsert_diver_minimal(conn, diver)

                    log_scraped(conn, "event_results", event["event_id"])

                conn.commit()


# ── Phase 4: Diver Profiles ───────────────────────────────────────────────────

def run_diver_profiles(year: int = None, org: str = None):
    """Enrich diver records with full profile data."""
    logger.info("=" * 60)
    logger.info("PHASE 4: Diver Profiles")
    logger.info("=" * 60)

    years = [year] if year else range(START_YEAR, CURRENT_YEAR + 1)
    orgs_filter = [org.lower()] if org else ["ncaa", "usa"]

    for y in years:
        for o in orgs_filter:
            with get_conn(o, y) as conn:
                diver_ids = divers_scraper.get_unscraped_diver_ids(conn)
                if not diver_ids:
                    continue

                limit = MAX_DIVER_PROFILES_PER_RUN
                if limit:
                    diver_ids = diver_ids[:limit]

                logger.info(f"  {o.upper()} {y}: {len(diver_ids)} diver profiles to fetch")

                for diver_id, profile_url in diver_ids:
                    profile = divers_scraper.fetch_diver_profile(diver_id, profile_url)
                    if profile:
                        divers_scraper.upsert_diver_full(conn, profile)
                        log_scraped(conn, "diver", diver_id)
                    else:
                        log_scraped(conn, "diver", diver_id, status="error",
                                    message="Profile parse failed")

                conn.commit()


# ── Phase 5: Dive Sheets ──────────────────────────────────────────────────────

def run_dive_sheets(year: int = None, org: str = None):
    """Scrape individual dive-level data for each result."""
    logger.info("=" * 60)
    logger.info("PHASE 5: Dive Sheets")
    logger.info("=" * 60)

    years = [year] if year else range(START_YEAR, CURRENT_YEAR + 1)
    orgs_filter = [org.lower()] if org else ["ncaa", "usa"]

    for y in years:
        for o in orgs_filter:
            with get_conn(o, y) as conn:
                # Get results without dive sheets yet
                results = conn.execute("""
                    SELECT r.result_id, r.diver_id, r.event_id, r.meet_id
                    FROM results r
                    LEFT JOIN scrape_log sl
                        ON sl.entity_type = 'divesheet' AND sl.entity_id = r.result_id
                    WHERE sl.id IS NULL
                      AND r.diver_id GLOB '[0-9]*'  -- numeric IDs only
                    ORDER BY r.result_id
                """).fetchall()

                if not results:
                    continue

                limit = MAX_DIVE_SHEETS_PER_RUN
                if limit:
                    results = results[:limit]

                logger.info(f"  {o.upper()} {y}: {len(results)} dive sheets to fetch")

                for res_row in results:
                    result = dict(res_row)

                    # Get event details for URL params
                    event = conn.execute(
                        "SELECT * FROM events WHERE event_id=?", (result["event_id"],)
                    ).fetchone()
                    if not event:
                        continue
                    event = dict(event)

                    meet_num = _get_meet_num_from_url(event.get("event_url", ""))
                    if not meet_num:
                        continue
                    event["_meet_num"] = meet_num
                    event["_event_num"] = str(event.get("event_num", ""))

                    dives = dives_scraper.fetch_dive_sheet(result, event)
                    for dive in dives:
                        dives_scraper.upsert_dive(conn, dive)

                    log_scraped(conn, "divesheet", result["result_id"],
                                status="ok" if dives else "skipped",
                                message=f"{len(dives)} dives")

                conn.commit()


# ── Phase 6: Team Standings ───────────────────────────────────────────────────

def run_standings(year: int = None, org: str = None):
    """Scrape team standings for all meets."""
    logger.info("=" * 60)
    logger.info("PHASE 6: Team Standings")
    logger.info("=" * 60)

    years = [year] if year else range(START_YEAR, CURRENT_YEAR + 1)
    orgs_filter = [org.lower()] if org else ["ncaa", "usa"]

    for y in years:
        for o in orgs_filter:
            with get_conn(o, y) as conn:
                meets = conn.execute("""
                    SELECT m.*
                    FROM meets m
                    LEFT JOIN scrape_log sl
                        ON sl.entity_type = 'standings' AND sl.entity_id = m.meet_id
                    WHERE sl.id IS NULL
                    ORDER BY m.meet_id
                """).fetchall()

                if not meets:
                    continue

                logger.info(f"  {o.upper()} {y}: {len(meets)} meets to scrape standings")

                for meet_row in meets:
                    meet = dict(meet_row)
                    meet["_meet_num"] = _get_meet_num_from_url(meet.get("meet_url", ""))

                    standings = standings_scraper.fetch_team_standings(meet)
                    for s in standings:
                        standings_scraper.upsert_standing(conn, s)

                    log_scraped(conn, "standings", meet["meet_id"])

                conn.commit()


# ── Utilities ─────────────────────────────────────────────────────────────────

def _get_meet_num_from_url(url: str) -> str | None:
    import re
    m = re.search(r"meetnum=(\d+)", url or "")
    return m.group(1) if m else None


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="DiveMeets Scraper")
    parser.add_argument("--year", type=int, default=None,
                        help="Scrape only this season year (default: all years 2018-present)")
    parser.add_argument("--org", choices=["ncaa", "usa"], default=None,
                        help="Scrape only this org (default: both)")
    parser.add_argument("--phase", type=int, default=None,
                        help="Run only this phase (1-6). Default: all phases.")
    parser.add_argument("--no-dive-sheets", action="store_true",
                        help="Skip dive sheet scraping (fastest mode)")
    args = parser.parse_args()

    logger.info("DiveMeets Scraper starting")
    logger.info(f"  Year: {args.year or 'all (2018-present)'}")
    logger.info(f"  Org:  {args.org or 'ncaa + usa'}")
    logger.info(f"  Phase: {args.phase or 'all'}")

    phases = {
        1: lambda: run_meets_and_events(args.year, args.org),
        2: lambda: run_meets_and_events(args.year, args.org),  # same as 1
        3: lambda: run_results(args.year, args.org),
        4: lambda: run_diver_profiles(args.year, args.org),
        5: lambda: run_dive_sheets(args.year, args.org),
        6: lambda: run_standings(args.year, args.org),
    }

    if args.phase:
        if args.phase in phases:
            phases[args.phase]()
        else:
            logger.error(f"Unknown phase: {args.phase}")
    else:
        run_meets_and_events(args.year, args.org)
        run_results(args.year, args.org)
        run_diver_profiles(args.year, args.org)
        if not args.no_dive_sheets:
            run_dive_sheets(args.year, args.org)
        run_standings(args.year, args.org)

    logger.info("DiveMeets Scraper complete.")


if __name__ == "__main__":
    main()
