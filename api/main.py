"""Harman - SPHEREx Zaman Makinesi web sunucusu.

Çalıştırma (proje kök klasöründen):
    uvicorn api.main:app --reload
Sonra tarayıcıda http://127.0.0.1:8000 adresini açın.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import pipeline
from .resolver import ResolveError, resolve_name

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("harman.api")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = PROJECT_ROOT / "web"
CACHE_DIR = Path(os.environ.get("SPHEREX_CACHE_DIR", PROJECT_ROOT / "cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="Harman — SPHEREx Zaman Makinesi",
    description="Bir gökyüzü koordinatı için SPHEREx görüntülerini zaman sırasıyla üretir.",
    version="1.0.0",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def no_stale_cache(request: Request, call_next):
    """Üretilen dosyalar refresh=1 ile değişebilir: tarayıcı her seferinde sunucuya sorsun (ETag ile ucuz)."""
    response = await call_next(request)
    if request.url.path.startswith("/cache/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


def _error(message: str, status_code: int = 200) -> JSONResponse:
    return JSONResponse({"status": "error", "message": message}, status_code=status_code)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    """Eksik/yanlış parametrelerde de sözleşmedeki hata gövdesini döndür."""
    fields = ", ".join(str(e["loc"][-1]) for e in exc.errors())
    return _error(f"Geçersiz ya da eksik parametre: {fields}", status_code=422)


@app.get("/api/health")
def health():
    return {"status": "success", "message": "çalışıyor"}


# Not: uç noktalar bilerek `def` (async değil): FastAPI bunları iş parçacığı havuzunda çalıştırır,
# böylece uzun süren indirme/işleme sunucuyu kilitlemez.
@app.get("/api/resolve")
def api_resolve(name: str = Query(..., description="Cisim adı, ör. 'RW Aur'")):
    try:
        result = resolve_name(name, cache_file=CACHE_DIR / "resolve.json")
    except ResolveError as exc:
        return _error(str(exc))
    except Exception:
        log.exception("Ad çözümlemede beklenmeyen hata: %r", name)
        return _error("Ad çözümlenirken beklenmeyen bir hata oluştu.", status_code=500)
    return {"status": "success", **result}


@app.get("/api/generate")
def api_generate(
    ra: float = Query(..., description="Sağ açıklık (derece)"),
    dec: float = Query(..., description="Dik açıklık (derece)"),
    fov: float = Query(4.0, description="Görüş alanı (yay-dakikası, 1..30)"),
    band: float = Query(2.5, description="Dalga boyu (mikrometre, 0.75..5.0)"),
    frames: int = Query(12, description="Kare sayısı (2..40)"),
    refresh: int = Query(0, description="1 ise önbelleği yok say"),
):
    try:
        return pipeline.generate(ra, dec, fov, band, frames, cache_dir=CACHE_DIR, refresh=bool(refresh))
    except pipeline.PipelineError as exc:
        log.warning("İstek başarısız (ra=%s dec=%s band=%s): %s", ra, dec, band, exc)
        return _error(str(exc))
    except Exception:
        log.exception("Beklenmeyen hata (ra=%s dec=%s fov=%s band=%s frames=%s)", ra, dec, fov, band, frames)
        return _error("Sunucuda beklenmeyen bir hata oluştu; ayrıntı sunucu günlüğünde.", status_code=500)


# Statik dosyalar: en sona eklenir ki /api/... yollarını gölgelemesin
@app.get("/api/diff")
def api_diff(
    key: str = Query(..., description="generate cevabındaki 'key'"),
    a: str = Query(..., description="Çıkarılan görüntü: f<kare no> ya da e<dönem no>"),
    b: str = Query(..., description="Çıkan görüntü: f<kare no> ya da e<dönem no>"),
):
    """İki görüntünün farkı (a - b); parlaklıklar eşitlenerek çıkarılır."""
    try:
        return pipeline.custom_diff(key, a, b, cache_dir=CACHE_DIR)
    except pipeline.PipelineError as exc:
        return _error(str(exc))
    except Exception:
        log.exception("Fark hesaplanamadı (key=%s a=%s b=%s)", key, a, b)
        return _error("Fark hesaplanırken beklenmeyen bir hata oluştu.")


app.mount("/cache", StaticFiles(directory=CACHE_DIR), name="cache")
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
else:
    log.warning("web/ klasörü bulunamadı; yalnızca API sunuluyor")
