"""Safe publication queue: no Instagram network calls until official OAuth integration."""
import os
import time
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException

def attach_publishing(auth, conn):
    router=APIRouter(prefix='/api')
    with conn() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS publish_jobs (
          post_id INTEGER PRIMARY KEY REFERENCES posts(id),
          business_id INTEGER NOT NULL REFERENCES businesses(id),
          state TEXT NOT NULL CHECK(state IN ('queued','ready','blocked','cancelled')),
          reason TEXT NOT NULL DEFAULT '', updated_at INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_publish_jobs_business ON publish_jobs(business_id,state);
        ''')
    @router.get('/publishing/readiness')
    def readiness(u=Depends(auth)):
        return {'instagram_connected':False,'oauth_configured':bool(os.getenv('META_APP_ID') and os.getenv('META_APP_SECRET') and os.getenv('META_REDIRECT_URI')),'public_media_delivery':False,
                'automatic_publishing_enabled':False,'scheduler_enabled':True,
                'message':'Meta kimlik bilgileri ayarlanmış olsa bile OAuth bağlantısı ve gönderim henüz uygulanmadı. Otomatik yayınlama kapalıdır.'}
    @router.get('/publishing/jobs')
    def jobs(u=Depends(auth)):
        with conn() as c:
            rows=c.execute('''SELECT j.post_id,j.state,j.reason,j.updated_at,p.scheduled_at,p.caption
              FROM publish_jobs j JOIN posts p ON p.id=j.post_id
              WHERE j.business_id=? ORDER BY p.scheduled_at''',(u['business_id'],)).fetchall()
        return [dict(row) for row in rows]
    @router.post('/publishing/refresh')
    def refresh(u=Depends(auth)):
        if u['role']!='admin':raise HTTPException(403,'Yönetici yetkisi gerekli')
        now=int(time.time()); ready=0
        with conn() as c:
            posts=c.execute("SELECT id,business_id,scheduled_at,status FROM posts WHERE business_id=?",(u['business_id'],)).fetchall()
            for post in posts:
                if post['status']=='draft':continue
                if post['status']=='cancelled':
                    state,reason='cancelled','İçerik iptal edildi.'
                else:
                    due=datetime.fromisoformat(post['scheduled_at'].replace('Z','+00:00')).astimezone(timezone.utc).timestamp()<=now
                    state='blocked' if due else 'queued'
                    reason='Meta bağlantısı kurulmadığından paylaşım yapılmadı.' if due else 'Planlanan saat bekleniyor.'
                    if due:ready+=1
                c.execute('''INSERT INTO publish_jobs(post_id,business_id,state,reason,updated_at) VALUES(?,?,?,?,?)
                  ON CONFLICT(post_id) DO UPDATE SET state=excluded.state,reason=excluded.reason,updated_at=excluded.updated_at''',
                  (post['id'],post['business_id'],state,reason,now))
        return {'blocked_due_to_missing_meta':ready,'instagram_published':0,'message':'Kuyruk güncellendi. Meta bağlantısı olmayan zamanı gelmiş gönderiler engellendi; Instagram paylaşımı yapılmadı.'}
    return router
