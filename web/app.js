document.addEventListener("DOMContentLoaded", () => {
    console.log("Harman Gelişmiş Arayüz Yüklendi!");
    loadTargets();

    const loadButton = document.querySelector(".controls button");
    if (loadButton) {
        loadButton.onclick = fetchSkyImages;
    }
});

// Hedef butonlarını yükleyen fonksiyon
async function loadTargets() {
    try {
        let response = await fetch("targets.json");
        let targets = await response.json();
        let container = document.getElementById("target-buttons");
        container.innerHTML = "";
        
        targets.forEach(t => {
            let btn = document.createElement("button");
            btn.innerText = t.name;
            btn.onclick = () => {
                document.getElementById("ra-input").value = t.ra;
                document.getElementById("dec-input").value = t.dec;
                document.getElementById("size-input").value = t.size;
                console.log(`Seçilen hedef: ${t.name} (${t.description})`);
            };
            container.appendChild(btn);
        });
    } catch (e) {
        console.error("Hedefler yüklenemedi:", e);
    }
}

// SPHEREx zaman tüneli verilerini çekip GIF'i ekranda oynatan fonksiyon
async function fetchSkyImages() {
    let ra = document.getElementById("ra-input").value;
    let dec = document.getElementById("dec-input").value;
    let size = document.getElementById("size-input").value;
    let viewer = document.getElementById("viewer-container");

    if (!ra || !dec) {
        alert("Lütfen koordinat girin veya yukarıdan bir hedef seçin!");
        return;
    }

    viewer.innerHTML = `<p style="color: #38bdf8;">NASA IRSA verileri indiriliyor ve zaman tüneli GIF'i oluşturuluyor, lütfen bekleyin...</p>`;

    try {
        let response = await fetch(`http://127.0.0.1:8000/api/generate?ra=${ra}&dec=${dec}&fov=${size}`);
        let data = await response.json();

        if (data.status === "success") {
            viewer.innerHTML = `
                <div style="text-align: center;">
                    <p style="color: #4ade80; margin-bottom: 10px; font-weight: bold;">✔ ${data.message}</p>
                    <img src="${data.gif_url}" alt="SPHEREx Zaman Tüneli" style="max-width: 100%; border-radius: 8px; border: 1px solid #334155; box-shadow: 0 4px 12px rgba(0,0,0,0.5);" />
                </div>
            `;
        } else {
            viewer.innerHTML = `<p style="color: #ef4444;">Hata: ${data.message}</p>`;
        }
    } catch (e) {
        viewer.innerHTML = `<p style="color: #ef4444;">Bağlantı hatası: ${e}</p>`;
    }
}