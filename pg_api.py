"""Isolated PostgreSQL-backed API proof of concept.

This is NOT the production entrypoint. No Instagram publishing or media uploads.
Start only in a disposable test environment with DATABASE_URL set.
"""
import hashlib
import hmac
import secrets
import time
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, File, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
import psycopg

import postgres_store as store
import durable_media

app = FastAPI(title="SocialPlan PostgreSQL isolated API")


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 310000).hex()


class Registration(BaseModel):
    business: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=12, max_length=128)


class Login(BaseModel):
    email: str
    password: str


class Post(BaseModel):
    caption: str = Field(min_length=1, max_length=2200)
    media_url: str = Field(min_length=8, max_length=2000)
    scheduled_at: str = Field(min_length=16, max_length=40)


def auth(authorization: str = Header(default="")):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Giriş gerekli")
    token_hash = hashlib.sha256(authorization[7:].encode()).hexdigest()
    user = store.get_session_user(token_hash, int(time.time()))
    if not user:
        raise HTTPException(401, "Oturum geçersiz")
    return user


@app.post("/api/register")
def register(x: Registration):
    salt = secrets.token_hex(16)
    try:
        store.register_business(
            x.business.strip(), x.email.strip().lower(), salt,
            password_hash(x.password, salt),
        )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "Bu e-posta zaten kayıtlı")
    return {"message": "İşletme oluşturuldu; giriş yapabilirsiniz"}


@app.post("/api/login")
def login(x: Login):
    email = x.email.strip().lower()
    email_hash = hashlib.sha256(email.encode()).hexdigest()
    now = int(time.time())
    attempt = store.get_login_attempt(email_hash)
    if attempt and attempt["locked_until"] > now:
        raise HTTPException(429, "Çok fazla başarısız deneme. 15 dakika sonra tekrar deneyin.")
    user = store.get_user_by_email(email)
    salt = user["salt"] if user else "00" * 16
    expected = user["passhash"] if user else "00" * 32
    valid = hmac.compare_digest(expected, password_hash(x.password, salt)) and user is not None
    if not valid:
        store.record_failed_login(email_hash, now)
        raise HTTPException(401, "Giriş bilgileri hatalı")
    store.clear_login_attempts(email_hash)
    token = secrets.token_urlsafe(32)
    store.create_session(hashlib.sha256(token.encode()).hexdigest(), user["id"], now + 86400)
    return {"token": token, "role": user["role"]}


@app.get("/api/me")
def me(user=Depends(auth)):
    with store.connection() as db:
        business = db.execute("SELECT name FROM businesses WHERE id=%s", (user["business_id"],)).fetchone()
    return {"email": user["email"], "role": user["role"], "business": business["name"]}


@app.get("/api/posts")
def posts(user=Depends(auth)):
    return [dict(row) for row in store.list_posts(user["business_id"])]


@app.post("/api/posts")
def create_post(x: Post, user=Depends(auth)):
    try:
        when = datetime.fromisoformat(x.scheduled_at.replace("Z", "+00:00"))
        if when.tzinfo is None or when.astimezone(timezone.utc) <= datetime.now(timezone.utc):
            raise ValueError()
    except ValueError:
        raise HTTPException(422, "Gelecekte, saat dilimi belirtilmiş geçerli tarih seçin")
    # This isolated API has no durable media backend. Reject local media paths
    # rather than accepting posts whose files would disappear on redeploy.
    if not x.media_url.startswith("https://"):
        raise HTTPException(422, "Kalıcı medya deposu kurulana kadar yalnızca HTTPS medya bağlantısı kullanılabilir")
    post_id = store.create_post(
        user["business_id"], user["id"], x.caption, x.media_url,
        x.scheduled_at, int(time.time()),
    )
    return {"id": post_id, "status": "draft"}


@app.post("/api/posts/{post_id}/approve")
def approve(post_id: int, user=Depends(auth)):
    if user["role"] != "admin":
        raise HTTPException(403, "Yönetici yetkisi gerekli")
    if not store.change_post_status(user["business_id"], user["id"], post_id, "approve", int(time.time())):
        raise HTTPException(404, "Onaylanabilir içerik bulunamadı")
    return {"status": "approved", "note": "Otomatik Instagram yayınlama henüz bağlı değil"}


@app.post("/api/posts/{post_id}/cancel")
def cancel(post_id: int, user=Depends(auth)):
    if user["role"] != "admin":
        raise HTTPException(403, "Yönetici yetkisi gerekli")
    if not store.change_post_status(user["business_id"], user["id"], post_id, "cancel", int(time.time())):
        raise HTTPException(404, "İptal edilebilir içerik bulunamadı")
    return {"status": "cancelled"}


@app.post("/api/logout")
def logout(authorization: str = Header(default=""), user=Depends(auth)):
    store.revoke_session(hashlib.sha256(authorization[7:].encode()).hexdigest())
    return {"ok": True}


@app.post("/api/media")
async def upload_media(file: UploadFile = File(...), user=Depends(auth)):
    content_type = file.content_type or ""
    if content_type not in durable_media.TYPES:
        raise HTTPException(422, "Desteklenmeyen medya türü")
    data = await file.read(durable_media.MAX_BYTES + 1)
    if not data or len(data) > durable_media.MAX_BYTES:
        raise HTTPException(413, "Medya dosyası boş veya 25 MB sınırını aşıyor")
    try:
        key = durable_media.save(user["business_id"], data, content_type)
    except RuntimeError:
        raise HTTPException(503, "Kalıcı medya depolaması yapılandırılmamış")
    return {"media_key": key, "size": len(data), "content_type": content_type}


@app.get("/api/media/{key:path}")
def download_media(key: str, user=Depends(auth)):
    try:
        data, content_type = durable_media.read(user["business_id"], key)
    except PermissionError:
        raise HTTPException(404, "Medya bulunamadı")
    except RuntimeError:
        raise HTTPException(503, "Kalıcı medya depolaması yapılandırılmamış")
    return Response(content=data, media_type=content_type,
                    headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@app.get("/api/health")
def health():
    return {"status": "ok", "database_engine": "postgresql", "instagram_publishing": "not_configured"}
