"""IRSA kapalıyken yedek veri yolu: NASA'nın açık S3 deposu (nasa-irsa-spherex).

IRSA'nın sorgu (TAP) ve kesit servisleri yanıt vermediğinde araç yine de çalışsın diye:

  * query():  scripts/build_s3_index.py'nin çıkardığı konum dizininden hedefi kapsayan pozları bulur.
              Dizin yalnızca dedektör 4 için tutulur; diğer dedektörlerin konumu, aynı pozdaki sabit
              odak düzlemi kaymasından (AFFINE) hesaplanır.
  * fetch():  62 MB'lık dosyanın tamamını indirmez; HTTP Range istekleriyle yalnızca hedef çevresindeki
              satırları (görüntü, bayrak, varyans) ve dalga boyu tablosunu okur, bunlardan küçük bir FITS
              kurar. Çıktı IRSA kesit servisinin verdiğiyle aynı biçimde işlenir.
"""
from __future__ import annotations

import io
import json
import logging
import math
import re
import threading
import time
import warnings
from pathlib import Path

import numpy as np
import requests
from astropy.io import fits
from astropy.wcs import WCS

log = logging.getLogger("harman.s3")

BUCKET = "https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com"
INDEX_DIR = Path(__file__).resolve().parent.parent / "cache" / "s3index" / "4"
BLOCK = 2880
N_PIX = 2040
TIMEOUT = (15, 60)
IRSA_RETRY_AFTER_S = 600  # IRSA düşükse bu kadar süre tekrar denemeden doğrudan S3'e git

# Dedektör n'nin pikseli = M @ (D4 pikseli) + t ; gerçek başlıklardan ölçüldü (aynı poz, altı dedektör).
# D1 ile D4, D2 ile D5, D3 ile D6 aynı gökyüzü alanını görür (ışık bir ayna ile ikiye bölünür).
AFFINE = {
    1: ((1.0065020, 0.0048201, -20.7777), (0.0099250, 1.0054662, -9.3843)),
    2: ((1.0038594, -0.0088211, -2263.0249), (-0.0182502, 1.0046782, 49.6227)),
    3: ((0.9876030, -0.0168545, -4446.7516), (-0.0466573, 0.9973306, 178.6712)),
    4: ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    5: ((1.0034055, -0.0036770, -2264.8606), (-0.0221801, 1.0033872, 53.3598)),
    6: ((0.9866266, -0.0152385, -4443.5699), (-0.0483875, 0.9959929, 178.3338)),
}

_lock = threading.Lock()
_index: dict = {"stamp": None}
_irsa_down_until = 0.0


# ----------------------------------------------------------------------------
# IRSA durumu
# ----------------------------------------------------------------------------
def mark_irsa_down() -> None:
    global _irsa_down_until
    _irsa_down_until = time.time() + IRSA_RETRY_AFTER_S


def irsa_recently_down() -> bool:
    return time.time() < _irsa_down_until


# ----------------------------------------------------------------------------
# Dizin
# ----------------------------------------------------------------------------
def available() -> bool:
    return INDEX_DIR.is_dir() and any(INDEX_DIR.glob("*.json"))


def _load() -> dict:
    """Dizin parçalarını belleğe alır; yeni parça eklenmişse yeniden yükler."""
    files = sorted(INDEX_DIR.glob("*.json"))
    stamp = (len(files), max((f.stat().st_mtime for f in files), default=0))
    with _lock:
        if _index.get("stamp") == stamp:
            return _index
        prefixes, names, pidx, nums = [], [], [], []
        for f in files:
            try:
                part = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                log.warning("Dizin parçası okunamadı: %s", f.name)
                continue
            prefixes.append(part["prefix"])
            for row in part["rows"]:
                names.append(row[0])
                pidx.append(len(prefixes) - 1)
                nums.append(row[1:])
        arr = np.asarray(nums, dtype=np.float64).reshape(-1, 9)
        _index.clear()
        _index.update(stamp=stamp, prefixes=prefixes, names=names, pidx=np.asarray(pidx, dtype=np.int32),
                      arr=arr, parts=len(prefixes))
        log.info("S3 dizini yüklendi: %d hafta parçası, %d poz", len(prefixes), len(names))
        return _index


def stats() -> tuple[int, int]:
    idx = _load()
    return idx.get("parts", 0), len(idx.get("names", []))


