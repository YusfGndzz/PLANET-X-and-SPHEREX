"""SPHEREx Zaman Makinesi veri hattı.

Akış:
  1. IRSA TAP (spherex.obscore) -> hedefi kapsayan, istenen dedektördeki pozlar
  2. Pozları dönemlere (epoch) ayır, her dönemden aday seç
  3. Kesitleri (cutout) indir
  4. Her kesiti ortak, kuzey-yukarı / doğu-sol bir TAN ızgarasına yeniden örnekle
  5. Arka planı çıkar, TEK bir parlaklık ölçeğiyle PNG üret, dönem yığınları + fark görüntüsü + GIF
  6. Hedefte açıklık fotometrisi -> spektrum
  7. Sonucu cache/<anahtar>/ altına yaz
"""
from __future__ import annotations

import csv
import io
import json
import logging
import math
import re
import shutil
import threading
import time
import warnings
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import requests
from astropy import log as astropy_log
from astropy import units as u
from astropy.io import fits
from astropy.stats import sigma_clipped_stats
from astropy.wcs import WCS, FITSFixedWarning
from astropy.wcs.utils import proj_plane_pixel_scales
from PIL import Image, ImageDraw, ImageFont
from reproject import reproject_interp
from scipy import ndimage
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import s3index

log = logging.getLogger("harman.pipeline")
logging.getLogger("urllib3").setLevel(logging.ERROR)
astropy_log.setLevel("WARNING")  # 'W' WCS'i okunurken basılan uzun SIP bilgi mesajını sustur

# ----------------------------------------------------------------------------
# Sabitler
# ----------------------------------------------------------------------------
TAP_URL = "https://irsa.ipac.caltech.edu/TAP/sync"
TAP_TIMEOUT = (15, 120)
DOWNLOAD_TIMEOUT = (15, 60)
USER_AGENT = "spherex-time-machine/1.0 (NASA Space Apps)"

OUT_PX = 400  # bütün PNG'lerin kenar uzunluğu (piksel)
NATIVE_SCALE_ARCSEC = 6.15  # SPHEREx piksel ölçeği
EPOCH_GAP_DAYS = 30.0  # bundan uzun boşluk yeni dönem başlatır
OVERSAMPLE = 3  # istenen kare sayısının kaç katı aday indirilsin (dalga boyuna göre seçim + spektrum için)
MAX_WORKERS = 3  # IRSA'ya aynı anda en fazla kaç indirme
S3_WORKERS = 6  # S3 yedek yolunda aynı anda kaç poz okunur
REQUEST_PAUSE_S = 0.25  # istekler arası kısa bekleme (IRSA'ya nazik olalım)
MIN_COVERAGE = 0.40  # karenin en az bu kadarı veriyle dolu olmalı
GIF_FRAME_MS = 550
FLUX_UNIT = "MJy/sr (aperture sum)"

# Dedektörlerin nominal dalga boyu aralıkları (µm). Sorgu asıl sınırları obscore'daki
# em_min/em_max sütunlarından alır; bu tablo yalnızca yedek ve doğrulama içindir.
DETECTOR_BANDS = {
    "D1": (0.75, 1.11),
    "D2": (1.10, 1.63),
    "D3": (1.62, 2.42),
    "D4": (2.42, 3.82),
    "D5": (3.81, 4.42),
    "D6": (4.41, 5.01),
}
BAND_MIN_UM, BAND_MAX_UM = 0.75, 5.0

# FLAGS uzantısında "bu piksel kullanılamaz" anlamına gelen bitler (ad -> bit no).
# Adlar FITS başlığındaki MSKNxxxx anahtarlarından okunur; bu tablo yedektir.
# SOURCE (21) bilerek yok: o bit yıldızların kendisini işaretler.
# OVERFLOW/NONLINEAR da yok: parlak yıldız çekirdeklerini delik gibi göstermemek için
# görüntüde maskelemiyoruz, ama fotometride "doymuş" diye raporluyoruz.
# HARD: değer tamamen güvenilmez. SOFT: kozmik ışın vb. algılanmış; değer çoğunlukla makul ama
# gerçek dosyalarda bu piksellerin ~%25'i 5 sigmadan fazla sapıyor. Görüntüde ikisi de maskelenir
# (videoda sahte parlamalar olmasın); fotometride yalnızca HARD olanlar ölçümü geçersiz kılar.
HARD_FLAG_NAMES = {"NONFUNC": 6, "MISSING_DATA": 9, "HOT": 10, "COLD": 11}
SOFT_FLAG_NAMES = {"TRANSIENT": 0, "SUR_ERROR": 2}
BAD_FLAG_NAMES = {**HARD_FLAG_NAMES, **SOFT_FLAG_NAMES}
SATURATION_FLAG_NAMES = {"OVERFLOW": 1, "NONLINEAR": 15}

_irsa_slots = threading.Semaphore(MAX_WORKERS)  # bütün istekler arasında paylaşılan IRSA sınırı
_key_locks: dict[str, threading.Lock] = {}
_key_locks_guard = threading.Lock()


class PipelineError(Exception):
    """Kullanıcıya gösterilebilecek (Türkçe) hata mesajı taşır."""


# ----------------------------------------------------------------------------
# Küçük yardımcılar
# ----------------------------------------------------------------------------
def create_http_session() -> requests.Session:
    """Geçici sunucu hatalarında otomatik yeniden deneyen HTTP oturumu."""
    session = requests.Session()
    retry = Retry(
        total=4,
        connect=2,  # sunucu tamamen kapalıysa kullanıcıyı uzun süre bekletme
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=S3_WORKERS + 2)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers["User-Agent"] = USER_AGENT
    return session


def mjd_to_date(mjd: float) -> str:
    """MJD -> 'YYYY-AA-GG' (MJD 0 = 17 Kasım 1858)."""
    return (datetime(1858, 11, 17) + timedelta(days=float(mjd))).strftime("%Y-%m-%d")


def point_in_s_region(ra_deg: float, dec_deg: float, s_region: str) -> bool:
    """Hedef, s_region çokgeninin içinde mi? (ADQL tarafı desteklemezse yerel yedek test)"""
    if not s_region:
        return True
    nums = [float(x) for x in re.findall(r"[-+]?\d*\.\d+(?:[eE][-+]?\d+)?|[-+]?\d+", str(s_region))]
    if len(nums) < 6 or len(nums) % 2 != 0:
        return True
    ra_v = np.deg2rad(nums[0::2])
    dec_v = np.deg2rad(nums[1::2])
    ra0, dec0 = np.deg2rad(ra_deg), np.deg2rad(dec_deg)
    cos_c = np.sin(dec0) * np.sin(dec_v) + np.cos(dec0) * np.cos(dec_v) * np.cos(ra_v - ra0)
    if np.any(cos_c <= 0):
        return False
    # Köşeleri hedef merkezli teğet düzleme izdüşür; hedef (0,0) olur
    x = (np.cos(dec_v) * np.sin(ra_v - ra0)) / cos_c
    y = (np.cos(dec0) * np.sin(dec_v) - np.sin(dec0) * np.cos(dec_v) * np.cos(ra_v - ra0)) / cos_c
    inside = False
    j = len(x) - 1
    for i in range(len(x)):
        if ((y[i] > 0) != (y[j] > 0)) and (0 < (x[j] - x[i]) * (-y[i]) / (y[j] - y[i] + 1e-15) + x[i]):
            inside = not inside
        j = i
    return inside


def cache_key(ra: float, dec: float, fov: float, band: float, frames: int) -> str:
    """Önbellek klasör adı: yuvarlanmış parametrelerden."""
    sign = "p" if dec >= 0 else "m"
    return f"ra{ra:.4f}_dec{sign}{abs(dec):.4f}_fov{fov:.1f}_b{band:.2f}_n{frames}".replace(".", "d")


def detector_for_band(band_um: float) -> str | None:
    """Nominal tabloya göre dalga boyunu içeren dedektör (çakışmada dalga boyu kenara en uzak olan)."""
    best, best_margin = None, -1.0
    for name, (lo, hi) in DETECTOR_BANDS.items():
        if lo <= band_um <= hi:
            margin = min(band_um - lo, hi - band_um)
            if margin > best_margin:
                best, best_margin = name, margin
    return best


# ----------------------------------------------------------------------------
# 1) IRSA sorgusu
# ----------------------------------------------------------------------------
_COLUMNS = "obs_id, obs_collection, t_min, em_min, em_max, s_ra, s_dec, access_url, access_format"


