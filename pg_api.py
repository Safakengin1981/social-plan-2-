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
input,textarea,button{font:inherit;padding:10px;margin:6px 0;width:100%;box-sizing:border-box}
button{background:#154e69;color:white;border:0;border-radius:6px;cursor:pointer}
section{border:1px solid #ddd;border-radius:8px;padding:16px;margin:16px 0}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f5f5;padding:12px}
</style></head><body>
<h1>SocialPlan 5.8 — PostgreSQL test</h1>
<p>Bu ekran yalnızca izole geliştirme API'si içindir. Instagram'a otomatik yayın yapmaz.</p>
<section><h2>Yeni test işletmesi oluştur</h2><input id="business" placeholder="İşletme adı"><button id="register">İşletme oluştur</button><p>Şifre en az 12 karakter olmalıdır. Aynı e-posta ile tekrar kayıt yapılamaz.</p></section>
<section><h2>Giriş</h2><input id="email" type="email" placeholder="E-posta">
<input id="password" type="password" placeholder="Şifre">
<button id="login">Giriş yap</button><button id="logout">Çıkış yap</button></section>
<section><h2>Medya kaynağı (R2 gerektirmez)</h2>
<input id="mediaurl" type="url" placeholder="Herkese açık HTTPS görsel/video bağlantısı">
<p>Neon kayıt testi için HTTPS bağlantısı kullanın. Dosya yükleme yalnızca ayrı depolama yapılandırıldığında çalışır.</p>
<h3>İsteğe bağlı dosya yükleme</h3>
<input id="file" type="file" accept="image/jpeg,image/png,image/webp,video/mp4">
<button id="upload">Dosyayı yükle</button><p id="media"></p></section>
<section><h2>İçerik taslağı</h2><textarea id="caption" placeholder="Açıklama"></textarea>
<input id="scheduled" type="datetime-local"><button id="create">Taslak oluştur</button>
<button id="refresh">Taslakları listele</button></section>
<pre id="output" role="status"></pre>
<script>
let token = "", mediaKey = "";
const out = document.getElementById("output");
async function api(path, opts = {}) {
  const headers = {...(opts.headers || {})};
  if (token) headers.Authorization = "Bearer " + token;
  if (opts.body && !(opts.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {...opts, headers});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "İstek başarısız: " + response.status);
  return data;
}
function report(p) { out.textContent = typeof p === "string" ? p : JSON.stringify(p,null,2); }
document.getElementById("register").onclick = async () => {
 try { const r=await api("/api/register",{method:"POST",body:JSON.stringify({
 business:document.getElementById("business").value,
 email:document.getElementById("email").value,
 password:document.getElementById("password").value})}); report(r); }
 catch(e){report(e.message);}
};
document.getElementById("login").onclick = async () => {
 try { const r=await api("/api/login",{method:"POST",body:JSON.stringify({
 email:document.getElementById("email").value,password:document.getElementById("password").value})});
 token=r.token; report("Giriş başarılı. Rol: "+r.role); } catch(e){report(e.message);}
};
document.getElementById("logout").onclick = async () => {
 try {if(token) await api("/api/logout",{method:"POST"});} catch(e){report(e.message);}
 token=""; mediaKey=""; report("Oturum kapatıldı.");
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
 scheduled_at:new Date(local).toISOString()})});report(r);}catch(e){report(e.message);}
};
document.getElementById("refresh").onclick = async () => {
 try{report(await api("/api/posts"));}catch(e){report(e.message);}
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