def query(ra: float, dec: float, band_um: float) -> tuple[list[dict], str]:
    """Hedefi kapsayan pozlar (zamana göre sıralı) ve dedektör adı. pipeline.query_exposures ile aynı biçim."""
    from .pipeline import DETECTOR_BANDS, detector_for_band  # döngüsel içe aktarmayı önlemek için burada

    detector = detector_for_band(band_um) or "D4"
    n = int(detector[1])
    idx = _load()
    arr = idx["arr"]
    if arr.size == 0:
        return [], detector

    crval1, crval2, crpix1, crpix2, pc11, pc12, pc21, pc22, mjd = arr.T
    d2r = math.pi / 180.0
    a0, d0, a, d = crval1 * d2r, crval2 * d2r, ra * d2r, dec * d2r
    cosc = np.sin(d0) * math.sin(d) + np.cos(d0) * math.cos(d) * np.cos(a - a0)
    near = cosc > 0.95  # dedektör merkezine ~18 dereceden yakın
    with np.errstate(divide="ignore", invalid="ignore"):
        xi = math.cos(d) * np.sin(a - a0) / cosc / d2r
        eta = (np.cos(d0) * math.sin(d) - np.sin(d0) * math.cos(d) * np.cos(a - a0)) / cosc / d2r
        det = pc11 * pc22 - pc12 * pc21
        x4 = (pc22 * xi - pc12 * eta) / det + crpix1 - 1.0
        y4 = (-pc21 * xi + pc11 * eta) / det + crpix2 - 1.0
    (mxx, mxy, tx), (myx, myy, ty) = AFFINE[n]
    xn = mxx * x4 + mxy * y4 + tx
    yn = myx * x4 + myy * y4 + ty
    margin = 40 if n in (1, 4) else 70  # kenardaki pozlar zaten işe yaramaz; kayma hesabındaki payı da kapsar
    ok = near & (xn > margin) & (xn < N_PIX - 1 - margin) & (yn > margin) & (yn < N_PIX - 1 - margin)

    lo, hi = DETECTOR_BANDS[detector]
    rows = []
    for i in np.flatnonzero(ok):
        name = idx["names"][i]
        prefix = idx["prefixes"][idx["pidx"][i]]
        if n != 4:
            prefix = re.sub(r"/4/$", f"/{n}/", prefix)
            name = name.replace("D4_spx", f"D{n}_spx")
        m = re.search(r"level2_(.+?)_spx", name)
        rows.append({
            "obs_id": m.group(1) if m else name,
            "collection": prefix.split("/")[0],
            "t_min": float(mjd[i]),
            "em_min_um": lo,
            "em_max_um": hi,
            "access_url": f"{BUCKET}/{prefix}{name}",
            "access_format": "application/fits",
            "detector": detector,
            "s3": True,
        })
    rows.sort(key=lambda r: r["t_min"])
    log.info("S3 dizini: (%.4f, %.4f) için %s dedektöründe %d poz", ra, dec, detector, len(rows))
    return rows, detector


# ----------------------------------------------------------------------------
# Parçalı okuma
# ----------------------------------------------------------------------------
def _range(session: requests.Session, url: str, start: int, end: int) -> bytes | None:
    """Dosyanın [start, end] baytlarını okur (start negatifse sondan)."""
    spec = f"bytes={start}" if start < 0 else f"bytes={start}-{end}"
    last = None
    for attempt in range(3):
        try:
            resp = session.get(url, headers={"Range": spec}, timeout=TIMEOUT)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.content
        except requests.RequestException as exc:
            last = exc
            time.sleep(0.8 * (attempt + 1))
    raise last  # type: ignore[misc]


def _header(buf: bytes, pos: int) -> tuple[fits.Header, int] | None:
    """buf içinde pos'tan başlayan FITS başlığını çözer; (başlık, veri başlangıcı) döner."""
    i = pos
    while i + 80 <= len(buf):
        if buf[i:i + 8] == b"END     ":
            end = i + 80
            data_start = pos + int(math.ceil((end - pos) / BLOCK)) * BLOCK
            return fits.Header.fromstring(buf[pos:end].decode("ascii", "replace")), data_start
        i += 80
    return None


