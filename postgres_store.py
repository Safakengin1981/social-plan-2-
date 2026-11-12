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


def get_user_by_email(email):
    with connection() as db:
        return db.execute(
            "SELECT id,business_id,email,salt,passhash,role FROM users WHERE email=%s",
            (email,),
        ).fetchone()


def get_login_attempt(email_hash):
    with connection() as db:
        return db.execute(
            "SELECT failures,first_attempt,locked_until FROM login_attempts WHERE email_hash=%s",
            (email_hash,),
        ).fetchone()


def record_failed_login(email_hash, now, window_seconds=900, max_failures=5):
    """Serialize per-email failure counting across concurrent requests."""
    with connection() as db:
        db.execute(
            "INSERT INTO login_attempts(email_hash,failures,first_attempt,locked_until) "
            "VALUES (%s,0,%s,0) ON CONFLICT(email_hash) DO NOTHING",
            (email_hash, now),
        )
        row = db.execute(
            "SELECT failures,first_attempt,locked_until FROM login_attempts "
            "WHERE email_hash=%s FOR UPDATE", (email_hash,),
        ).fetchone()
        if row["locked_until"] > now:
            return row["locked_until"]
        within_window = now - row["first_attempt"] < window_seconds
        failures = row["failures"] + 1 if within_window else 1
        first_attempt = row["first_attempt"] if within_window else now
        locked_until = now + window_seconds if failures >= max_failures else 0
        db.execute(
            "UPDATE login_attempts SET failures=%s,first_attempt=%s,locked_until=%s "
            "WHERE email_hash=%s", (failures, first_attempt, locked_until, email_hash),
        )
        return locked_until


def clear_login_attempts(email_hash):
    with connection() as db:
        db.execute("DELETE FROM login_attempts WHERE email_hash=%s", (email_hash,))


def create_session(token_hash, user_id, expires):
    with connection() as db:
        db.execute(
            "INSERT INTO sessions(token_hash,user_id,expires) VALUES (%s,%s,%s)",
            (token_hash, user_id, expires),
        )


def get_session_user(token_hash, now):
    with connection() as db:
        return db.execute(
            "SELECT users.id,users.business_id,users.email,users.role "
            "FROM sessions JOIN users ON users.id=sessions.user_id "
            "WHERE sessions.token_hash=%s AND sessions.expires>%s",
            (token_hash, now),
        ).fetchone()


def revoke_session(token_hash):
    with connection() as db:
        db.execute("DELETE FROM sessions WHERE token_hash=%s", (token_hash,))


def create_instagram_oauth_state(state_hash, business_id, user_id, expires, now):
    with connection() as db:
        db.execute("DELETE FROM instagram_oauth_states WHERE expires<%s", (now,))
        db.execute(
            "INSERT INTO instagram_oauth_states(state_hash,business_id,user_id,expires) "
            "VALUES (%s,%s,%s,%s)", (state_hash, business_id, user_id, expires),
        )


def consume_instagram_oauth_state(state_hash, now):
    """Single-use, atomic consumption prevents replay across workers."""
    with connection() as db:
        return db.execute(
            "DELETE FROM instagram_oauth_states WHERE state_hash=%s AND expires>=%s "
            "RETURNING business_id,user_id", (state_hash, now),
        ).fetchone()


def upsert_publish_job(post_id, business_id, state, reason, updated_at):
    """Queue state is durable and scoped to the owning business."""
    if state not in ("queued", "ready", "blocked", "cancelled"):
        raise ValueError("Unsupported queue state")
    with connection() as db:
        row = db.execute(
            "SELECT id FROM posts WHERE id=%s AND business_id=%s",
            (post_id, business_id),
        ).fetchone()
        if row is None:
            return False
        db.execute(
            "INSERT INTO publish_jobs(post_id,business_id,state,reason,updated_at) "
            "VALUES (%s,%s,%s,%s,%s) ON CONFLICT(post_id) DO UPDATE SET "
            "state=EXCLUDED.state,reason=EXCLUDED.reason,updated_at=EXCLUDED.updated_at "
            "WHERE publish_jobs.business_id=EXCLUDED.business_id",
            (post_id, business_id, state, reason, updated_at),
        )
        return True


def list_publish_jobs(business_id):
    with connection() as db:
        return db.execute(
            "SELECT j.post_id,j.state,j.reason,j.updated_at,p.scheduled_at,p.caption "
            "FROM publish_jobs j JOIN posts p ON p.id=j.post_id "
            "WHERE j.business_id=%s AND p.business_id=%s ORDER BY p.scheduled_at",
            (business_id, business_id),
        ).fetchall()


def get_instagram_connection(business_id):
    """Return public connection status only; never expose encrypted tokens."""
    with connection() as db:
        return db.execute(
            "SELECT instagram_id,username,expires,verified_at "
            "FROM instagram_connections WHERE business_id=%s", (business_id,),
        ).fetchone()


def save_instagram_connection(business_id, instagram_id, username, encrypted_token, expires, verified_at):
    """Only encrypted tokens may be persisted; caller must encrypt before invoking."""
    from cryptography.fernet import Fernet, InvalidToken
    key = os.getenv("SOCIALPLAN_FERNET_KEY")
    if not key:
        raise RuntimeError("SOCIALPLAN_FERNET_KEY is required for Instagram token storage")
    try:
        plaintext = Fernet(key.encode()).decrypt(encrypted_token.encode())
        if not plaintext:
            raise ValueError("Empty Instagram token")
    except (InvalidToken, ValueError) as exc:
        raise ValueError("Invalid encrypted Instagram token") from exc
    with connection() as db:
        db.execute(
            "INSERT INTO instagram_connections "
            "(business_id,instagram_id,username,encrypted_token,expires,verified_at) "
            "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(business_id) DO UPDATE SET "
            "instagram_id=EXCLUDED.instagram_id,username=EXCLUDED.username,"
            "encrypted_token=EXCLUDED.encrypted_token,expires=EXCLUDED.expires,"
            "verified_at=EXCLUDED.verified_at",
            (business_id, instagram_id, username, encrypted_token, expires, verified_at),
        )
