"""Isolated integration test: use a temporary SQLite database and upload directory.
Run: pip install httpx pytest; pytest -q tests/test_content_export.py
Never point SOCIALPLAN_DB at a live database.
"""
import importlib
import io
import json
import os
import sys
import zipfile

def test_export_reports_missing_media_and_keeps_tenant_data_private(tmp_path, monkeypatch):
    monkeypatch.setenv("SOCIALPLAN_DB", str(tmp_path / "test.sqlite3"))
    monkeypatch.setenv("SOCIALPLAN_UPLOAD_DIR", str(tmp_path / "uploads"))
    sys.modules.pop("app", None)
    import app
    from fastapi.testclient import TestClient
    with TestClient(app.app) as client:
        def signup(name, email):
            response = client.post("/api/register", json={
                "business": name, "email": email, "password": "safe-test-password-123"})
            assert response.status_code == 200, response.text
            login = client.post("/api/login", json={"email": email, "password": "safe-test-password-123"})
            assert login.status_code == 200, login.text
            return {"Authorization": "Bearer " + login.json()["token"]}
        a = signup("Business A", "a@example.test")
        b = signup("Business B", "b@example.test")
        with app.conn() as db:
            ua = db.execute("SELECT id,business_id FROM users WHERE email=?", ("a@example.test",)).fetchone()
            ub = db.execute("SELECT id,business_id FROM users WHERE email=?", ("b@example.test",)).fetchone()
            db.execute("INSERT INTO posts(business_id,author_id,caption,media_url,scheduled_at,status,created_at) VALUES(?,?,?,?,?,?,?)",
                       (ua["business_id"], ua["id"], "A private caption", "/api/media/missing.jpg", "2027-01-01T12:00:00+00:00", "draft", 1))
            db.execute("INSERT INTO posts(business_id,author_id,caption,media_url,scheduled_at,status,created_at) VALUES(?,?,?,?,?,?,?)",
                       (ub["business_id"], ub["id"], "B private caption", "https://example.com/b.jpg", "2027-01-01T12:00:00+00:00", "draft", 1))
        response = client.get("/api/admin/export-content.zip", headers=a)
        assert response.status_code == 200, response.text
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            manifest = json.loads(archive.read("content.json"))
            assert [p["caption"] for p in manifest["posts"]] == ["A private caption"]
            assert manifest["missing_media"] == ["missing.jpg"]
            assert manifest["media_complete"] is False
            assert all("token" not in key.lower() for key in manifest.keys())
        response_b = client.get("/api/admin/export-content.zip", headers=b)
        assert response_b.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response_b.content)) as archive:
            manifest_b = json.loads(archive.read("content.json"))
            assert [p["caption"] for p in manifest_b["posts"]] == ["B private caption"]
            assert manifest_b["media_complete"] is True
