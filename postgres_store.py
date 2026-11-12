"""PostgreSQL persistence adapter for the SocialPlan migration branch.

Not yet wired into app.py. Uses psycopg3, parameterized SQL and a transactional
connection. Secrets must be provided through DATABASE_URL (never in source).
"""
import os
from contextlib import contextmanager
from urllib.parse import urlsplit

SCHEMA_FILE = "migrations/postgres/001_initial.sql"


def require_database_url():
    url = os.getenv("DATABASE_URL", "")
    parsed = urlsplit(url)
    if parsed.scheme not in ("postgresql", "postgres") or not parsed.hostname:
        raise RuntimeError("DATABASE_URL must be a PostgreSQL connection string")
    return url


@contextmanager
def connection():
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(require_database_url(), row_factory=dict_row,
                         connect_timeout=10) as db:
        with db.transaction():
            yield db


def register_business(name, email, salt, password_hash):
    """Create business and administrator atomically; return the new business ID."""
    with connection() as db:
        with db.cursor() as cur:
            cur.execute("INSERT INTO businesses(name) VALUES (%s) RETURNING id", (name,))
            business_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO users(business_id,email,salt,passhash,role) "
                "VALUES (%s,%s,%s,%s,'admin')",
                (business_id, email, salt, password_hash),
            )
            return business_id


def create_post(business_id, author_id, caption, media_url, scheduled_at, created_at):
    """Insert post and its audit entry in one transaction."""
    with connection() as db:
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO posts(business_id,author_id,caption,media_url,scheduled_at,status,created_at) "
                "VALUES (%s,%s,%s,%s,%s,'draft',%s) RETURNING id",
                (business_id, author_id, caption, media_url, scheduled_at, created_at),
            )
            post_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO audit_events(business_id,user_id,action,post_id,created_at) "
                "VALUES (%s,%s,'post_created',%s,%s)",
                (business_id, author_id, post_id, created_at),
            )
            return post_id


def list_posts(business_id):
    with connection() as db:
        return db.execute(
            "SELECT id,caption,media_url,scheduled_at,status FROM posts "
            "WHERE business_id=%s ORDER BY scheduled_at",
            (business_id,),
        ).fetchall()


def change_post_status(business_id, user_id, post_id, action, now):
    if action not in ("approve", "cancel"):
        raise ValueError("Unsupported action")
    target = "approved" if action == "approve" else "cancelled"
    allowed = ("draft",) if action == "approve" else ("draft", "approved")
    with connection() as db:
        with db.cursor() as cur:
            cur.execute(
                "UPDATE posts SET status=%s WHERE id=%s AND business_id=%s "
                "AND status = ANY(%s) RETURNING id",
                (target, post_id, business_id, list(allowed)),
            )
            if cur.fetchone() is None:
                return False
            cur.execute(
                "INSERT INTO audit_events(business_id,user_id,action,post_id,created_at) "
                "VALUES (%s,%s,%s,%s,%s)",
                (business_id, user_id, "post_approved" if action == "approve" else "post_cancelled",
                 post_id, now),
            )
            return True
