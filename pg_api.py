"""Isolated PostgreSQL-backed API proof of concept.

This is NOT the production entrypoint. No Instagram publishing or media uploads.
Start only in a disposable test environment with DATABASE_URL set.
"""
import hashlib
import logging
from contextlib import asynccontextmanager
import hmac
import secrets
import time
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, File, UploadFile
from fastapi.responses import Response, HTMLResponse
from pydantic import BaseModel, Field
import psycopg

import postgres_store as store
import durable_media

logger = logging.getLogger("socialplan.persistence_probe")
PROBE_KEY = hashlib.sha256(b"socialplan-neon-isolated-persistence-probe-v1").hexdigest()


@asynccontextmanager
async def lifespan(app):
    # Isolated test only: an idempotent marker survives process restarts/deploys.
    # No existing business, user, post, or session rows are modified.
    with store.connection() as db:
        existing = db.execute(
            "SELECT first_attempt FROM login_attempts WHERE email_hash=%s",
            (PROBE_KEY,),
        ).fetchone()
        if existing is None:
            now = int(time.time())
            db.execute(
                "INSERT INTO login_attempts(email_hash,failures,first_attempt,locked_until) "
                "VALUES (%s,0,%s,0) ON CONFLICT(email_hash) DO NOTHING",
                (PROBE_KEY, now),
            )
            logger.warning("PERSISTENCE_PROBE initialized marker in isolated Neon database")
        else:
            logger.warning(
                "PERSISTENCE_PROBE existing marker recovered from Neon; created_at=%s",
                existing["first_attempt"],
            )
    run_workflow_probe()
    yield


app = FastAPI(title="SocialPlan PostgreSQL isolated API", lifespan=lifespan)


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 310000).hex()


def run_workflow_probe():
    """One synthetic tenant in the isolated Neon test DB; no external publishing."""
    email = "socialplan-workflow-probe-v1@example.invalid"
    caption = "SOCIALPLAN_WORKFLOW_PROBE_V1"
    user = store.get_user_by_email(email)
    if user is None:
        password = secrets.token_urlsafe(32)
        salt = secrets.token_hex(16)
        business_id = store.register_business(
            "SocialPlan isolated workflow probe", email, salt,
            password_hash(password, salt),
        )
        user = store.get_user_by_email(email)
        if not user or user["business_id"] != business_id:
            raise RuntimeError("WORKFLOW_PROBE registration verification failed")
        if not hmac.compare_digest(user["passhash"], password_hash(password, user["salt"])):
            raise RuntimeError("WORKFLOW_PROBE password verification failed")
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now = int(time.time())
        store.create_session(token_hash, user["id"], now + 300)
        authenticated = store.get_session_user(token_hash, now)
        if not authenticated or authenticated["business_id"] != business_id:
            raise RuntimeError("WORKFLOW_PROBE session verification failed")
        post_id = store.create_post(
            business_id, user["id"], caption,
            "https://example.com/socialplan-workflow-probe.jpg",
            "2035-01-01T12:00:00+00:00", now,
        )
        if not any(p["id"] == post_id and p["status"] == "draft"
                   for p in store.list_posts(business_id)):
            raise RuntimeError("WORKFLOW_PROBE draft verification failed")
        if not store.change_post_status(business_id, user["id"], post_id, "approve", now):
            raise RuntimeError("WORKFLOW_PROBE approval failed")
        if not store.change_post_status(business_id, user["id"], post_id, "cancel", now):
            raise RuntimeError("WORKFLOW_PROBE cancellation failed")
        store.revoke_session(token_hash)
        if store.get_session_user(token_hash, now):
            raise RuntimeError("WORKFLOW_PROBE logout failed")
        logger.warning("WORKFLOW_PROBE initialized: registration, login, draft, approval, cancellation, logout verified")
    else:
        business_id = user["business_id"]
        logger.warning("WORKFLOW_PROBE existing synthetic tenant recovered")
    with store.connection() as db:
        row = db.execute(
            "SELECT p.id,p.status,p.caption FROM posts p "
            "WHERE p.business_id=%s AND p.caption=%s ORDER BY p.id LIMIT 1",
            (business_id, caption),
        ).fetchone()
        if not row or row["status"] != "cancelled":
            raise RuntimeError("WORKFLOW_PROBE persisted post missing or wrong status")
        # An unrelated tenant ID must never read or change this post.
        # Use a real, separate synthetic tenant rather than a nonexistent ID.
        outsider_email = "socialplan-isolation-probe-v1@example.invalid"
        outsider = store.get_user_by_email(outsider_email)
        if outsider is None:
            outsider_salt = secrets.token_hex(16)
            store.register_business(
                "SocialPlan isolated second tenant", outsider_email,
                outsider_salt, password_hash(secrets.token_urlsafe(32), outsider_salt),
            )
            outsider = store.get_user_by_email(outsider_email)
        if not outsider or outsider["business_id"] == business_id:
            raise RuntimeError("WORKFLOW_PROBE second tenant setup failed")
        outsider_business_id = outsider["business_id"]
        # Give the second tenant its own durable post, then verify both directions.
        outsider_caption = "SOCIALPLAN_SECOND_TENANT_PROBE_V1"
        outsider_posts = store.list_posts(outsider_business_id)
        if not any(p["caption"] == outsider_caption for p in outsider_posts):
            store.create_post(
                outsider_business_id, outsider["id"], outsider_caption,
                "https://example.com/socialplan-second-tenant.jpg",
                "2035-01-02T12:00:00+00:00", int(time.time()),
            )
            outsider_posts = store.list_posts(outsider_business_id)
        if not any(p["caption"] == outsider_caption for p in outsider_posts):
            raise RuntimeError("WORKFLOW_PROBE second tenant post missing")
        if any(p["caption"] == caption for p in outsider_posts):
            raise RuntimeError("WORKFLOW_PROBE tenant isolation read failed")
        if any(p["caption"] == outsider_caption for p in store.list_posts(business_id)):
            raise RuntimeError("WORKFLOW_PROBE reverse tenant isolation read failed")
        outsider_post = next(p for p in outsider_posts if p["caption"] == outsider_caption)
        if store.change_post_status(
            business_id, user["id"], outsider_post["id"], "approve", int(time.time())
        ):
            raise RuntimeError("WORKFLOW_PROBE reverse tenant isolation write failed")
        if store.change_post_status(
            outsider_business_id, outsider["id"], row["id"], "approve", int(time.time())
        ):
            raise RuntimeError("WORKFLOW_PROBE tenant isolation write failed")
        # Durable publishing queue is only a dry-run: no Instagram publishing.
        now = int(time.time())
        previous_jobs = store.list_publish_jobs(outsider_business_id)
        existing_job = next(
            (j for j in previous_jobs if j["post_id"] == outsider_post["id"]), None
        )
        if existing_job is None:
            if not store.upsert_publish_job(
                outsider_post["id"], outsider_business_id, "blocked",
                "isolation-test-no-instagram", now,
            ):
                raise RuntimeError("WORKFLOW_PROBE queue write failed")
            logger.warning("WORKFLOW_PROBE publishing queue marker initialized")
        elif existing_job["state"] == "blocked" and existing_job["reason"] == "isolation-test-no-instagram":
            logger.warning("WORKFLOW_PROBE publishing queue marker recovered from Neon")
        else:
            raise RuntimeError("WORKFLOW_PROBE persisted queue marker changed unexpectedly")
        if store.upsert_publish_job(
            outsider_post["id"], business_id, "ready", "cross-tenant-attempt", now,
        ):
            raise RuntimeError("WORKFLOW_PROBE cross-tenant queue mutation allowed")
        own_jobs = store.list_publish_jobs(outsider_business_id)
        if not any(j["post_id"] == outsider_post["id"] and j["state"] == "blocked"
                   for j in own_jobs):
            raise RuntimeError("WORKFLOW_PROBE own queue read failed")
        if any(j["post_id"] == outsider_post["id"]
               for j in store.list_publish_jobs(business_id)):
            raise RuntimeError("WORKFLOW_PROBE cross-tenant queue read allowed")
        logger.warning("WORKFLOW_PROBE publishing queue tenant isolation verified (dry-run)")
        actions = db.execute(
            "SELECT action FROM audit_events WHERE business_id=%s AND post_id=%s",
            (business_id, row["id"]),
        ).fetchall()
        if {a["action"] for a in actions} != {"post_created", "post_approved", "post_cancelled"}:
            raise RuntimeError("WORKFLOW_PROBE audit trail missing")
    logger.warning("WORKFLOW_PROBE persisted post and audit trail verified")


