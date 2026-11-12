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
        return {"Body": io.BytesIO(obj["Body"]), "ContentType": obj["ContentType"]}


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
