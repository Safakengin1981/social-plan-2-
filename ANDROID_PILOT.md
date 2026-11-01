# SocialPlan Android pilot kontrolü (v1.5)

## Bu pakette
- Önceki v1.4 uygulama kodu korunmuştur.
- `pilot_check.py`: dosyaları ve yerel sunucu sağlık yanıtını **salt okunur** kontrol eder.
- `python -m unittest discover -s tests -v` ile mevcut testler çalıştırılır.

## Yerel deneme
1. Bilgisayarda Python kurulu olmalıdır.
2. `python -m pip install -r requirements.txt`
3. `python -m uvicorn app:app --host 127.0.0.1 --port 8000`
4. Ayrı terminalde `python pilot_check.py`.
5. Bilgisayarda `http://127.0.0.1:8000` açın.

## Android pilotuna geçiş kapıları
- Uygulama internete açılmadan önce güvenlik incelemesi yapılmalı.
- Sunucu ve HTTPS alan adı kurulmalı; test kullanıcıları tanımlanmalı.
- Android Chrome üzerinden giriş, içerik yükleme ve onay işlemleri test edilmeli.
- PWA kurulumunun çalışması doğrulanmalı.
- Meta OAuth ve gerçek Instagram yayınlama bu pakette **yoktur**.

**Uyarı:** Paket APK değildir; doğrudan internete açılması önerilmez.