class Registration(BaseModel):
    business: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=12, max_length=128)


class Login(BaseModel):
    email: str
    password: str


class Post(BaseModel):
    caption: str = Field(min_length=1, max_length=2200)
    media_url: str | None = Field(default=None, max_length=2000)
    media_key: str | None = Field(default=None, max_length=2000)
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
    user = store.get_user_by_email(email)
    salt = user["salt"] if user else "00" * 16
    expected = user["passhash"] if user else "00" * 32
    valid = hmac.compare_digest(expected, password_hash(x.password, salt)) and user is not None
    if not valid:
        attempt = store.get_login_attempt(email_hash)
        if attempt and attempt["locked_until"] > now:
            remaining = attempt["locked_until"] - now
            raise HTTPException(
                429, f"Çok fazla başarısız deneme. {((remaining + 59) // 60)} dakika sonra tekrar deneyin.",
                headers={"Retry-After": str(remaining)},
            )
        locked_until = store.record_failed_login(email_hash, now)
        if locked_until > now:
            raise HTTPException(
                429, "Çok fazla başarısız deneme. 15 dakika sonra tekrar deneyin.",
                headers={"Retry-After": str(locked_until - now)},
            )
        raise HTTPException(401, "Giriş bilgileri hatalı")
    # A correct password can recover a locked account; failed attempts remain throttled.
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
    # Exactly one source. Uploaded objects remain private and tenant-scoped.
    if bool(x.media_key) == bool(x.media_url):
        raise HTTPException(422, "Tek bir medya kaynağı seçin: media_key veya HTTPS media_url")
    if x.media_key:
        try:
            durable_media.read(user["business_id"], x.media_key)
        except (PermissionError, KeyError):
            raise HTTPException(404, "Bu işletmeye ait medya bulunamadı")
        except ValueError:
            raise HTTPException(502, "Depolanan medya dosyası bozuk veya boyut sınırını aşıyor")
        except RuntimeError:
            raise HTTPException(503, "Kalıcı medya depolaması yapılandırılmamış")
        media_reference = "private-s3://" + x.media_key
    else:
        if not x.media_url.startswith("https://"):
            raise HTTPException(422, "Yalnızca HTTPS medya bağlantısı kullanılabilir")
        media_reference = x.media_url
    post_id = store.create_post(
        user["business_id"], user["id"], x.caption, media_reference,
        x.scheduled_at, int(time.time()),
    )
    return {"id": post_id, "status": "draft"}


