[README.md](https://github.com/user-attachments/files/34234731/README.md)
# SocialPlan v1.6 — Android pilot hazırlığı

Bu sürümde uygulama sürüm numarası eşitlendi, bağımlılık dosyasındaki tekrar kaldırıldı ve `deployment_audit.py` salt-okunur kontrolü eklendi.

## Testler
`python -m unittest discover -s tests -v`

## Dağıtım ön kontrolü
`python deployment_audit.py`

## Yerel çalıştırma
`python -m pip install -r requirements.txt`
`python -m uvicorn app:app --host 127.0.0.1 --port 8000`

Bu yerel adres Android telefondan doğrudan erişilemez. Android PWA kurulumu için güvenlik incelemesinden geçmiş HTTPS dağıtımı gereklidir. **APK yoktur. Instagram paylaşımı yoktur. İnternete doğrudan açmayın.**