def _padded(n: int) -> int:
    return int(math.ceil(n / BLOCK)) * BLOCK


_FORM_SIZE = {"L": 1, "X": 1, "B": 1, "I": 2, "J": 4, "K": 8, "A": 1, "E": 4, "D": 8, "C": 8, "M": 16}


def _read_flags(session, url, offset, y0, y1, primary: bytes):
    """Sıkıştırılmış FLAGS uzantısından yalnızca y0..y1 satırlarını okur. (dizi, başlık) ya da (None, veri sonu)."""
    head = _range(session, url, offset, offset + 9 * BLOCK - 1)
    parsed = _header(head, 0) if head else None
    if parsed is None:
        return None, None, None
    fh, data_rel = parsed
    row_bytes, n_rows, pcount = int(fh["NAXIS1"]), int(fh["NAXIS2"]), int(fh.get("PCOUNT", 0))
    data_start = offset + data_rel
    next_hdu = data_start + _padded(row_bytes * n_rows + pcount)
    try:
        if not fh.get("ZIMAGE") or int(fh.get("ZTILE1", 0)) != int(fh["ZNAXIS1"]) or int(fh.get("ZTILE2", 1)) != 1 \
                or n_rows != int(fh["ZNAXIS2"]):
            return None, None, next_hdu  # satır satır sıkıştırılmamış: parçalı okunamaz
        # satırdaki değişken uzunluklu sütunların (uzunluk, yığın konumu) çiftlerinin yeri
        cols, pos = [], 0
        for k in range(1, int(fh["TFIELDS"]) + 1):
            m = re.match(r"^(\d*)([PQ]?)([A-Z])", str(fh[f"TFORM{k}"]).strip())
            rep = int(m.group(1)) if m.group(1) else 1
            if m.group(2) == "P":
                cols.append((pos, ">i4")); width = 8 * rep
            elif m.group(2) == "Q":
                cols.append((pos, ">i8")); width = 16 * rep
            else:
                width = rep * _FORM_SIZE[m.group(3)]
            pos += width
        table = bytearray(_range(session, url, data_start + y0 * row_bytes, data_start + y1 * row_bytes - 1))
        n = y1 - y0
        desc = []
        for cpos, dtype in cols:
            size = np.dtype(dtype).itemsize
            for r in range(n):
                p = r * row_bytes + cpos
                length, off = np.frombuffer(bytes(table[p:p + 2 * size]), dtype=dtype)
                desc.append((p, dtype, int(length), int(off)))
        used = [d for d in desc if d[2] > 0]
        if not used:
            return None, None, next_hdu
        lo = min(d[3] for d in used)
        hi = max(d[3] + d[2] for d in used)
        theap = int(fh.get("THEAP", row_bytes * n_rows))
        heap = _range(session, url, data_start + theap + lo, data_start + theap + hi - 1)
        for p, dtype, length, off in desc:  # yığın konumlarını yeni (küçük) yığına göre yeniden yaz
            size = np.dtype(dtype).itemsize
            table[p:p + 2 * size] = np.array([length, off - lo if length > 0 else 0], dtype=dtype).tobytes()
        nh = fh.copy()
        nh["NAXIS2"] = n
        nh["ZNAXIS2"] = n
        nh["PCOUNT"] = len(heap)
        for key in ("THEAP", "CHECKSUM", "DATASUM", "ZHECKSUM", "ZDATASUM"):
            nh.remove(key, ignore_missing=True)
        body = bytes(table) + heap
        raw = primary + nh.tostring(padding=True).encode("ascii") + body + b"\0" * (_padded(len(body)) - len(body))
        with fits.open(io.BytesIO(raw), memmap=False) as hd:
            data = np.asarray(hd[1].data)
            mask_cards = [c for c in hd[1].header.cards if c.keyword.startswith("MSKN")]
        return data, mask_cards, next_hdu
    except Exception as exc:
        log.warning("FLAGS parçalı okunamadı (%s); bayraksız devam", exc)
        return None, None, next_hdu


