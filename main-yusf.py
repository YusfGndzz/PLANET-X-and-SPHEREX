from concurrent.futures import ThreadPoolExecutor #aynı anda birden fazla dosya indirmek için 
import io #internetten gelen veriyi diske yazdırmadan bellekte dosya açmak için
import re #düzenli ifadeler kontrolu
import numpy as np #resimler aslında sayı tablolarıdır , onlarla hesap yapmak için
import pyvo #Sanal gözlemevi standardı. Burada Datalink'i çözmek için kullanılır
import requests #dosya indirmek için 
from astropy.coordinates import SkyCoord #gökyüzü kordinatları nesnesi
from astropy.io import fits #astronomların resim formatı olan FITS'i okumak için
from astropy.nddata import Cutout2D #büyük bir resimden belli bir kordinat etrafını kesmek için
from astropy.time import Time #MJD tarihini normal tarihe çevirmek için
import astropy.units as u #birimler: u.deg (derece), u.arcmin (yay dakikası)
from astropy.visualization import AsinhStretch, ImageNormalize, ZScaleInterval #resmi göze güzel gelecek şekilde parlaklık ayarı
from astropy.wcs import WCS #World Coordinate System: “resmin şu pikseli gökyüzünde nereye denk geliyor?” sorusunun cevabı
from astroquery.ipac.irsa import Irsa #ASA/IPAC IRSA TAP servisine ADQL sorgusu göndererek spherex.obscore tablosundan gözlem kayıtlarını çeker.
from PIL import Image, ImageDraw #PNG ve GİF üretmek, üstüne yazı yazmak için 


#kullanıcıdan bilgi alma satırı 
def parse_user_target() -> tuple[SkyCoord, str, float, float, int]: 
    """Kullanıcıdan hedef adı veya RA/Dec koordinatları ile görüntü ayarlarını alır."""
    print("=== SPHEREx Zaman Serisi GIF Oluşturucu ===")
    print("Hedef adı (örn: R Draconis) veya RA koordinatı girin.")
    first_input = input("Hedef Adı veya RA (örn: 08h55m10.831s veya 133.7951): ").strip()

    if not first_input:
        raise ValueError("Boş giriş yaptınız. Lütfen bir hedef adı veya RA değeri girin.")

    is_coordinate = bool(re.match(r"^[0-9+\-.:hmsd\s]+$", first_input, re.IGNORECASE)) #“Yazılan şey sadece rakam, + - . :, h m s d harfleri ve boşluktan mı oluşuyor?” diye bakıyor. 08h55m10.831s ya da 133.79 gibi bir şeyse koordinattır. R Draconis gibi başka harfler içeriyorsa isimdir.

    if is_coordinate:
        dec_input = input("Dec koordinatı (örn: -07d14m42.53s veya -7.2451): ").strip()
        try:
            ra_val = float(first_input)
            dec_val = float(dec_input)
            coord = SkyCoord(ra=ra_val * u.deg, dec=dec_val * u.deg, frame="icrs")
        except ValueError:
            coord = SkyCoord(first_input, dec_input, unit=(u.hourangle, u.deg), frame="icrs")
        safe_name = f"ra{coord.ra.deg:.2f}_dec{coord.dec.deg:.2f}"
    else:
        print(f"'{first_input}' koordinatları astronomi kataloğundan çözümleniyor...")
        coord = SkyCoord.from_name(first_input)
        safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", first_input.lower()).strip("_")

    fov_raw = input("Görüş alanı / FOV yay-dakikası (varsayılan 4.0 için Enter): ").strip()
    fov_arcmin = float(fov_raw) if fov_raw else 4.0

    band_raw = input("Dalga boyu mikrometre (0.75 - 5.0 arası, varsayılan 2.5 için Enter): ").strip()
    band_um = float(band_raw) if band_raw else 2.5

    frames_raw = input("Maksimum kare sayısı (varsayılan 15 için Enter): ").strip()
    max_frames = int(frames_raw) if frames_raw else 15

    output_gif = f"spherex_{safe_name}.gif"
    return coord, output_gif, fov_arcmin, band_um, max_frames


