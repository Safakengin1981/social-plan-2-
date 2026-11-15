# SocialPlan 5.9 — Güvenli dağıtım notu

## Kaynak
- Temel alınan çalışan Render commit'i: `fa339998f8a97c5df40a959c9b119f59e1f69476`.
- Gerçek çalışan uygulama, bu commit'teki Dockerfile tarafından `SocialPlan_5_8_Yeniden_Baslatma_Testi.zip.zip` arşivinden açılıyor.
- 5.9 geliştirme arşivi: `SocialPlan_5_9_Genel_Bakis_Test.zip` (sohbette oluşturuldu; henüz GitHub'a aktarılmadı).

## Dağıtım öncesi zorunlu kontroller
1. 5.9 arşivindeki Python ve statik dosyaları kaynak kontrolüne aktar; `data/*.sqlite3`, yüklemeler, oturumlar ve sırları kesinlikle aktarma.
2. Dockerfile'ı yeni kaynaklarla tutarlı hale getir; eski 5.8 arşivinden kurulum yapmaya devam etmemeli.
3. Testleri çalıştır; test veritabanı ile üretim veritabanını ayır.
4. Yedek ve geri dönüş planını doğrula.
5. Önce ayrı Render test servisine dağıt; canlı `socialplan-v58-test` servisini değiştirme.
6. Giriş, işletme ayrımı, içerik oluşturma, onay, iptal, yedek, gösterge paneli ve Meta yayınlamama güvenliğini doğrula.

## Durum
Bu dal hazırlık amaçlıdır. Canlı servise dağıtım yapılmadı.