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

    # Session lifecycle: only hashed bearer tokens are persisted.
    store.create_session("hashed-test-token", ua, 100)
    assert store.get_session_user("hashed-test-token", 99)["business_id"] == a
    assert store.get_session_user("hashed-test-token", 100) is None
    store.revoke_session("hashed-test-token")
    assert store.get_session_user("hashed-test-token", 99) is None

    # Brute-force counters reset after the configured window.
    key = "hashed-test-email"
    assert store.record_failed_login(key, 1000) == 0
    assert store.record_failed_login(key, 1001) == 0
    assert store.record_failed_login(key, 1002) == 0
    assert store.record_failed_login(key, 1003) == 0
    assert store.record_failed_login(key, 1004) == 1904
    assert store.record_failed_login(key, 1005) == 1904
    store.clear_login_attempts(key)
    assert store.get_login_attempt(key) is None

    # OAuth state can be consumed once and must not outlive expiry.
    store.create_instagram_oauth_state("hashed-oauth-state", a, ua, 1100, 1000)
    assert store.consume_instagram_oauth_state("hashed-oauth-state", 1050)["business_id"] == a
    assert store.consume_instagram_oauth_state("hashed-oauth-state", 1050) is None
    store.create_instagram_oauth_state("expired-oauth-state", a, ua, 1000, 999)
    assert store.consume_instagram_oauth_state("expired-oauth-state", 1001) is None

    # Publish queue must reject cross-tenant mutation.
    assert store.upsert_publish_job(p, b, "queued", "wrong tenant", 4) is False
    assert store.upsert_publish_job(p, a, "queued", "waiting", 4) is True
    assert [j["state"] for j in store.list_publish_jobs(a)] == ["queued"]
    assert store.list_publish_jobs(b) == []
    assert store.upsert_publish_job(p, a, "blocked", "no Meta connection", 5) is True
    assert store.list_publish_jobs(a)[0]["state"] == "blocked"

    # Only public connection fields may be returned by the status reader.
    store.save_instagram_connection(a, "ig-123", "rhm_teras", "gAAAAencrypted-test-placeholder", 2000, 1000)
    connection = store.get_instagram_connection(a)
    assert connection["username"] == "rhm_teras"
    assert "encrypted_token" not in connection
    assert store.get_instagram_connection(b) is None
