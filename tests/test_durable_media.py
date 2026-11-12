"""No cloud credentials required: test tenant isolation and private object writes."""
import hashlib
import io

import pytest
import durable_media as media


class FakeClient:
    def __init__(self):
        self.objects = {}

    def put_object(self, **kwargs):
        assert "ACL" not in kwargs
        self.objects[kwargs["Key"]] = kwargs

    def get_object(self, Bucket, Key):
        obj = self.objects[Key]
        return {"Body": io.BytesIO(obj["Body"]), "ContentType": obj["ContentType"],
                "Metadata": obj["Metadata"], "ContentLength": len(obj["Body"])}


def test_durable_media_private_and_tenant_scoped(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(media, "client", lambda: fake)
    monkeypatch.setattr(media, "bucket", lambda: "ci-private-bucket")
    key = media.save(11, b"sample-image", "image/jpeg")
    assert key.startswith("tenants/11/")
    assert fake.objects[key]["Metadata"]["sha256"] == hashlib.sha256(b"sample-image").hexdigest()
    assert media.read(11, key) == (b"sample-image", "image/jpeg")
    with pytest.raises(PermissionError):
        media.read(12, key)
    with pytest.raises(PermissionError):
        media.read(11, "tenants/11/../../secrets")
    with pytest.raises(ValueError):
        media.save(11, b"image", "text/html")
    with pytest.raises(ValueError):
        media.save(11, b"x" * (media.MAX_BYTES + 1), "image/jpeg")

def test_durable_media_detects_corruption(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(media, "client", lambda: fake)
    monkeypatch.setattr(media, "bucket", lambda: "ci-private-bucket")
    key = media.save(7, b"original", "image/png")
    fake.objects[key]["Body"] = b"tampered"
    with pytest.raises(ValueError, match="checksum mismatch"):
        media.read(7, key)


def test_durable_media_rejects_oversized_download(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(media, "client", lambda: fake)
    monkeypatch.setattr(media, "bucket", lambda: "ci-private-bucket")
    key = media.save(7, b"small", "image/png")
    fake.objects[key]["Body"] = b"x" * (media.MAX_BYTES + 1)
    with pytest.raises(ValueError, match="exceeds"):
        media.read(7, key)

def test_durable_media_requires_checksum_metadata(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(media, "client", lambda: fake)
    monkeypatch.setattr(media, "bucket", lambda: "ci-private-bucket")
    key = media.save(7, b"sample", "image/jpeg")
    fake.objects[key]["Metadata"] = {}
    with pytest.raises(ValueError, match="checksum missing"):
        media.read(7, key)
