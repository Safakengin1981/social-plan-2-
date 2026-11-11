# SocialPlan 5.8 — Instagram Login ve doğrudan yayınlama uygulama planı

## Kullanıcı hedefi
RHM Teras'ın profesyonel `@rhm_teras` Instagram hesabını SocialPlan içinden Instagram'ın **resmi** giriş/yetkilendirme ekranıyla bağlamak. Günlük kullanımda Meta Business Suite portföy davetleri, Facebook hesap seçimi, Render ve GitHub ekranları gerekmemeli. Instagram şifresi SocialPlan'a **asla** gönderilmemeli veya saklanmamalı.

## Planable ile doğrulananlar (11 Kasım 2026)
- `@rhm_teras` bağlantısı Planable'da **Aktif**.
- Gönderileri, Reels'i ve hikâyeleri doğrudan yayınlama anahtarları etkin.
- Bunlar Planable'a verilmiş izinlerdir; SocialPlan'a devredilemez.

## Resmi teknik akış
1. Backend `INSTAGRAM_CLIENT_ID`, `INSTAGRAM_CLIENT_SECRET`, `INSTAGRAM_REDIRECT_URI` ve `INSTAGRAM_OAUTH_ENABLED` yapılandırmasını kontrol eder; eksikse kullanıcıya doğru, anlaşılır durum gösterir.
2. Yönetici `Instagram Hesabını Bağla` butonuna basar. Backend tek kullanımlık, süreli ve mevcut oturuma bağlı `state` üretir; resmi `instagram.com/oauth/authorize` adresine yönlendirir.
3. Instagram profesyonel hesabı için gereken asgari izinler istenir (basic ve content_publish). Meta'nın gerekli geliştirici uygulaması ve uygulama inceleme/erişim gereksinimleri atlanamaz.
4. Callback `state` doğrular, kodu sunucuda token'a çevirir, uzun ömürlü token'ı güvenli şekilde saklar; kimliği `@rhm_teras` olarak API üzerinden doğrular.
5. `Bağlı` göstergesi ancak gerçek API doğrulamasından sonra görünür; `stored_unverified` ayrı bir uyarı durumudur.
6. Planlanan içerik için idempotent kuyruk, medya URL erişilebilirliği, Instagram container oluşturma/yayınlama, yeniden deneme, oran sınırı, sonuç/başarısızlık kaydı ve izleme eklenir.
7. Gönderi/Reels/hikâye için resmi API'nin güncel format ve kısıtları ayrı ayrı doğrulanır. Desteklenmeyen özel müzik/çıkartma gibi öğelerde otomatik yayın vaat edilmez.

## Güvenlik ve operasyon
- Sosyal medya şifresini isteyen bir form **yapılmayacak**.
- OAuth `state`, CSRF, yönetici yetkisi, token şifreleme ve token yenileme uygulanacak.
- Kullanıcıya `bağlandı` veya `otomatik yayın açık` bilgisi gerçek doğrulama olmadan verilmeyecek.
- Testte gerçek hesaba paylaşım yapılmayacak; canlıya geçmeden önce kullanıcı onayı alınacak.
- Mevcut üretim/staging verileri ve Planable bağlantısı değiştirilmeyecek.
- Free Render test servisi kalıcı depolama sunmadığı için token kalıcılığı testi bu serviste geçerli sayılmayacak.

## Mevcut blokaj
SocialPlan'ın Meta geliştirici uygulamasının Instagram Login API için gerekli ayarları ve izinleri tamamlanmadan Instagram'ın resmi yetkilendirme akışı çalışmaz. Meta Business Suite'teki davetleri atlamak mümkün olabilir, **Meta geliştirici uygulaması ve izinlerini atlamak mümkün değildir**.

## Kabul kriterleri
- Kullanıcı SocialPlan'dan resmi Instagram giriş ekranına geçebilir.
- Bağlantı API ile doğrulanır, token yeniden başlatmadan sonra kalır.
- Yetki iptali/sona ermesi açıkça gösterilir ve yeniden bağlanma çalışır.
- Test içeriği kullanıcı onayıyla yayımlanır ve Instagram medya ID'si kaydedilir.
- Gönderi, Reels ve hikâye senaryoları ayrı ayrı doğrulanır.
