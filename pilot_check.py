"""Read-only preflight for local SocialPlan pilot; does not publish or alter data."""
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def main():
    checks = []
    def add(label, ok, detail):
        checks.append((label, ok, detail))
    for relative in ('app.py', 'static/index.html', 'static/manifest.webmanifest', 'compose.yaml', 'Dockerfile'):
        add('Dosya: ' + relative, (ROOT / relative).is_file(), 'Mevcut' if (ROOT / relative).is_file() else 'Eksik')
    address = os.getenv('SOCIALPLAN_TEST_URL', 'http://127.0.0.1:8000').rstrip('/')
    if not (address.startswith('http://127.0.0.1:') or address.startswith('http://localhost:')):
        add('Test adresi', False, 'Güvenlik için yalnızca yerel test adresi kabul edilir')
    else:
        try:
            with urllib.request.urlopen(address + '/api/health', timeout=3) as response:
                add('Yerel sunucu', response.status == 200, 'HTTP ' + str(response.status))
        except Exception:
            add('Yerel sunucu', False, 'Çalışmıyor veya erişilemiyor')
    for name, ok, detail in checks:
        print(('OK' if ok else 'BEKLİYOR') + ' | ' + name + ' | ' + detail)
    print('\nNot: Bu kontrol canlı sunucu, HTTPS, Android veya Instagram entegrasyonu doğrulaması değildir.')
    return 0 if all(ok for _, ok, _ in checks) else 1

if __name__ == '__main__':
    sys.exit(main())