def find_spherex_table() -> tuple[str, bool]:
    """IRSA TAP_SCHEMA üzerinden geçerli SPHEREx ObsCore tablosunu bulur."""
    schema_query = """
    SELECT table_name
    FROM TAP_SCHEMA.tables
    WHERE table_name LIKE '%spherex%'
       OR table_name LIKE '%SPHEREx%'
       OR table_name = 'ivoa.obscore'
    """
    tables = Irsa.query_tap(schema_query).to_table()
    names = [str(name) for name in tables["table_name"]]

    for candidate in names:
        if "spherex" in candidate.lower() and "obscore" in candidate.lower():
            return candidate, False

    if "ivoa.obscore" in names:
        return "ivoa.obscore", True

    raise RuntimeError(f"SPHEREx tablosu bulunamadı. Mevcut: {names}")


def resolve_fits_url(access_url: str, access_format: str) -> str | None:
    """DataLink veya doğrudan FITS bağlantısından indirilebilir URL'yi çözer."""
    if "datalink" in (access_format or "").lower():
        dl_results = pyvo.dal.adhoc.DatalinkResults.from_result_url(access_url)
        this_link = next(dl_results.bysemantics("#this"), None)
        return this_link.access_url if this_link is not None else None
    return access_url


def extract_image_hdu(hdul: fits.HDUList):
    """Multi-Extension FITS (MEF) içindeki ana 2D görüntü katmanını bulur."""
    if "IMAGE" in hdul:
        return hdul["IMAGE"]
    for hdu in hdul:
        if hdu.data is not None and hdu.data.ndim == 2:
            return hdu
    return None


