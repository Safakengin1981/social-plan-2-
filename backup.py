"""Online-consistent SQLite backup (run where SOCIALPLAN_DB is accessible)."""
import os,sqlite3,datetime,pathlib
source=os.environ.get('SOCIALPLAN_DB','socialplan.sqlite3')
out=pathlib.Path(os.environ.get('SOCIALPLAN_BACKUP_DIR','backups'))
out.mkdir(parents=True,exist_ok=True)
target=out/('socialplan-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.sqlite3')
with sqlite3.connect(source) as src,sqlite3.connect(target) as dest: src.backup(dest)
with sqlite3.connect(target) as c: assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
print('Backup verified:',target)