def fetch(row: dict, ra: float, dec: float, size_deg: float, session: requests.Session) -> bytes | None:
    """S3'teki tam kare dosyadan hedef çevresini okuyup küçük bir FITS (bayt dizisi) döndürür."""
    url = row["access_url"]
    head = _range(session, url, 0, 16 * BLOCK - 1)
    if not head:
        return None
    p0 = _header(head, 0)
    if p0 is None:
        return None
    prim_hdr, img_pos = p0
    primary_block = head[:img_pos]
    p1 = _header(head, img_pos)
    if p1 is None:
        return None
    img_hdr, img_data = p1
    nx, ny = int(img_hdr["NAXIS1"]), int(img_hdr["NAXIS2"])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        px = WCS(img_hdr).all_world2pix([[ra, dec]], 0, quiet=True)[0]
    x, y = float(px[0]), float(px[1])
    if not (np.isfinite(x) and np.isfinite(y) and 0 <= x <= nx - 1 and 0 <= y <= ny - 1):
        log.info("Hedef pozun dışında (dizin yaklaşık): %s", row["obs_id"])
        return None
    half = int(math.ceil(size_deg * 3600.0 / 6.15 / 2.0)) + 10
    x0, x1 = max(0, int(x) - half), min(nx, int(x) + half + 1)
    y0, y1 = max(0, int(y) - half), min(ny, int(y) + half + 1)

    def rows_of(data_start: int, dtype: str) -> np.ndarray:
        raw = _range(session, url, data_start + y0 * nx * 4, data_start + y1 * nx * 4 - 1)
        return np.frombuffer(raw, dtype=dtype).reshape(y1 - y0, nx)[:, x0:x1]

    image = rows_of(img_data, ">f4")
    hdr = img_hdr.copy()
    for suffix in [""] + [chr(c) for c in range(65, 91)]:  # asıl WCS ve bütün ek WCS'ler (W: dalga boyu)
        if f"CRPIX1{suffix}" in hdr:
            hdr[f"CRPIX1{suffix}"] = float(hdr[f"CRPIX1{suffix}"]) - x0
            hdr[f"CRPIX2{suffix}"] = float(hdr[f"CRPIX2{suffix}"]) - y0
    for key in ("CHECKSUM", "DATASUM"):
        hdr.remove(key, ignore_missing=True)
    hdus = [fits.PrimaryHDU(header=prim_hdr), fits.ImageHDU(data=np.array(image), header=hdr, name="IMAGE")]

    # FLAGS (sıkıştırılmış, satır satır)
    flags_pos = img_data + _padded(nx * ny * 4)
    flags, mask_cards, var_pos = _read_flags(session, url, flags_pos, y0, y1, primary_block)
    if flags is not None and flags.shape == (y1 - y0, nx):
        fh = fits.Header()
        for card in mask_cards:
            fh.append(card)
        hdus.append(fits.ImageHDU(data=np.array(flags[:, x0:x1]).astype(np.int32), header=fh, name="FLAGS"))

    # VARIANCE (akı hatası için; okunamazsa hatasız devam)
    try:
        if var_pos is not None:
            vhead = _range(session, url, var_pos, var_pos + 6 * BLOCK - 1)
            pv = _header(vhead, 0) if vhead else None
            if pv is not None and str(pv[0].get("EXTNAME", "")).strip().upper() == "VARIANCE" \
                    and int(pv[0]["BITPIX"]) == -32 and int(pv[0]["NAXIS1"]) == nx:
                hdus.append(fits.ImageHDU(data=np.array(rows_of(var_pos + pv[1], ">f4")), name="VARIANCE"))
    except Exception as exc:
        log.warning("VARIANCE okunamadı (%s): %s", row["obs_id"], exc)

    # WCS-WAVE: dosyanın son uzantısı (küçük tablo)
    try:
        tail = _range(session, url, -4 * BLOCK, 0)
        for pos in range(0, len(tail), BLOCK):
            if tail[pos:pos + 8] == b"XTENSION" and b"WCS-WAVE" in tail[pos:pos + BLOCK]:
                with fits.open(io.BytesIO(primary_block + tail[pos:]), memmap=False) as hd:
                    hdus.append(fits.BinTableHDU(data=hd[1].data.copy(), header=hd[1].header.copy(), name="WCS-WAVE"))
                break
        else:
            log.warning("WCS-WAVE tablosu bulunamadı: %s", row["obs_id"])
    except Exception as exc:
        log.warning("WCS-WAVE okunamadı (%s): %s", row["obs_id"], exc)

    out = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fits.HDUList(hdus).writeto(out)
    return out.getvalue()