def fetch_epoch_frame(  #tek bir tarih için tek bir GIF karesi üreten fonksiyon
    obs_date: str,      #karenin tarihi, ör. "2025-09-12" (etikette yazacak)
    candidates: list,   #O tarihteki aday görüntüler (merkeze en yakın 2 tanesi)
    coord: SkyCoord,    #hedefin gökyüzündeki konumu (RA,Dec)
    fov_arcmin: float,  #kesilecek karenin boyu, yay dakikası cinsinden
) -> tuple[float, Image.Image, str] | None: #başarılıysa (zaman, resim, etiket), başarısızsa None döner
    """Belirli bir gözlem tarihi için hedefi kapsayan en temiz kareyi indirip işler."""
    def fetch_epoch_frame(                      # Tek bir tarih için tek bir GIF karesi üreten fonksiyon
    obs_date: str,                          # Karenin tarihi, ör. "2025-09-12" (etikette yazacak)
    candidates: list,                       # O tarihteki aday görüntüler (merkeze en yakın 2 tanesi)
    coord: SkyCoord,                        # Hedefin gökyüzündeki konumu (RA, Dec)
    fov_arcmin: float,                      # Kesilecek karenin boyu, yay dakikası cinsinden
) -> tuple[float, Image.Image, str] | None: # Başarılıysa (zaman, resim, etiket), başarısızsa None döner
     """Belirli bir gözlem tarihi için hedefi kapsayan en temiz kareyi indirip işler."""
    for row in candidates:                  # Adayları sırayla dene, ilk başarılı olanı kullan
        try:                                # Bu aday hata verirse program çökmesin, sıradakine geçilsin
            fits_url = resolve_fits_url(row["access_url"], row["access_format"])  # DataLink ise gerçek dosya linkini bul
            if not fits_url:                # Link bulunamadıysa
                continue                    # bu adayı atla, sıradakine geç

            # Tüm dosya (~70 MB) yerine sadece hedefin çevresini iste (birkaç yüz KB)
            size_deg = fov_arcmin / 60 * 1.5  # Yay dakikasını dereceye çevir (÷60), %50 pay bırak (×1.5)
            sep = "&" if "?" in fits_url else "?"  # Linkte zaten "?" varsa ek parametre "&" ile, yoksa "?" ile başlar
            cutout_url = f"{fits_url}{sep}center={coord.ra.deg},{coord.dec.deg}d&size={size_deg}"  # IRSA'ya "sadece bu noktanın çevresini kes" diyen link
            print(f"  indiriliyor: {row['obs_id']}")  # Ekrana hangi gözlemin indirildiğini yaz (donma sanılmasın)

            resp = requests.get(cutout_url, timeout=60)  # Kesilmiş parçayı indir; 60 sn hiç veri gelmezse vazgeç
            resp.raise_for_status()         # Sunucu hata döndürdüyse (404, 500...) bunu bir hataya çevir → except'e düşer

            with fits.open(io.BytesIO(resp.content)) as hdul:  # İnen veriyi diske yazmadan bellekte FITS olarak aç
                img_hdu = extract_image_hdu(hdul)  # FITS'in içinden asıl resim katmanını (IMAGE) çıkar
                if img_hdu is None:         # Resim katmanı yoksa
                    continue                # bu adayı atla

                wcs = WCS(img_hdu.header)   # Resmin "pikselden gökyüzüne" haritasını oku
                if not wcs.footprint_contains(coord):  # Hedef bu resmin sınırları içinde değilse
                    continue                # bu adayı atla

                cutout = Cutout2D(          # Resimden hedefi ortalayan bir kare kes
                    img_hdu.data,           # Kesilecek resmin piksel verisi
                    position=coord,         # Karenin merkezi: hedefin konumu
                    size=fov_arcmin * u.arcmin,  # Karenin boyu (ör. 4 yay dakikası)
                    wcs=wcs,                # Koordinatı piksele çevirmek için harita
                    mode="partial",         # Hedef kenardaysa da kes, taşan kısmı doldur
                    fill_value=np.nan,      # Taşan (resim dışı) kısımları "boş" (NaN) ile doldur
                )

                data = cutout.data          # Kesilen karenin piksel değerleri
                if np.isnan(data).mean() > 0.4:  # Karenin %40'tan fazlası boşsa (hedef resmin kenarındaysa)
                    continue                # bu kare işe yaramaz, atla

                median_val = np.nanmedian(data)  # Boş olmayan piksellerin ortanca değeri (tipik arka plan)
                clean_data = np.nan_to_num(data, nan=median_val)  # Boş pikselleri bu değerle doldur (siyah delik olmasın)

                norm = ImageNormalize(      # Ham veriyi göze uygun 0-1 aralığına çevirecek ayar
                    clean_data,             # Ayarın hesaplanacağı veri
                    interval=ZScaleInterval(),  # Arka plana göre akıllıca siyah ve beyaz noktası seç
                    stretch=AsinhStretch(), # Sönük şeyleri parlat, çok parlakları bastır
                )
                scaled = np.clip(norm(clean_data), 0, 1) * 255.0  # 0-1 dışını kırp, 0-255 piksel değerine çevir
                uint8_img = np.flipud(scaled.astype(np.uint8))  # Tam sayıya çevir ve dikey çevir (FITS'te y yukarı, PNG'de aşağı)

                pil_img = Image.fromarray(uint8_img).convert("RGB")  # Sayı tablosunu renkli (RGB) bir resme dönüştür
                pil_img = pil_img.resize((400, 400), resample=Image.Resampling.NEAREST)  # 400x400'e büyüt, pikselleri bulanıklaştırmadan

                wavelength = float(row["em_min"]) * 1e6  # Dalga boyunu metreden mikrona çevir
                label = f"{obs_date} | {wavelength:.2f} um"  # Karenin üstüne yazılacak metin: tarih | dalga boyu

                draw = ImageDraw.Draw(pil_img)  # Resmin üstüne çizim yapmak için "kalem" al
                draw.rectangle([(5, 5), (235, 32)], fill=(0, 0, 0))  # Sol üste siyah bir kutu çiz (yazı okunsun diye)
                draw.text((10, 12), label, fill=(255, 230, 100))  # Kutunun içine sarı renkte etiketi yaz

                return float(row["t_min"]), pil_img, label  # Başarılı: (MJD zamanı, resim, etiket) döndür

        except Exception as e:              # try içinde herhangi bir hata olursa
            print(f"  atlandı ({row['obs_id']}): {e}")  # hangi gözlemin neden atlandığını ekrana yaz
            continue                        # ve sıradaki adaya geç

    return None                             # Hiçbir aday işe yaramadıysa "bu tarih için kare yok" de