def _tap_csv(session: requests.Session, adql: str) -> list[dict] | None:
    """ADQL sorgusunu çalıştırır; satır listesi döner. Sorgu reddedilirse None (yedek sorguya geçilir)."""
    resp = session.post(
        TAP_URL,
        data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "MAXREC": "200000", "QUERY": adql},
        timeout=TAP_TIMEOUT,
    )
    text = resp.text
    first_line = text.lstrip().splitlines()[0] if text.strip() else ""
    if resp.status_code != 200 or "obs_id" not in first_line:
        log.warning("TAP sorgusu reddedildi (HTTP %s): %.400s", resp.status_code, text)
        return None
    return list(csv.DictReader(io.StringIO(text)))


def query_exposures(session: requests.Session, ra: float, dec: float, band_um: float) -> tuple[list[dict], str]:
    """Hedefi kapsayan ve dalga boyu aralığı `band_um`'u içeren pozları (zamana göre sıralı) döndürür.

    Dönüş: (satırlar, dedektör adı). Aynı pozun birden çok sürümü varsa en yenisi tutulur.
    """
    band_m = band_um * 1e-6
    common = (
        f"dataproduct_type = 'image' AND em_min <= {band_m:.10e} AND em_max >= {band_m:.10e}"
    )
    # A) Kapsama testini sunucuda yap: satır sınırı (TOP) yok, yeni gözlemler kaybolmaz
    adql_a = (
        f"SELECT {_COLUMNS} FROM spherex.obscore "
        f"WHERE CONTAINS(POINT('ICRS', {ra:.6f}, {dec:.6f}), s_region) = 1 AND {common} ORDER BY t_min"
    )
    # B) Yedek: görüntü merkezine göre daire (dedektör yarı köşegeni ~2.5 derece) + yerel çokgen testi
    adql_b = (
        f"SELECT {_COLUMNS}, s_region FROM spherex.obscore "
        f"WHERE CONTAINS(POINT('ICRS', s_ra, s_dec), CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, 2.6)) = 1 "
        f"AND {common} ORDER BY t_min"
    )
    # C) Son yedek: yalnızca eski betiğin kullandığı (var olduğu kesin) sütunlar; em_max nominal tablodan
    adql_c = (
        "SELECT obs_id, t_min, em_min, s_ra, s_dec, s_region, access_url, access_format FROM spherex.obscore "
        f"WHERE CONTAINS(POINT('ICRS', s_ra, s_dec), CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, 2.6)) = 1 "
        f"AND dataproduct_type = 'image' AND em_min <= {band_m:.10e} ORDER BY t_min"
    )
    try:
        with _irsa_slots:
            raw = _tap_csv(session, adql_a)
            if raw is None:
                log.info("s_region üzerinde CONTAINS çalışmadı; daire + yerel çokgen testine geçiliyor")
                raw = _tap_csv(session, adql_b)
                if raw is None:
                    log.info("Tam sütunlu sorgu çalışmadı; asgari sütunlarla deneniyor")
                    raw = _tap_csv(session, adql_c)
                    if raw is None:
                        raise PipelineError("NASA IRSA sorguyu reddetti. Lütfen biraz sonra tekrar deneyin.")
                    raw = [r for r in raw if _fill_band_from_table(r, band_um)]
                raw = [r for r in raw if point_in_s_region(ra, dec, r.get("s_region", ""))]
    except requests.RequestException as exc:
        log.error("IRSA TAP isteği başarısız: %s", exc)
        raise PipelineError(
            "NASA IRSA arşivine ulaşılamadı (sunucu yanıt vermiyor olabilir). Lütfen biraz sonra tekrar deneyin."
        ) from exc

    rows = []
    for r in raw:
        try:
            em_min_um = float(r["em_min"]) * 1e6
            em_max_um = float(r["em_max"]) * 1e6
            rows.append({
                "obs_id": r["obs_id"],
                "collection": r.get("obs_collection", "") or "",
                "t_min": float(r["t_min"]),
                "em_min_um": em_min_um,
                "em_max_um": em_max_um,
                "access_url": r["access_url"],
                "access_format": r.get("access_format", "") or "",
            })
        except (KeyError, ValueError):
            log.debug("Bozuk obscore satırı atlandı: %r", r)
    if not rows:
        return [], detector_for_band(band_um) or "?"

    # Dedektör sınırlarında (ör. 2.42 µm) iki dedektör birden eşleşebilir: dalga boyu aralığına göre grupla,
    # istenen dalga boyunun kenara en uzak olduğu grubu seç.
    groups: dict[tuple[float, float], list[dict]] = {}
    for row in rows:
        groups.setdefault((round(row["em_min_um"], 2), round(row["em_max_um"], 2)), []).append(row)
    (lo, hi), chosen = max(groups.items(), key=lambda kv: (min(band_um - kv[0][0], kv[0][1] - band_um), len(kv[1])))
    detector = _detector_name(chosen[0], lo, hi)
    log.info("obscore: %d satır, %d bant grubu; seçilen %s (%.2f-%.2f µm, %d poz)",
             len(rows), len(groups), detector, lo, hi, len(chosen))

    # Aynı pozun farklı veri sürümleri (ör. QR ve DR) varsa en iyisini tut
    best: dict[int, dict] = {}
    for row in chosen:
        k = int(round(row["t_min"] * 2000))  # ~43 sn çözünürlük; pozlar ~2 dk arayla
        if k not in best or _collection_rank(row["collection"]) >= _collection_rank(best[k]["collection"]):
            best[k] = row
    result = sorted(best.values(), key=lambda x: x["t_min"])
    for row in result:
        row["detector"] = detector
    return result, detector


def _fill_band_from_table(raw_row: dict, band_um: float) -> bool:
    """em_max sütunu yoksa: em_min'e en yakın nominal dedektör aralığından tamamlar; bant uyuyorsa True."""
    try:
        em_min_um = float(raw_row["em_min"]) * 1e6
    except (KeyError, ValueError):
        return False
    lo, hi = min(DETECTOR_BANDS.values(), key=lambda b: abs(b[0] - em_min_um))
    if abs(lo - em_min_um) > 0.15:
        return False
    raw_row["em_max"] = str(hi * 1e-6)
    return em_min_um <= band_um <= hi


def _detector_name(row: dict, lo: float, hi: float) -> str:
    """Dedektör adını obs_id / URL içinden ya da dalga boyu aralığından bulur."""
    for text in (row.get("obs_id", ""), row.get("access_url", "")):
        m = re.search(r"\dD([1-6])(?:\D|$)", text)
        if m:
            return f"D{m.group(1)}"
    mid = 0.5 * (lo + hi)
    for name, (a, b) in DETECTOR_BANDS.items():
        if a <= mid <= b:
            return name
    return "?"


def _collection_rank(collection: str) -> tuple[int, int]:
    """Veri sürümü önceliği: DR > QR, aynı türde büyük numara daha yeni."""
    c = collection.lower()
    num = int(m.group(1)) if (m := re.search(r"(\d+)", c)) else 0
    return (1 if "dr" in c else 0, num)


# ----------------------------------------------------------------------------
# 2) Dönemler ve aday seçimi
# ----------------------------------------------------------------------------
def group_epochs(rows: list[dict]) -> list[list[dict]]:
    """Zamana göre sıralı pozları, aralarında EPOCH_GAP_DAYS'ten uzun boşluk olan dönemlere böler."""
    epochs: list[list[dict]] = []
    for row in rows:
        if epochs and row["t_min"] - epochs[-1][-1]["t_min"] <= EPOCH_GAP_DAYS:
            epochs[-1].append(row)
        else:
            epochs.append([row])
    return epochs


def epoch_quotas(sizes: list[int], frames: int) -> list[int]:
    """`frames` kareyi dönemlere dağıtır; mümkünse her dönem en az 1 kare alır."""
    n = len(sizes)
    quotas = [0] * n
    if n == 0:
        return quotas
    if n >= frames:
        # Dönem sayısı kareden fazla: ilk ve son dahil eşit aralıklı dönemler
        for i in sorted(set(np.linspace(0, n - 1, frames).round().astype(int).tolist())):
            quotas[i] = 1
        return quotas
    remaining = frames
    while remaining > 0:
        progressed = False
        for i in range(n):
            if remaining > 0 and quotas[i] < sizes[i]:
                quotas[i] += 1
                remaining -= 1
                progressed = True
        if not progressed:
            break
    return quotas


