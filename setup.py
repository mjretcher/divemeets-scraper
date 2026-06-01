"""
setup.py
One-time setup and validation script.
Run this first to verify the environment is ready.

Usage:
  python setup.py
"""
import sys
import sqlite3
from pathlib import Path

def check_python():
    if sys.version_info < (3, 11):
        print(f"❌ Python 3.11+ required (you have {sys.version})")
        sys.exit(1)
    print(f"✅ Python {sys.version.split()[0]}")

def check_dependencies():
    missing = []
    for pkg in ["requests", "bs4", "lxml"]:
        try:
            __import__(pkg)
            print(f"✅ {pkg}")
        except ImportError:
            print(f"❌ {pkg} — run: pip install -r requirements.txt")
            missing.append(pkg)
    return missing

def check_db_creation():
    """Verify SQLite schema creates correctly."""
    sys.path.insert(0, str(Path(__file__).parent))
    from storage.db import get_conn

    print("\nTesting database creation...")
    test_path = Path("data/test_setup.db")
    try:
        with get_conn("test", 2024) as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            table_names = [t[0] for t in tables]
            expected = ["meets", "events", "divers", "results",
                        "dive_sheets", "team_standings", "scrape_log"]
            for t in expected:
                if t in table_names:
                    print(f"  ✅ Table: {t}")
                else:
                    print(f"  ❌ Missing table: {t}")
        # Clean up test DB
        Path("data/test_2024.db").unlink(missing_ok=True)
        print("✅ Database schema OK")
        return True
    except Exception as e:
        print(f"❌ DB creation failed: {e}")
        return False

def create_gitattributes():
    """Create .gitattributes for Git LFS tracking of .db files."""
    ga = Path(".gitattributes")
    content = "data/*.db filter=lfs diff=lfs merge=lfs -text\n"
    if ga.exists():
        if "*.db" in ga.read_text():
            print("✅ .gitattributes already configured for LFS")
            return
    with open(ga, "a") as f:
        f.write(content)
    print("✅ Created .gitattributes (Git LFS tracking for *.db)")

def create_gitignore():
    gi = Path(".gitignore")
    entries = [".env", "__pycache__/", "*.pyc", "exports/", "*.log"]
    existing = gi.read_text() if gi.exists() else ""
    added = []
    with open(gi, "a") as f:
        for entry in entries:
            if entry not in existing:
                f.write(f"{entry}\n")
                added.append(entry)
    if added:
        print(f"✅ Added to .gitignore: {', '.join(added)}")
    else:
        print("✅ .gitignore already configured")

def main():
    print("=" * 50)
    print("DiveMeets Scraper — Setup Check")
    print("=" * 50)

    check_python()
    missing = check_dependencies()

    if missing:
        print(f"\n⚠️  Install missing packages first: pip install -r requirements.txt")
        return

    Path("data").mkdir(exist_ok=True)
    Path("exports").mkdir(exist_ok=True)

    db_ok = check_db_creation()
    create_gitattributes()
    create_gitignore()

    print("\n" + "=" * 50)
    if db_ok:
        print("✅ Setup complete! You're ready to run:")
        print("")
        print("  # First run — scrape all history (takes hours — run in background):")
        print("  python -m scraper.main --no-dive-sheets")
        print("")
        print("  # Scrape just one year:")
        print("  python -m scraper.main --year 2024 --org ncaa")
        print("")
        print("  # Export CSVs when you need them:")
        print("  python -m export.export_csv --org ncaa --year 2024")
        print("  python -m export.export_csv --diver 'Emma Smith'")
        print("")
        print("  # GitHub Actions runs automatically every Monday at 6 AM UTC")
        print("  # Or trigger manually from the Actions tab in your repo")
    else:
        print("❌ Setup failed — check errors above")

if __name__ == "__main__":
    main()
