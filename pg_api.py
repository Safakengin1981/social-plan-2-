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
from fastapi.responses import Response, HTMLResponse
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
