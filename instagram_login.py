"""Instagram Login OAuth: explicit admin initiation, one-time state, encrypted token storage.
No publishing operations. Never accept Instagram passwords.
"""
import os, time, secrets, json, urllib.parse, urllib.request
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from cryptography.fernet import Fernet, InvalidToken

AUTH_URL="https://www.instagram.com/oauth/authorize"
TOKEN_URL="https://api.instagram.com/oauth/access_token"
GRAPH_URL="https://graph.instagram.com"
SCOPES="instagram_business_basic,instagram_business_content_publish"

def attach_instagram_login(auth,conn):
    router=APIRouter(prefix="/api")
    with conn() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS instagram_oauth_states(
          state_hash TEXT PRIMARY KEY, business_id INTEGER NOT NULL,
          user_id INTEGER NOT NULL, expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS instagram_connections(
          business_id INTEGER PRIMARY KEY, instagram_id TEXT NOT NULL,
          username TEXT NOT NULL, encrypted_token TEXT NOT NULL,
          expires INTEGER NOT NULL, verified_at INTEGER NOT NULL);
        """)
    import hashlib
    def config():
        names=("INSTAGRAM_CLIENT_ID","INSTAGRAM_CLIENT_SECRET","INSTAGRAM_REDIRECT_URI","INSTAGRAM_TOKEN_ENCRYPTION_KEY")
        missing=[n for n in names if not os.getenv(n)]
        enabled=os.getenv("INSTAGRAM_OAUTH_ENABLED")=="1"
        return enabled,missing
    def crypt():
        try:return Fernet(os.environ["INSTAGRAM_TOKEN_ENCRYPTION_KEY"].encode())
        except (KeyError,ValueError) as exc:raise HTTPException(503,"Instagram şifreleme anahtarı geçersiz") from exc
    def request_json(url, data=None):
        request=urllib.request.Request(url,data=data,headers={"User-Agent":"SocialPlan/5.8","Accept":"application/json"})
        with urllib.request.urlopen(request,timeout=12) as response:
            return json.load(response)
    @router.get("/admin/instagram-login/status")
    def status(u=Depends(auth)):
        if u["role"]!="admin":raise HTTPException(403,"Yönetici yetkisi gerekli")
        enabled,missing=config()
        with conn() as db:
            row=db.execute("SELECT username,instagram_id,expires,verified_at FROM instagram_connections WHERE business_id=?",(u["business_id"],)).fetchone()
        return {"configured":enabled and not missing,"missing":missing,
                "connected":bool(row and row["expires"]>int(time.time())),
                "account":dict(row) if row else None,"auto_publish":False,
                "message":"Instagram bağlantısı için resmi giriş kullanılacak. Otomatik yayınlama henüz kapalı."}
    @router.post("/admin/instagram-login/start")
    def start(u=Depends(auth)):
        if u["role"]!="admin":raise HTTPException(403,"Yönetici yetkisi gerekli")
        enabled,missing=config()
        if not enabled or missing:raise HTTPException(503,"Instagram bağlantısı henüz yapılandırılmadı.")
        state=secrets.token_urlsafe(36)
        with conn() as db:
            db.execute("DELETE FROM instagram_oauth_states WHERE expires<?",(int(time.time()),))
            db.execute("INSERT INTO instagram_oauth_states VALUES(?,?,?,?)",
                       (hashlib.sha256(state.encode()).hexdigest(),u["business_id"],u["id"],int(time.time())+600))
        query=urllib.parse.urlencode({"client_id":os.environ["INSTAGRAM_CLIENT_ID"],
           "redirect_uri":os.environ["INSTAGRAM_REDIRECT_URI"],"response_type":"code",
           "scope":SCOPES,"state":state})
        return {"authorization_url":AUTH_URL+"?"+query}
    @router.get("/instagram-login/callback",response_class=HTMLResponse)
    def callback(request:Request,code:str|None=None,state:str|None=None,error:str|None=None):
        if error:return HTMLResponse("<h3>Instagram bağlantısı iptal edildi.</h3>",status_code=400)
        if not code or not state:raise HTTPException(400,"Eksik yetkilendirme bilgisi")
        enabled,missing=config()
        if not enabled or missing:raise HTTPException(503,"Instagram bağlantısı yapılandırılmadı")
        now=int(time.time())
        with conn() as db:
            db.execute("BEGIN IMMEDIATE")
            key=hashlib.sha256(state.encode()).hexdigest()
            row=db.execute("SELECT business_id,expires FROM instagram_oauth_states WHERE state_hash=?",(key,)).fetchone()
            db.execute("DELETE FROM instagram_oauth_states WHERE state_hash=?",(key,))
            if not row or row["expires"]<now:raise HTTPException(400,"Yetkilendirme süresi doldu veya kullanıldı")
            business_id=row["business_id"]
        form=urllib.parse.urlencode({"client_id":os.environ["INSTAGRAM_CLIENT_ID"],
          "client_secret":os.environ["INSTAGRAM_CLIENT_SECRET"],"grant_type":"authorization_code",
          "redirect_uri":os.environ["INSTAGRAM_REDIRECT_URI"],"code":code}).encode()
        try:
            short=request_json(TOKEN_URL,form)
            token=short["access_token"]
            exchange=urllib.parse.urlencode({"grant_type":"ig_exchange_token",
              "client_secret":os.environ["INSTAGRAM_CLIENT_SECRET"],"access_token":token})
            long=request_json(GRAPH_URL+"/access_token?"+exchange)
            token=long["access_token"]
            expires=now+int(long.get("expires_in",0))
            if expires<=now:raise ValueError("Token expiry unavailable")
            info=request_json(GRAPH_URL+"/me?"+urllib.parse.urlencode({"fields":"id,username","access_token":token}))
            ig_id=str(info["id"]);username=str(info["username"])
            if not ig_id or not username:raise ValueError("Account identity unavailable")
        except Exception:
            # Never include provider response, authorization code, or token in errors.
            raise HTTPException(502,"Instagram yetkilendirmesi doğrulanamadı. Ayarları ve izinleri kontrol edin.")
        with conn() as db:
            db.execute("""INSERT INTO instagram_connections(business_id,instagram_id,username,encrypted_token,expires,verified_at)
              VALUES(?,?,?,?,?,?) ON CONFLICT(business_id) DO UPDATE SET
              instagram_id=excluded.instagram_id,username=excluded.username,
              encrypted_token=excluded.encrypted_token,expires=excluded.expires,verified_at=excluded.verified_at""",
              (business_id,ig_id,username,crypt().encrypt(token.encode()).decode(),expires,now))
        return HTMLResponse("<!doctype html><html lang='tr'><meta charset='utf-8'><title>Bağlantı başarılı</title><h3>Instagram hesabı bağlandı.</h3><p>SocialPlan'a dönüp bağlantı durumunu yenileyin.</p></html>")
    return router
