from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import os
import re
from typing import Any, cast
from astropy.coordinates import SkyCoord
import astropy.units as u
import numpy as np
import streamlit as st

import importlib
import sys
from pathlib import Path

_main_module = None

try:
    import main as _main_module  # type: ignore[no-redef]
except ImportError:
    try:
        if __package__:
            _main_module = importlib.import_module(f"{__package__}.main")
    except ImportError:
        pass

if _main_module is None:
    root_dir = Path(__file__).resolve().parent
    if str(root_dir) not in sys.path:
        sys.path.insert(0, str(root_dir))
    _main_module = importlib.import_module("main")

angular_sep_deg = _main_module.angular_sep_deg
create_http_session = _main_module.create_http_session
download_epoch_image = _main_module.download_epoch_image
generate_offline_demo_results = _main_module.generate_offline_demo_results
mjd_to_date = _main_module.mjd_to_date
point_in_s_region = _main_module.point_in_s_region
query_irsa_catalog = _main_module.query_irsa_catalog
save_outputs = _main_module.save_outputs

st.set_page_config(page_title="SPHEREx Zaman Serisi & Planet X Analizi", layout="wide")

st.title("SPHEREx Kızılötesi Zaman Serisi & Fark Analizi")
st.caption("NASA/IPAC IRSA arşivinden çok dönemli kesitler alır, WCS ile hizalar, yığar ve fark GIF'i üretir.")

# URL parametrelerinden varsayılan değerleri oku (Madde 6: ?hedef=TRAPPIST-1&fov=4.0&bant=2.5&kare=12)
qp = st.query_params
default_target = qp.get("hedef", "TRAPPIST-1")
default_fov = float(qp.get("fov", 4.0))
default_band = float(qp.get("bant", 2.5))
default_frames = int(qp.get("kare", 12))
auto_run = qp.get("oto", "0") == "1"

with st.sidebar:
    st.header("Gözlem Parametreleri")
    input_mode = st.radio("Giriş Türü", ["Gök Cismi Adı", "RA / Dec Koordinatı"])

    if input_mode == "Gök Cismi Adı":
        target_name = st.text_input("Hedef Adı", value=default_target, placeholder="Örn: TRAPPIST-1, R Draconis")
        ra_input, dec_input = "", ""
    else:
        target_name = ""
        ra_input = st.text_input("RA (Derece veya Saat Açısı)", value="346.6224")
        dec_input = st.text_input("Dec (Derece)", value="-5.0414")

    fov_arcmin = st.slider("Görüş Alanı / FOV (yay-dakikası)", min_value=1.0, max_value=20.0, value=default_fov, step=0.5)
    band_um = st.slider("Dalga Boyu (µm)", min_value=0.75, max_value=5.0, value=default_band, step=0.05)
    max_frames = st.slider("Maksimum Kare Sayısı", min_value=3, max_value=24, value=default_frames, step=1)
    use_demo_fallback = st.checkbox("IRSA erişilemezse simülasyon verisi kullan", value=True)

    run_btn = st.button("Analizi Başlat ve GIF Üret", type="primary", use_container_width=True)

