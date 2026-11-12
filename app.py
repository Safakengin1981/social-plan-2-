import os, sqlite3, hashlib, hmac, secrets, time, threading, logging
from datetime import datetime, timezone
from contextlib import contextmanager, asynccontextmanager
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE=os.path.dirname(__file__)
DB=os.getenv('SOCIALPLAN_DB',os.path.join(BASE,'socialplan.sqlite3'))
# Fail closed: a DATABASE_URL must never be silently ignored while SQLite is active.
# PostgreSQL cutover requires adapting every endpoint, scheduler, media and OAuth path.
if os.getenv('DATABASE_URL'):
 raise RuntimeError('DATABASE_URL is set, but PostgreSQL cutover is not enabled. Refusing to write to SQLite silently.')
logger=logging.getLogger('socialplan.scheduler')
stop_scheduler=threading.Event()
def sync_queue():
    """Refresh all tenants safely; never publish without Meta integration."""
    now=int(time.time())
    with conn() as c:
        c.execute("""INSERT INTO publish_jobs(post_id,business_id,state,reason,updated_at)
          SELECT id,business_id,
          CASE WHEN status='cancelled' THEN 'cancelled'
               WHEN CAST(strftime('%s',replace(replace(scheduled_at,'T',' '),'Z','')) AS INTEGER)<=? THEN 'blocked' ELSE 'queued' END,
          CASE WHEN status='cancelled' THEN 'İçerik iptal edildi.'
               WHEN CAST(strftime('%s',replace(replace(scheduled_at,'T',' '),'Z','')) AS INTEGER)<=? THEN 'Meta bağlantısı bulunmadığından yayın engellendi.'
               ELSE 'Planlanan saat bekleniyor.' END,?
          FROM posts WHERE status IN ('approved','cancelled')
          ON CONFLICT(post_id) DO UPDATE SET state=excluded.state,
          reason=excluded.reason,updated_at=excluded.updated_at""",(now,now,now))
def scheduler_loop():
    while not stop_scheduler.is_set():
        try:sync_queue()
        except Exception:logger.exception('Yayın kuyruğu güncellenemedi')
        stop_scheduler.wait(60)
@asynccontextmanager
async def lifespan(app):
    stop_scheduler.clear()
    worker=threading.Thread(target=scheduler_loop,daemon=True,name='socialplan-queue')
    worker.start()
    yield
    stop_scheduler.set()
    worker.join(timeout=3)
app=FastAPI(title='SocialPlan API',version='1.6',lifespan=lifespan)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
 async def dispatch(self,request:Request,call_next):
  response=await call_next(request)
  response.headers['X-Content-Type-Options']='nosniff'
  response.headers['X-Frame-Options']='DENY'
  response.headers['Referrer-Policy']='strict-origin-when-cross-origin'
  if request.url.path.startswith('/api/'):
   response.headers['Cache-Control']='no-store'
  return response
app.add_middleware(SecurityHeadersMiddleware)
app.mount('/static',StaticFiles(directory=os.path.join(BASE,'static')),name='static')
@contextmanager
def conn():
 c=sqlite3.connect(DB);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON')
 try: yield c;c.commit()
 finally:c.close()
def init():
 with conn() as c:
  c.executescript('''CREATE TABLE IF NOT EXISTS businesses(id INTEGER PRIMARY KEY,name TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,business_id INTEGER NOT NULL REFERENCES businesses(id),email TEXT NOT NULL UNIQUE,salt TEXT NOT NULL,passhash TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('admin','editor')));
 CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),expires INTEGER NOT NULL);
 CREATE TABLE IF NOT EXISTS login_attempts(email_hash TEXT PRIMARY KEY,failures INTEGER NOT NULL,first_attempt INTEGER NOT NULL,locked_until INTEGER NOT NULL);
 CREATE TABLE IF NOT EXISTS posts(id INTEGER PRIMARY KEY,business_id INTEGER NOT NULL REFERENCES businesses(id),author_id INTEGER NOT NULL REFERENCES users(id),caption TEXT NOT NULL,media_url TEXT NOT NULL,scheduled_at TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('draft','approved','cancelled')),created_at INTEGER NOT NULL);''')
init()
def pw_hash(password,salt):return hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),310000).hex()
class Register(BaseModel):
 business:str=Field(min_length=2,max_length=120)
 email:str=Field(min_length=5,max_length=254)
 password:str=Field(min_length=12,max_length=128)
class Login(BaseModel):
 email:str;password:str
