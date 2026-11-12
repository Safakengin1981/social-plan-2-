"""End-to-end HTTP tests for isolated PostgreSQL API, using disposable CI DB."""
import os
import pytest

@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="PostgreSQL CI only")
def test_pg_api_two_tenants_login_posts_and_logout(monkeypatch):
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

    import durable_media
    from test_durable_media import FakeClient
    fake_media = FakeClient()
    monkeypatch.setattr(durable_media, "client", lambda: fake_media)
    monkeypatch.setattr(durable_media, "bucket", lambda: "ci-private-bucket")
    a = login("a@ci.example")
    b = login("b@ci.example")
    assert client.get("/api/me", headers=a).json()["business"] == "a@ci.example"
    upload = client.post("/api/media", headers=a, files={
        "file": ("image.jpg", b"test-image-bytes", "image/jpeg")
    })
    assert upload.status_code == 200, upload.text
    media_key = upload.json()["media_key"]
    assert media_key.startswith("tenants/")
    assert client.get("/api/media/" + media_key, headers=a).content == b"test-image-bytes"
    assert client.get("/api/media/" + media_key, headers=b).status_code == 404
    assert client.post("/api/media", headers=a, files={
        "file": ("unsafe.html", b"<script>bad</script>", "text/html")
    }).status_code == 422

    uploaded_post = client.post("/api/posts", headers=a, json={
        "caption": "Private uploaded image", "media_key": media_key,
        "scheduled_at": "2030-01-01T12:00:00Z",
    })
    assert uploaded_post.status_code == 200, uploaded_post.text
    other_tenant_post = client.post("/api/posts", headers=b, json={
        "caption": "Unauthorized media", "media_key": media_key,
        "scheduled_at": "2030-01-01T12:00:00Z",
    })
    assert other_tenant_post.status_code == 404, other_tenant_post.text
    assert client.post("/api/posts", headers=a, json={
        "caption": "Ambiguous media", "media_key": media_key,
        "media_url": "https://example.test/another.jpg",
        "scheduled_at": "2030-01-01T12:00:00Z",
    }).status_code == 422
    rejected = client.post("/api/posts", headers=a, json={
        "caption": "Unsafe local media", "media_url": "/api/media/temporary.jpg",
        "scheduled_at": "2030-01-01T12:00:00Z",
    })
    assert rejected.status_code == 422, rejected.text
    response = client.post("/api/posts", headers=a, json={
        "caption": "A private post", "media_url": "https://example.test/post.jpg",
        "scheduled_at": "2030-01-01T12:00:00Z",
    })
    assert response.status_code == 200, response.text
    post_id = response.json()["id"]
    assert len(client.get("/api/posts", headers=a).json()) == 2
    assert client.get("/api/posts", headers=b).json() == []
    assert client.post(f"/api/posts/{post_id}/approve", headers=b).status_code == 404
    assert client.post(f"/api/posts/{post_id}/approve", headers=a).status_code == 200
    assert client.post(f"/api/posts/{post_id}/cancel", headers=a).status_code == 200
    assert client.post("/api/logout", headers=a).status_code == 200
    assert client.get("/api/posts", headers=a).status_code == 401
    assert client.get("/api/posts", headers=b).status_code == 200
