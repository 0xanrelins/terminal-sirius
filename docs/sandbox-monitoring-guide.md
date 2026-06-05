# Sandbox Paper Trade Dashboard Rehberi

NautilusTrader sandbox icin hazir bir dashboard vermez; ama profesyonel bir paper trade dashboard'u olusturmak icin gerekli ham veriyi, hesaplari ve raporlari native olarak sunar.

## Dashboard'da Takip Edilecek Veriler

- **Run durumu** - Strateji calisiyor mu, ne zaman basladi, hangi venue ve hangi enstrumanlarda aktif?
- **Baglanti sagligi** - Data client ve execution client bagli mi, son veri ne zaman geldi?
- **Son piyasa verisi** - Son quote, son trade, son bar ve fiyat guncelligi `Cache` uzerinden takip edilir.
- **Acik emirler** - Bekleyen, kismen dolan veya iptal bekleyen emirler `Cache` ve order report ile izlenir.
- **Fill akisi** - Hangi emir ne zaman, hangi fiyattan, ne kadar doldu bilgisi `OrderFilled` eventleri ve fills report ile alinir.
- **Acik pozisyonlar** - Long/short/flat durum, miktar, ortalama giris fiyati ve pozisyon suresi positions report ile takip edilir.
- **Gerceklesen PnL** - Kapanan pozisyonlardan gelen net sonuc `Portfolio` ve positions report uzerinden hesaplanir.
- **Gerceklesmemis PnL** - Acik pozisyonlarin anlik kar/zarari `Portfolio.unrealized_pnl()` ile izlenir.
- **Toplam PnL** - Gerceklesen ve gerceklesmemis sonuc birlikte `Portfolio.total_pnl()` ile takip edilir.
- **Equity ve bakiye** - Hesap bakiyesi, acik pozisyon degeri ve toplam equity `Portfolio.equity()` ile uretilir.
- **Exposure** - Enstruman veya venue bazinda ne kadar risk tasindigi `Portfolio.net_exposure()` ile izlenir.
- **Risk yogunlugu** - Tek enstrumana, tek yonde veya tek sinyale fazla agirlik binip binmedigi pozisyon ve exposure verilerinden cikarilir.
- **Emir kalitesi** - Fill fiyati, ortalama fiyat, komisyon ve slippage fills report ile analiz edilir.
- **Trade sayisi** - Gunluk/oturumluk kac emir ve kac fill oldugu orders/fills report ile sayilir.
- **Win rate** - Kapanan pozisyonlarin ne kadari karli bitti `PortfolioAnalyzer` ile hesaplanir.
- **Drawdown** - Equity zirvesinden ne kadar geri gelindigi `PortfolioAnalyzer` veya session sonu analiz ile hesaplanir.
- **Profit factor** - Kazanan trade'lerin kaybeden trade'lere oranli kalitesi `PortfolioAnalyzer` ile takip edilir.
- **Sharpe / return metrikleri** - Stratejinin risk ayarli performansi `PortfolioAnalyzer` istatistiklerinden alinir.
- **Hata ve uyarilar** - Rejected order, denied order, reconciliation farklari ve missing price durumlari loglardan takip edilir.
- **Session ozeti** - Oturum sonunda orders, fills, positions, account ve analyzer ciktisi tek rapor haline getirilir.

## Nautilus'tan Veriyi Alma Yollari

- **Loglar** - En hizli takip katmani; emir, fill, hata ve state degisimlerini insan okuyabilir sekilde verir.
- **Strategy callback'leri** - `on_order_filled`, `on_position_opened`, `on_position_closed` gibi olaylarda anlik dashboard mesaji uretmek icin kullanilir.
- **Actor callback'leri** - Stratejiden bagimsiz izleme, raporlama veya alarm uretmek icin temiz yoldur.
- **Cache** - Anlik durum kaynagi; emirler, pozisyonlar, son fiyatlar ve enstruman bilgileri buradan okunur.
- **Portfolio** - Bakiye, PnL, equity ve exposure gibi finansal hesaplarin ana kaynagidir.
- **Trader reports** - Orders, fills, positions ve account tablolarini pandas DataFrame olarak verir.
- **PortfolioAnalyzer** - Performans metriklerini hesaplayan resmi analiz katmanidir.
- **Actor timer** - Belirli araliklarla rapor veya dashboard snapshot'i uretmek icin kullanilir.
- **MessageBus / Redis** - Dashboard baska process veya servis olacaksa event ve data akisini disari tasir.
- **Parquet streaming** - Oturum verisini saklayip daha sonra offline analiz, replay veya backtest icin kullanirsin.
- **Tearsheet** - Canli panel degil; oturum sonunda HTML performans raporu uretmek icin uygundur.

## Pratik Dashboard Akisi

- **Anlik panel** - Cache + Portfolio + callback eventleri ile beslenir.
- **Periyodik snapshot** - Actor timer her 1-5 dakikada orders, positions, PnL ve equity ozeti uretir.
- **Alarm katmani** - Rejected order, buyuk drawdown, veri gecikmesi veya missing price durumunda uyarir.
- **Gun sonu raporu** - Trader reports + PortfolioAnalyzer ile session ozeti cikarilir.
- **Offline analiz** - Parquet ve rapor ciktisi daha sonra strateji iyilestirme icin kullanilir.

## Ozet

Profesyonel paper trade monitoring icin Nautilus'ta tek bir hazir ekran yoktur; dogru yol `Cache`, `Portfolio`, `Trader reports`, `PortfolioAnalyzer`, loglar ve event callback'lerini birlestirip kendi dashboard veri katmanini kurmaktir.


