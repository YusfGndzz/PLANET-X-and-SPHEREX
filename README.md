# Harman — SPHEREx Zaman Makinesi

NASA Space Apps Challenge 2026 · Takım **Harman**

Bir gökyüzü koordinatı (RA, Dec) gir. NASA'nın SPHEREx teleskobunun o bölgeyi farklı
tarihlerde çektiği görüntüler mini bir video gibi oynasın. Hareket eden cisimler
(yakın yıldızlar, kahverengi cüceler, asteroitler) ve parlaklığı değişen yıldızlar görünür hale gelsin.

**Test hedefleri:** Barnard Yıldızı · Epsilon Indi B · WISE 0855 · RW Aurigae · R Draconis

## Nasıl çalışır?

1. NASA IRSA arşivine (TAP, `spherex.obscore` tablosu) sorulur: bu noktayı kapsayan ve istenen
   dalga boyunu içeren dedektördeki bütün pozlar.
2. Pozlar **dönemlere** ayrılır (SPHEREx aynı noktaya yaklaşık 6 ayda bir döner; 30 günden uzun
   boşluk yeni dönem sayılır). Her dönemden kare seçilir.
3. IRSA kesit (cutout) servisiyle hedefin çevresi indirilir.
4. Her kesit, hedef merkezli ortak bir ızgaraya **yeniden örneklenir**: kuzey yukarı, doğu solda.
   Böylece yıldızlar bütün karelerde aynı yerde durur; yer değiştiren şey gerçekten hareket ediyordur.
5. Her kareden arka plan çıkarılır ve **bütün kareler aynı parlaklık ölçeğiyle** çizilir; parlaklık
   değişimi gerçek değişimdir.
6. Hedefteki **gerçek dalga boyu** FITS dosyasından okunur (SPHEREx'te dalga boyu dedektör üzerindeki
   konuma göre değişir).
7. Dönem yığınları (ortanca), ilk–son dönem **fark görüntüsü**, GIF ve hedefin **spektrumu** üretilir.

## Klasörler