if run_btn or auto_run:
    try:
        with st.spinner("Koordinat çözümleniyor..."):
            if input_mode == "Gök Cismi Adı":
                if not target_name.strip():
                    st.error("Lütfen bir hedef adı girin.")
                    st.stop()
                coord_obj = SkyCoord.from_name(target_name.strip())
                clean_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", target_name.strip().lower()).strip("_")
                base_name = f"spherex_{clean_name}"
            else:
                try:
                    coord_obj = SkyCoord(ra=float(ra_input) * u.deg, dec=float(dec_input) * u.deg, frame="icrs")
                except ValueError:
                    coord_obj = SkyCoord(ra=ra_input, dec=dec_input, unit=(u.hourangle, u.deg), frame="icrs")
                base_name = ""

            ra_deg = float(cast(Any, coord_obj.ra).to_value(u.deg))
            dec_deg = float(cast(Any, coord_obj.dec).to_value(u.deg))
            if not base_name:
                base_name = f"spherex_ra{ra_deg:.2f}_dec{dec_deg:.2f}"

        st.info(f"Hedef Koordinatı: **RA = {ra_deg:.4f}°**, **Dec = {dec_deg:.4f}°** | Bant: **{band_um:.2f} µm**")

        session = create_http_session()
        with st.spinner("NASA IRSA kataloğu sorgulanıyor..."):
            raw_rows = query_irsa_catalog(session, ra_deg, dec_deg, band_um)

        results: list[dict[str, Any]] = []
        total_mb = 0.0

        if raw_rows is None:
            session.close()
            if use_demo_fallback:
                st.warning("NASA IRSA sunucusuna şu an erişilemedi; çevrimdışı simülasyon verisi kullanılıyor.")
                base_name = f"{base_name}_demo"
                results = generate_offline_demo_results(band_um, max_frames)
            else:
                st.error("NASA IRSA sunucusuna bağlanılamadı.")
                st.stop()
        else:
            if not raw_rows or "obs_id" not in raw_rows[0]:
                session.close()
                st.warning("Bu koordinat ve dalga boyunda SPHEREx görüntüsü bulunamadı.")
                st.stop()

            valid_rows = [
                {
                    "obs_id": r["obs_id"],
                    "t_min": float(r["t_min"]),
                    "em_min": float(r["em_min"]),
                    "s_ra": float(r["s_ra"]),
                    "s_dec": float(r["s_dec"]),
                    "access_url": r["access_url"],
                    "access_format": r["access_format"],
                    "obs_date": mjd_to_date(float(r["t_min"])),
                }
                for r in raw_rows
                if point_in_s_region(ra_deg, dec_deg, r.get("s_region", ""))
            ]

            if not valid_rows:
                session.close()
                st.warning("Hedefi tam kapsayan dedektör çekimi bulunamadı.")
                st.stop()

            seps = angular_sep_deg(
                np.array([x["s_ra"] for x in valid_rows], dtype=np.float64),
                np.array([x["s_dec"] for x in valid_rows], dtype=np.float64),
                ra_deg,
                dec_deg,
            )
            candidates_by_date: dict[str, list[dict[str, Any]]] = {}
            for row, sep in zip(valid_rows, seps):
                row["sep"] = float(sep)
                candidates_by_date.setdefault(row["obs_date"], []).append(row)

            for d in candidates_by_date:
                candidates_by_date[d].sort(key=lambda x: x["sep"])
                candidates_by_date[d] = candidates_by_date[d][:2]

            sorted_dates = sorted(candidates_by_date.keys())
            target_count = min(len(sorted_dates), max_frames)
            indices = np.linspace(0, len(sorted_dates) - 1, target_count, dtype=int)
            selected_dates = [sorted_dates[i] for i in indices]

            progress_bar = st.progress(0, text=f"{target_count} farklı tarih indiriliyor...")
            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = [
                    executor.submit(download_epoch_image, d, candidates_by_date[d], ra_deg, dec_deg, fov_arcmin, session)
                    for d in selected_dates
                ]
                completed = 0
                for future in as_completed(futures):
                    completed += 1
                    res = future.result()
                    if res is not None:
                        total_mb += float(res["mb"])
                        results.append(res)
                    progress_bar.progress(
                        completed / target_count,
                        text=f"İndirilen kare: {len(results)}/{target_count} ({total_mb:.1f} MB)",
                    )
            session.close()

        if len(results) < 2:
            st.error("GIF oluşturmak için en az 2 geçerli tarih gerekli.")
            st.stop()

        save_outputs(results, base_name, total_mb)

        gif_path = f"{base_name}.gif"
        diff_gif_path = f"{base_name}_fark.gif"
        stack_path = os.path.join(f"{base_name}_kareler", "00_toplam_stack.png")
        csv_path = os.path.join(f"{base_name}_kareler", "meta_veri_raporu.csv")

        st.success(f"Tamamlandı! Toplam {len(results)} kare işlendi ({total_mb:.1f} MB).")

        col1, col2, col3 = st.columns(3)
        with col1:
            st.subheader("1. Kronolojik Zaman Serisi")
            gif_bytes = open(gif_path, "rb").read()
            st.image(gif_bytes, use_container_width=True)
            st.download_button("Kronolojik GIF İndir", data=gif_bytes, file_name=gif_path, mime="image/gif")

        with col2:
            st.subheader("2. Fark / Hareket Analizi")
            diff_bytes = open(diff_gif_path, "rb").read()
            st.image(diff_bytes, use_container_width=True)
            st.download_button("Fark GIF İndir", data=diff_bytes, file_name=diff_gif_path, mime="image/gif")

        with col3:
            st.subheader("3. Medyan Toplam (Stack)")
            stack_bytes = open(stack_path, "rb").read()
            st.image(stack_bytes, use_container_width=True)
            st.download_button(
                "Stack PNG İndir", data=stack_bytes, file_name="00_toplam_stack.png", mime="image/png"
            )

        st.subheader("Meta Veri ve Kirlenme Bayrakları Raporu")
        with open(csv_path, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        st.dataframe(rows, use_container_width=True)

    except Exception as exc:
        st.error(f"İşlem sırasında hata oluştu: {exc}")