def pick_candidates(epochs: list[list[dict]], quotas: list[int], frames: int) -> list[dict]:
    """Her dönemden, zamana eşit yayılmış adaylar seçer (kotadan fazla: dalga boyu seçimi ve yedek için)."""
    max_total = min(frames * OVERSAMPLE, max(frames + 8, 48))
    scale = min(OVERSAMPLE, max_total / max(1, sum(quotas)))
    picked = []
    for e_idx, (rows, quota) in enumerate(zip(epochs, quotas)):
        if quota == 0:
            continue
        want = min(len(rows), max(quota + 1, int(math.ceil(quota * scale))))
        idx = sorted(set(np.linspace(0, len(rows) - 1, want).round().astype(int).tolist()))
        for i in idx:
            row = dict(rows[i])
            row["epoch_raw"] = e_idx
            picked.append(row)
    return picked


# ----------------------------------------------------------------------------
# 3) İndirme
# ----------------------------------------------------------------------------
def resolve_fits_url(access_url: str, access_format: str, session: requests.Session) -> str | None:
    """obscore'daki bağlantı datalink ise, içinden asıl FITS dosyasının (#this) adresini bulur."""
    if "datalink" not in (access_format or "").lower():
        return access_url
    resp = session.get(access_url, timeout=DOWNLOAD_TIMEOUT)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    fields = [el.attrib.get("name", "") for el in root.iter() if el.tag.endswith("FIELD")]
    if "access_url" not in fields or "semantics" not in fields:
        return None
    url_idx, sem_idx = fields.index("access_url"), fields.index("semantics")
    for tr in root.iter():
        if tr.tag.endswith("TR"):
            tds = [td.text or "" for td in tr if td.tag.endswith("TD")]
            if len(tds) > max(url_idx, sem_idx) and "#this" in tds[sem_idx]:
                return tds[url_idx].strip()
    return None


def fetch_cutout(row: dict, ra: float, dec: float, size_deg: float, session: requests.Session) -> bytes | None:
    """Bir poz için IRSA kesit servisinden hedef çevresindeki FITS kesitini indirir."""
    with _irsa_slots:
        time.sleep(REQUEST_PAUSE_S)
        fits_url = resolve_fits_url(row["access_url"], row["access_format"], session)
        if not fits_url:
            log.warning("Datalink içinde FITS adresi bulunamadı: %s", row["obs_id"])
            return None
        sep = "&" if "?" in fits_url else "?"
        last_status = None
        # Önce geniş kesit (döndürme sonrası köşeler dolsun), olmazsa küçüğü; iki yazım biçimi de denenir
        for size in (size_deg, size_deg / 1.5):
            for center in (f"{ra:.6f},{dec:.6f}", f"{ra:.6f},{dec:.6f}d"):
                resp = session.get(f"{fits_url}{sep}center={center}&size={size:.6f}", timeout=DOWNLOAD_TIMEOUT)
                last_status = resp.status_code
                if resp.status_code in (400, 422):
                    continue
                resp.raise_for_status()
                return resp.content
        log.warning("Kesit alınamadı (HTTP %s): %s", last_status, row["obs_id"])
        return None


# ----------------------------------------------------------------------------
# 4) Tek bir pozun işlenmesi
# ----------------------------------------------------------------------------
@dataclass
class Grid:
    """Bütün karelerin yeniden örneklendiği ortak ızgara (kuzey yukarı, doğu solda)."""
    n: int
    scale_arcsec: float
    wcs: WCS
    fov_arcmin: float


def make_grid(ra: float, dec: float, fov_arcmin: float) -> Grid:
    # n, OUT_PX'i tam bölsün ki NEAREST büyütmede pikseller eşit boyda olsun; hedef ölçek <= ~3.5"/px
    n = OUT_PX
    for cand in (40, 50, 80, 100, 200, 400):
        if fov_arcmin * 60.0 / cand <= 3.5:
            n = cand
            break
    scale = fov_arcmin * 60.0 / n
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [ra, dec]
    w.wcs.crpix = [(n + 1) / 2.0, (n + 1) / 2.0]
    w.wcs.cdelt = [-scale / 3600.0, scale / 3600.0]  # x arttıkça RA azalır -> doğu solda
    w.wcs.cunit = ["deg", "deg"]
    return Grid(n=n, scale_arcsec=scale, wcs=w, fov_arcmin=fov_arcmin)


@dataclass
class Processed:
    obs_id: str
    mjd: float
    date: str
    detector: str
    wavelength_um: float
    bandwidth_um: float | None
    wavelength_approx: bool
    image: np.ndarray  # ortak ızgarada, arka planı çıkarılmış (satır 0 = güney)
    noise: float
    flagged_fraction: float
    flux: float | None
    flux_err: float | None
    saturated: bool
    phot_flagged: bool = False  # açıklıkta kozmik ışın vb. işaretli piksel var (ölçüm daha az güvenilir)
    epoch_raw: int = 0
    epoch: int = 0


def _find_hdu(hdul: fits.HDUList, name: str):
    """Adı `name` ile başlayan ilk uzantı (kesit servisi adlara ek koyarsa da bulunur)."""
    for hdu in hdul:
        if str(hdu.name).upper() == name:
            return hdu
    for hdu in hdul:
        if str(hdu.name).upper().startswith(name):
            return hdu
    return None


def _flag_mask_bits(header: fits.Header, wanted: dict[str, int]) -> int:
    """FLAGS başlığındaki MSKNxxxx adlarından bit maskesi üretir; ad bulunamazsa yedek bit no kullanılır."""
    found: dict[str, int] = {}
    for key in header:
        if key.startswith("MSKN"):
            try:
                found[str(header[key]).strip().upper()] = int(key[4:])
            except ValueError:
                continue
    mask = 0
    for name, default_bit in wanted.items():
        mask |= 1 << found.get(name, default_bit)
    return mask


def _neighbor_stat(arr: np.ndarray, func=np.nanmedian) -> np.ndarray:
    """Her piksel için 8 komşusunun (NaN'lar hariç) ortancası (ya da verilen başka istatistiği)."""
    p = np.pad(arr, 1, constant_values=np.nan)
    neigh = np.stack([
        p[1 + dy: p.shape[0] - 1 + dy, 1 + dx: p.shape[1] - 1 + dx]
        for dy in (-1, 0, 1) for dx in (-1, 0, 1) if (dy, dx) != (0, 0)
    ])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return func(neigh, axis=0)


def _mask_negative_outliers(arr: np.ndarray) -> np.ndarray:
    """FLAGS'te işaretlenmemiş 'soğuk' pikselleri bulur: 8 komşusunun HEPSİNDEN 6 sigma daha karanlık tekil pikseller.

    Yalnızca negatif yönde bakılır; pozitif sivri uçlar yıldız olabilir. Komşuların en küçüğüyle
    karşılaştırmak şart: ortancayla karşılaştırılırsa parlak yıldızların dik etekleri de maskelenir.
    """
    finite = np.isfinite(arr)
    if finite.sum() < 50:
        return np.zeros(arr.shape, dtype=bool)
    _, _, noise = sigma_clipped_stats(arr[finite], sigma=3.0, maxiters=5)
    if not np.isfinite(noise) or noise <= 0:
        return np.zeros(arr.shape, dtype=bool)
    return finite & (arr < _neighbor_stat(arr, np.nanmin) - 6.0 * noise)


def _fill_small_holes(arr: np.ndarray, max_hole_px: int = 600, passes: int = 16) -> np.ndarray:
    """Küçük NaN adacıklarını (maskelenmiş kötü pikseller, doymuş yıldız çekirdeği) komşu ortancasıyla doldurur.

    Büyük boşluklara ve görüntü kenarına değen bölgelere dokunulmaz (orada gerçekten veri yok).
    """
    holes = ~np.isfinite(arr)
    if not holes.any() or holes.all():
        return arr
    labels, count = ndimage.label(holes)
    if count == 0:
        return arr
    sizes = ndimage.sum(holes, labels, index=np.arange(1, count + 1))
    edge_labels = set(np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])).tolist())
    fillable_ids = [i + 1 for i, s in enumerate(sizes) if s <= max_hole_px and (i + 1) not in edge_labels]
    fillable = np.isin(labels, fillable_ids)
    out = arr
    for _ in range(passes):
        todo = fillable & ~np.isfinite(out)
        if not todo.any():
            break
        out = np.where(todo, _neighbor_stat(out), out)
    return out


