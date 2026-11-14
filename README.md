# Harman - SPHEREx Zaman Makinesi

NASA Space Apps Challenge 2026 - Takım Harman

Gökyüzü koordinatı (RA, Dec) gir; SPHEREx'in o bölgeyi farklı tarihlerde çektiği
görüntüler mini video gibi oynasın. Hareket eden ve parlaklığı değişen cisimler görünür hale gelsin.

Test hedefleri: Barnard Yıldızı, Epsilon Indi B, WISE 0855, RW Aurigae, R Draconis

## Klasörler
- api/ (Kişi 1): FastAPI, IRSA sorguları, PNG/GIF
- processing/ (Kişi 2): temizleme, hizalama, birleştirme, spektrum
- notebooks/ (Kişi 2): denemeler (notebooks/data/ GitHub'a girmez)
- web/ (Kişi 3): arayüz
- tests/ (herkes)

## Kurulum
    python -m venv .venv
    .venv\Scripts\activate
    pip install -r requirements.txt

## Çalıştırma
    uvicorn api.main:app --reload

## Git kuralları
- main dalına doğrudan push yok; dallar: backend/..., processing/..., web/...
- Her iş Pull Request ve bir onayla birleşir.
- FITS dosyaları ve cache/ GitHub'a yüklenmez.

## Veri
NASA/IPAC IRSA, SPHEREx (spherex_dr1, spherex_qr3). API anahtarı gerekmez.
