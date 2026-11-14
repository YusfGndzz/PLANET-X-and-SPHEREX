"""SPHEREx Level-2 dosyalarının gökyüzü dizinini NASA'nın açık S3 deposundan çıkarır.

IRSA'nın sorgu servisi (TAP) kapalıyken aracın yine de herhangi bir koordinatta çalışabilmesi için,
her pozun başlığından yalnızca konum bilgisini (WCS) okuyup cache/s3index/<dedektör>/ altına yazar.
Dosyaların kendisi inmez; her dosyadan ~23 KB okunur.

Kullanım:
    python scripts/build_s3_index.py            # dedektör 4, bütün haftalar
    python scripts/build_s3_index.py 4 2026W3   # yalnızca adı "2026W3" ile başlayan haftalar

Yarıda kesilirse kaldığı yerden devam eder (biten hafta parçaları atlanır). Yeni veri yayınlandıkça
tekrar çalıştırmak yeterlidir.
"""
from __future__ import annotations

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter

BUCKET = "https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "cache" / "s3index"
# Aynı hafta birden çok yayında varsa soldaki tercih edilir (DR1 > hızlı yayınlar)
COLLECTIONS = ["dr1", "qr3", "qr2"]
KEYS = ("CRVAL1", "CRVAL2", "CRPIX1", "CRPIX2", "PC1_1", "PC1_2", "PC2_1", "PC2_2", "MJD-AVG")
THREADS = 72

session = requests.Session()
session.mount("https://", HTTPAdapter(pool_connections=4, pool_maxsize=THREADS + 8, max_retries=3))


def listing(prefix: str, delimiter: str | None = None) -> list[str]:
    out, token = [], None
    while True:
        params = {"list-type": "2", "prefix": prefix}
        if delimiter:
            params["delimiter"] = delimiter
        if token:
            params["continuation-token"] = token
        text = session.get(BUCKET + "/", params=params, timeout=60).text
        out += re.findall(r"<Key>([^<]+)</Key>", text)
        out += re.findall(r"<CommonPrefixes><Prefix>([^<]+)</Prefix>", text)
        m = re.search(r"<NextContinuationToken>([^<]+)<", text)
        if not m:
            return out
        token = m.group(1)


def read_wcs(key: str):
    """Dosyanın ilk 23 KB'ından IMAGE başlığındaki WCS anahtarlarını okur."""
    for attempt in range(3):
        try:
            r = session.get(f"{BUCKET}/{key}", headers={"Range": "bytes=0-23039"}, timeout=40)
            r.raise_for_status()
            text = r.content.decode("ascii", "replace")
            kv = {}
            for i in range(0, len(text), 80):
                card = text[i:i + 80]
                k = card[:8].strip()
                if k in KEYS and k not in kv:
                    kv[k] = float(card[10:30].split("/")[0])
            if all(k in kv for k in KEYS):
                return [key.rsplit("/", 1)[1]] + [round(kv[k], 9) for k in KEYS]
            return None
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return None


def week_order(weeks: list[str]) -> list[str]:
    """Önce seyrek, sonra araları doldurarak: yarım dizin bile bütün yıla yayılmış olsun."""
    weeks = sorted(weeks)
    return [w for off in (0, 2, 1, 3) for w in weeks[off::4]]


def main() -> None:
    det = sys.argv[1] if len(sys.argv) > 1 else "4"
    only = sys.argv[2] if len(sys.argv) > 2 else ""
    out_dir = OUT / det
    out_dir.mkdir(parents=True, exist_ok=True)

    parts: dict[str, str] = {}  # "2025W38_1A" -> "dr1/level2/2025W38_1A/"
    for coll in COLLECTIONS:
        for p in listing(f"{coll}/level2/", "/"):
            name = p.rstrip("/").rsplit("/", 1)[1]
            if p.endswith("/") and re.match(r"^\d{4}W\d{2}_", name) and name not in parts:
                parts[name] = p
    names = [n for n in week_order(list(parts)) if n.startswith(only)]
    print(f"dedektör {det}: {len(names)} hafta parçası", flush=True)

    t0, total = time.time(), 0
    for i, name in enumerate(names, 1):
        target = out_dir / f"{name}.json"
        if target.exists():
            continue
        versions = sorted(v for v in listing(parts[name], "/") if v.endswith("/") and "retry" not in v)
        if not versions:
            continue
        base = versions[-1] + det + "/"
        keys = [k for k in listing(base) if k.endswith(".fits")]
        with ThreadPoolExecutor(THREADS) as ex:
            rows = [r for r in ex.map(read_wcs, keys) if r]
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps({"prefix": base, "columns": ["file", *KEYS], "rows": rows}), encoding="utf-8")
        tmp.replace(target)
        total += len(keys)
        rate = total / max(1e-6, time.time() - t0)
        print(f"[{i}/{len(names)}] {name}: {len(rows)}/{len(keys)} poz  ({rate:.0f} dosya/sn)", flush=True)
    print("bitti", flush=True)


if __name__ == "__main__":
    main()