def aperture_photometry(data: np.ndarray, var: np.ndarray | None, x: float, y: float,
                        r_ap: float = 2.0, r_in: float = 5.0, r_out: float = 8.0):
    """Basit açıklık fotometrisi: (x, y) çevresinde r_ap yarıçaplı daire toplamı - halka ortancası.

    Dönüş: (akı, hata) ya da açıklıkta kötü piksel / yetersiz veri varsa None.
    """
    ny, nx = data.shape
    ix, iy = int(round(x)), int(round(y))
    half = int(math.ceil(r_out)) + 1
    if ix - half < 0 or iy - half < 0 or ix + half >= nx or iy + half >= ny:
        return None
    yy, xx = np.mgrid[iy - half: iy + half + 1, ix - half: ix + half + 1]
    sub = data[iy - half: iy + half + 1, ix - half: ix + half + 1]
    rr = np.hypot(xx - x, yy - y)
    annulus = (rr >= r_in) & (rr <= r_out) & np.isfinite(sub)
    if annulus.sum() < 20:
        return None
    _, bg, bg_std = sigma_clipped_stats(sub[annulus], sigma=3.0, maxiters=5)

    # Kenar pikselleri için 5x5 alt-piksel ağırlığı
    weights = np.zeros(sub.shape, dtype=float)
    offsets = (np.arange(5) + 0.5) / 5.0 - 0.5
    for oy in offsets:
        for ox in offsets:
            weights += np.hypot(xx + ox - x, yy + oy - y) <= r_ap
    weights /= 25.0
    in_ap = weights > 0
    if not np.all(np.isfinite(sub[in_ap])):
        return None
    flux = float(np.sum((sub[in_ap] - bg) * weights[in_ap]))
    n_ap = float(weights.sum())
    bg_term = (n_ap ** 2) * (bg_std ** 2) / float(annulus.sum())
    err = None
    if var is not None:
        vsub = var[iy - half: iy + half + 1, ix - half: ix + half + 1]
        if np.all(np.isfinite(vsub[in_ap])) and np.all(vsub[in_ap] >= 0):
            err = float(np.sqrt(np.sum(vsub[in_ap] * weights[in_ap] ** 2) + bg_term))
    if err is None:
        err = float(np.sqrt(n_ap * bg_std ** 2 + bg_term))
    if not np.isfinite(flux):
        return None
    return flux, (err if np.isfinite(err) else None)


def _table_wavelength(hdul: fits.HDUList, header: fits.Header, x: float, y: float, key: str):
    """WCS-WAVE tablosundan elle çift doğrusal ara değerleme (astropy'nin 'W' WCS'i okunamazsa yedek).

    Tablonun X/Y denetim noktaları özgün dedektörün 1-tabanlı piksel koordinatlarındadır. Kesitte
    özgün koordinata dönmek için başlıktaki doğrusal dönüşüm kullanılır: key='W' (dalga boyu WCS'i)
    ya da key='A' ("0-based active pixel coordinates").
    """
    table_hdu = _find_hdu(hdul, "WCS-WAVE")
    if table_hdu is None or table_hdu.data is None:
        return None, None
    try:
        tx = np.asarray(table_hdu.data["X"][0], dtype=float)
        ty = np.asarray(table_hdu.data["Y"][0], dtype=float)
        values = np.asarray(table_hdu.data["VALUES"][0], dtype=float)  # (ny, nx, 2): dalga boyu, bant genişliği
        orig = []
        for axis, pix in ((1, x), (2, y)):
            crpix = float(header[f"CRPIX{axis}{key}"])
            crval = float(header[f"CRVAL{axis}{key}"])
            cdelt = float(header.get(f"CDELT{axis}{key}", 1.0))
            value = crval + cdelt * (pix + 1.0 - crpix)
            orig.append(value + 1.0 if key == "A" else value)  # 'A' 0-tabanlı, tablo 1-tabanlı
        ox, oy = orig
        if not (tx[0] - 1 <= ox <= tx[-1] + 1 and ty[0] - 1 <= oy <= ty[-1] + 1):
            return None, None
        # Y ekseninde tekrarlayan/bitişik düğümler olabilir; np.interp bunlarla sorunsuz çalışır
        out = []
        for k in range(2):
            along_x = np.array([np.interp(ox, tx, values[row, :, k]) for row in range(values.shape[0])])
            out.append(float(np.interp(oy, ty, along_x)))
        return out[0], out[1]
    except Exception as exc:
        log.warning("WCS-WAVE tablosu elle okunamadı (key=%s): %s", key, exc)
        return None, None


def _valid_wavelength(lam, lo, hi) -> bool:
    if lam is None or not np.isfinite(lam) or not (BAND_MIN_UM - 0.1 <= lam <= BAND_MAX_UM + 0.1):
        return False
    return lo is None or hi is None or (lo - 0.1 <= lam <= hi + 0.1)


def read_wavelength(hdul: fits.HDUList, header: fits.Header, x: float, y: float,
                    lo: float | None = None, hi: float | None = None):
    """Hedef pikselindeki gerçek dalga boyunu (µm) ve bant genişliğini okur.

    SPHEREx'te dalga boyu dedektör üzerinde konuma göre değişir (doğrusal değişken filtre).
    Level-2 dosyalarında bu, 'W' adlı ikinci WCS ile verilir (CTYPE = WAVE-TAB): WCS-WAVE
    uzantısındaki arama tablosundan çift doğrusal ara değerleme yapılır.
    Sıra: astropy 'W' WCS'i -> tablo + 'W' doğrusal terimleri -> tablo + 'A' piksel WCS'i.
    Sonuç dedektör aralığına (lo..hi) düşmüyorsa reddedilir. Hiçbiri olmazsa (None, None).
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            wave_wcs = WCS(header, fobj=hdul, key="W")
            wave_wcs.sip = None  # dalga boyu optik bozulmaya değil dedektör konumuna bağlı
            lam, bw = wave_wcs.pixel_to_world(x, y)
        lam_um = float(u.Quantity(lam).to_value(u.um))
        bw_um = float(u.Quantity(bw).to_value(u.um))
        if _valid_wavelength(lam_um, lo, hi):
            return lam_um, (bw_um if np.isfinite(bw_um) else None)
        log.warning("'W' WCS'inden gelen dalga boyu (%.3f µm) dedektör aralığı dışında", lam_um)
    except Exception as exc:
        log.warning("Dalga boyu WCS'i ('W') okunamadı: %s", exc)
    for key in ("W", "A"):
        lam_um, bw_um = _table_wavelength(hdul, header, x, y, key)
        if _valid_wavelength(lam_um, lo, hi):
            return lam_um, bw_um
    return None, None


def process_exposure(content: bytes, row: dict, ra: float, dec: float, grid: Grid) -> Processed | None:
    """İndirilen FITS'i (kesit ya da tam kare) ortak ızgaraya taşır, arka planı çıkarır, fotometri yapar."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FITSFixedWarning)
        with fits.open(io.BytesIO(content), memmap=False) as hdul:
            img_hdu = _find_hdu(hdul, "IMAGE")
            if img_hdu is None:
                img_hdu = next((h for h in hdul if getattr(h, "data", None) is not None
                                and getattr(h.data, "ndim", 0) == 2), None)
            if img_hdu is None:
                log.warning("FITS içinde görüntü yok: %s", row["obs_id"])
                return None
            header = img_hdu.header
            data = np.asarray(img_hdu.data, dtype=np.float64)
            wcs = WCS(header)  # gök WCS'i (TAN + SIP bozulması)
            ny, nx = data.shape

            px = wcs.all_world2pix([[ra, dec]], 0, quiet=True)[0]
            x, y = float(px[0]), float(px[1])
            if not (np.isfinite(x) and np.isfinite(y) and -0.5 <= x <= nx - 0.5 and -0.5 <= y <= ny - 0.5):
                log.info("Hedef görüntünün dışında: %s", row["obs_id"])
                return None

            # Yalnızca hedef çevresini kes (tam kare gelirse de hızlı kalsın)
            native_scale = float(np.mean(proj_plane_pixel_scales(wcs.celestial))) * 3600.0
            half = int(math.ceil(grid.fov_arcmin * 60.0 * 0.75 / native_scale)) + 8
            x0, x1 = max(0, int(x) - half), min(nx, int(x) + half + 1)
            y0, y1 = max(0, int(y) - half), min(ny, int(y) + half + 1)
            window = (slice(y0, y1), slice(x0, x1))
            sub = data[window].copy()
            sub_wcs = wcs[window]

            # Kötü pikseller: FLAGS uzantısı + sonlu olmayan değerler
            flag_bad = np.zeros(sub.shape, dtype=bool)
            hard_bad = np.zeros(sub.shape, dtype=bool)
            saturated = np.zeros(sub.shape, dtype=bool)
            flags_hdu = _find_hdu(hdul, "FLAGS")
            if flags_hdu is not None and flags_hdu.data is not None and flags_hdu.data.shape == data.shape:
                fl = np.asarray(flags_hdu.data[window]).astype(np.int64)
                flag_bad = (fl & _flag_mask_bits(flags_hdu.header, BAD_FLAG_NAMES)) != 0
                hard_bad = (fl & _flag_mask_bits(flags_hdu.header, HARD_FLAG_NAMES)) != 0
                saturated = (fl & _flag_mask_bits(flags_hdu.header, SATURATION_FLAG_NAMES)) != 0
            phot_data = sub.copy()  # fotometri için: yalnızca kesin kötü pikseller NaN
            sub[flag_bad | ~np.isfinite(sub)] = np.nan
            cold = _mask_negative_outliers(sub)  # FLAGS'te olmayan tekil karanlık pikseller
            sub[cold] = np.nan
            phot_data[hard_bad | cold | ~np.isfinite(phot_data)] = np.nan
            soft_bad = flag_bad & ~hard_bad
            flag_bad |= cold

            # Fotometri: özgün piksel ızgarasında (yeniden örnekleme akıyı bulandırmasın diye)
            var = None
            var_hdu = _find_hdu(hdul, "VARIANCE")
            if var_hdu is not None and var_hdu.data is not None and var_hdu.data.shape == data.shape:
                var = np.asarray(var_hdu.data[window], dtype=np.float64)
            phot = aperture_photometry(phot_data, var, x - x0, y - y0)
            sy, sx = int(round(y - y0)), int(round(x - x0))
            near = (slice(max(0, sy - 2), sy + 3), slice(max(0, sx - 2), sx + 3))
            is_saturated = bool(saturated[near].any())
            phot_flagged = bool(soft_bad[near].any())

            # Gerçek dalga boyu
            lo, hi = row.get("em_min_um"), row.get("em_max_um")
            lam, bw = read_wavelength(hdul, header, x, y, lo, hi)
            approx = False
            if lam is None:
                log.warning("Gerçek dalga boyu okunamadı (%s); yaklaşık değer kullanılıyor", row["obs_id"])
                # Yedek: dedektör aralığının orta noktası (yaklaşık!) - JSON'da wavelength_approx=true
                lam = 0.5 * (lo + hi) if lo is not None and hi is not None else float("nan")
                bw, approx = None, True

            mjd = header.get("MJD-AVG") or header.get("MJD-OBS") or row["t_min"]
            det = header.get("DETECTOR")
            detector = f"D{int(det)}" if isinstance(det, (int, np.integer)) and 1 <= int(det) <= 6 else row.get("detector", "?")

    # Ortak ızgaraya yeniden örnekle (çift doğrusal). Maske en yakın komşu ile taşınır.
    filled = _fill_small_holes(sub)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        image, footprint = reproject_interp((filled, sub_wcs), grid.wcs, shape_out=(grid.n, grid.n), order="bilinear")
        mask_r, _ = reproject_interp((flag_bad.astype(float), sub_wcs), grid.wcs,
                                     shape_out=(grid.n, grid.n), order="nearest-neighbor")
    covered = footprint > 0
    image[~covered] = np.nan
    finite = np.isfinite(image)
    if finite.mean() < MIN_COVERAGE:
        log.info("Kare yeterince dolu değil (%.0f%%): %s", 100 * finite.mean(), row["obs_id"])
        return None
    flagged_fraction = float(np.nanmean(mask_r[covered] > 0.5)) if covered.any() else 0.0

    # Kare başına arka plan (zodyak ışığı vb.): sigma-kırpmalı ortanca
    _, background, noise = sigma_clipped_stats(image[finite], sigma=3.0, maxiters=5)
    image = image - background
    if not np.isfinite(noise) or noise <= 0:
        noise = float(np.nanstd(image)) or 1.0

    return Processed(
        obs_id=row["obs_id"],
        mjd=float(mjd),
        date=mjd_to_date(float(mjd)),
        detector=detector,
        wavelength_um=float(lam),
        bandwidth_um=bw,
        wavelength_approx=approx,
        image=image,
        noise=float(noise),
        flagged_fraction=flagged_fraction,
        flux=phot[0] if phot else None,
        flux_err=phot[1] if phot else None,
        saturated=is_saturated,
        phot_flagged=phot_flagged,
        epoch_raw=row.get("epoch_raw", 0),
    )


