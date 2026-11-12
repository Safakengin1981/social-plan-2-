"""End-to-end HTTP tests for isolated PostgreSQL API, using disposable CI DB."""
import os
import pytest

@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="PostgreSQL CI only")
def test_pg_api_two_tenants_login_posts_and_logout():
    import psycopg
    from fastapi.testclient import TestClient
    from pg_api import app

    with psycopg.connect(os.environ["DATABASE_URL"]) as db:
        db.execute("TRUNCATE instagram_connections,instagram_oauth_states,publish_jobs,audit_events,"
                   "posts,sessions,login_attempts,users,businesses RESTART IDENTITY CASCADE")

    client = TestClient(app)
    password = "strong-test-password-123"
    for email in ("a@ci.example", "b@ci.example"):
        response = client.post("/api/register", json={
            "business": email, "email": email, "password": password,
        })
        assert response.status_code == 200, response.text

    def login(email):
        response = client.post("/api/login", json={"email": email, "password": password})
        assert response.status_code == 200, response.text
        return {"Authorization": "Bearer " + response.json()["token"]}

    a = login("a@ci.example")
    b = login("b@ci.example")
    assert client.get("/api/me", headers=a).json()["business"] == "a@ci.example"
    response = client.post("/api/posts", headers=a, json={
        "caption": "A private post", "media_url": "https://example.test/post.jpg",
        "scheduled_at": "2030-01-01T12:00:00Z",
    })
    assert response.status_code == 200, response.text
    post_id = response.json()["id"]
    assert len(client.get("/api/posts", headers=a).json()) == 1
    assert client.get("/api/posts", headers=b).json() == []
    assert client.post(f"/api/posts/{post_id}/approve", headers=b).status_code == 404
    assert client.post(f"/api/posts/{post_id}/approve", headers=a).status_code == 200
    assert client.post(f"/api/posts/{post_id}/cancel", headers=a).status_code == 200
    assert client.post("/api/logout", headers=a).status_code == 200
    assert client.get("/api/posts", headers=a).status_code == 401
    assert client.get("/api/posts", headers=b).status_code == 200