class PostIn(BaseModel):
 caption:str=Field(min_length=1,max_length=2200)
 media_url:str=Field(min_length=8,max_length=2000)
 scheduled_at:str=Field(min_length=16,max_length=40)
@app.get('/manifest.webmanifest')
def manifest():return FileResponse(os.path.join(BASE,'static','manifest.webmanifest'),media_type='application/manifest+json')
@app.get('/sw.js')
def sw():return FileResponse(os.path.join(BASE,'static','sw.js'),media_type='text/javascript')
@app.get('/')
def home():return FileResponse(os.path.join(BASE,'static','index.html'))
@app.post('/api/register')
def register(x:Register):
 email=x.email.strip().lower();salt=secrets.token_hex(16)
 try:
  with conn() as c:
   cur=c.execute('INSERT INTO businesses(name) VALUES(?)',(x.business.strip(),));bid=cur.lastrowid
   c.execute('INSERT INTO users(business_id,email,salt,passhash,role) VALUES(?,?,?,?,?)',(bid,email,salt,pw_hash(x.password,salt),'admin'))
 except sqlite3.IntegrityError:raise HTTPException(409,'Bu e-posta zaten kayıtlı')
 return {'message':'İşletme oluşturuldu; giriş yapabilirsiniz'}
@app.post('/api/login')
def login(x:Login):
 email=x.email.strip().lower()
 key=hashlib.sha256(email.encode()).hexdigest()
 now=int(time.time())
 with conn() as c:
  c.execute('BEGIN IMMEDIATE')
  attempt=c.execute('SELECT * FROM login_attempts WHERE email_hash=?',(key,)).fetchone()
  if attempt and attempt['locked_until']>now:
   raise HTTPException(429,'Çok fazla başarısız deneme. 15 dakika sonra tekrar deneyin.')
  u=c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
  # Unknown accounts use a real hash operation to reduce timing differences.
  salt=u['salt'] if u else '00'*16
  expected=u['passhash'] if u else '00'*32
  valid=hmac.compare_digest(expected,pw_hash(x.password,salt)) and u is not None
  if not valid:
   failures=(attempt['failures']+1) if attempt and now-attempt['first_attempt']<900 else 1
   first=attempt['first_attempt'] if attempt and now-attempt['first_attempt']<900 else now
   locked=now+900 if failures>=5 else 0
   c.execute('INSERT INTO login_attempts VALUES(?,?,?,?) ON CONFLICT(email_hash) DO UPDATE SET failures=excluded.failures,first_attempt=excluded.first_attempt,locked_until=excluded.locked_until',(key,failures,first,locked))
   # Explicit commit before raising HTTP error.
   c.commit()
   raise HTTPException(401,'Giriş bilgileri hatalı')
  c.execute('DELETE FROM login_attempts WHERE email_hash=?',(key,))
  token=secrets.token_urlsafe(32)
  c.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),u['id'],now+86400))
 return {'token':token,'role':u['role']}
def auth(authorization:str=Header(default='')):
 if not authorization.startswith('Bearer '):raise HTTPException(401,'Giriş gerekli')
 th=hashlib.sha256(authorization[7:].encode()).hexdigest()
 with conn() as c:
  u=c.execute('SELECT users.* FROM sessions JOIN users ON users.id=sessions.user_id WHERE token_hash=? AND expires>?',(th,int(time.time()))).fetchone()
 if not u:raise HTTPException(401,'Oturum geçersiz')
 return dict(u)
@app.get('/api/admin/storage/status')
def storage_status(u=Depends(auth)):
 if u['role']!='admin':raise HTTPException(403,'Yönetici yetkisi gerekli')
 # Do not expose credentials, filesystem paths, or database contents.
 database_external=False
 persistent_disk=DB.startswith('/var/data/')
 return {
  'database_engine':'sqlite',
  'external_database':database_external,
  'persistent_disk_configured':persistent_disk,
  'durable':False, # Mount path alone does not prove persistence.
  'warning':('SQLite points to a persistent-style path; verify the actual mounted disk and backups.'
    if persistent_disk else
    'SQLite is on ephemeral storage. Render restart or redeploy can delete accounts and content.'),
  'media_durable':False,
  'backup_export_available':True,
  'automatic_restore_verified':False
 }
@app.get('/api/health')
def health():
 return {'status':'ok','instagram_publishing':'not_configured','release':'1.6'}

@app.get('/api/me')
def me(u=Depends(auth)):
 with conn() as c:b=c.execute('SELECT name FROM businesses WHERE id=?',(u['business_id'],)).fetchone()
 return {'email':u['email'],'role':u['role'],'business':b['name']}
