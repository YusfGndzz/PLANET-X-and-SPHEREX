"""Gök cismi adını koordinata çevirir (CDS Sesame, birkaç ayna sunucu ile).

Önemli: SIMBAD koordinatları J2000 dönemindedir. Barnard Yıldızı gibi hızlı
cisimler 25 yılda birkaç yay-dakikası kayar; bu yüzden öz hareket (proper
motion) biliniyorsa konumu SPHEREx gözlemlerinin ortasına (2026.0) taşıyoruz.
"""
from __future__ import annotations

import json
import logging
import math
import re
import threading
from pathlib import Path
from urllib.parse import quote

import requests

log = logging.getLogger("harman.resolver")

# Sırayla denenecek Sesame sunucuları (CDS bazen yanıt vermiyor, Harvard aynası yedek)
SESAME_MIRRORS = [
    "https://vizier.cfa.harvard.edu/viz-bin/nph-sesame/-oI/SNV?",
    "https://cds.unistra.fr/cgi-bin/nph-sesame/-oI/SNV?",
    "http://vizier.cfa.harvard.edu/viz-bin/nph-sesame/-oI/SNV?",
]
SESAME_TIMEOUT = (5, 12)  # (bağlantı, okuma) saniye
SPHEREX_EPOCH = 2026.0  # öz hareketin taşınacağı dönem (SPHEREx verisinin ortası)

_lock = threading.Lock()
_memory_cache: dict[str, dict] = {}


class ResolveError(Exception):
    """Ad çözümlenemedi (bulunamadı ya da servis yanıt vermedi)."""


def _parse_coordinate_text(text: str) -> tuple[float, float] | None:
    """'269.45 4.69' ya da '17h57m48s +04d41m36s' gibi doğrudan koordinat girdilerini çözer."""
    if not re.match(r"^[0-9+\-.:hmsd°'\"\s,]+$", text, re.IGNORECASE):
        return None
    parts = [p for p in re.split(r"[\s,]+", text.strip()) if p]
    try:
        if len(parts) == 2:
            try:
                return float(parts[0]) % 360.0, float(parts[1])
            except ValueError:
                pass
        import astropy.units as u
        from astropy.coordinates import SkyCoord

        c = SkyCoord(text.replace(",", " "), unit=(u.hourangle, u.deg), frame="icrs")
        return float(c.ra.deg), float(c.dec.deg)
    except Exception:
        return None


def _parse_sesame(text: str) -> dict | None:
    """Sesame düz metin cevabından J2000 konumu ve öz hareketi çıkarır."""
    ra = dec = None
    pm_ra = pm_dec = None
    for line in text.splitlines():
        if line.startswith("%J ") and ra is None:
            nums = re.findall(r"[-+]?\d+\.?\d*(?:[eE][-+]?\d+)?", line[3:].split("=")[0])
            if len(nums) >= 2:
                ra, dec = float(nums[0]), float(nums[1])
        elif line.startswith("%P ") and pm_ra is None:
            nums = re.findall(r"[-+]?\d+\.?\d*(?:[eE][-+]?\d+)?", line[3:].split("[")[0])
            if len(nums) >= 2:
                pm_ra, pm_dec = float(nums[0]), float(nums[1])
    if ra is None:
        return None
    return {"ra_j2000": ra, "dec_j2000": dec, "pm_ra_masyr": pm_ra, "pm_dec_masyr": pm_dec}


def apply_proper_motion(ra: float, dec: float, pm_ra: float | None, pm_dec: float | None,
                        epoch: float = SPHEREX_EPOCH) -> tuple[float, float]:
    """J2000 konumunu doğrusal öz hareketle verilen döneme taşır (pm_ra = mu_alpha*cos(dec), mas/yıl)."""
    if not pm_ra and not pm_dec:
        return ra, dec
    dt = epoch - 2000.0
    new_dec = dec + (pm_dec or 0.0) * dt / 3.6e6
    cosd = max(math.cos(math.radians(dec)), 1e-6)
    new_ra = (ra + (pm_ra or 0.0) * dt / 3.6e6 / cosd) % 360.0
    return new_ra, max(-90.0, min(90.0, new_dec))


def resolve_name(name: str, cache_file: Path | None = None) -> dict:
    """Adı çözer; {'name','ra','dec', ...} döndürür. Başarısızsa ResolveError fırlatır."""
    name = (name or "").strip()
    if not name:
        raise ResolveError("Lütfen bir cisim adı girin.")
    if len(name) > 120:
        raise ResolveError("Cisim adı çok uzun.")

    direct = _parse_coordinate_text(name)
    if direct is not None:
        ra, dec = direct
        if not -90.0 <= dec <= 90.0:
            raise ResolveError("Dec değeri -90 ile +90 arasında olmalı.")
        return {"name": name, "ra": round(ra, 6), "dec": round(dec, 6), "source": "koordinat"}

    key = name.lower()
    with _lock:
        if key in _memory_cache:
            return dict(_memory_cache[key])
        # Diskteki küçük önbellek: demo sırasında CDS çökerse de çalışsın
        if cache_file is not None and cache_file.exists():
            try:
                disk = json.loads(cache_file.read_text(encoding="utf-8"))
                if key in disk:
                    _memory_cache[key] = disk[key]
                    return dict(disk[key])
            except Exception:
                log.warning("Ad önbelleği okunamadı: %s", cache_file, exc_info=True)

    errors = []
    for base in SESAME_MIRRORS:
        try:
            resp = requests.get(base + quote(name), timeout=SESAME_TIMEOUT,
                                headers={"User-Agent": "spherex-time-machine/1.0"})
            resp.raise_for_status()
        except requests.RequestException as exc:
            log.warning("Sesame sunucusu yanıt vermedi (%s): %s", base, exc)
            errors.append(str(exc))
            continue

        parsed = _parse_sesame(resp.text)
        if parsed is None:
            if "Nothing found" in resp.text:
                raise ResolveError(f"'{name}' adlı cisim SIMBAD/NED/VizieR'de bulunamadı.")
            log.warning("Sesame cevabı anlaşılamadı (%s): %.200s", base, resp.text)
            errors.append("anlaşılamayan cevap")
            continue

        ra, dec = apply_proper_motion(parsed["ra_j2000"], parsed["dec_j2000"],
                                      parsed["pm_ra_masyr"], parsed["pm_dec_masyr"])
        result = {
            "name": name,
            "ra": round(ra, 6),
            "dec": round(dec, 6),
            "epoch": SPHEREX_EPOCH,
            "ra_j2000": round(parsed["ra_j2000"], 6),
            "dec_j2000": round(parsed["dec_j2000"], 6),
            "pm_ra_masyr": parsed["pm_ra_masyr"],
            "pm_dec_masyr": parsed["pm_dec_masyr"],
            "source": "CDS Sesame",
        }
        with _lock:
            _memory_cache[key] = result
            if cache_file is not None:
                try:
                    disk = {}
                    if cache_file.exists():
                        disk = json.loads(cache_file.read_text(encoding="utf-8"))
                    disk[key] = result
                    cache_file.parent.mkdir(parents=True, exist_ok=True)
                    cache_file.write_text(json.dumps(disk, ensure_ascii=False, indent=1), encoding="utf-8")
                except Exception:
                    log.warning("Ad önbelleği yazılamadı", exc_info=True)
        return dict(result)

    raise ResolveError(
        "Ad çözümleme servisine (CDS Sesame) ulaşılamadı; lütfen RA/Dec değerlerini elle girin. "
        f"Ayrıntı: {errors[-1] if errors else 'bilinmiyor'}"
    )
