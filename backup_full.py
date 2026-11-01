"""Consistent SQLite + uploaded media backup; run in maintenance window for media consistency."""
import os, sqlite3, datetime, pathlib, tempfile, zipfile, json
DB=pathlib.Path(os.getenv('SOCIALPLAN_DB','socialplan.sqlite3'))
MEDIA=pathlib.Path(os.getenv('SOCIALPLAN_UPLOAD_DIR','uploads'))
OUT=pathlib.Path(os.getenv('SOCIALPLAN_BACKUP_DIR','backups'))

def create_backup():
    if not DB.is_file(): raise FileNotFoundError(f'Database not found: {DB}')
    OUT.mkdir(parents=True,exist_ok=True)
    name='socialplan-full-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.zip'
    dest=OUT/name
    with tempfile.TemporaryDirectory() as temp:
        snapshot=pathlib.Path(temp)/'socialplan.sqlite3'
        with sqlite3.connect(f'file:{DB.resolve()}?mode=ro',uri=True) as src,sqlite3.connect(snapshot) as target:
            src.backup(target)
        with sqlite3.connect(snapshot) as check:
            if check.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise RuntimeError('SQLite integrity check failed')
        with zipfile.ZipFile(dest,'x',compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(snapshot,'database/socialplan.sqlite3')
            count=0
            if MEDIA.is_dir():
                for path in sorted(MEDIA.rglob('*')):
                    if path.is_file() and not path.is_symlink():
                        archive.write(path,'uploads/'+path.relative_to(MEDIA).as_posix());count+=1
            archive.writestr('manifest.json',json.dumps({'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'media_files':count,'note':'Media may change during backup; pause uploads for consistent snapshot.'}))
    with zipfile.ZipFile(dest) as archive:
        if archive.testzip() is not None: raise RuntimeError('ZIP verification failed')
    return dest
if __name__=='__main__': print('Verified full backup:',create_backup())