@app.get('/api/posts')
def posts(u=Depends(auth)):
 with conn() as c:r=c.execute('SELECT id,caption,media_url,scheduled_at,status FROM posts WHERE business_id=? ORDER BY scheduled_at',(u['business_id'],)).fetchall()
 return [dict(x) for x in r]
@app.post('/api/posts')
def create(x:PostIn,u=Depends(auth)):
 try:
  when=datetime.fromisoformat(x.scheduled_at.replace('Z','+00:00'))
  if when.tzinfo is None or when.astimezone(timezone.utc)<=datetime.now(timezone.utc):raise ValueError()
 except ValueError:raise HTTPException(422,'Gelecekte, saat dilimi belirtilmiş geçerli tarih seçin')
 if not (x.media_url.startswith('https://') or (x.media_url.startswith('/api/media/') and '..' not in x.media_url)):raise HTTPException(422,'Geçersiz medya bağlantısı')
 with conn() as c:
  cur=c.execute('INSERT INTO posts(business_id,author_id,caption,media_url,scheduled_at,status,created_at) VALUES(?,?,?,?,?,?,?)',(u['business_id'],u['id'],x.caption,x.media_url,x.scheduled_at,'draft',int(time.time())))
  post_id=cur.lastrowid
  c.execute('INSERT INTO audit_events(business_id,user_id,action,post_id,created_at) VALUES(?,?,?,?,?)',
            (u['business_id'],u['id'],'post_created',post_id,int(time.time())))
 return {'id':post_id,'status':'draft'}
@app.post('/api/posts/{post_id}/approve')
def approve(post_id:int,u=Depends(auth)):
 if u['role']!='admin':raise HTTPException(403,'Yönetici yetkisi gerekli')
 with conn() as c:
  cur=c.execute("UPDATE posts SET status='approved' WHERE id=? AND business_id=? AND status='draft'",(post_id,u['business_id']))
  if cur.rowcount==0:raise HTTPException(404,'Onaylanabilir içerik bulunamadı')
  c.execute('INSERT INTO audit_events(business_id,user_id,action,post_id,created_at) VALUES(?,?,?,?,?)',
            (u['business_id'],u['id'],'post_approved',post_id,int(time.time())))
 return {'status':'approved','note':'Onaylandı; otomatik Instagram yayınlama henüz bağlı değil'}
@app.post('/api/logout')
def logout(authorization:str=Header(default=''),u=Depends(auth)):
 with conn() as c:c.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(authorization[7:].encode()).hexdigest(),))
 return {'ok':True}

from extra import attach
app.include_router(attach(auth,conn,pw_hash,BASE))

@app.post('/api/posts/{post_id}/cancel')
def cancel(post_id:int,u=Depends(auth)):
 if u['role']!='admin':raise HTTPException(403,'Yönetici yetkisi gerekli')
 with conn() as c:
  cur=c.execute("UPDATE posts SET status='cancelled' WHERE id=? AND business_id=? AND status IN ('draft','approved')",(post_id,u['business_id']))
  if cur.rowcount==0:raise HTTPException(404,'İptal edilebilir içerik bulunamadı')
  c.execute('INSERT INTO audit_events(business_id,user_id,action,post_id,created_at) VALUES(?,?,?,?,?)',
            (u['business_id'],u['id'],'post_cancelled',post_id,int(time.time())))
 return {'status':'cancelled'}

from instagram_login import attach_instagram_login
app.include_router(attach_instagram_login(auth,conn))

from publishing import attach_publishing
app.include_router(attach_publishing(auth,conn))

@app.get('/api/meta/setup-status')
def meta_setup_status(u=Depends(auth)):
 if u['role']!='admin':raise HTTPException(403,'Yönetici yetkisi gerekli')
 required=('META_APP_ID','META_APP_SECRET','META_REDIRECT_URI')
 return {'configured_fields':[k for k in required if bool(os.getenv(k))],
         'missing_fields':[k for k in required if not os.getenv(k)],
         'oauth_implemented':False,'instagram_connected':False,
         'publishing_enabled':False,
         'next_step':'Meta geliştirici uygulaması, onaylı yönlendirme adresi ve hesap yetkilendirme akışı gerekir.'}

# Tenant-scoped, append-only operational audit history. No passwords or access tokens logged.
from fastapi.responses import StreamingResponse
from io import StringIO
import csv
with conn() as c:
 c.execute("""CREATE TABLE IF NOT EXISTS audit_events (
 id INTEGER PRIMARY KEY, business_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
 action TEXT NOT NULL, post_id INTEGER, created_at INTEGER NOT NULL)""")
 c.execute("CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_events(business_id,id)")
