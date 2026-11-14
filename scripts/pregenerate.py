"""Demo öncesi önbelleği ısıtır: web/targets.json içindeki bütün test hedeflerini önceden üretir.

Kullanım (proje kök klasöründen, sunucunun çalışması gerekmez):
    python scripts/pregenerate.py              # eksik olanları üret
    python scripts/pregenerate.py --refresh    # hepsini baştan üret
    python scripts/pregenerate.py --frames 16

targets.json alanları: name, ra, dec, fov (yay-dakikası), band (µm), description
Arayüz hangi parametrelerle istek atıyorsa (özellikle frames) burada da aynısı kullanılmalı,
yoksa önbellek anahtarı tutmaz.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from api import pipeline  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Test hedefleri için önbelleği ısıt")
    parser.add_argument("--targets", default=str(PROJECT_ROOT / "web" / "targets.json"))
    parser.add_argument("--cache", default=str(PROJECT_ROOT / "cache"))
    parser.add_argument("--frames", type=int, default=12)
    parser.add_argument("--refresh", action="store_true", help="önbellekte olsa da yeniden üret")
    args = parser.parse_args()
    # Windows konsolu (cp1254) "→" gibi karakterleri basamayabilir; çökmek yerine yerine "?" koy
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    targets = json.loads(Path(args.targets).read_text(encoding="utf-8"))

    failed = 0
    for t in targets:
        name = t.get("name", "?")
        try:
            ra, dec = float(t["ra"]), float(t["dec"])
            fov = float(t.get("fov", 4.0))
            band = float(t.get("band", 2.5))
            frames = int(t.get("frames", args.frames))
        except (KeyError, TypeError, ValueError) as exc:
            print(f"[ATLANDI] {name}: targets.json kaydı eksik/bozuk ({exc})")
            failed += 1
            continue
        print(f"\n=== {name} (ra={ra}, dec={dec}, fov={fov}', band={band} µm, {frames} kare) ===")
        try:
            result = pipeline.generate(ra, dec, fov, band, frames, cache_dir=args.cache, refresh=args.refresh)
            where = "önbellekten" if result.get("cached") else f"{result.get('elapsed_s', '?')} sn"
            print(f"[TAMAM] {result['message']} ({where})")
        except pipeline.PipelineError as exc:
            print(f"[HATA] {name}: {exc}")
            failed += 1
        except Exception as exc:  # beklenmeyen hata: diğer hedeflere devam et
            logging.exception("Beklenmeyen hata: %s", name)
            print(f"[HATA] {name}: {exc}")
            failed += 1

    print(f"\nBitti: {len(targets) - failed}/{len(targets)} hedef hazır.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
