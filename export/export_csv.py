"""
export/export_csv.py
On-demand CSV exporter for DiveMeets SQLite data.
Exports clean, analytics-ready CSVs from any org/year DB.

Usage:
  python -m export.export_csv --org ncaa --year 2024
  python -m export.export_csv --org usa --year 2023 --tables meets events results
  python -m export.export_csv --org ncaa --year 2024 --diver "Emma Smith"
  python -m export.export_csv --all          # export everything
"""
import argparse
import csv
import json
import logging
import sqlite3
from pathlib import Path
from datetime import datetime

from storage.db import db_path, DATA_DIR

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

EXPORT_DIR = Path("exports")

START_YEAR = 2018
CURRENT_YEAR = datetime.now().year

# ── Table export queries ──────────────────────────────────────────────────────
# Each entry: (filename_suffix, SQL query)
# Queries are designed to be human-readable in Excel/Sheets (no raw IDs alone)

TABLE_QUERIES = {
    "meets": """
        SELECT
            meet_id, name, org, year, start_date, end_date,
            location, city, state, host_team, meet_url, scraped_at
        FROM meets
        ORDER BY start_date
    """,

    "events": """
        SELECT
            e.event_id,
            m.name AS meet_name,
            m.start_date AS meet_date,
            m.location AS meet_location,
            e.event_num,
            e.name AS event_name,
            e.gender,
            e.board_height,
            e.division,
            e.num_dives,
            e.event_url,
            e.meet_id
        FROM events e
        JOIN meets m ON e.meet_id = m.meet_id
        ORDER BY m.start_date, e.event_num
    """,

    "results": """
        SELECT
            r.result_id,
            m.name AS meet_name,
            m.start_date AS meet_date,
            m.location AS meet_location,
            m.org,
            m.year,
            e.name AS event_name,
            e.gender,
            e.board_height,
            e.division,
            d.full_name AS diver_name,
            d.first_name,
            d.last_name,
            r.team,
            d.hometown,
            d.home_state,
            r.place,
            r.total_score,
            r.prelim_score,
            r.semifinal_score,
            r.final_score,
            r.qualified,
            r.meet_id,
            r.event_id,
            r.diver_id
        FROM results r
        JOIN meets m ON r.meet_id = m.meet_id
        JOIN events e ON r.event_id = e.event_id
        JOIN divers d ON r.diver_id = d.diver_id
        ORDER BY m.start_date, e.event_num, r.place
    """,

    "divers": """
        SELECT
            diver_id, full_name, first_name, last_name,
            gender, team, hometown, home_state, country,
            coach, profile_url, scraped_at
        FROM divers
        ORDER BY last_name, first_name
    """,

    "dive_sheets": """
        SELECT
            ds.dive_id,
            m.name AS meet_name,
            m.start_date AS meet_date,
            e.name AS event_name,
            e.gender,
            e.board_height,
            e.division,
            d.full_name AS diver_name,
            r.team,
            r.place AS event_place,
            ds.round,
            ds.dive_num,
            ds.dive_code,
            ds.dive_name,
            ds.position,
            ds.dd,
            ds.judge_scores,
            ds.net_score,
            ds.total_points
        FROM dive_sheets ds
        JOIN results r ON ds.result_id = r.result_id
        JOIN meets m ON r.meet_id = m.meet_id
        JOIN events e ON ds.event_id = e.event_id
        JOIN divers d ON ds.diver_id = d.diver_id
        ORDER BY m.start_date, e.event_num, r.place, ds.round, ds.dive_num
    """,

    "team_standings": """
        SELECT
            ts.standing_id,
            m.name AS meet_name,
            m.start_date AS meet_date,
            m.location AS meet_location,
            m.org,
            m.year,
            ts.team_name,
            ts.place,
            ts.total_points
        FROM team_standings ts
        JOIN meets m ON ts.meet_id = m.meet_id
        ORDER BY m.start_date, ts.place
    """,
}

# ── Diver-specific query ──────────────────────────────────────────────────────

DIVER_RESULTS_QUERY = """
    SELECT
        m.name AS meet_name,
        m.start_date AS meet_date,
        m.org,
        m.year,
        m.location AS meet_location,
        e.name AS event_name,
        e.gender,
        e.board_height,
        e.division,
        d.full_name AS diver_name,
        r.team,
        r.place,
        r.total_score,
        r.prelim_score,
        r.semifinal_score,
        r.final_score,
        r.qualified
    FROM results r
    JOIN meets m ON r.meet_id = m.meet_id
    JOIN events e ON r.event_id = e.event_id
    JOIN divers d ON r.diver_id = d.diver_id
    WHERE LOWER(d.full_name) LIKE LOWER(?)
    ORDER BY m.start_date, e.event_num
"""