def audit_record(user, action, post_id=None):
 with conn() as c:
  c.execute('INSERT INTO audit_events(business_id,user_id,action,post_id,created_at) VALUES(?,?,?,?,?)',
            (user['business_id'],user['id'],action,post_id,int(time.time())))
@app.get('/api/audit')
def audit_events(u=Depends(auth)):
 if u['role']!='admin':raise HTTPException(403,'Yönetici yetkisi gerekli')
 with conn() as c:
  rows=c.execute("""SELECT a.id,a.action,a.post_id,a.created_at,users.email AS actor
    FROM audit_events a JOIN users ON users.id=a.user_id
    WHERE a.business_id=? ORDER BY a.id DESC LIMIT 200""",(u['business_id'],)).fetchall()
 return [dict(r) for r in rows]
@app.get('/api/posts/export.csv')
def export_posts(u=Depends(auth)):
 if u['role']!='admin':raise HTTPException(403,'Yönetici yetkisi gerekli')
 with conn() as c:
  rows=c.execute('SELECT id,caption,media_url,scheduled_at,status FROM posts WHERE business_id=? ORDER BY id',
                 (u['business_id'],)).fetchall()
 out=StringIO();out.write('\ufeff');w=csv.writer(out)
 w.writerow(['id','caption','media_url','scheduled_at','status'])
 for r in rows:
  values=[r[k] for k in ('id','caption','media_url','scheduled_at','status')]
  # Prevent spreadsheet formula execution when exported CSV is opened.
  values=["'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v for v in values]
  w.writerow(values)
 return StreamingResponse(iter([out.getvalue()]),media_type='text/csv; charset=utf-8',
  headers={'Content-Disposition':'attachment; filename="socialplan_posts.csv"','Cache-Control':'no-store'})

@app.get('/api/admin/export-content.zip')
def export_content_archive(u=Depends(auth)):
 """Tenant-scoped content export; intentionally excludes credentials and OAuth tokens."""
 if u['role']!='admin':raise HTTPException(403,'Yönetici yetkisi gerekli')
 import io, json, zipfile
 from pathlib import Path
 from fastapi.responses import Response
 from datetime import datetime, timezone
 with conn() as c:
  posts_rows=[dict(x) for x in c.execute(
   'SELECT id,caption,media_url,scheduled_at,status,created_at FROM posts WHERE business_id=? ORDER BY id',
   (u['business_id'],)).fetchall()]
  audit_rows=[dict(x) for x in c.execute(
   'SELECT action,post_id,created_at FROM audit_events WHERE business_id=? ORDER BY id',
   (u['business_id'],)).fetchall()]
  business=c.execute('SELECT name FROM businesses WHERE id=?',(u['business_id'],)).fetchone()
 media_dir=Path(os.getenv('SOCIALPLAN_UPLOAD_DIR',os.path.join(BASE,'uploads')))/str(u['business_id'])
 limit=100*1024*1024
 total=0
 buffer=io.BytesIO()
 with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=5) as archive:
  metadata={'format':'socialplan-content-export-v1','exported_at':datetime.now(timezone.utc).isoformat(),
   'business':business['name'] if business else '', 'posts':posts_rows,'audit_events':audit_rows,
   'warning':'Content export only. Does not contain user accounts, sessions, Instagram tokens or a verified restoration procedure.'}
  missing_media=[]
  for post in posts_rows:
   url=post['media_url']
   if not url.startswith('/api/media/'):continue
   name=url.removeprefix('/api/media/')
   if not name or name in ('.','..') or '/' in name or '\\' in name:continue
   path=media_dir/name
   if not path.is_file() or path.is_symlink():
    missing_media.append(name)
    continue
   size=path.stat().st_size
   if size>25*1024*1024 or total+size>limit:
    raise HTTPException(413,'Medya arşivi 100 MB sınırını aşıyor. Önce medya dosyalarını azaltın.')
   total+=size
   archive.write(path,arcname='media/'+name)
  metadata['missing_media']=missing_media
  metadata['media_complete']=not missing_media
  archive.writestr('content.json',json.dumps(metadata,ensure_ascii=False,indent=2))
 return Response(content=buffer.getvalue(),media_type='application/zip',
  headers={'Content-Disposition':'attachment; filename="socialplan-content-export.zip"',
   'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
