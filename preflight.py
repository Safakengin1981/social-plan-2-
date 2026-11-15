"""Non-destructive deployment readiness check; does not expose credentials."""
import os
from pathlib import Path

def check(env=None):
    env = os.environ if env is None else env
    checks = []
    def add(name, ok, note): checks.append({'check':name,'ok':bool(ok),'note':note})
    db = env.get('SOCIALPLAN_DB','')
    media = env.get('SOCIALPLAN_MEDIA','')
    status = env.get('SOCIALPLAN_BACKUP_STATUS','')
    require = env.get('SOCIALPLAN_REQUIRE_PERSISTENT') == '1'
    add('Kalıcı disk zorunluluğu',require,'SOCIALPLAN_REQUIRE_PERSISTENT=1 gerekli')
    for name,path in [('Veritabanı yolu',db),('Medya yolu',media),('Yedek durum yolu',status)]:
        add(name,bool(path and Path(path).is_absolute() and path.startswith('/var/data/')),f'{name} /var/data altında olmalı')
    add('Otomatik zamanlayıcı',env.get('SOCIALPLAN_AUTO_BACKUP')=='1','SOCIALPLAN_AUTO_BACKUP=1 gerekli')
    add('Harici S3 bucket',bool(env.get('SOCIALPLAN_BACKUP_BUCKET','').strip()),'S3 bucket tanımlanmalı')
    add('S3 erişim bilgileri',bool(env.get('AWS_ACCESS_KEY_ID') and env.get('AWS_SECRET_ACCESS_KEY')),'Kimlik bilgileri ortam değişkenlerinde olmalı; IAM rolü kullanan kurulumlar ayrıca doğrulanmalı')
    hook=env.get('SOCIALPLAN_ALERT_WEBHOOK','')
    add('Hata bildirimi',hook.startswith('https://'),'HTTPS uyarı webhook adresi gerekli')
    return checks

if __name__=='__main__':
    results=check()
    for item in results:
        print(('PASS' if item['ok'] else 'FAIL'),item['check'],'—',item['note'])
    print(f"Sonuç: {sum(x['ok'] for x in results)}/{len(results)} kontrol başarılı")
    raise SystemExit(0 if all(x['ok'] for x in results) else 1)
