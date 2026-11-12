"""Run against an isolated PostgreSQL CI database only."""
import os
import pytest

@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="DATABASE_URL not configured")
def test_postgres_tenant_isolation_and_atomic_audit():
    import psycopg
    import postgres_store as store
    # CI uses a fresh disposable postgres service.
    with psycopg.connect(os.environ["DATABASE_URL"]) as db:
        with db.cursor() as cur:
            cur.execute("TRUNCATE instagram_connections,instagram_oauth_states,publish_jobs,audit_events,"
                        "posts,sessions,login_attempts,users,businesses RESTART IDENTITY CASCADE")
    a = store.register_business("Tenant A", "a@ci.example", "salt", "hash")
    b = store.register_business("Tenant B", "b@ci.example", "salt", "hash")
    with store.connection() as db:
        ua = db.execute("SELECT id FROM users WHERE business_id=%s", (a,)).fetchone()["id"]
        ub = db.execute("SELECT id FROM users WHERE business_id=%s", (b,)).fetchone()["id"]
    p = store.create_post(a, ua, "Tenant A secret", "https://example.test/a.jpg",
                          "2027-01-01T00:00:00Z", 1)
    assert [row["caption"] for row in store.list_posts(a)] == ["Tenant A secret"]
    assert store.list_posts(b) == []
    assert store.change_post_status(b, ub, p, "approve", 2) is False
    assert store.change_post_status(a, ua, p, "approve", 2) is True
    assert store.change_post_status(a, ua, p, "approve", 3) is False
    with store.connection() as db:
        audit = db.execute("SELECT action FROM audit_events WHERE business_id=%s ORDER BY id", (a,)).fetchall()
        assert [row["action"] for row in audit] == ["post_created", "post_approved"]