def create_spherex_gif(
    coord: SkyCoord,
    output_gif: str,
    fov_arcmin: float = 4.0,
    band_um: float = 2.5,
    band_tol: float = 0.25,
    max_frames: int = 15,
):
    ra_deg, dec_deg = coord.ra.deg, coord.dec.deg

    table_name, use_collection_filter = find_spherex_table()
    min_m = (band_um - band_tol) * 1e-6
    max_m = (band_um + band_tol) * 1e-6
    collection_clause = (
        "AND obs_collection LIKE '%SPHEREx%'" if use_collection_filter else ""
    )

    adql_query = f"""
    SELECT TOP 1500
        obs_id, t_min, em_min, s_ra, s_dec, access_url, access_format
    FROM {table_name}
    WHERE CONTAINS(POINT('ICRS', s_ra, s_dec), CIRCLE('ICRS', {ra_deg:.6f}, {dec_deg:.6f}, 2.3)) = 1
      AND dataproduct_type = 'image'
      AND em_min >= {min_m:.9f}
      AND em_min <= {max_m:.9f}
      {collection_clause}
    """

    print(f"\nIRSA TAP ({table_name}) sorgulanıyor: RA={ra_deg:.4f}°, Dec={dec_deg:.4f}°...")
    table = Irsa.query_tap(adql_query).to_table()

    if len(table) == 0:
        print("Belirtilen koordinat ve bant aralığında SPHEREx verisi bulunamadı.")
        return

    obs_coords = SkyCoord(ra=table["s_ra"], dec=table["s_dec"], unit="deg")
    table["sep_deg"] = obs_coords.separation(coord).deg
    table["obs_date"] = [t[:10] for t in Time(table["t_min"], format="mjd").iso]

    grouped_by_date: dict[str, list] = {}
    for row in table:
        grouped_by_date.setdefault(row["obs_date"], []).append(row)

    for obs_date in grouped_by_date:
        grouped_by_date[obs_date].sort(key=lambda r: r["sep_deg"])
        grouped_by_date[obs_date] = grouped_by_date[obs_date][:2]

    sorted_dates = sorted(grouped_by_date.keys())
    print(f"{len(table)} ham poz -> {len(sorted_dates)} aday tarih bulundu. Hedef: {max_frames} geçerli kare...")

    valid_results = []
    tested_dates = set()

    # İstenen kare sayısına ulaşana kadar kalan tarih havuzundan tamamlama yap
    with ThreadPoolExecutor(max_workers=6) as executor:
        while len(valid_results) < max_frames and len(tested_dates) < len(sorted_dates):
            remaining_dates = [d for d in sorted_dates if d not in tested_dates]
            needed = max_frames - len(valid_results)
            # Kenara denk gelip elenebilecek kareleri telafi etmek için +5 yedekli örnekle
            batch_size = min(len(remaining_dates), needed + 5)

            indices = np.linspace(0, len(remaining_dates) - 1, batch_size, dtype=int)
            batch_dates = [remaining_dates[i] for i in indices]
            tested_dates.update(batch_dates)

            futures = [
                executor.submit(
                    fetch_epoch_frame,
                    obs_date,
                    grouped_by_date[obs_date],
                    coord,
                    fov_arcmin,
                )
                for obs_date in batch_dates
            ]
            for future in futures:
                res = future.result()
                if res is not None:
                    valid_results.append(res)

    # Kronolojik sırala ve yedeklerle birlikte fazladan kare geldiyse tam max_frames sayısına sabitle
    valid_results.sort(key=lambda item: item[0])
    if len(valid_results) > max_frames:
        keep_indices = np.linspace(0, len(valid_results) - 1, max_frames, dtype=int)
        valid_results = [valid_results[i] for i in keep_indices]

    for _, _, label in valid_results:
        print(f"Kare eklendi: {label}")

    frames = [item[1] for item in valid_results]

    if len(frames) >= 2:
        frames[0].save(
            output_gif,
            save_all=True,
            append_images=frames[1:],
            duration=550,
            loop=0,
        )
        print(f"\nTamamlandı! Kronolojik {len(frames)} karelik GIF kaydedildi: {output_gif}")
    else:
        print(f"\nYeterli farklı tarih bulunamadı (bulunan: {len(frames)}).")


if __name__ == "__main__":
    hedef_coord, dosya_adi, fov, bant, maks_kare = parse_user_target()
    create_spherex_gif(
        coord=hedef_coord,
        output_gif=dosya_adi,
        fov_arcmin=fov,
        band_um=bant,
        max_frames=maks_kare,
    )