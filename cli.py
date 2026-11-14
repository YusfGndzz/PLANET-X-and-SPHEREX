"""SPHEREx Zaman Makinesi - komut satırı sürümü (web sunucusu olmadan).

Örnekler:
    python cli.py "Barnard's Star"
    python cli.py --ra 269.446 --dec 4.768 --fov 4 --band 2.5 --frames 12
    python cli.py                      # soruları tek tek sorar

Asıl iş api/pipeline.py içindedir; bu dosya yalnızca ince bir sarmalayıcıdır.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from api import pipeline  # noqa: E402
from api.resolver import ResolveError, resolve_name  # noqa: E402


def ask(prompt: str, default: str = "") -> str:
    value = input(prompt).strip()
    return value or default


def main() -> int:
    parser = argparse.ArgumentParser(description="SPHEREx görüntülerinden zaman serisi PNG/GIF üretir")
    parser.add_argument("name", nargs="?", help="Cisim adı (ör. 'R Dra'); --ra/--dec verilirse gerekmez")
    parser.add_argument("--ra", type=float, help="Sağ açıklık (derece)")
    parser.add_argument("--dec", type=float, help="Dik açıklık (derece)")
    parser.add_argument("--fov", type=float, default=None, help="Görüş alanı, yay-dakikası (varsayılan 4)")
    parser.add_argument("--band", type=float, default=None, help="Dalga boyu, mikrometre (varsayılan 2.5)")
    parser.add_argument("--frames", type=int, default=None, help="Kare sayısı (varsayılan 12)")
    parser.add_argument("--cache", default=str(PROJECT_ROOT / "cache"), help="Çıktı/önbellek klasörü")
    parser.add_argument("--refresh", action="store_true", help="Önbelleği yok say")
    args = parser.parse_args()
    # Windows konsolu (cp1254) "→" gibi karakterleri basamayabilir; çökmek yerine yerine "?" koy
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    interactive = args.name is None and (args.ra is None or args.dec is None)

    try:
        if interactive:
            print("=== SPHEREx Zaman Makinesi ===")
            first = ask("Hedef adı ya da 'RA Dec' (örn: R Draconis ya da 133.7598 -7.2422): ")
            if not first:
                print("Bir hedef adı ya da koordinat girmelisiniz.")
                return 2
            target = resolve_name(first, cache_file=Path(args.cache) / "resolve.json")
            ra, dec = target["ra"], target["dec"]
            fov = float(ask("Görüş alanı, yay-dakikası (varsayılan 4): ", "4"))
            band = float(ask("Dalga boyu, mikrometre (varsayılan 2.5): ", "2.5"))
            frames = int(ask("Kaç kare? (varsayılan 12): ", "12"))
        else:
            if args.ra is not None and args.dec is not None:
                ra, dec = args.ra, args.dec
            else:
                target = resolve_name(args.name, cache_file=Path(args.cache) / "resolve.json")
                ra, dec = target["ra"], target["dec"]
            fov = args.fov if args.fov is not None else 4.0
            band = args.band if args.band is not None else 2.5
            frames = args.frames if args.frames is not None else 12
    except ResolveError as exc:
        print(f"Hata: {exc}")
        return 1
    except ValueError:
        print("Hata: sayısal bir değer bekleniyordu.")
        return 2

    print(f"\nHedef: RA={ra:.5f}°, Dec={dec:.5f}° | fov={fov}' | {band} µm | {frames} kare")
    print("NASA IRSA sorgulanıyor ve görüntüler indiriliyor...")
    try:
        result = pipeline.generate(ra, dec, fov, band, frames, cache_dir=args.cache, refresh=args.refresh)
    except pipeline.PipelineError as exc:
        print(f"Hata: {exc}")
        return 1

    out_dir = Path(args.cache) / result["gif_url"].split("/")[2]
    print(f"\n{result['message']}")
    for frame in result["frames"]:
        approx = " (yaklaşık)" if frame.get("wavelength_approx") else ""
        print(f"  {frame['index']:02d}  {frame['date']}  {frame['wavelength_um']:.3f} µm{approx}  dönem {frame['epoch']}")
    print(f"\nÇıktı klasörü : {out_dir}")
    print(f"GIF           : {out_dir / 'anim.gif'}")
    if result.get("diff_url"):
        print(f"Fark görüntüsü: {out_dir / 'diff.png'}")
    print(f"Spektrum      : {len(result['spectrum'])} nokta (result.json içinde)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