DIVER_DIVES_QUERY = """
    SELECT
        m.name AS meet_name,
        m.start_date AS meet_date,
        e.name AS event_name,
        e.board_height,
        ds.round,
        ds.dive_num,
        ds.dive_code,
        ds.dive_name,
        ds.position,
        ds.dd,
        ds.judge_scores,
        ds.net_score,
        ds.total_points
    FROM dive_sheets ds
    JOIN results r ON ds.result_id = r.result_id
    JOIN meets m ON r.meet_id = m.meet_id
    JOIN events e ON ds.event_id = e.event_id
    JOIN divers d ON ds.diver_id = d.diver_id
    WHERE LOWER(d.full_name) LIKE LOWER(?)
    ORDER BY m.start_date, e.event_num, ds.round, ds.dive_num
"""


# ── Export functions ──────────────────────────────────────────────────────────

def export_table(org: str, year: int, table: str, out_dir: Path, diver_filter: str = None):
    """Export one table from one org/year DB to CSV."""
    path = db_path(org, year)
    if not path.exists():
        logger.debug(f"  DB not found: {path}")
        return 0

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row

    query = TABLE_QUERIES.get(table)
    if not query:
        logger.warning(f"  Unknown table: {table}")
        conn.close()
        return 0

    try:
        rows = conn.execute(query).fetchall()
    except sqlite3.OperationalError as e:
        logger.debug(f"  Query error for {org}_{year} {table}: {e}")
        conn.close()
        return 0
    finally:
        conn.close()

    if not rows:
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{org}_{year}_{table}.csv"

    with open(out_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows([dict(r) for r in rows])

    logger.info(f"  ✓ {out_file.name} — {len(rows):,} rows")
    return len(rows)


def export_diver(name: str, out_dir: Path):
    """Export all results + dive sheets for a specific diver across all DBs."""
    name_pattern = f"%{name}%"
    all_results = []
    all_dives = []

    for org in ["ncaa", "usa"]:
        for year in range(START_YEAR, CURRENT_YEAR + 1):
            path = db_path(org, year)
            if not path.exists():
                continue

            conn = sqlite3.connect(str(path))
            conn.row_factory = sqlite3.Row

            try:
                results = conn.execute(DIVER_RESULTS_QUERY, (name_pattern,)).fetchall()
                dives = conn.execute(DIVER_DIVES_QUERY, (name_pattern,)).fetchall()
                all_results.extend([dict(r) for r in results])
                all_dives.extend([dict(d) for d in dives])
            except sqlite3.OperationalError:
                pass
            finally:
                conn.close()

    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = name.replace(" ", "_").lower()

    if all_results:
        out_file = out_dir / f"diver_{safe_name}_results.csv"
        with open(out_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=all_results[0].keys())
            writer.writeheader()
            writer.writerows(all_results)
        logger.info(f"  ✓ {out_file.name} — {len(all_results):,} results")

    if all_dives:
        out_file = out_dir / f"diver_{safe_name}_dives.csv"
        with open(out_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=all_dives[0].keys())
            writer.writeheader()
            writer.writerows(all_dives)
        logger.info(f"  ✓ {out_file.name} — {len(all_dives):,} dives")

    if not all_results and not all_dives:
        logger.warning(f"  No data found for diver matching '{name}'")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Export DiveMeets SQLite data to CSV",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Export all tables for NCAA 2024
  python -m export.export_csv --org ncaa --year 2024

  # Export only results and events for USA Diving 2023
  python -m export.export_csv --org usa --year 2023 --tables results events

  # Export all data for a specific diver across all years
  python -m export.export_csv --diver "Emma Smith"

  # Export everything (all orgs, all years, all tables)
  python -m export.export_csv --all
        """
    )
    parser.add_argument("--org", choices=["ncaa", "usa"], default=None)
    parser.add_argument("--year", type=int, default=None)
    parser.add_argument("--tables", nargs="+",
                        choices=list(TABLE_QUERIES.keys()),
                        default=list(TABLE_QUERIES.keys()),
                        help="Which tables to export (default: all)")
    parser.add_argument("--diver", type=str, default=None,
                        help="Export all data for a specific diver (partial name match)")
    parser.add_argument("--all", action="store_true",
                        help="Export all orgs, all years, all tables")
    parser.add_argument("--out-dir", type=str, default="exports",
                        help="Output directory (default: ./exports)")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    logger.info(f"DiveMeets CSV Exporter — output: {out_dir}/")

    # Diver export
    if args.diver:
        logger.info(f"Exporting diver: {args.diver}")
        export_diver(args.diver, out_dir / "divers")
        return

    # Determine scope
    orgs = ["ncaa", "usa"] if (args.all or not args.org) else [args.org]
    years = range(START_YEAR, CURRENT_YEAR + 1) if (args.all or not args.year) else [args.year]
    tables = args.tables

    total_rows = 0
    for org in orgs:
        for year in years:
            for table in tables:
                n = export_table(org, year, table, out_dir / f"{org}_{year}")
                total_rows += n

    logger.info(f"\nExport complete — {total_rows:,} total rows across {len(orgs)*len(list(years))} DBs")
    logger.info(f"Files saved to: {out_dir.resolve()}/")


if __name__ == "__main__":
    main()