| Yol | İçerik |
|---|---|
| `api/main.py` | FastAPI sunucusu: API uç noktaları + `web/` ve `cache/` klasörlerini sunar |
| `api/pipeline.py` | Veri hattı: IRSA sorgusu, indirme, hizalama, PNG/GIF, spektrum, önbellek |
| `api/resolver.py` | Cisim adı → koordinat (CDS Sesame), öz hareket düzeltmesiyle |
| `web/` | Arayüz: `index.html` (tanıtım sayfası), `explorer.html` (araç), `app.js`, `style.css`, `targets.json` (test hedefleri) |
| `scripts/pregenerate.py` | Demo öncesi test hedeflerini önceden üretip önbelleğe alır |
| `cli.py` | Sunucusuz komut satırı kullanımı |
| `cache/` | Üretilen dosyalar (git'e girmez) |

## Kurulum

Python 3.10 ya da daha yenisi gerekir.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

## Çalıştırma

Proje kök klasöründen:

```powershell
uvicorn api.main:app --reload
```

Sonra tarayıcıda <http://127.0.0.1:8000> adresini açın (tanıtım sayfası); aracın kendisi
<http://127.0.0.1:8000/explorer.html> adresindedir. Etkileşimli API belgesi: <http://127.0.0.1:8000/docs>

İlk istek, görüntüler NASA'dan indirildiği için yarım dakika kadar sürebilir; aynı istek ikinci kez
anında döner (disk önbelleği).

### Adres çubuğundan kullanım

Araç sayfası adresteki parametreleri okur; bağlantı paylaşılabilir:

```
http://127.0.0.1:8000/explorer.html?ra=269.446&dec=4.768&fov=4&band=2.5
```

Kısa biçim de çalışır: `/?ra=269.446&dec=4.768` adresi araç sayfasına yönlendirilir.

### Demo öncesi önbelleği ısıtma

```powershell
python scripts/pregenerate.py            # web/targets.json içindeki bütün hedefler
python scripts/pregenerate.py --refresh  # baştan üret
```

### Komut satırından

```powershell
python cli.py "Barnard's Star"
python cli.py --ra 248.1672 --dec 66.7550 --fov 6 --band 2.5 --frames 12
```

## API

Bütün cevaplar JSON'dur. Hata durumunda gövde her zaman `{"status":"error","message":"..."}` biçimindedir.

### `GET /api/resolve?name=<cisim adı>`

Adı koordinata çevirir (SIMBAD/NED/VizieR, CDS Sesame üzerinden).

```json
{"status": "success", "name": "Barnard's Star", "ra": 269.446269, "dec": 4.768204,
 "epoch": 2026.0, "ra_j2000": 269.452077, "dec_j2000": 4.693365,
 "pm_ra_masyr": -801.551, "pm_dec_masyr": 10362.394, "source": "CDS Sesame"}
```

`ra`/`dec`, öz hareket biliniyorsa **2026.0 dönemine taşınmış** konumdur (SPHEREx gözlemlerinin
yapıldığı zaman). Barnard Yıldızı J2000 konumundan 4.5 yay-dakikası kaymıştır; J2000 konumu
kullanılsaydı 4 yay-dakikalık karede yıldız görünmezdi. Özgün katalog konumu `ra_j2000`/`dec_j2000`
alanlarındadır. `name` olarak `"269.446 4.768"` ya da `"17h57m47s +04d46m05s"` gibi koordinat da verilebilir.

### `GET /api/generate`

| Parametre | Varsayılan | Açıklama |
|---|---|---|
| `ra` | zorunlu | Sağ açıklık, derece |
| `dec` | zorunlu | Dik açıklık, derece |
| `fov` | 4 | Görüş alanı, yay-dakikası (1–30 arasına sınırlanır) |
| `band` | 2.5 | Dalga boyu, mikrometre (0.75–5.0). Bu dalga boyunu içeren dedektör seçilir |
| `frames` | 12 | Kare sayısı (2–40 arasına sınırlanır) |
| `refresh` | 0 | `1` ise önbellek yok sayılır, baştan üretilir |

```json
{
  "status": "success",
  "message": "12 kare, 3 dönem (2025-09-21 → 2026-09-24); D4 dedektörü, hedefte 2.43–2.71 µm.",
  "target": {"ra": 269.446, "dec": 4.768, "fov_arcmin": 4.0, "band_um": 2.5, "detector": "D4",
             "pixel_scale_arcsec": 3.0},
  "frames": [
    {"index": 0, "url": "/cache/<anahtar>/frame_00.png", "date": "2025-09-21", "mjd": 60939.31245,
     "wavelength_um": 2.4712, "bandwidth_um": 0.0601, "detector": "D4", "obs_id": "...",
     "epoch": 0, "flagged_fraction": 0.02}
  ],
  "epochs": [
    {"id": 0, "label": "2025-09", "start": "2025-09-18", "end": "2025-09-27", "n_frames": 4,
     "n_stacked": 12, "n_available": 17, "stack_url": "/cache/<anahtar>/epoch_0.png"}
  ],
  "diff_url": "/cache/<anahtar>/diff.png",
  "gif_url": "/cache/<anahtar>/anim.gif",
  "spectrum": [{"wavelength_um": 2.4712, "flux": 1.2, "flux_err": 0.1, "date": "2025-09-21",
                "mjd": 60939.31245, "epoch": 0}],
  "flux_unit": "MJy/sr (aperture sum)",
  "cached": false,
  "elapsed_s": 31.4
}
```

Notlar:

- Bütün görüntü adresleri kök-göreli (`/cache/...`). Bütün PNG'ler 400×400 pikseldir; kuzey yukarı,
  doğu solda, hedef tam ortada (camgöbeği nişangâh).
- `frames[].epoch`, `epochs[].id` değerine karşılık gelir. `n_frames` videodaki kare sayısı,
  `n_stacked` dönem yığınına giren poz sayısı, `n_available` arşivdeki toplam poz sayısıdır.
- `diff_url`: son dönem yığını − ilk dönem yığını. İkiden az dönem varsa `null`. Hareket eden cisim
  yan yana siyah-beyaz bir çift olarak görünür; parlaklığı değişen yıldız tek renk leke olur.
- `frames[].wavelength_approx: true` yalnızca gerçek dalga boyu dosyadan okunamadıysa bulunur
  (o zaman değer dedektör aralığının orta noktasıdır ve resimde `~` ile işaretlenir).
- `spectrum`: indirilen her poz için hedefte açıklık fotometrisi (yarıçap 2 piksel, 5–8 piksel halkadan
  arka plan), dalga boyuna göre sıralı. Boş liste olabilir; `flux_err` `null` olabilir. Açıklıkta
  doymuş piksel varsa noktada `"saturated": true`, kozmik ışın işaretli piksel varsa `"flagged": true`
  bulunur (ölçüm daha az güvenilir). Açıklıkta ölü/eksik piksel varsa o poz spektruma girmez.
- `flagged_fraction`: karede FLAGS uzantısına göre kötü sayılan (kozmik ışın, sıcak/soğuk/ölü piksel,
  eksik veri) piksellerin oranı. Bu pikseller komşularından doldurulur.

### Statik dosyalar

- `/` → `web/` klasörü (`/` tanıtım sayfası, `/explorer.html` araç)
- `/cache/...` → üretilen PNG/GIF/JSON dosyaları
- CORS bütün kaynaklara açıktır (arayüz başka bir adresten de sunulabilir).

## İnternete açma (kısaca)

Uygulama tek bir Python süreci ve bir disk klasöründen ibarettir:

```bash
pip install -r requirements.txt
uvicorn api.main:app --host 0.0.0.0 --port $PORT
```

- **Render / Railway / Fly.io:** depoyu bağlayın, "start command" olarak yukarıdaki satırı verin.
  Önbelleğin yeniden başlatmalarda silinmemesi için kalıcı bir disk bağlayıp `SPHEREX_CACHE_DIR`
  ortam değişkenini o diske yönlendirin.
- **Kendi sunucunuz (VPS):** aynı komutu `systemd` servisi olarak çalıştırıp önüne Nginx/Caddy ile HTTPS koyun.
- Yayına almadan önce `python scripts/pregenerate.py` ile test hedeflerini üretin; demo sırasında
  IRSA yavaşlasa ya da kapansa bile bu hedefler anında açılır.
- Tek süreç (`--workers 1`) kullanın: aynı isteğin iki kez üretilmesini engelleyen kilit süreç içidir.

## Bilinen sınırlamalar

- **Veri kaynağına bağımlılık:** önbellekte olmayan her istek NASA IRSA'ya gider. IRSA bakımdaysa ya da
  yanıt vermiyorsa yalnızca önbellekteki hedefler çalışır (hata mesajı bunu söyler).
- **Kareler tam aynı dalga boyunda değildir.** SPHEREx'te her pozda hedef, dedektörün farklı bir yerine,
  dolayısıyla farklı bir dalga boyuna düşer. İstenen dalga boyuna en yakın pozlar seçilir ve her karenin
  gerçek dalga boyu etikette yazar; yine de kareler arası parlaklık farkının bir kısmı renk (spektrum)
  kaynaklı olabilir. Değişkenlik için aynı dalga boyundaki kareleri karşılaştırın.
- **Spektrum yalnızca seçilen dedektörün aralığını kapsar** (örneğin D4: 2.42–3.82 µm). Altı dedektörün
  hepsi için ayrı istek atmak gerekir.
- **Çözünürlük:** SPHEREx pikseli 6.15 yay-saniyesidir; yılda birkaç yay-saniyesinden yavaş hareketler
  bir yıllık veride piksel altı kalır. Kalabalık alanlarda komşu yıldızlar fotometriyi kirletir.
- **Çok parlak yıldızlar** (ör. R Draconis) doyabilir; fotometri noktaları `saturated` ile işaretlenir.
- **Hızlı cisimler:** girilen koordinat sabittir; hedef kare içinde hareket eder (zaten görmek istediğimiz
  budur) ama fotometri hep girilen noktada yapılır. Ad çözümleme konumu 2026.0 dönemine taşır.
- Ad çözümleme için CDS Sesame'e (Harvard aynası yedekli) erişim gerekir; erişilemezse RA/Dec elle girilir.

## IRSA kapalıyken: S3 yedek yolu

NASA IRSA'nın sorgu servisi yanıt vermediğinde araç kendiliğinden NASA'nın açık S3 deposuna
(`nasa-irsa-spherex`) geçer; kullanıcı aynı sonucu alır, mesajın sonunda veri kaynağı yazar.

- `api/s3index.py`: hedefi kapsayan pozları yerel konum dizininden bulur ve 62 MB'lık dosyanın
  yalnızca hedef çevresindeki satırlarını (HTTP Range ile, poz başına ~150 KB) okur.
- Dizin `cache/s3index/4/` altında durur ve GitHub'a girmez. Bir kez şu komutla üretilir
  (yaklaşık yarım saat sürer, her dosyadan yalnızca başlık okunur, toplam ~6 GB veri trafiği):

  ```
  python scripts/build_s3_index.py
  ```

  Yeni veri yayınlandıkça aynı komut tekrar çalıştırılır; yalnızca eksik haftaları ekler.
- Dizin yalnızca dedektör 4 için tutulur; diğer dedektörlerin konumu aynı pozdaki sabit odak
  düzlemi kaymasından hesaplanır (D1≈D4, D2≈D5, D3≈D6 aynı alanı görür).
- IRSA bir kez yanıt vermezse 10 dakika boyunca tekrar denenmez, istekler doğrudan S3'e gider.

Windows'ta sunucuyu başlatmak için `baslat.bat` dosyasına çift tıklamak yeterlidir.
