"""Read-only Neon Free readiness check. No schema changes or writes.

Usage:
  DATABASE_URL='postgresql://...?sslmode=require' python scripts/check_neon_free.py
Do not paste database URLs into chat or commit them to Git.
"""
import os
from urllib.parse import parse_qs, urlparse


def main():
    url = os.getenv("DATABASE_URL", "")
    if not url:
        raise SystemExit("DATABASE_URL missing; no connection attempted")
    parsed = urlparse(url)
    if parsed.scheme not in ("postgresql", "postgres") or not parsed.hostname:
        raise SystemExit("Invalid PostgreSQL URL")
    sslmode = parse_qs(parsed.query).get("sslmode", [""])[0]
    if sslmode not in ("require", "verify-ca", "verify-full"):
        raise SystemExit("TLS is required: add sslmode=require or stronger")
    if not parsed.username or not parsed.password:
        raise SystemExit("Database user and password required")
    import psycopg
    # Only a SELECT in a read-only transaction; no table or credential output.
    with psycopg.connect(url, connect_timeout=8) as db:
        with db.transaction():
            with db.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute("SELECT current_database(), current_setting('server_version_num')::integer")
                database, version = cur.fetchone()
                cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
                tables = cur.fetchone()[0]
    print("Neon/PostgreSQL TLS connection: OK")
    print("Server major version:", version // 10000)
    print("Public schema table count:", tables)
    if tables:
        print("STOP: Database is not empty; do not apply test migration without review.")
    else:
        print("Empty public schema: ready for isolated migration after approval.")


if __name__ == "__main__":
    main()
