"""Validate SocialPlan distribution archives before publishing."""
from pathlib import Path
import sys
from zipfile import ZipFile, BadZipFile

FORBIDDEN_PARTS = {'data', '__pycache__', '.pytest_cache', '.git', '.venv', 'node_modules'}
FORBIDDEN_NAMES = {'.env', '.env.local', '.env.production', 'id_rsa', 'id_ed25519'}
FORBIDDEN_SUFFIXES = {'.sqlite3', '.sqlite', '.db', '.pem', '.key', '.pyc', '.p12', '.pfx'}
REQUIRED = {'app.py', 'requirements.txt', 'Dockerfile', 'README.md', 'static/index.html', 'static/app.js'}


def check_archive(path):
    errors = []
    try:
        with ZipFile(path) as z:
            names = set(z.namelist())
            missing = REQUIRED - names
            if missing:
                errors.append('Eksik dosyalar: ' + ', '.join(sorted(missing)))
            for name in sorted(names):
                p = Path(name)
                if p.is_absolute() or '..' in p.parts:
                    errors.append('Guvenli olmayan dosya yolu: ' + name)
                if FORBIDDEN_PARTS.intersection(p.parts) or p.name in FORBIDDEN_NAMES or p.suffix.lower() in FORBIDDEN_SUFFIXES:
                    errors.append('Paket icinde yasakli dosya: ' + name)
            bad = z.testzip()
            if bad:
                errors.append('ZIP butunluk hatasi: ' + bad)
    except (BadZipFile, OSError) as exc:
        errors.append(str(exc))
    return errors


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Kullanim: python release_check.py paket.zip')
    problems = check_archive(sys.argv[1])
    if problems:
        print('\n'.join(problems))
        raise SystemExit(1)
    print('Paket kontrolu basarili: uygulama dosyalari mevcut, veritabani/gizli dosya yok, ZIP saglam.')
