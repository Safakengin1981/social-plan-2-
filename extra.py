"""Additional SocialPlan MVP endpoints. Files are private to each business."""
import os, secrets, sqlite3, time, hashlib
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

router=APIRouter(prefix='/api')

def attach(auth,conn,pw_hash,base):
    root=Path(os.getenv('SOCIALPLAN_UPLOAD_DIR',str(Path(base)/'uploads')));root.mkdir(parents=True,exist_ok=True)
    class Member(BaseModel):
        email:str=Field(min_length=5,max_length=254)
        password:str=Field(min_length=12,max_length=128)
        role:str='editor'
    @router.get('/members')
    def members(u=Depends(auth)):
        if u['role']!='admin':raise HTTPException(403,'Yönetici yetkisi gerekli')
        with conn() as c:
            rows=c.execute('SELECT id,email,role FROM users WHERE business_id=? ORDER BY id',(u['business_id'],)).fetchall()
        return [dict(r) for r in rows]
    @router.post('/members',status_code=201)
    def add_member(x:Member,u=Depends(auth)):
        if u['role']!='admin':raise HTTPException(403,'Yönetici yetkisi gerekli')
        if x.role not in ('editor','admin'):raise HTTPException(422,'Geçersiz rol')
        salt=secrets.token_hex(16)
        try:
            with conn() as c:
                cur=c.execute('INSERT INTO users(business_id,email,salt,passhash,role) VALUES(?,?,?,?,?)',
                   (u['business_id'],x.email.strip().lower(),salt,pw_hash(x.password,salt),x.role))
                uid=cur.lastrowid
        except sqlite3.IntegrityError:raise HTTPException(409,'E-posta zaten kayıtlı')
        return {'id':uid,'email':x.email.strip().lower(),'role':x.role}
    @router.post('/media',status_code=201)
    async def upload(file:UploadFile=File(...),u=Depends(auth)):
        allowed={'image/jpeg':'.jpg','image/png':'.png','image/webp':'.webp','video/mp4':'.mp4'}
        if file.content_type not in allowed:raise HTTPException(415,'JPG, PNG, WebP veya MP4 yükleyin')
        folder=root/str(u['business_id']);folder.mkdir(exist_ok=True)
        name=secrets.token_hex(16)+allowed[file.content_type];target=folder/name
        size=0
        try:
            with target.open('wb') as out:
                while chunk:=await file.read(1024*1024):
                    size+=len(chunk)
                    if size>25*1024*1024:raise HTTPException(413,'Dosya 25 MB sınırını aşıyor')
                    out.write(chunk)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        finally:await file.close()
        if size==0:
            target.unlink(missing_ok=True)
            raise HTTPException(422,'Boş dosya yüklenemez')
        with target.open('rb') as f: signature=f.read(32)
        valid=(file.content_type=='image/jpeg' and signature.startswith(b'\xff\xd8\xff')) or (file.content_type=='image/png' and signature.startswith(b'\x89PNG\r\n\x1a\n')) or (file.content_type=='image/webp' and signature[:4]==b'RIFF' and signature[8:12]==b'WEBP') or (file.content_type=='video/mp4' and signature[4:8]==b'ftyp')
        if not valid:
            target.unlink(missing_ok=True)
            raise HTTPException(415,'Dosya içeriği bildirilen formatla eşleşmiyor')
        return {'media_path':f'/api/media/{name}','size':size,'note':'Yalnızca giriş yapmış aynı işletme kullanıcıları erişebilir. Instagram yayınlama için henüz uygun genel medya URL altyapısı kurulmadı.'}
    @router.get('/media/{name}')
    def get_media(name:str,u=Depends(auth)):
        if len(name)>80 or '/' in name or '\\' in name or name.startswith('.'):raise HTTPException(404)
        target=root/str(u['business_id'])/name
        if not target.is_file():raise HTTPException(404,'Medya bulunamadı')
        return FileResponse(target,headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
    return router