# ----------------------------------------------------------------------------
# 5) Görselleştirme
# ----------------------------------------------------------------------------
NODATA_RGB = (24, 26, 48)  # veri olmayan bölgelerin rengi (koyu lacivert)


def _font(size: int = 14):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # eski Pillow
        return ImageFont.load_default()


def global_scale(images: list[np.ndarray], noises: list[float]) -> tuple[float, float, float]:
    """Bütün kareler için ortak (vmin, vmax, yumuşatma) değerleri."""
    sigma = float(np.median(noises)) if noises else 1.0
    if not np.isfinite(sigma) or sigma <= 0:
        sigma = 1.0
    values = np.concatenate([im[np.isfinite(im)].ravel() for im in images]) if images else np.array([0.0])
    vmin = -1.5 * sigma
    vmax = float(np.percentile(values, 99.8)) if values.size else 10 * sigma
    # Alt sınır: boş alanlarda gürültüyü abartma. Üst sınır: çok parlak (doymuş) bir yıldız ölçeği
    # ele geçirip sönük cisimleri görünmez yapmasın; o yıldız yalnızca beyaza doyar.
    vmax = min(max(vmax, vmin + 10.0 * sigma), 150.0 * sigma)
    return vmin, vmax, 2.0 * sigma


def stretch_to_rgb(image: np.ndarray, vmin: float, vmax: float, soft: float) -> Image.Image:
    """asinh germe + 8 bit; diziyi dikey çevirir (satır 0 = kuzey) ve OUT_PX'e büyütür."""
    finite = np.isfinite(image)
    clipped = np.clip(np.where(finite, image, vmin), vmin, vmax)
    norm = np.arcsinh((clipped - vmin) / soft) / np.arcsinh((vmax - vmin) / soft)
    grey = (np.clip(norm, 0, 1) * 255.0).astype(np.uint8)
    rgb = np.stack([grey, grey, grey], axis=-1)
    rgb[~finite] = NODATA_RGB
    return _upscale(Image.fromarray(np.flipud(rgb), mode="RGB"))


def diff_to_rgb(diff: np.ndarray) -> Image.Image:
    """Fark görüntüsü: simetrik gri ölçek (0 = orta gri). Hareket eden cisim siyah/beyaz çift olarak görünür."""
    finite = np.isfinite(diff)
    if finite.any():
        absval = np.abs(diff[finite])
        _, _, std = sigma_clipped_stats(diff[finite], sigma=3.0, maxiters=5)
        # Ölçek gürültüye bağlı tutulur: çok parlak (doymuş) bir yıldız ölçeği ele geçirip
        # sönük değişimleri görünmez yapmasın
        std = float(std) if np.isfinite(std) and std > 0 else float(np.std(diff[finite])) or 1e-12
        limit = min(max(float(np.percentile(absval, 99.5)), 5.0 * std), 25.0 * std)
    else:
        limit = 1.0
    norm = np.clip(np.where(finite, diff, 0.0) / limit, -1, 1)
    grey = ((norm * 0.5 + 0.5) * 255.0).astype(np.uint8)
    rgb = np.stack([grey, grey, grey], axis=-1)
    rgb[~finite] = NODATA_RGB
    return _upscale(Image.fromarray(np.flipud(rgb), mode="RGB"))


def _upscale(img: Image.Image) -> Image.Image:
    if img.size == (OUT_PX, OUT_PX):
        return img
    return img.resize((OUT_PX, OUT_PX), resample=Image.Resampling.NEAREST)


