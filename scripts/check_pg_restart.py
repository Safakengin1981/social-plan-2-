"""Disposable PostgreSQL persistence smoke test, run in two separate processes.

Usage:
  python scripts/check_pg_restart.py write
  python scripts/check_pg_restart.py read
The first process writes and exits; the second verifies durable state.
Only run against a fresh, disposable test database.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import psycopg
from postgres_store import require_database_url

MARKER = "socialplan-ci-restart-check"

def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("write", "read"):
        raise SystemExit("Usage: check_pg_restart.py write|read")
    with psycopg.connect(require_database_url()) as db:
        with db.cursor() as cur:
            if sys.argv[1] == "write":
                cur.execute("INSERT INTO businesses(name) VALUES (%s)", (MARKER,))
                print("Wrote restart marker")
            else:
                cur.execute("SELECT count(*) FROM businesses WHERE name=%s", (MARKER,))
                count = cur.fetchone()[0]
                if count != 1:
                    raise SystemExit(f"Expected one persisted marker, found {count}")
                print("Verified restart marker in a separate process")

if __name__ == "__main__":
    main()