@app.put("/api/posts/{post_id}")
def edit_draft(post_id: int, x: Post, user=Depends(auth)):
    if user["role"] != "admin":
        raise HTTPException(403, "Yönetici yetkisi gerekli")
    try:
        when = datetime.fromisoformat(x.scheduled_at.replace("Z", "+00:00"))
        if when.tzinfo is None or when.astimezone(timezone.utc) <= datetime.now(timezone.utc):
            raise ValueError()
    except ValueError:
        raise HTTPException(422, "Gelecekte, saat dilimi belirtilmiş geçerli tarih seçin")
    if not x.media_url or x.media_key or not x.media_url.startswith("https://"):
        raise HTTPException(422, "Düzenleme için HTTPS medya bağlantısı gereklidir")
    changed = store.update_draft(
        user["business_id"], user["id"], post_id,
        x.caption, x.media_url, x.scheduled_at, int(time.time()),
    )
    if not changed:
        raise HTTPException(404, "Düzenlenebilir taslak bulunamadı")
    return {"id": post_id, "status": "draft", "message": "Taslak güncellendi"}


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
    except ValueError:
        raise HTTPException(502, "Depolanan medya dosyası bozuk veya boyut sınırını aşıyor")
    except RuntimeError:
        raise HTTPException(503, "Kalıcı medya depolaması yapılandırılmamış")
    return Response(content=data, media_type=content_type,
                    headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@app.get("/pg-test", response_class=HTMLResponse)
def test_interface():
    """Isolated testing UI only; never mount on the production entrypoint."""
    return """<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SocialPlan PostgreSQL Test</title>
<style>body{font:16px system-ui;max-width:720px;margin:30px auto;padding:0 16px;color:#17212b}
input,textarea,button,select{font:inherit;padding:10px;margin:6px 0;width:100%;box-sizing:border-box}
button{background:#154e69;color:white;border:0;border-radius:6px;cursor:pointer}
[hidden]{display:none!important}section{border:1px solid #ddd;border-radius:8px;padding:16px;margin:16px 0}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f5f5;padding:12px}.post-card{border:1px solid #dce2e7;border-radius:10px;padding:16px;margin:12px 0;background:#fff}.post-card h3{margin:0 0 8px}.post-card p{overflow-wrap:anywhere}.post-card img{max-width:180px;max-height:140px;object-fit:cover;border-radius:6px}.post-actions{display:flex;gap:8px;flex-wrap:wrap}.post-actions button{width:auto;flex:1;min-width:140px}.post-card .meta{color:#52606d;font-size:14px}.post-card .state{font-weight:700}.post-card button:disabled{opacity:.5;cursor:not-allowed}.dashboard{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.metric{border:1px solid #dce2e7;border-radius:8px;padding:12px;background:#f8fafb}.metric strong{display:block;font-size:25px}.calendar-grid{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:4px;margin:14px 0}.calendar-day{min-height:95px;border:1px solid #dce2e7;border-radius:6px;padding:6px;min-width:0;background:#fff}.calendar-day.other{background:#f5f7f8;color:#7b8790}.calendar-day strong{display:block;font-size:12px}.calendar-chip{display:block;width:100%;text-align:left;font-size:11px;line-height:1.3;margin:4px 0;padding:5px;border:1px solid #a9bdc7;background:#eaf2f6;border-radius:4px;overflow-wrap:anywhere;cursor:pointer}.calendar-chip.cancelled{background:#f0f0f0;border-color:#ddd}.calendar-chip.approved{background:#eaf4ec;border-color:#b5d4bc}.calendar-weekday{font-size:12px;text-align:center;font-weight:700}.calendar-grid.weekly{grid-template-columns:1fr;gap:8px}.calendar-grid.weekly .calendar-weekday{display:none}.calendar-grid.weekly .calendar-day{min-height:64px;display:grid;grid-template-columns:100px minmax(0,1fr);align-items:start;gap:8px}.calendar-grid.weekly .calendar-day strong{font-size:14px;padding:7px 0}.calendar-grid.weekly .calendar-chip{grid-column:2;font-size:13px;white-space:normal}.calendar-chip,.calendar-chip:hover{color:#153247!important;font-weight:600!important;box-shadow:none!important}.calendar-chip.cancelled{color:#424a51!important;background:#e9eaec!important;border-color:#c9cdd1!important}.calendar-chip.approved{color:#18582b!important;background:#e4f3e8!important;border-color:#a9cfb2!important}.calendar-chip.draft{color:#174c70!important;background:#e3f1fb!important;border-color:#a5c9e0!important}.calendar-chip:focus-visible{outline:3px solid #1d668e;outline-offset:2px}.calendar-day{overflow:hidden}.calendar-chip{white-space:nowrap;text-overflow:ellipsis;overflow:hidden;line-height:1.4;min-height:32px}.calendar-detail{border:1px solid #cbd6dd;border-radius:8px;padding:14px;background:#f7fafc;margin-top:12px}.calendar-detail p{overflow-wrap:anywhere}.calendar-detail button{margin-top:8px}@media(max-width:640px){.calendar-grid:not(.weekly){overflow-x:auto;min-width:640px}.calendar-day{padding:4px}.calendar-chip{font-size:10px}}.calendar-list{list-style:none;padding:0}.calendar-list li{border-bottom:1px solid #e5e9ec;padding:10px 0}.calendar-list time{font-weight:600}.calendar-list small{display:block;color:#52606d}@media(min-width:650px){.dashboard{grid-template-columns:repeat(4,minmax(0,1fr))}}
.overview-layout{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-top:16px}.overview-card{border:1px solid #dce3e8;border-radius:9px;padding:16px;min-width:0}.overview-card h3{margin:0 0 8px}.overview-item{border-top:1px solid #e4e9ed;padding:11px 0;display:flex;align-items:center;justify-content:space-between;gap:10px}.overview-item span{font-size:13px;overflow-wrap:anywhere}.overview-item small{display:block;color:#536879}.overview-item button{width:auto;flex:0 0 auto;padding:8px 10px;margin:0;font-size:12px}.overview-empty{color:#536879;font-size:13px}@media(max-width:680px){.overview-layout{grid-template-columns:1fr}}.app-header{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:18px 4px;border-bottom:1px solid #dbe3e8}.app-header h1{margin:0;font-size:29px;letter-spacing:-.6px}.app-header h1 span{color:#247397}.app-header p{margin:5px 0;color:#516475;font-size:13px}.test-label{font-size:11px;letter-spacing:.3px;font-weight:700;color:#73551c;background:#fff2d6;padding:8px;border-radius:6px}.app-nav{display:flex;gap:7px;overflow-x:auto;position:sticky;top:0;background:white;padding:12px 0;z-index:10;border-bottom:1px solid #e1e7eb}.app-nav button{flex:0 0 auto;width:auto;padding:10px 13px;margin:0;background:#edf3f6;color:#19425b;font-size:14px}.app-nav button.active{background:#154e69;color:#fff}.app-panel{display:none}.app-panel.active{display:block}.app-panel section{margin:14px 0}.app-panel>section:first-child{margin-top:18px}body{max-width:960px}.app-nav button:focus-visible{outline:3px solid #3984a6}@media(max-width:640px){.app-header{display:block}.test-label{display:inline-block;margin-top:9px}.app-nav button{font-size:12px;padding:9px}}
</style></head><body>
<header class="app-header"><div><h1>SocialPlan <span>5.8</span></h1><p>RHM Teras · Bağımsız PostgreSQL test paneli</p></div><span class="test-label">TEST ORTAMI · Instagram yayını kapalı</span></header><nav id="app-nav" class="app-nav" aria-label="Yönetim bölümleri" hidden><button type="button" data-nav="overview">Genel bakış</button><button type="button" data-nav="calendar">Yayın takvimi</button><button type="button" data-nav="create">İçerik oluştur</button><button type="button" data-nav="posts">İçerik yönetimi</button><button type="button" data-nav="account">Hesap</button></nav>
<section id="registration-section"><h2>1. Yeni test işletmesi oluştur</h2><label>İşletme adı</label><input id="business" placeholder="Örnek Test İşletmesi"><label>E-posta adresi</label><input id="register-email" type="email" autocomplete="email" placeholder="ornek@eposta.com"><label>Test şifresi (en az 12 karakter)</label><input id="register-password" type="password" autocomplete="new-password" placeholder="En az 12 karakter"><button id="register">İşletme oluştur</button><p id="register-status" role="status" aria-live="polite"></p><p>Aynı e-posta daha önce kaydedildiyse yeniden işletme oluşturmayın; aşağıdan giriş yapın.</p></section>
<section id="login-section"><h2>2. Giriş</h2><input id="email" type="email" autocomplete="email" placeholder="Kayıtlı e-posta">
<input id="password" type="password" placeholder="Şifre">
<button id="login">Giriş yap</button><button id="logout">Çıkış yap</button><p id="login-status" role="status" aria-live="polite"></p></section>
<section><h2>Medya kaynağı (R2 gerektirmez)</h2>
<input id="mediaurl" type="url" placeholder="Herkese açık HTTPS görsel/video bağlantısı">
<p>Neon kayıt testi için HTTPS bağlantısı kullanın. Dosya yükleme yalnızca ayrı depolama yapılandırıldığında çalışır.</p>
<h3>İsteğe bağlı dosya yükleme</h3>
<input id="file" type="file" accept="image/jpeg,image/png,image/webp,video/mp4">
<button id="upload">Dosyayı yükle</button><p id="media"></p></section>
<section><h2>İçerik taslağı</h2><textarea id="caption" placeholder="Açıklama"></textarea>
<input id="scheduled" type="datetime-local"><button id="create">Taslak oluştur</button>
<button id="refresh">Taslakları listele</button><h3>İçerik işlemleri</h3><label for="post-id">İçerik numarası (ID)</label><input id="post-id" type="number" min="1" step="1" placeholder="Örnek: 3"><button id="approve">İçeriği onayla</button><button id="cancel">İçeriği iptal et</button><p>Onay işlemi Instagram'da paylaşım yapmaz. İptal edilen içerik kaydı silinmez.</p></section>
<section id="edit-section" hidden><h2>Taslağı düzenle</h2><p>Yalnızca taslak durumundaki içerikler düzenlenebilir.</p><label for="edit-id">İçerik ID</label><input id="edit-id" readonly><label for="edit-caption">Açıklama</label><textarea id="edit-caption"></textarea><label for="edit-media">HTTPS medya bağlantısı</label><input id="edit-media" type="url"><label for="edit-date">Planlanan tarih</label><input id="edit-date" type="datetime-local"><button id="save-edit">Değişiklikleri kaydet</button><button id="close-edit" type="button">Vazgeç</button><p id="edit-status" role="status"></p></section>
<section><h2>Yönetim özeti</h2><div class="dashboard" id="dashboard" aria-live="polite"><div class="metric">İçerik verileri giriş sonrasında yüklenir.</div></div><div class="overview-layout"><div class="overview-card"><h3>Yaklaşan paylaşımlar</h3><p class="meta">İptal edilen içerikler bu listede yer almaz. Onaylama Instagram yayını anlamına gelmez.</p><div id="upcoming-posts" aria-live="polite"></div></div><div class="overview-card"><h3>Son içerik işlemleri</h3><p class="meta">Kayıtların mevcut durumuna göre listelenir; işlem geçmişi değildir.</p><div id="recent-posts" aria-live="polite"></div></div></div></section>
<section><h2>Yayın takvimi</h2><p>Planlanan içerikler, yayınlanmadan yalnızca takvim görünümünde listelenir.</p><label for="calendar-view">Takvim görünümü</label><select id="calendar-view"><option value="month">Aylık</option><option value="week">Haftalık</option></select><label for="calendar-month">Ay seçin</label><input id="calendar-month" type="month"><label for="calendar-week-date" id="calendar-week-label" hidden>Haftadan bir gün seçin</label><input id="calendar-week-date" type="date" hidden><div id="calendar-grid" class="calendar-grid" aria-live="polite"></div><div id="calendar-detail" class="calendar-detail" hidden aria-live="polite"></div><details><summary>Kronolojik liste</summary><ul id="calendar-list" class="calendar-list"></ul></details></section>
<section><h2>İçerik yönetimi</h2><p>İçerikleriniz burada tarih, açıklama ve durumlarıyla görüntülenir.</p><label for="status-filter">Duruma göre filtrele</label><select id="status-filter"><option value="all">Tüm içerikler</option><option value="draft">Taslaklar</option><option value="approved">Onaylananlar</option><option value="cancelled">İptal edilenler</option></select><p id="post-count" aria-live="polite"></p><div id="post-list" aria-live="polite">İçerikleri görmek için giriş yapıp Taslakları listele seçeneğine basın.</div></section><details><summary>Teknik işlem çıktısı</summary><pre id="output" role="status"></pre></details>
<script>
let activePanel="overview";
function setupPanels(){
 const sections=[...document.querySelectorAll("body > section")];
 const groups={overview:[],calendar:[],create:[],posts:[],account:[]};
 for(const section of sections){
  const heading=section.querySelector("h2")?.textContent||"";
  const panel=section.id==="registration-section"||section.id==="login-section"?"account":
   section.id==="edit-section"||heading==="İçerik yönetimi"?"posts":
   heading==="Yönetim özeti"?"overview":
   heading==="Yayın takvimi"?"calendar":"create";
  groups[panel].push(section);
 }
 const nav=document.getElementById("app-nav");
 for(const [name,items] of Object.entries(groups)){
  const wrap=document.createElement("div");wrap.className="app-panel";wrap.dataset.panel=name;
  for(const item of items)wrap.append(item);
  nav.after(wrap);
 }
 nav.querySelectorAll("[data-nav]").forEach(button=>button.onclick=()=>showPanel(button.dataset.nav));
 showPanel("account");
}
function showPanel(name){
 activePanel=name;
 document.querySelectorAll(".app-panel").forEach(p=>p.classList.toggle("active",p.dataset.panel===name));
 document.querySelectorAll("[data-nav]").forEach(b=>{b.classList.toggle("active",b.dataset.nav===name);b.setAttribute("aria-current",b.dataset.nav===name?"page":"false");});
}
setupPanels();

let token = sessionStorage.getItem("socialplan_pg_test_token") || "", mediaKey = "", cachedPosts = [];
const out = document.getElementById("output");
async function api(path, opts = {}) {
  const headers = {...(opts.headers || {})};
  if (token) headers.Authorization = "Bearer " + token;
  if (opts.body && !(opts.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {...opts, headers});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) { const detail = Array.isArray(data.detail) ? data.detail.map(x => (x.loc || []).slice(-1).join("") + ": " + x.msg).join("; ") : data.detail; throw new Error(detail || "İstek başarısız: " + response.status); }
  return data;
}
function report(p) { out.textContent = typeof p === "string" ? p : JSON.stringify(p,null,2); }
document.getElementById("register").onclick = async () => {
 const status=document.getElementById("register-status");
 const business=document.getElementById("business").value.trim();
 const email=document.getElementById("register-email").value.trim();
 const password=document.getElementById("register-password").value;
 status.textContent=""; 
 if(business.length<2){status.textContent="İşletme adı en az 2 karakter olmalıdır.";return;}
 if(!email.includes("@")){status.textContent="Geçerli bir e-posta girin.";return;}
 if(password.length<12){status.textContent="Şifre en az 12 karakter olmalıdır.";return;}
 try {
  const result=await api("/api/register",{method:"POST",body:JSON.stringify({business,email,password})});
  status.textContent="İşletme oluşturuldu. Şimdi aşağıdaki Giriş bölümünden oturum açabilirsiniz.";
  document.getElementById("email").value=email;
  document.getElementById("password").value=password;
  report(result);
 } catch(e) {
  status.textContent=e.message.includes("zaten kayıtlı")?"Bu e-posta zaten kayıtlı. Yeniden işletme oluşturmak yerine aşağıdan giriş yapın.":e.message;
  report(status.textContent);
 }
};
document.getElementById("login").onclick = async () => {
 const status=document.getElementById("login-status");
 status.textContent="";
 try {
  const r=await api("/api/login",{method:"POST",body:JSON.stringify({
   email:document.getElementById("email").value,password:document.getElementById("password").value})});
  token=r.token;
  sessionStorage.setItem("socialplan_pg_test_token", token);
  const profile=await api("/api/me");
  status.textContent="Giriş başarılı: "+profile.business+" ("+profile.email+")";
  updateAuthView(true);
  report(status.textContent);
  await loadPosts();
 } catch(e){status.textContent=e.message;report(e.message);}
};
document.getElementById("logout").onclick = async () => {
 try {if(token) await api("/api/logout",{method:"POST"});} catch(e){report(e.message);}
 token=""; sessionStorage.removeItem("socialplan_pg_test_token"); mediaKey=""; cachedPosts=[]; document.getElementById("edit-section").hidden=true; updateDashboard([]); updateCalendar([]); updateAuthView(false); document.getElementById("post-list").textContent="İçerikleri görüntülemek için giriş yapın."; report("Oturum kapatıldı.");
};
document.getElementById("upload").onclick = async () => {
 try {const file=document.getElementById("file").files[0]; if(!file) throw Error("Dosya seçin");
 const form=new FormData();form.append("file",file);
 const r=await api("/api/media",{method:"POST",body:form});mediaKey=r.media_key;
 document.getElementById("media").textContent="Dosya yüklendi ("+r.size+" bayt)";
 report("Medya anahtarı oluşturuldu.");}catch(e){report(e.message);}
};
document.getElementById("create").onclick = async () => {
 try {const mediaUrl=document.getElementById("mediaurl").value.trim();
 if(!mediaKey && !mediaUrl) throw Error("HTTPS medya bağlantısı girin veya dosya yükleyin");
 if(mediaUrl && !mediaUrl.startsWith("https://")) throw Error("Medya bağlantısı HTTPS olmalıdır");
 if(mediaKey && mediaUrl) throw Error("Tek bir medya kaynağı seçin");
 const local=document.getElementById("scheduled").value;
 if(!local) throw Error("Yayın tarihi seçin");
 const r=await api("/api/posts",{method:"POST",body:JSON.stringify({
 caption:document.getElementById("caption").value,
 ...(mediaKey ? {media_key:mediaKey} : {media_url:mediaUrl}),
 scheduled_at:new Date(local).toISOString()})});report(r);await loadPosts();}catch(e){report(e.message);}
};
function openDraftEditor(post){
 if(post.status!=="draft"){report("Yalnızca taslaklar düzenlenebilir.");return;}
 if(!post.media_url || !post.media_url.startsWith("https://")){report("Bu içerik HTTPS medya bağlantısıyla düzenlenemiyor.");return;}
 document.getElementById("edit-id").value=post.id;
 document.getElementById("edit-caption").value=post.caption;
 document.getElementById("edit-media").value=post.media_url;
 const date=new Date(post.scheduled_at);
 if(Number.isNaN(date.getTime())){report("İçeriğin yayın tarihi geçersiz.");return;}
 const local=new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16);
 document.getElementById("edit-date").value=local;
 document.getElementById("edit-status").textContent="";
 document.getElementById("edit-section").hidden=false;
 showPanel("posts");
 document.getElementById("edit-section").scrollIntoView({behavior:"smooth",block:"start"});
}
document.getElementById("close-edit").onclick=()=>{document.getElementById("edit-section").hidden=true;};
document.getElementById("save-edit").onclick=async()=>{
 const id=Number(document.getElementById("edit-id").value);
 const caption=document.getElementById("edit-caption").value.trim();
 const media_url=document.getElementById("edit-media").value.trim();
 const local=document.getElementById("edit-date").value;
 const status=document.getElementById("edit-status");
 if(!caption || !media_url.startsWith("https://") || !local){status.textContent="Açıklama, HTTPS bağlantısı ve yayın tarihi gereklidir.";return;}
 const date=new Date(local);
 if(Number.isNaN(date.getTime()) || date<=new Date()){status.textContent="Gelecekte bir yayın tarihi seçin.";return;}
 try{
  await api("/api/posts/"+id,{method:"PUT",body:JSON.stringify({caption,media_url,scheduled_at:date.toISOString()})});
  status.textContent="Taslak güncellendi.";
  await loadPosts();
  document.getElementById("edit-section").hidden=true;
 }catch(e){status.textContent=e.message;report(e.message);}
};
function updateOverviewLists(posts){
 const upcoming=document.getElementById("upcoming-posts"),recent=document.getElementById("recent-posts");
 const now=Date.now();
 const valid=posts.filter(p=>Number.isFinite(new Date(p.scheduled_at).getTime()));
 const next=valid.filter(p=>p.status!=="cancelled"&&new Date(p.scheduled_at).getTime()>=now)
  .sort((a,b)=>new Date(a.scheduled_at)-new Date(b.scheduled_at)).slice(0,5);
 const latest=[...posts].sort((a,b)=>b.id-a.id).slice(0,5);
 const labels={draft:"Taslak",approved:"Onaylandı",cancelled:"İptal edildi"};
 for(const [root,items,empty] of [[upcoming,next,"Yaklaşan paylaşım bulunmuyor."],[recent,latest,"Henüz içerik kaydı bulunmuyor."]]){
  root.replaceChildren();
  if(!items.length){const p=document.createElement("p");p.className="overview-empty";p.textContent=empty;root.append(p);}
  for(const post of items){
   const row=document.createElement("div");row.className="overview-item";
   const info=document.createElement("span");info.textContent="#"+post.id+" · "+(post.caption.length>48?post.caption.slice(0,48)+"…":post.caption);
   const sub=document.createElement("small");const d=new Date(post.scheduled_at);
   sub.textContent=(Number.isNaN(d.getTime())?post.scheduled_at:d.toLocaleString("tr-TR"))+" · "+(labels[post.status]||post.status);
   info.append(sub);
   const btn=document.createElement("button");btn.type="button";btn.textContent="Görüntüle";
   btn.onclick=()=>{showPanel("posts");document.getElementById("status-filter").value="all";renderPosts(cachedPosts);document.getElementById("post-id").value=post.id;document.getElementById("post-list").scrollIntoView({behavior:"smooth"});};
   row.append(info,btn);root.append(row);
  }
 }
}
function updateDashboard(posts) {
 const counts={all:posts.length,draft:0,approved:0,cancelled:0};
 for(const p of posts){if(Object.hasOwn(counts,p.status))counts[p.status]++;}
 const root=document.getElementById("dashboard");root.replaceChildren();
 for(const [label,key] of [["Toplam içerik","all"],["Taslak","draft"],["Onaylanan","approved"],["İptal edilen","cancelled"]]){
  const card=document.createElement("div");card.className="metric";
  const name=document.createElement("span");name.textContent=label;
  const number=document.createElement("strong");number.textContent=counts[key];
  card.append(name,number);root.append(card);
 }
 updateOverviewLists(posts);
}
function localDateKey(date){return [date.getFullYear(),String(date.getMonth()+1).padStart(2,"0"),String(date.getDate()).padStart(2,"0")].join("-");}
function showCalendarDetail(post){
 const root=document.getElementById("calendar-detail");root.replaceChildren();root.hidden=false;
 const title=document.createElement("h3");title.textContent="İçerik #"+post.id;
 const caption=document.createElement("p");caption.textContent=post.caption;
 const when=document.createElement("p");when.textContent="Planlanan: "+new Date(post.scheduled_at).toLocaleString("tr-TR");
 const labels={draft:"Taslak",approved:"Onaylandı",cancelled:"İptal edildi"};
 const status=document.createElement("p");status.textContent="Durum: "+(labels[post.status]||post.status);
 root.append(title,caption,when,status);
 if(post.status==="draft" && post.media_url?.startsWith("https://")){
  const edit=document.createElement("button");edit.textContent="Taslağı düzenle";edit.onclick=()=>openDraftEditor(post);root.append(edit);
 }
 const go=document.createElement("button");go.textContent="İçerik yönetiminde göster";
 go.onclick=()=>{document.getElementById("status-filter").value="all";renderPosts(cachedPosts);document.getElementById("post-id").value=post.id;showPanel("posts");document.getElementById("post-list").scrollIntoView({behavior:"smooth"});};
 root.append(go);
}
function updateCalendar(posts){
 const mode=document.getElementById("calendar-view").value;
 const month=document.getElementById("calendar-month").value;
 const weekValue=document.getElementById("calendar-week-date").value;
 document.getElementById("calendar-month").hidden=mode==="week";
 document.querySelector('label[for="calendar-month"]').hidden=mode==="week";
 document.getElementById("calendar-week-date").hidden=mode!=="week";
 document.getElementById("calendar-week-label").hidden=mode!=="week";
 const grid=document.getElementById("calendar-grid");grid.replaceChildren();grid.classList.toggle("weekly",mode==="week");document.getElementById("calendar-detail").hidden=true;
 const root=document.getElementById("calendar-list");root.replaceChildren();
 let start,end;
 if(mode==="week"){
  const base=new Date((weekValue||localDateKey(new Date()))+"T12:00:00");
  start=new Date(base.getFullYear(),base.getMonth(),base.getDate()-(base.getDay()+6)%7);
  end=new Date(start.getFullYear(),start.getMonth(),start.getDate()+7);
 }else{
  const parts=(month||localDateKey(new Date()).slice(0,7)).split("-").map(Number);
  const first=new Date(parts[0],parts[1]-1,1);
  start=new Date(first.getFullYear(),first.getMonth(),1-(first.getDay()+6)%7);
  const next=new Date(parts[0],parts[1],1);
  end=new Date(next.getFullYear(),next.getMonth(),next.getDate()+(7-(next.getDay()+6)%7)%7);
 }
 const names={draft:"Taslak",approved:"Onaylandı",cancelled:"İptal edildi"};
 const grouped=new Map();
 const rows=posts.filter(p=>{const d=new Date(p.scheduled_at);return !Number.isNaN(d.getTime())&&d>=start&&d<end;})
 .sort((a,b)=>new Date(a.scheduled_at)-new Date(b.scheduled_at));
 for(const p of rows){const key=localDateKey(new Date(p.scheduled_at));if(!grouped.has(key))grouped.set(key,[]);grouped.get(key).push(p);}
 for(const name of ["Pzt","Sal","Çar","Per","Cum","Cmt","Paz"]){const h=document.createElement("div");h.className="calendar-weekday";h.textContent=name;grid.append(h);}
 for(let day=new Date(start);day<end;day.setDate(day.getDate()+1)){
  const cell=document.createElement("div");cell.className="calendar-day";
  if(mode==="month"&&localDateKey(day).slice(0,7)!==month)cell.classList.add("other");
  const title=document.createElement("strong");title.textContent=mode==="week"?day.toLocaleDateString("tr-TR",{day:"numeric",month:"short",weekday:"short"}):String(day.getDate());cell.append(title);
  for(const p of grouped.get(localDateKey(day))||[]){
   const chip=document.createElement("button");chip.type="button";chip.className="calendar-chip "+p.status;
   chip.textContent=new Date(p.scheduled_at).toLocaleTimeString("tr-TR",{hour:"2-digit",minute:"2-digit"})+" · #"+p.id+" · "+(p.caption.length>34?p.caption.slice(0,34)+"…":p.caption);
   chip.title=(names[p.status]||p.status)+" — "+p.caption;
   chip.onclick=()=>showCalendarDetail(p);
   cell.append(chip);
  }
  grid.append(cell);
 }
 if(!rows.length){const li=document.createElement("li");li.textContent="Seçilen dönemde planlanmış içerik yok.";root.append(li);}
 for(const p of rows){const li=document.createElement("li");const t=document.createElement("time");t.textContent=new Date(p.scheduled_at).toLocaleString("tr-TR");const d=document.createElement("small");d.textContent="#"+p.id+" · "+(names[p.status]||p.status)+" · "+p.caption;li.append(t,d);root.append(li);}
}
document.getElementById("calendar-month").value=localDateKey(new Date()).slice(0,7);
document.getElementById("calendar-week-date").value=localDateKey(new Date());
document.getElementById("calendar-month").onchange=()=>updateCalendar(cachedPosts);
document.getElementById("calendar-week-date").onchange=()=>updateCalendar(cachedPosts);
document.getElementById("calendar-view").onchange=()=>updateCalendar(cachedPosts);

function renderPosts(posts) {
 cachedPosts=posts;
 updateDashboard(posts);
 updateCalendar(posts);
 const filtered=posts.filter(p=>document.getElementById("status-filter").value==="all" || p.status===document.getElementById("status-filter").value);
 document.getElementById("post-count").textContent=filtered.length+" / "+posts.length+" içerik gösteriliyor";
 const list=document.getElementById("post-list");
 list.replaceChildren();
 if(!filtered.length){list.textContent=posts.length?"Bu durumda içerik bulunmuyor.":"Henüz kayıtlı içerik bulunmuyor.";return;}
 const statusText={draft:"Taslak",approved:"Onaylandı",cancelled:"İptal edildi"};
 for(const post of filtered) {
  const card=document.createElement("article");card.className="post-card";
  const heading=document.createElement("h3");heading.textContent="İçerik #"+post.id;card.append(heading);
  const caption=document.createElement("p");caption.textContent=post.caption;card.append(caption);
  const date=document.createElement("p");date.className="meta";
  const parsed=new Date(post.scheduled_at);
  date.textContent="Planlanan: "+(Number.isNaN(parsed.getTime())?post.scheduled_at:parsed.toLocaleString("tr-TR"));card.append(date);
  const state=document.createElement("p");state.className="state";state.textContent="Durum: "+(statusText[post.status]||post.status);card.append(state);
  const media=document.createElement("p");media.className="meta";
  media.textContent="Medya: "+(post.media_url||"Belirtilmemiş");card.append(media);
  if(post.media_url && /^https:\/\//i.test(post.media_url) && /\.(png|jpe?g|webp)(\?|$)/i.test(post.media_url)){
   const img=document.createElement("img");img.src=post.media_url;img.alt="İçerik görseli";img.loading="lazy";img.referrerPolicy="no-referrer";card.append(img);
  }
  const actions=document.createElement("div");actions.className="post-actions";
  for(const [action,label,enabled] of [["approve","Onayla",post.status==="draft"],["cancel","İptal et",post.status!=="cancelled"]]){
   const btn=document.createElement("button");btn.textContent=label;btn.disabled=!enabled;
   btn.onclick=()=>{document.getElementById("post-id").value=post.id;changeStatus(action);};
   actions.append(btn);
  }
  if(post.status==="draft" && post.media_url && post.media_url.startsWith("https://")){
   const edit=document.createElement("button");edit.textContent="Düzenle";edit.onclick=()=>openDraftEditor(post);actions.append(edit);
  }
  card.append(actions);list.append(card);
 }
}
document.getElementById("status-filter").onchange=()=>renderPosts(cachedPosts);
function updateAuthView(loggedIn){
 document.getElementById("app-nav").hidden=!loggedIn;
 if(!loggedIn)showPanel("account");else if(activePanel==="account")showPanel("overview");
 document.getElementById("registration-section").hidden=loggedIn;
 document.getElementById("login-section").querySelector("#email").disabled=loggedIn;
 document.getElementById("login-section").querySelector("#password").disabled=loggedIn;
 document.getElementById("login").disabled=loggedIn;
}
async function loadPosts(){
 if(!token){document.getElementById("post-list").textContent="Önce giriş yapın.";return;}
 const posts=await api("/api/posts");renderPosts(posts);report(posts);
}
async function changeStatus(action) {
 const id=Number(document.getElementById("post-id").value);
 if(!Number.isSafeInteger(id)||id<=0){report("Geçerli bir içerik numarası (ID) girin.");return;}
 if(!token){report("Önce giriş yapın.");return;}
 const question=action==="approve"?"Bu taslağı onaylamak istiyor musunuz?":"Bu içeriği iptal etmek istiyor musunuz?";
 if(!confirm(question)) return;
 try {
  const result=await api("/api/posts/"+id+"/"+action,{method:"POST"});
  const all=await api("/api/posts");
  report({message:action==="approve"?"İçerik onaylandı. Instagram paylaşımı yapılmadı.":"İçerik iptal edildi.",result,posts:all});
  renderPosts(all);
 } catch(e){report(e.message);}
}
document.getElementById("approve").onclick=()=>changeStatus("approve");
document.getElementById("cancel").onclick=()=>changeStatus("cancel");
async function restoreSession() {
 if (!token){updateAuthView(false);return;}
 try {
  const profile=await api("/api/me");
  document.getElementById("login-status").textContent="Oturum devam ediyor: "+profile.business+" ("+profile.email+")";
  updateAuthView(true);
  report("Oturum geri yüklendi. İçeriklerinizi listeleyebilirsiniz.");
  await loadPosts();
 } catch(e) {
  token=""; sessionStorage.removeItem("socialplan_pg_test_token"); updateAuthView(false);
  document.getElementById("login-status").textContent="Oturum süresi doldu. Yeniden giriş yapın.";
 }
}
restoreSession();
document.getElementById("refresh").onclick = async () => {
 try{await loadPosts();}catch(e){report(e.message);}
};
</script></body></html>"""


@app.get("/health")
@app.get("/api/health")
def health():
    """Verify that this isolated API can actually query Neon PostgreSQL."""
    try:
        with store.connection() as db:
            required_tables = (
                "businesses", "users", "sessions", "login_attempts", "posts",
                "audit_events", "publish_jobs", "instagram_oauth_states",
                "instagram_connections",
            )
            found = db.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_name = ANY(%s)",
                (list(required_tables),),
            ).fetchall()
            present = {row["table_name"] for row in found}
            if set(required_tables) != present:
                raise HTTPException(status_code=503, detail="PostgreSQL tablo semasi eksik")
    except (psycopg.Error, OSError, RuntimeError):
        raise HTTPException(status_code=503, detail="PostgreSQL baglantisi kullanilamiyor")
    return {"status": "ok", "database_engine": "postgresql", "database": "connected", "instagram_publishing": "not_configured"}