def annotate(img: Image.Image, label: str, fov_arcmin: float) -> Image.Image:
    """Etiket, hedef nişangâhı, ölçek çubuğu ve K/D (N/E) pusulası çizer."""
    draw = ImageDraw.Draw(img)
    font, small = _font(14), _font(12)
    yellow, cyan = (255, 230, 100), (90, 220, 255)

    # Etiket kutusu (sol üst)
    box = draw.textbbox((10, 8), label, font=font)
    draw.rectangle([(box[0] - 5, box[1] - 4), (box[2] + 5, box[3] + 4)], fill=(0, 0, 0))
    draw.text((10, 8), label, fill=yellow, font=font)

    # Hedef nişangâhı: merkezde, ortası boş dört çizgi
    c = OUT_PX / 2.0
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        draw.line([(c + dx * 14, c + dy * 14), (c + dx * 28, c + dy * 28)], fill=cyan, width=1)

    # Ölçek çubuğu (sol alt)
    bar_arcsec = next((b for b in (600, 300, 120, 60, 30, 10) if b <= fov_arcmin * 60.0 * 0.3), 10)
    bar_px = bar_arcsec / (fov_arcmin * 60.0) * OUT_PX
    y_bar = OUT_PX - 16
    draw.line([(12, y_bar), (12 + bar_px, y_bar)], fill=yellow, width=2)
    bar_text = f"{bar_arcsec // 60}'" if bar_arcsec >= 60 else f'{bar_arcsec}"'
    draw.text((12, y_bar - 17), bar_text, fill=yellow, font=small)

    # Pusula (sağ alt): kuzey yukarı, doğu solda
    ox, oy = OUT_PX - 22, OUT_PX - 16
    draw.line([(ox, oy), (ox, oy - 26)], fill=yellow, width=1)
    draw.line([(ox, oy), (ox - 26, oy)], fill=yellow, width=1)
    draw.polygon([(ox, oy - 30), (ox - 3, oy - 24), (ox + 3, oy - 24)], fill=yellow)
    draw.polygon([(ox - 30, oy), (ox - 24, oy - 3), (ox - 24, oy + 3)], fill=yellow)
    draw.text((ox + 5, oy - 34), "N", fill=yellow, font=small)
    draw.text((ox - 38, oy - 18), "E", fill=yellow, font=small)
    return img


def _smooth(image: np.ndarray, sigma: float = 1.2) -> np.ndarray:
    """NaN'ları koruyarak hafif Gauss yumuşatması (iki görüntünün keskinlik farkını azaltır)."""
    finite = np.isfinite(image)
    if not finite.any():
        return image
    num = ndimage.gaussian_filter(np.where(finite, image, 0.0), sigma)
    den = ndimage.gaussian_filter(finite.astype(float), sigma)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = num / den
    out[~finite | (den < 0.3)] = np.nan
    return out


def _field_stars(new: np.ndarray, old: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(ortak geçerli pikseller, hedef DIŞINDAKİ parlak yıldız pikselleri).

    Hedefin kendi ışığı (merkeze değen parlak bölgenin tamamı, halesiyle birlikte) dışarıda bırakılır:
    hizalama ve parlaklık eşitlemesi yalnızca alan yıldızlarına bakmalı, yoksa hedefin gerçek hareketini
    ya da parlaklık değişimini "düzeltip" siler.
    """
    n = new.shape[0]
    yy, xx = np.mgrid[0:n, 0:n]
    centre = np.hypot(xx - (n - 1) / 2.0, yy - (n - 1) / 2.0) < max(4.0, n * 0.09)
    ok = np.isfinite(new) & np.isfinite(old)
    if ok.sum() < 50:
        return ok, np.zeros_like(ok)
    noise = max(_robust_std(new[ok]), _robust_std(old[ok]))
    lit = ok & ((new > 6.0 * noise) | (old > 6.0 * noise))
    labels, _ = ndimage.label(ndimage.binary_dilation(lit, iterations=2))
    target_ids = set(np.unique(labels[centre])) - {0}
    target = np.isin(labels, list(target_ids)) if target_ids else np.zeros_like(lit)
    stars = ndimage.binary_dilation(lit & ~target, iterations=2) & ok & ~centre & ~target
    return ok, stars


def match_scale(new: np.ndarray, old: np.ndarray) -> float:
    """`old` görüntüsünü `new` ile aynı parlaklığa getiren çarpan.

    İki görüntü farklı dalga boylarında ya da farklı sayıda pozdan gelmiş olabilir; yıldızlar bu yüzden
    birinde daha parlaktır ve düz çıkarmada silinmez. Çarpan, hedef dışındaki alan yıldızlarından sağlam
    bir doğrusal uyumla bulunur. Alanda yeterli yıldız yoksa 1 döner (hedefe göre ölçeklemek, hedefin
    gerçek parlaklık değişimini silerdi).
    """
    _, stars = _field_stars(new, old)
    if stars.sum() < 12:
        return 1.0
    a, b = old[stars], new[stars]
    scale = float(np.sum(a * b) / max(np.sum(a * a), 1e-30))
    for _ in range(3):  # aykırı pikselleri at, yeniden uydur
        resid = b - scale * a
        keep = np.abs(resid) < 3.0 * (np.std(resid) + 1e-12)
        if keep.sum() < 8:
            break
        scale = float(np.sum(a[keep] * b[keep]) / max(np.sum(a[keep] * a[keep]), 1e-30))
    return float(min(5.0, max(0.2, scale))) if np.isfinite(scale) else 1.0


def _shift_nan(image: np.ndarray, dy: float, dx: float) -> np.ndarray:
    """NaN'ları koruyarak alt-piksel kaydırma."""
    finite = np.isfinite(image)
    data = ndimage.shift(np.where(finite, image, 0.0), (dy, dx), order=1, mode="constant", cval=0.0)
    weight = ndimage.shift(finite.astype(float), (dy, dx), order=1, mode="constant", cval=0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = data / weight
    out[weight < 0.6] = np.nan
    return out


def register_shift(new: np.ndarray, old: np.ndarray, max_px: float = 1.0, step: float = 0.125) -> tuple[float, float]:
    """`old`u `new` ile hizalayan küçük (dy, dx) kaymayı arar.

    İki dönemin gök konumu çözümü yarım piksele kadar farklı olabilir; bu, her sabit yıldızda
    siyah-beyaz bir çift bırakır. Kayma, hedef dışındaki parlak yıldızların artığını en aza indirerek
    bulunur; hedefin kendisi hesaba katılmadığı için gerçek hareketi silmez. Yeterli yıldız yoksa (0, 0).
    """
    ok, bright = _field_stars(new, old)
    if bright.sum() < 30:
        return 0.0, 0.0

    def cost(dy: float, dx: float) -> float:
        moved = _shift_nan(old, dy, dx) if (dy or dx) else old
        m = bright & np.isfinite(moved)
        if m.sum() < 20:
            return float("inf")
        a, b = moved[m], new[m]
        scale = float(np.sum(a * b) / max(np.sum(a * a), 1e-30))
        return float(np.mean((b - scale * a) ** 2))

    base = cost(0.0, 0.0)
    best, best_shift = base, (0.0, 0.0)
    offsets = np.arange(-max_px, max_px + 1e-9, step)
    for dy in offsets:
        for dx in offsets:
            c = cost(float(dy), float(dx))
            if c < best:
                best, best_shift = c, (float(dy), float(dx))
    # Kazanç küçükse ya da kayma arama sınırına dayandıysa dokunma: gerçek konum hatası pikselin altındadır,
    # sınıra dayanan bir sonuç gürültüye ya da başka bir şeye uymuş demektir
    at_edge = max(abs(best_shift[0]), abs(best_shift[1])) > max_px - step / 2
    return best_shift if best < 0.85 * base and not at_edge else (0.0, 0.0)


def make_diff(new: np.ndarray, old: np.ndarray) -> tuple[np.ndarray, float]:
    """new - çarpan * old: hafif yumuşatma, alt-piksel hizalama ve parlaklık eşitlemesinden sonra."""
    a, b = _smooth(new), _smooth(old)
    dy, dx = register_shift(a, b)
    if dy or dx:
        b = _shift_nan(b, dy, dx)
        log.info("Fark: hizalama düzeltmesi dy=%.2f dx=%.2f piksel", dy, dx)
    scale = match_scale(a, b)
    return a - scale * b, scale


def _nanmedian_stack(images: list[np.ndarray]) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmedian(np.stack(images), axis=0)


def _robust_std(image: np.ndarray) -> float:
    finite = np.isfinite(image)
    if not finite.any():
        return 1.0
    _, _, std = sigma_clipped_stats(image[finite], sigma=3.0, maxiters=5)
    return float(std) if np.isfinite(std) and std > 0 else 1.0


# ----------------------------------------------------------------------------
# 6) Seçim + çıktı üretimi
# ----------------------------------------------------------------------------
def select_frames(processed: list[Processed], quotas: dict[int, int], band_um: float, frames: int) -> list[Processed]:
    """Her dönemden kotası kadar, dalga boyu istenene en yakın kareleri seçer; boş kalan kotayı diğerlerinden doldurur."""
    def closeness(p: Processed):
        return (p.wavelength_approx, abs(p.wavelength_um - band_um) if np.isfinite(p.wavelength_um) else 99.0)

    chosen: list[Processed] = []
    leftovers: list[Processed] = []
    for epoch_raw, quota in quotas.items():
        members = sorted((p for p in processed if p.epoch_raw == epoch_raw), key=closeness)
        chosen += members[:quota]
        leftovers += members[quota:]
    # Kotası 0 olan dönemlerden gelen kare olmaz; yalnızca eksik kalanı tamamla
    missing = frames - len(chosen)
    if missing > 0:
        chosen += sorted(leftovers, key=closeness)[:missing]
    return sorted(chosen, key=lambda p: p.mjd)


def build_outputs(processed: list[Processed], archive_epochs: list[list[dict]], quotas: list[int],
                  ra: float, dec: float, grid: Grid, band_um: float, frames: int,
                  detector: str, out_dir: Path, url_prefix: str) -> dict:
    """İşlenmiş pozlardan PNG/GIF dosyalarını ve API cevabını üretir."""
    if not processed:
        raise PipelineError("Görüntüler indirilemedi ya da hedef hiçbir karede yeterince kapsanmıyor.")

    # Verisi olan dönemleri 0'dan başlayarak yeniden numarala
    raw_ids = sorted({p.epoch_raw for p in processed})
    renumber = {raw: i for i, raw in enumerate(raw_ids)}
    for p in processed:
        p.epoch = renumber[p.epoch_raw]

    selected = select_frames(processed, {i: q for i, q in enumerate(quotas) if q > 0}, band_um, frames)
    if len(selected) < 1:
        raise PipelineError("Gösterilecek kare bulunamadı.")

    # --- Kareler: tek ortak parlaklık ölçeği ---
    vmin, vmax, soft = global_scale([p.image for p in selected], [p.noise for p in selected])
    frame_entries, pil_frames = [], []
    for i, p in enumerate(selected):
        # Not: Pillow'un gömülü yazı tipinde µ harfi yok, bu yüzden resim üstünde "um" yazıyoruz
        wl_text = f"{'~' if p.wavelength_approx else ''}{p.wavelength_um:.2f} um"
        img = annotate(stretch_to_rgb(p.image, vmin, vmax, soft), f"{p.date} | {wl_text}", grid.fov_arcmin)
        name = f"frame_{i:02d}.png"
        img.save(out_dir / name)
        pil_frames.append(img)
        entry = {
            "index": i,
            "url": f"{url_prefix}/{name}",
            "date": p.date,
            "mjd": round(p.mjd, 5),
            "wavelength_um": round(p.wavelength_um, 4),
            "detector": p.detector,
            "obs_id": p.obs_id,
            "epoch": p.epoch,
            "flagged_fraction": round(p.flagged_fraction, 4),
        }
        if p.bandwidth_um is not None:
            entry["bandwidth_um"] = round(p.bandwidth_um, 4)
        if p.wavelength_approx:
            entry["wavelength_approx"] = True
        frame_entries.append(entry)

    gif_name = "anim.gif"
    pil_frames[0].save(out_dir / gif_name, save_all=True, append_images=pil_frames[1:],
                       duration=GIF_FRAME_MS, loop=0)

    # --- Dönem yığınları (o dönemde indirilen bütün pozların ortancası), ortak ölçek ---
    stacks: dict[int, np.ndarray] = {}
    for raw in raw_ids:
        members = [p.image for p in processed if p.epoch_raw == raw]
        stacks[renumber[raw]] = _nanmedian_stack(members)
    s_vmin, s_vmax, s_soft = global_scale(list(stacks.values()), [_robust_std(s) for s in stacks.values()])
    epoch_entries = []
    for raw in raw_ids:
        eid = renumber[raw]
        rows = archive_epochs[raw]
        start, end = mjd_to_date(rows[0]["t_min"]), mjd_to_date(rows[-1]["t_min"])
        n_stack = sum(1 for p in processed if p.epoch_raw == raw)
        name = f"epoch_{eid}.png"
        label = f"{start[:7]} | {n_stack} poz ortanca"
        annotate(stretch_to_rgb(stacks[eid], s_vmin, s_vmax, s_soft), label, grid.fov_arcmin).save(out_dir / name)
        epoch_entries.append({
            "id": eid,
            "label": start[:7],
            "start": start,
            "end": end,
            "n_frames": sum(1 for p in selected if p.epoch == eid),
            "n_stacked": n_stack,
            "n_available": len(rows),
            "stack_url": f"{url_prefix}/{name}",
        })

    # --- Fark görüntüsü: son dönem - ilk dönem ---
    diff_url = None
    if len(stacks) >= 2:
        first_id, last_id = min(stacks), max(stacks)
        # Renk farkı sahte artık bırakmasın diye: iki dönemden dalga boyu birbirine yakın pozları eşleştir
        new_all = [p for p in processed if p.epoch == last_id and np.isfinite(p.wavelength_um)]
        old_all = [p for p in processed if p.epoch == first_id and np.isfinite(p.wavelength_um)]
        img_new, img_old, wl_note = stacks[last_id], stacks[first_id], ""
        if new_all and old_all:
            gaps = np.abs(np.subtract.outer([p.wavelength_um for p in new_all], [q.wavelength_um for q in old_all]))
            tol = max(0.09, float(gaps.min()) * 1.0001)  # ~bir bant genişliği: her yanda birkaç poz kalsın
            new_sel = [p for i, p in enumerate(new_all) if gaps[i].min() <= tol]
            old_sel = [q for j, q in enumerate(old_all) if gaps[:, j].min() <= tol]
            if new_sel and old_sel and float(gaps.min()) <= 0.15:
                img_new = _nanmedian_stack([p.image for p in new_sel])
                img_old = _nanmedian_stack([q.image for q in old_sel])
                mean_wl = float(np.mean([p.wavelength_um for p in new_sel + old_sel]))
                wl_note = f" | ~{mean_wl:.2f} um"
        diff, _scale = make_diff(img_new, img_old)
        label = f"{epoch_entries[-1]['label']} - {epoch_entries[0]['label']}{wl_note}"
        annotate(diff_to_rgb(diff), label, grid.fov_arcmin).save(out_dir / "diff.png")
        diff_url = f"{url_prefix}/diff.png"

    # Kullanıcının seçtiği iki görüntünün farkı sonradan hesaplanabilsin diye sayısal dizileri sakla
    np.savez_compressed(
        out_dir / "arrays.npz",
        frames=np.stack([p.image for p in selected]).astype(np.float32),
        stacks=np.stack([stacks[e["id"]] for e in epoch_entries]).astype(np.float32),
    )

    # --- Spektrum: indirilen bütün pozlar, dalga boyuna göre sıralı ---
    spectrum = []
    for p in sorted(processed, key=lambda q: q.wavelength_um):
        if p.flux is None or p.wavelength_approx or not np.isfinite(p.wavelength_um):
            continue
        point = {
            "wavelength_um": round(p.wavelength_um, 4),
            "flux": round(p.flux, 5),
            "flux_err": round(p.flux_err, 5) if p.flux_err is not None else None,
            "date": p.date,
            "mjd": round(p.mjd, 5),
            "epoch": p.epoch,
        }
        if p.saturated:
            point["saturated"] = True
        if p.phot_flagged:
            point["flagged"] = True
        spectrum.append(point)

    waves = [p.wavelength_um for p in selected if np.isfinite(p.wavelength_um)]
    wave_text = f"hedefte {min(waves):.2f}–{max(waves):.2f} µm" if waves else "dalga boyu bilinmiyor"
    message = (
        f"{len(selected)} kare, {len(epoch_entries)} dönem "
        f"({frame_entries[0]['date']} → {frame_entries[-1]['date']}); "
        f"{detector} dedektörü, {wave_text}."
    )
    if any(p.wavelength_approx for p in selected):
        message += " Bazı karelerde gerçek dalga boyu okunamadı (~ ile işaretli, yaklaşık)."
    if not spectrum:
        message += " Hedef pikselleri doymuş ya da maskeli olduğu için spektrum çıkarılamadı."
    if len(epoch_entries) < 2:
        message += " Tek dönem bulunduğu için fark görüntüsü üretilemedi."

    return {
        "status": "success",
        "message": message,
        "target": {
            "ra": round(ra, 6),
            "dec": round(dec, 6),
            "fov_arcmin": grid.fov_arcmin,
            "band_um": band_um,
            "detector": detector,
            "pixel_scale_arcsec": round(grid.scale_arcsec, 3),
        },
        "frames": frame_entries,
        "epochs": epoch_entries,
        "diff_url": diff_url,
        "gif_url": f"{url_prefix}/{gif_name}",
        "spectrum": spectrum,
        "flux_unit": FLUX_UNIT,
    }


# ----------------------------------------------------------------------------
# 7) Ana giriş noktası
# ----------------------------------------------------------------------------
def normalize_params(ra: float, dec: float, fov: float, band: float, frames: int):
    """Parametreleri doğrular / sınırlar. Geçersizse PipelineError."""
    if not all(math.isfinite(v) for v in (ra, dec, fov, band)):
        raise PipelineError("RA, Dec, fov ve band sonlu sayılar olmalı.")
    if not -90.0 <= dec <= 90.0:
        raise PipelineError("Dec değeri -90 ile +90 derece arasında olmalı.")
    if not -360.0 <= ra <= 720.0:
        raise PipelineError("RA değeri 0 ile 360 derece arasında olmalı.")
    if not BAND_MIN_UM <= band <= BAND_MAX_UM:
        raise PipelineError(f"SPHEREx {BAND_MIN_UM}–{BAND_MAX_UM} µm arasını görür; band={band} bu aralığın dışında.")
    ra = ra % 360.0
    fov = min(30.0, max(1.0, fov))
    frames = min(40, max(2, int(frames)))
    return round(ra, 6), round(dec, 6), round(fov, 2), round(band, 3), frames


def generate(ra: float, dec: float, fov: float = 4.0, band: float = 2.5, frames: int = 12,
             cache_dir: Path | str = "cache", refresh: bool = False) -> dict:
    """Bütün hattı çalıştırır ve API cevabını (dict) döndürür. Sonuç diske önbelleklenir."""
    ra, dec, fov, band, frames = normalize_params(ra, dec, fov, band, frames)
    key = cache_key(ra, dec, fov, band, frames)
    out_dir = Path(cache_dir) / key
    result_file = out_dir / "result.json"

    with _key_locks_guard:
        lock = _key_locks.setdefault(key, threading.Lock())
    with lock:  # aynı istek iki kez gelirse ikincisi birincinin sonucunu bekler
        if not refresh and result_file.exists():
            try:
                cached = json.loads(result_file.read_text(encoding="utf-8"))
                cached["cached"] = True
                cached.setdefault("key", key)
                cached["custom_diff"] = (out_dir / "arrays.npz").exists()
                return cached
            except Exception:
                log.warning("Önbellek dosyası bozuk, yeniden üretilecek: %s", result_file, exc_info=True)

        started = time.time()
        if out_dir.exists():
            shutil.rmtree(out_dir, ignore_errors=True)
        out_dir.mkdir(parents=True, exist_ok=True)

        session = create_http_session()
        try:
            result = _run(session, ra, dec, fov, band, frames, out_dir, f"/cache/{key}")
        except Exception:
            shutil.rmtree(out_dir, ignore_errors=True)  # yarım kalan çıktıyı bırakma
            raise
        finally:
            session.close()

        result["cached"] = False
        result["key"] = key
        result["custom_diff"] = True
        result["elapsed_s"] = round(time.time() - started, 1)
        tmp = result_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(result_file)
        log.info("Tamamlandı (%s) %.1f sn: %s", key, result["elapsed_s"], result["message"])
        return result


def _run(session: requests.Session, ra: float, dec: float, fov: float, band: float, frames: int,
         out_dir: Path, url_prefix: str) -> dict:
    # Asıl kaynak IRSA; yanıt vermiyorsa NASA'nın açık S3 deposundaki konum dizinine düş
    from_s3 = False
    try:
        if s3index.irsa_recently_down() and s3index.available():
            raise PipelineError("IRSA az önce yanıt vermedi; doğrudan S3 kullanılıyor")
        rows, detector = query_exposures(session, ra, dec, band)
    except PipelineError as exc:
        if not s3index.available():
            raise
        log.warning("IRSA kullanılamıyor (%s); S3 dizinine geçiliyor", exc)
        s3index.mark_irsa_down()
        rows, detector = s3index.query(ra, dec, band)
        from_s3 = True
        if not rows:
            parts, n_exp = s3index.stats()
            raise PipelineError(
                f"NASA IRSA arşivi yanıt vermiyor; yedek dizinde ({parts} hafta parçası, {n_exp} poz) "
                f"bu koordinatı ({ra:.4f}, {dec:.4f}) {band} µm'de kapsayan görüntü bulunamadı."
            ) from exc
    if not rows:
        raise PipelineError(
            f"Bu koordinatta ({ra:.4f}, {dec:.4f}) {band} µm'yi içeren SPHEREx görüntüsü bulunamadı."
        )
    archive_epochs = group_epochs(rows)
    quotas = epoch_quotas([len(e) for e in archive_epochs], frames)
    candidates = pick_candidates(archive_epochs, quotas, frames)
    log.info("%d poz, %d dönem; %d aday indirilecek", len(rows), len(archive_epochs), len(candidates))

    grid = make_grid(ra, dec, fov)
    size_deg = (fov * 1.5 + 1.0) / 60.0  # döndürme payı (kök 2) + kenar boşluğu

    def work(row: dict) -> Processed | None:
        try:
            if row.get("s3"):
                content = s3index.fetch(row, ra, dec, size_deg, session)
            else:
                content = fetch_cutout(row, ra, dec, size_deg, session)
            if content is None:
                return None
            return process_exposure(content, row, ra, dec, grid)
        except requests.RequestException as exc:
            log.warning("İndirme hatası (%s): %s", row["obs_id"], exc)
        except Exception:
            log.exception("Poz işlenemedi: %s", row["obs_id"])
        return None

    with ThreadPoolExecutor(max_workers=S3_WORKERS if from_s3 else MAX_WORKERS) as pool:
        processed = [p for p in pool.map(work, candidates) if p is not None]
    log.info("%d / %d poz başarıyla işlendi", len(processed), len(candidates))
    if not processed:
        raise PipelineError(
            "Arşivde poz bulundu ama hiçbiri indirilemedi/işlenemedi (IRSA geçici hata veriyor olabilir). "
            "Lütfen biraz sonra tekrar deneyin."
        )
    result = build_outputs(processed, archive_epochs, quotas, ra, dec, grid, band, frames,
                           detector, out_dir, url_prefix)
    result["source"] = "s3" if from_s3 else "irsa"
    if from_s3:
        result["message"] += " Veri kaynağı: NASA'nın açık S3 deposu (IRSA sorgu servisi yanıt vermiyor)."
    return result


# ----------------------------------------------------------------------------
# 8) Kullanıcının seçtiği iki görüntünün farkı
# ----------------------------------------------------------------------------
def custom_diff(key: str, a: str, b: str, cache_dir: Path | str = "cache") -> dict:
    """Önbellekteki bir sonucun iki görüntüsünü çıkarır: a - b.

    a ve b: "f<kare no>" (tek kare) ya da "e<dönem no>" (dönem yığını). Örn. a="e1", b="f0".
    """
    if not re.fullmatch(r"[A-Za-z0-9_]+", key or ""):
        raise PipelineError("Geçersiz sonuç anahtarı.")
    out_dir = Path(cache_dir) / key
    result_file, arrays_file = out_dir / "result.json", out_dir / "arrays.npz"
    if not result_file.exists():
        raise PipelineError("Bu sonuç önbellekte yok; görüntüleri yeniden yükleyin.")
    if not arrays_file.exists():
        raise PipelineError("Bu sonuç eski sürümle üretilmiş; seçmeli fark için görüntüleri yeniden yükleyin.")
    meta = json.loads(result_file.read_text(encoding="utf-8"))
    with np.load(arrays_file) as data:
        frames, stacks = data["frames"].astype(np.float64), data["stacks"].astype(np.float64)

    def pick(code: str) -> tuple[np.ndarray, str]:
        m = re.fullmatch(r"([fe])(\d{1,3})", code or "")
        if not m:
            raise PipelineError("Görüntü seçimi geçersiz.")
        kind, i = m.group(1), int(m.group(2))
        if kind == "f":
            if i >= len(frames):
                raise PipelineError("Seçilen kare bulunamadı.")
            f = meta["frames"][i]
            return frames[i], f"{f['date']} {f['wavelength_um']:.2f}um"
        if i >= len(stacks):
            raise PipelineError("Seçilen dönem bulunamadı.")
        return stacks[i], f"{meta['epochs'][i]['label']} yigin"

    if a == b:
        raise PipelineError("Fark için iki farklı görüntü seçin.")
    img_a, name_a = pick(a)
    img_b, name_b = pick(b)
    diff, scale = make_diff(img_a, img_b)
    if not np.isfinite(diff).any():
        raise PipelineError("Seçilen iki görüntünün ortak verisi yok.")
    name = f"diff_{a}_{b}.png"
    annotate(diff_to_rgb(diff), f"{name_a} - {name_b}", float(meta["target"]["fov_arcmin"])).save(out_dir / name)
    return {
        "status": "success",
        "url": f"/cache/{key}/{name}",
        "scale": round(scale, 3),
        "label": f"{name_a} − {name_b}",
    }
