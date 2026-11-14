/* Harman - SPHEREx Zaman Tüneli: arayüz mantığı (derleme adımı yok, saf JS) */
(function (root) {
    "use strict";

    // ------------------------------------------------------------------
    // 1) Saf yardımcılar (DOM gerektirmez; node ile test edilebilir)
    // ------------------------------------------------------------------

    const DETECTORS = [
        { id: "D1", min: 0.75, max: 1.11, center: 0.93 },
        { id: "D2", min: 1.10, max: 1.65, center: 1.37 },
        { id: "D3", min: 1.64, max: 2.42, center: 2.03 },
        { id: "D4", min: 2.42, max: 3.82, center: 3.12 },
        { id: "D5", min: 3.81, max: 4.42, center: 4.11 },
        { id: "D6", min: 4.42, max: 5.00, center: 4.71 }
    ];
    const DEFAULT_DETECTOR = "D4";
    const FOV_MIN = 1, FOV_MAX = 30, FOV_DEFAULT = 4;
    const FRAMES_MIN = 2, FRAMES_MAX = 40, FRAMES_DEFAULT = 12;

    /** Verilen dalga boyunu (µm) içeren dedektör; örtüşmede merkezi en yakın olan. */
    function detectorForBand(band) {
        if (typeof band !== "number" || !isFinite(band)) return null;
        let best = null;
        for (const d of DETECTORS) {
            if (band >= d.min && band <= d.max) {
                if (!best || Math.abs(band - d.center) < Math.abs(band - best.center)) best = d;
            }
        }
        return best;
    }

    /** "12", "+12.5", "-.5" gibi düz ondalık sayı mı? */
    function parsePlainNumber(s) {
        if (!/^[+-]?(\d+\.?\d*|\.\d+)$/.test(s)) return null;
        const v = Number(s);
        return isFinite(v) ? v : null;
    }

    /**
     * Altmışlık metni ayrıştırır: "08h55m10.831s", "08:55:10.83", "08 55 10.83",
     * "-7d14m42.53s", "-07:14:42.5", "+04°41′36″".
     * Dönüş: {value, sign} (value işaretli, birim girdiyle aynı: saat ya da derece) veya null.
     */
    function parseSexagesimal(text) {
        let s = String(text).trim().replace(/[−–—]/g, "-");
        let sign = 1;
        if (s[0] === "+" || s[0] === "-") {
            if (s[0] === "-") sign = -1;
            s = s.slice(1).trim();
        }
        s = s.replace(/[hdms°'"′″:]/gi, " ").trim();
        if (!s) return null;
        const parts = s.split(/\s+/);
        if (parts.length < 1 || parts.length > 3) return null;
        const nums = [];
        for (let i = 0; i < parts.length; i++) {
            if (!/^(\d+\.?\d*|\.\d+)$/.test(parts[i])) return null;
            // yalnızca son bileşen kesirli olabilir
            if (i < parts.length - 1 && parts[i].indexOf(".") !== -1) return null;
            nums.push(Number(parts[i]));
        }
        if (nums.length > 1 && nums[1] >= 60) return null;
        if (nums.length > 2 && nums[2] >= 60) return null;
        const value = nums[0] + (nums[1] || 0) / 60 + (nums[2] || 0) / 3600;
        return { value: sign * value, sign: sign };
    }

    /**
     * RA ayrıştır -> {ok:true, deg} | {ok:false, error}.
     * Düz sayı = derece. h / : / boşluklu biçim = saat. d / ° içeriyorsa = derece.
     */
    function parseRA(text) {
        const s = String(text == null ? "" : text).trim();
        if (!s) return { ok: false, error: "RA girin (derece ya da ss:dd:ss)." };
        let deg;
        const plain = parsePlainNumber(s.replace(/[−–—]/g, "-"));
        if (plain !== null) {
            deg = plain;
        } else {
            const sx = parseSexagesimal(s);
            if (!sx) return { ok: false, error: "RA anlaşılamadı. Örnek: 133.7951 veya 08h55m10.83s" };
            if (sx.sign < 0) return { ok: false, error: "RA negatif olamaz." };
            const isDegrees = /[d°]/i.test(s) && !/h/i.test(s);
            if (isDegrees) {
                deg = sx.value;
            } else {
                if (sx.value >= 24) return { ok: false, error: "RA saat cinsinden 0–24 arasında olmalı." };
                deg = sx.value * 15;
            }
        }
        if (!(deg >= 0 && deg <= 360)) return { ok: false, error: "RA 0–360° arasında olmalı." };
        if (deg === 360) deg = 0;
        return { ok: true, deg: deg };
    }

    /** Dec ayrıştır -> {ok:true, deg} | {ok:false, error}. */
    function parseDec(text) {
        const s = String(text == null ? "" : text).trim();
        if (!s) return { ok: false, error: "Dec girin (derece ya da dd:dd:ss)." };
        let deg;
        const plain = parsePlainNumber(s.replace(/[−–—]/g, "-"));
        if (plain !== null) {
            deg = plain;
        } else {
            if (/h/i.test(s)) return { ok: false, error: "Dec saat (h) cinsinden olamaz." };
            const sx = parseSexagesimal(s);
            if (!sx) return { ok: false, error: "Dec anlaşılamadı. Örnek: -7.2451 veya -7d14m42.5s" };
            deg = sx.value;
        }
        if (!(deg >= -90 && deg <= 90)) return { ok: false, error: "Dec −90° ile +90° arasında olmalı." };
        return { ok: true, deg: deg };
    }

    /** "RA, Dec" ya da "08h55m10s -7d14m42s" gibi tek satırı ikiye böler; bölemezse null. */
    function splitCoordPair(text) {
        const s = String(text).trim().replace(/[−–—]/g, "-");
        let m = s.match(/^(.+?)\s*[,;]\s*(.+)$/);
        if (m) return [m[1].trim(), m[2].trim()];
        m = s.match(/^(.+?)\s+([+-].+)$/);
        if (m) return [m[1].trim(), m[2].trim()];
        return null;
    }

    function pad2(n) { return (n < 10 ? "0" : "") + n; }

    /** Derece -> "08h55m10.83s" */
    function formatRAHms(deg) {
        let totalCs = Math.round(((deg % 360 + 360) % 360) / 15 * 3600 * 100);
        if (totalCs >= 24 * 3600 * 100) totalCs = 0;
        const h = Math.floor(totalCs / 360000);
        const m = Math.floor((totalCs - h * 360000) / 6000);
        const sec = (totalCs - h * 360000 - m * 6000) / 100;
        return pad2(h) + "h" + pad2(m) + "m" + (sec < 10 ? "0" : "") + sec.toFixed(2) + "s";
    }

    /** Derece -> "-07d14m42.5s" */
    function formatDecDms(deg) {
        const sign = deg < 0 ? "-" : "+";
        const totalDs = Math.round(Math.abs(deg) * 3600 * 10);
        const d = Math.floor(totalDs / 36000);
        const m = Math.floor((totalDs - d * 36000) / 600);
        const sec = (totalDs - d * 36000 - m * 600) / 10;
        return sign + pad2(d) + "d" + pad2(m) + "m" + (sec < 10 ? "0" : "") + sec.toFixed(1) + "s";
    }

    /** Eksen için "güzel" çentikler. */
    function niceTicks(min, max, count) {
        if (!(max > min)) { max = min + 1; }
        const span = max - min;
        const raw = span / Math.max(1, count);
        const mag = Math.pow(10, Math.floor(Math.log10(raw)));
        const norm = raw / mag;
        const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
        const ticks = [];
        for (let v = Math.ceil(min / step - 1e-9) * step; v <= max + step * 1e-9; v += step) {
            ticks.push(Math.abs(v) < step * 1e-9 ? 0 : v);
        }
        return { ticks: ticks, step: step };
    }

    function formatTick(v, step) {
        const a = Math.abs(v);
        if (a !== 0 && (a >= 1e5 || a < 1e-3)) return v.toExponential(1);
        const decimals = Math.max(0, Math.min(6, -Math.floor(Math.log10(step) + 1e-9)));
        return v.toFixed(decimals);
    }

    function formatNumber(v, digits) {
        if (typeof v !== "number" || !isFinite(v)) return "—";
        const a = Math.abs(v);
        if (a !== 0 && (a >= 1e5 || a < 1e-3)) return v.toExponential(2);
        return v.toFixed(digits == null ? 3 : digits);
    }

    /** API tabanını çöz: ?api= > window.API_BASE > aynı köken (""). */
    function resolveApiBase(search, globalBase) {
        let base = "";
        try {
            const p = new URLSearchParams(search || "");
            if (p.get("api")) base = p.get("api");
            else if (typeof globalBase === "string") base = globalBase;
        } catch (e) { base = typeof globalBase === "string" ? globalBase : ""; }
        base = String(base).trim().replace(/\/+$/, "");
        if (base && !/^https?:\/\//i.test(base)) return ""; // yalnızca http(s) kabul et
        return base;
    }

    /** Kök-göreli sunucu yolunu API tabanıyla birleştir; yalnızca http(s)/göreli adreslere izin ver. */
    function assetUrl(base, url) {
        if (typeof url !== "string" || !url) return null;
        if (/^https?:\/\//i.test(url)) return url;
        if (/^[a-z][a-z0-9+.-]*:/i.test(url) || url.indexOf("//") === 0) return null; // javascript:, data: vb.
        return base + (url[0] === "/" ? "" : "/") + url;
    }

    const pure = {
        DETECTORS, detectorForBand, parseSexagesimal, parseRA, parseDec, splitCoordPair,
        formatRAHms, formatDecDms, niceTicks, formatTick, formatNumber, resolveApiBase, assetUrl
    };
    if (typeof module !== "undefined" && module.exports) module.exports = pure;
    if (typeof document === "undefined") return;

    // ------------------------------------------------------------------
    // 2) Tarayıcı tarafı
    // ------------------------------------------------------------------

    const API_BASE = resolveApiBase(root.location.search, root.API_BASE);
    // Önce CDS'in kendi adresi, ulaşılamazsa npm paketinin CDN kopyası denenir
    const ALADIN_JS = [
        "https://aladin.cds.unistra.fr/AladinLite/api/v3/latest/aladin.js",
        "https://cdn.jsdelivr.net/npm/aladin-lite@3.6.5/dist/aladin.js"
    ];
    const SVG_NS = "http://www.w3.org/2000/svg";
    // Koyu zemin için kategorik renkler (sabit sıra) + ikincil kodlama olarak şekil
    const SERIES_COLORS = ["#ece8df", "#ff5a1f", "#199e70", "#c98500", "#d55181", "#0ca30c", "#9085e9", "#e66767"];
    const SERIES_SHAPES = ["circle", "square", "diamond", "triangle"];
    const OTHER_COLOR = "#8a94a6";

    const $ = (id) => document.getElementById(id);
    const el = {};

    const state = {
        requestId: 0,
        abort: null,
        timer: null,
        busy: false,
        customBand: null,       // targets.json / URL'den gelen tam dalga boyu
        nameForCoords: "",      // koordinatların kaynağı olan cisim adı
        data: null,
        params: null,
        mode: "frames",
        playlists: { frames: [], epochs: [] },
        diffImg: null,
        index: 0,
        playing: false,
        playTimer: null,
        spectrum: { points: [], nodes: [], highlight: -1 },
        aladin: { status: "idle", instance: null, gaia: null, gaiaKey: "" }
    };

    function make(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text != null) node.textContent = text;
        return node;
    }
    function svg(tag, attrs) {
        const node = document.createElementNS(SVG_NS, tag);
        if (attrs) for (const k in attrs) node.setAttribute(k, attrs[k]);
        return node;
    }
    function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

    // ---------------- Form ----------------

    function buildBandSelect() {
        clear(el.band);
        for (const d of DETECTORS) {
            const opt = make("option", null,
                d.id + " · " + d.min.toFixed(2) + "–" + d.max.toFixed(2) + " µm");
            opt.value = d.id;
            el.band.appendChild(opt);
        }
        el.band.value = DEFAULT_DETECTOR;
        updateBandHint();
    }

    function currentBand() {
        const det = DETECTORS.find((d) => d.id === el.band.value) || DETECTORS[3];
        if (state.customBand != null) {
            const cd = detectorForBand(state.customBand);
            if (cd && cd.id === det.id) return state.customBand;
        }
        return det.center;
    }

    function updateBandHint() {
        el.bandHint.textContent = "Gönderilecek dalga boyu: " + currentBand() + " µm";
    }

    /** Sayısal dalga boyunu forma uygula; aralık dışıysa false. */
    function applyBand(band) {
        const det = detectorForBand(band);
        if (!det) return false;
        state.customBand = band;
        el.band.value = det.id;
        updateBandHint();
        return true;
    }

    function setError(id, msg) {
        const node = $(id + "-error");
        const input = $(id + "-input");
        if (node) node.textContent = msg || "";
        if (input) {
            if (msg) input.setAttribute("aria-invalid", "true");
            else input.removeAttribute("aria-invalid");
        }
    }
    function clearErrors() { ["name", "ra", "dec", "fov", "frames"].forEach((k) => setError(k, "")); }

    /** Formu oku + doğrula. Hata varsa satır içi gösterir ve null döner. */
    function readForm() {
        clearErrors();
        let ok = true;

        // "RA, Dec" tek kutuya yapıştırıldıysa böl
        if (el.ra.value.trim() && !el.dec.value.trim()) {
            const pair = splitCoordPair(el.ra.value);
            if (pair && parseRA(pair[0]).ok && parseDec(pair[1]).ok) {
                el.ra.value = pair[0];
                el.dec.value = pair[1];
            }
        }

        const ra = parseRA(el.ra.value);
        if (!ra.ok) { setError("ra", ra.error); ok = false; }
        const dec = parseDec(el.dec.value);
        if (!dec.ok) { setError("dec", dec.error); ok = false; }

        const fov = Number(String(el.fov.value).replace(",", "."));
        if (!el.fov.value.trim() || !isFinite(fov) || fov < FOV_MIN || fov > FOV_MAX) {
            setError("fov", "Görüş alanı " + FOV_MIN + "–" + FOV_MAX + " yay dakikası olmalı.");
            ok = false;
        }
        const frames = Number(el.frames.value);
        if (!el.frames.value.trim() || !Number.isInteger(frames) || frames < FRAMES_MIN || frames > FRAMES_MAX) {
            setError("frames", "Kare sayısı " + FRAMES_MIN + "–" + FRAMES_MAX + " arasında tam sayı olmalı.");
            ok = false;
        }
        if (!ok) return null;
        return {
            ra: Math.round(ra.deg * 1e6) / 1e6,
            dec: Math.round(dec.deg * 1e6) / 1e6,
            fov: fov,
            band: currentBand(),
            frames: frames
        };
    }

    // ---------------- Durum / bekleme ----------------

    function setStatus(kind, text, opts) {
        opts = opts || {};
        el.status.hidden = false;
        el.status.className = "status " + kind;
        el.statusText.textContent = text;
        el.statusSub.textContent = opts.sub || "";
        el.statusBar.hidden = !opts.progress;
        if (!opts.keepElapsed) el.statusElapsed.textContent = "";
    }

    function startTimer() {
        stopTimer();
        const t0 = Date.now();
        const tick = () => {
            const s = Math.floor((Date.now() - t0) / 1000);
            el.statusElapsed.textContent = "Geçen süre: " +
                (s >= 60 ? Math.floor(s / 60) + " dk " + (s % 60) + " sn" : s + " sn");
            if (s >= 20) {
                el.statusSub.textContent = "Hâlâ çalışıyor — ilk istekte veriler NASA IRSA’dan indirilir; " +
                    "bu bir dakika ya da daha uzun sürebilir. Sayfayı kapatmayın.";
            }
        };
        tick();
        state.timer = setInterval(tick, 1000);
    }
    function stopTimer() {
        if (state.timer) { clearInterval(state.timer); state.timer = null; }
    }

    function setBusy(busy) {
        state.busy = busy;
        el.loadBtn.disabled = busy;
        el.resolveBtn.disabled = busy;
        el.loadBtn.textContent = busy ? "Yükleniyor…" : "Görüntüleri Yükle";
        el.targets.querySelectorAll("button").forEach((b) => { b.disabled = busy; });
    }

    async function fetchJson(url, signal) {
        let response;
        try {
            response = await fetch(url, { signal: signal, headers: { "Accept": "application/json" } });
        } catch (e) {
            if (e && e.name === "AbortError") throw e;
            throw new Error("Sunucuya ulaşılamadı. Arka uç çalışıyor mu? (" + (API_BASE || "aynı köken") + ")");
        }
        let data = null;
        try { data = await response.json(); } catch (e) { data = null; }
        if (!data || typeof data !== "object") {
            throw new Error("Sunucudan geçersiz yanıt alındı (HTTP " + response.status + ").");
        }
        if (data.status !== "success") {
            let msg = data.message;
            if (!msg && data.detail) msg = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
            throw new Error(String(msg || ("İstek başarısız oldu (HTTP " + response.status + ").")));
        }
        return data;
    }

    // ---------------- Ad çözümleme ----------------

    async function resolveName(thenRun) {
        const name = el.name.value.trim();
        clearErrors();
        if (!name) { setError("name", "Bir cisim adı yazın."); return false; }
        if (state.busy) return false;

        const myId = ++state.requestId;
        if (state.abort) state.abort.abort();
        state.abort = new AbortController();
        setBusy(true);
        setStatus("loading", "“" + name + "” aranıyor…", { progress: true });
        try {
            const data = await fetchJson(API_BASE + "/api/resolve?name=" + encodeURIComponent(name), state.abort.signal);
            if (myId !== state.requestId) return false;
            const ra = Number(data.ra), dec = Number(data.dec);
            if (!isFinite(ra) || !isFinite(dec)) throw new Error("Sunucu koordinat döndürmedi.");
            el.ra.value = String(Math.round(ra * 1e6) / 1e6);
            el.dec.value = String(Math.round(dec * 1e6) / 1e6);
            state.nameForCoords = name;
            setBusy(false);
            setStatus("success", "Bulundu: " + String(data.name || name) + " → RA " +
                formatRAHms(ra) + ", Dec " + formatDecDms(dec));
            if (thenRun) run();
            return true;
        } catch (e) {
            if (myId !== state.requestId || (e && e.name === "AbortError")) return false;
            setBusy(false);
            el.status.hidden = true;
            setError("name", "Bulunamadı: " + e.message);
            return false;
        }
    }

    // ---------------- Ana istek ----------------

    function loadImage(url) {
        return new Promise((resolve) => {
            if (!url) { resolve(null); return; }
            const img = new Image();
            img.onload = () => resolve(img);
            img.onerror = () => resolve(null);
            img.src = url;
        });
    }

    async function run(opts) {
        opts = opts || {};
        const params = readForm();
        if (!params) return;
        if (state.busy) return;

        const myId = ++state.requestId;
        if (state.abort) state.abort.abort();
        state.abort = new AbortController();
        pause();
        setBusy(true);
        setStatus("loading", "SPHEREx görüntüleri hazırlanıyor…", {
            progress: true,
            sub: "Veriler NASA IRSA arşivinden alınıyor. İlk istek bir dakikadan uzun sürebilir."
        });
        startTimer();

        const q = new URLSearchParams({
            ra: String(params.ra), dec: String(params.dec), fov: String(params.fov),
            band: String(params.band), frames: String(params.frames)
        });
        if (opts.refresh) q.set("refresh", "1");

        try {
            const data = await fetchJson(API_BASE + "/api/generate?" + q.toString(), state.abort.signal);
            if (myId !== state.requestId) return;

            const frames = Array.isArray(data.frames) ? data.frames.filter((f) => f && typeof f === "object") : [];
            const epochs = Array.isArray(data.epochs) ? data.epochs.filter((e) => e && typeof e === "object") : [];
            if (!frames.length) throw new Error(String(data.message || "Bu konum için görüntü bulunamadı."));

            // Tüm görüntüleri önceden yükle
            const jobs = [];
            let done = 0;
            const track = (p) => p.then((img) => {
                done++;
                if (myId === state.requestId) {
                    el.statusText.textContent = "Görüntüler yükleniyor… " + done + " / " + jobs.length;
                }
                return img;
            });
            frames.forEach((f) => jobs.push(null));
            epochs.forEach((e) => jobs.push(null));
            jobs.push(null);
            let j = 0;
            const framePromises = frames.map((f) => { const p = track(loadImage(assetUrl(API_BASE, f.url))); jobs[j++] = p; return p; });
            const epochPromises = epochs.map((e) => { const p = track(loadImage(assetUrl(API_BASE, e.stack_url))); jobs[j++] = p; return p; });
            const diffPromise = track(loadImage(assetUrl(API_BASE, data.diff_url))); jobs[j++] = diffPromise;

            const frameImgs = await Promise.all(framePromises);
            const epochImgs = await Promise.all(epochPromises);
            const diffImg = await diffPromise;
            if (myId !== state.requestId) return;

            const framePl = [];
            frames.forEach((f, i) => { if (frameImgs[i]) framePl.push({ img: frameImgs[i], frame: f }); });
            const epochPl = [];
            epochs.forEach((e, i) => { if (epochImgs[i]) epochPl.push({ img: epochImgs[i], epoch: e }); });
            if (!framePl.length) throw new Error("Görüntü dosyaları sunucudan yüklenemedi.");

            stopTimer();
            setBusy(false);
            const missing = frames.length - framePl.length;
            setStatus("success", "✔ " + String(data.message || (framePl.length + " kare yüklendi.")), {
                keepElapsed: true,
                sub: missing > 0 ? missing + " kare yüklenemedi ve atlandı." : ""
            });

            state.data = data;
            state.params = params;
            state.epochs = epochs;
            state.playlists = { frames: framePl, epochs: epochPl };
            state.diffImg = diffImg;
            showResults();
            updateUrl(params);
        } catch (e) {
            if (myId !== state.requestId || (e && e.name === "AbortError")) return;
            stopTimer();
            setBusy(false);
            setStatus("error", "Hata: " + (e && e.message ? e.message : String(e)), { keepElapsed: true });
        }
    }

    // ---------------- Sonuçlar / oynatıcı ----------------

    function showResults() {
        const data = state.data, p = state.params;
        el.empty.hidden = true;
        el.results.hidden = false;

        const t = (data.target && typeof data.target === "object") ? data.target : {};
        const ra = isFinite(Number(t.ra)) && t.ra != null ? Number(t.ra) : p.ra;
        const dec = isFinite(Number(t.dec)) && t.dec != null ? Number(t.dec) : p.dec;
        const fov = isFinite(Number(t.fov_arcmin)) && t.fov_arcmin != null ? Number(t.fov_arcmin) : p.fov;
        state.view = { ra: ra, dec: dec, fov: fov };

        el.summary.textContent = (state.nameForCoords ? state.nameForCoords + " · " : "") +
            formatRAHms(ra) + "  " + formatDecDms(dec);
        el.stageFov.textContent = fov + "′ × " + fov + "′";

        clear(el.targetList);
        addMeta(el.targetList, "RA", ra.toFixed(5) + "°  (" + formatRAHms(ra) + ")");
        addMeta(el.targetList, "Dec", dec.toFixed(5) + "°  (" + formatDecDms(dec) + ")");
        addMeta(el.targetList, "Görüş alanı", fov + " yay dk");
        addMeta(el.targetList, "Dalga boyu", (t.band_um != null ? t.band_um : p.band) + " µm" +
            (t.detector ? " (" + String(t.detector) + ")" : ""));
        addMeta(el.targetList, "Kare / dönem", state.playlists.frames.length + " / " + state.epochs.length);

        // Sekmelerin kullanılabilirliği
        const tabDiff = tabFor("diff"), tabEpochs = tabFor("epochs"), tabGif = tabFor("gif");
        tabEpochs.disabled = state.playlists.epochs.length === 0;
        tabEpochs.title = tabEpochs.disabled ? "Bu konum için dönem yığını üretilemedi." : "";
        state.diffLabel = "";
        fillDiffOptions();
        tabDiff.disabled = !state.diffImg && !(data.custom_diff && state.playlists.frames.length >= 2);
        tabDiff.title = tabDiff.disabled
            ? "Fark görüntüsü yok: karşılaştırmak için en az iki ayrı gözlem dönemi gerekir." : "";
        state.gifUrl = assetUrl(API_BASE, data.gif_url);
        tabGif.disabled = !state.gifUrl;
        tabGif.title = tabGif.disabled ? "Bu istek için GIF üretilmedi." : "";

        buildSpectrum();
        state.index = 0;
        setMode("frames");
        initAladin();
    }

    function addMeta(list, label, value) {
        list.appendChild(make("dt", null, label));
        list.appendChild(make("dd", null, value));
    }

    function tabFor(mode) { return el.tabs.querySelector('[data-mode="' + mode + '"]'); }

    function playlist() {
        return state.mode === "epochs" ? state.playlists.epochs : state.playlists.frames;
    }

    // ---------------- Seçmeli fark: tarihe göre iki görüntü ----------------
    function fillDiffOptions() {
        const selA = $("diff-a"), selB = $("diff-b");
        [selA, selB].forEach((sel) => {
            sel.textContent = "";
            const gE = document.createElement("optgroup"); gE.label = "Dönem yığınları";
            state.epochs.forEach((e, i) => {
                const o = document.createElement("option");
                o.value = "e" + i;
                o.textContent = String(e.label != null ? e.label : i) + " dönemi · " + (e.n_stacked != null ? e.n_stacked : e.n_frames) + " poz yığını";
                gE.appendChild(o);
            });
            const gF = document.createElement("optgroup"); gF.label = "Tek kareler (tarih · dalga boyu)";
            state.playlists.frames.forEach((it) => {
                const f = it.frame, o = document.createElement("option");
                o.value = "f" + f.index;
                o.textContent = String(f.date) + " · " + (typeof f.wavelength_um === "number" ? f.wavelength_um.toFixed(2) + " µm" : "?");
                gF.appendChild(o);
            });
            if (gE.children.length) sel.appendChild(gE);
            if (gF.children.length) sel.appendChild(gF);
        });
        const fr = state.playlists.frames;
        if (state.epochs.length >= 2) { selA.value = "e" + (state.epochs.length - 1); selB.value = "e0"; }
        else if (fr.length >= 2) { selA.value = "f" + fr[fr.length - 1].frame.index; selB.value = "f" + fr[0].frame.index; }
        $("diff-msg").textContent = "";
        const can = !!(state.data && state.data.custom_diff);
        $("diff-btn").disabled = !can;
        if (!can) $("diff-msg").textContent = "Bu sonuç eski sürümle üretilmiş; iki görüntüyü kendiniz seçmek için görüntüleri yeniden yükleyin.";
    }

    async function computeDiff() {
        const data = state.data, msg = $("diff-msg"), btn = $("diff-btn");
        if (!data || !data.custom_diff || !data.key) return;
        const a = $("diff-a").value, b = $("diff-b").value;
        if (!a || !b) return;
        if (a === b) { msg.textContent = "İki farklı görüntü seçin."; return; }
        const myId = state.requestId;
        btn.disabled = true;
        msg.textContent = "Fark hesaplanıyor…";
        try {
            const res = await fetchJson(API_BASE + "/api/diff?key=" + encodeURIComponent(data.key) +
                "&a=" + encodeURIComponent(a) + "&b=" + encodeURIComponent(b));
            const img = await loadImage(assetUrl(API_BASE, res.url));
            if (myId !== state.requestId) return;
            if (!img) throw new Error("Fark görüntüsü yüklenemedi.");
            state.diffImg = img;
            state.diffLabel = String(res.label || "");
            msg.textContent = "Parlaklık eşitleme çarpanı: ×" + Number(res.scale).toFixed(2) +
                " (çıkarılan görüntü bu kadar çarpıldı).";
            if (state.mode === "diff") {
                drawImage(img);
                el.stageLabel.textContent = "Fark: " + state.diffLabel;
            }
        } catch (e) {
            msg.textContent = "Hata: " + (e && e.message ? e.message : String(e));
        } finally {
            btn.disabled = false;
        }
    }

    function setMode(mode) {
        pause();
        state.mode = mode;
        el.tabs.querySelectorAll(".tab").forEach((b) => {
            const on = b.dataset.mode === mode;
            b.classList.toggle("active", on);
            b.setAttribute("aria-selected", on ? "true" : "false");
        });
        const isPlayer = mode === "frames" || mode === "epochs";
        el.controls.classList.toggle("disabled", !isPlayer);
        [el.prev, el.play, el.next, el.scrubber].forEach((c) => { c.disabled = !isPlayer; });
        el.canvas.hidden = mode === "gif";
        el.gif.hidden = mode !== "gif";
        el.gifLinks.hidden = mode !== "gif";
        el.stageLabel.textContent = "";
        $("diff-controls").hidden = mode !== "diff";

        if (isPlayer) {
            const pl = playlist();
            state.index = 0;
            el.scrubber.max = String(Math.max(0, pl.length - 1));
            el.scrubber.disabled = pl.length < 2;
            el.caption.textContent = mode === "frames"
                ? "Tek tek SPHEREx pozları. Kareler farklı dalga boylarında olabilir; parlaklık farkının bir kısmı bundandır."
                : (pl.length < 2
                    ? "Yalnızca bir gözlem dönemi var; karşılaştırma için SPHEREx bu bölgeyi yeniden taradığında tekrar deneyin."
                    : "Her dönemdeki karelerin ortalaması. Dönemler arasında gidip gelin: yer değiştiren ya da parlaklığı değişen kaynak göze çarpar.");
            render();
        } else if (mode === "diff") {
            const eps = state.epochs;
            if (state.diffImg) {
                drawImage(state.diffImg);
                el.stageLabel.textContent = state.diffLabel ? "Fark: " + state.diffLabel : (eps.length >= 2
                    ? "Fark: " + String(eps[eps.length - 1].label || "son") + " − " + String(eps[0].label || "ilk") : "Fark");
            } else {
                computeDiff();  // tek dönem: hazır fark yok, seçili iki kareden üret
            }
            el.caption.textContent = "Seçtiğiniz iki görüntünün farkı. Çıkarmadan önce parlaklıklar eşitlenir, " +
                "böylece sabit yıldızlar silinir; hareket eden cisim yan yana siyah–beyaz bir çift, parlaklığı " +
                "değişen yıldız tek bir siyah ya da beyaz nokta olarak kalır. Dalga boyu birbirine yakın iki kare en temiz sonucu verir. " +
                "Parlak yıldızların tam merkezinde küçük bir iz kalması normaldir: SPHEREx pikselleri yıldız görüntüsüne göre iridir.";
            renderMeta(null);
            highlightSpectrum(-1);
            el.counter.textContent = "—";
        } else if (mode === "gif") {
            el.gif.src = state.gifUrl;
            el.caption.textContent = "Tüm karelerin hazır animasyonu; indirip paylaşabilirsiniz.";
            clear(el.gifLinks);
            const a = make("a", "btn-link", "GIF’i indir");
            a.href = state.gifUrl;
            a.setAttribute("download", "spherex_" + state.view.ra.toFixed(4) + "_" + state.view.dec.toFixed(4) + ".gif");
            a.target = "_blank";
            a.rel = "noopener";
            el.gifLinks.appendChild(a);
            renderMeta(null);
            highlightSpectrum(-1);
            el.counter.textContent = "—";
        }
    }

    function drawImage(img) {
        if (!img) return;
        const c = el.canvas;
        if (c.width !== img.naturalWidth || c.height !== img.naturalHeight) {
            c.width = img.naturalWidth;
            c.height = img.naturalHeight;
        }
        const ctx = c.getContext("2d");
        ctx.imageSmoothingEnabled = false;
        ctx.clearRect(0, 0, c.width, c.height);
        ctx.drawImage(img, 0, 0);
    }

    function epochById(id) {
        if (id == null) return null;
        return (state.epochs || []).find((e) => e.id === id) || null;
    }

    function render() {
        const pl = playlist();
        if (!pl.length) return;
        if (state.index >= pl.length) state.index = 0;
        const item = pl[state.index];
        drawImage(item.img);
        el.scrubber.value = String(state.index);
        el.counter.textContent = (state.index + 1) + " / " + pl.length;
        if (state.mode === "frames") {
            const f = item.frame;
            el.stageLabel.textContent = String(f.date || "") +
                (typeof f.wavelength_um === "number" ? "  ·  " + (f.wavelength_approx ? "≈" : "") + f.wavelength_um.toFixed(2) + " µm" : "");
            renderMeta(f);
            highlightSpectrum(spectrumIndexForFrame(f));
        } else {
            const e = item.epoch;
            el.stageLabel.textContent = "Dönem " + String(e.label != null ? e.label : e.id);
            renderMeta(null, e);
            highlightSpectrum(-1);
        }
    }

    function renderMeta(frame, epoch) {
        clear(el.metaList);
        if (frame) {
            addMeta(el.metaList, "Tarih", frame.date != null ? String(frame.date) : "—");
            addMeta(el.metaList, "MJD", typeof frame.mjd === "number" ? frame.mjd.toFixed(4) : "—");
            addMeta(el.metaList, "Dalga boyu", typeof frame.wavelength_um === "number"
                ? (frame.wavelength_approx ? "≈ " : "") + frame.wavelength_um.toFixed(3) + " µm" +
                  (frame.wavelength_approx ? " (yaklaşık)" : "")
                : "—");
            addMeta(el.metaList, "Dedektör", frame.detector != null ? String(frame.detector) : "—");
            addMeta(el.metaList, "Gözlem kimliği", frame.obs_id != null ? String(frame.obs_id) : "—");
            const ep = epochById(frame.epoch);
            addMeta(el.metaList, "Dönem", ep ? String(ep.label != null ? ep.label : ep.id)
                : (frame.epoch != null ? String(frame.epoch) : "—"));
            addMeta(el.metaList, "İşaretli piksel", typeof frame.flagged_fraction === "number"
                ? "%" + (frame.flagged_fraction * 100).toFixed(1) : "—");
        } else if (epoch) {
            addMeta(el.metaList, "Dönem", String(epoch.label != null ? epoch.label : epoch.id));
            addMeta(el.metaList, "Başlangıç", epoch.start != null ? String(epoch.start) : "—");
            addMeta(el.metaList, "Bitiş", epoch.end != null ? String(epoch.end) : "—");
            addMeta(el.metaList, "Yığılan kare", epoch.n_frames != null ? String(epoch.n_frames) : "—");
        } else {
            addMeta(el.metaList, "Görünüm", state.mode === "diff" ? "Dönem farkı" : "GIF animasyonu");
        }
    }

    function step(delta) {
        const pl = playlist();
        if (pl.length < 2 || (state.mode !== "frames" && state.mode !== "epochs")) return;
        state.index = (state.index + delta + pl.length) % pl.length;
        render();
    }

    function play() {
        const pl = playlist();
        if (pl.length < 2 || (state.mode !== "frames" && state.mode !== "epochs")) return;
        if (!el.loop.checked && state.index >= pl.length - 1) state.index = 0;
        state.playing = true;
        el.play.textContent = "⏸";
        el.play.setAttribute("aria-label", "Duraklat");
        schedule();
        render();
    }
    function schedule() {
        if (state.playTimer) clearInterval(state.playTimer);
        const fps = Number(el.speed.value) || 2;
        state.playTimer = setInterval(() => {
            const pl = playlist();
            if (state.index >= pl.length - 1 && !el.loop.checked) { pause(); return; }
            state.index = (state.index + 1) % pl.length;
            render();
        }, 1000 / fps);
    }
    function pause() {
        state.playing = false;
        if (state.playTimer) { clearInterval(state.playTimer); state.playTimer = null; }
        if (el.play) {
            el.play.textContent = "▶";
            el.play.setAttribute("aria-label", "Oynat");
        }
    }
    function togglePlay() { if (state.playing) pause(); else play(); }

    // ---------------- Tayf grafiği ----------------

    function epochIndexForDate(date) {
        const eps = state.epochs || [];
        if (date != null) {
            const d = String(date);
            for (let i = 0; i < eps.length; i++) {
                if (eps[i].start != null && eps[i].end != null && d >= String(eps[i].start) && d <= String(eps[i].end)) return i;
            }
            const fr = (state.playlists.frames || []).find((it) => String(it.frame.date) === d);
            if (fr) {
                const i = eps.findIndex((e) => e.id === fr.frame.epoch);
                if (i >= 0) return i;
            }
        }
        return -1;
    }

    function seriesStyle(i) {
        if (i < 0 || i >= SERIES_COLORS.length) return { color: OTHER_COLOR, shape: "circle" };
        return { color: SERIES_COLORS[i], shape: SERIES_SHAPES[i % SERIES_SHAPES.length] };
    }

    function markNode(shape, x, y, r, color) {
        let node;
        if (shape === "square") {
            node = svg("rect", { x: x - r * 0.9, y: y - r * 0.9, width: r * 1.8, height: r * 1.8 });
        } else if (shape === "diamond") {
            node = svg("path", { d: "M" + x + " " + (y - r * 1.25) + "L" + (x + r * 1.25) + " " + y + "L" + x + " " + (y + r * 1.25) + "L" + (x - r * 1.25) + " " + y + "Z" });
        } else if (shape === "triangle") {
            node = svg("path", { d: "M" + x + " " + (y - r * 1.2) + "L" + (x + r * 1.15) + " " + (y + r * 0.9) + "L" + (x - r * 1.15) + " " + (y + r * 0.9) + "Z" });
        } else {
            node = svg("circle", { cx: x, cy: y, r: r });
        }
        node.setAttribute("fill", color);
        node.setAttribute("class", "mark");
        return node;
    }

    function buildSpectrum() {
        const data = state.data;
        const raw = Array.isArray(data.spectrum) ? data.spectrum : [];
        const pts = raw.filter((p) => p && typeof p.wavelength_um === "number" && isFinite(p.wavelength_um) &&
            typeof p.flux === "number" && isFinite(p.flux)).map((p) => ({
            w: p.wavelength_um, f: p.flux,
            e: (typeof p.flux_err === "number" && isFinite(p.flux_err) && p.flux_err > 0) ? p.flux_err : null,
            date: p.date != null ? String(p.date) : null,
            ep: epochIndexForDate(p.date)
        }));
        state.spectrum = { points: pts, nodes: [], highlight: -1 };
        clear(el.chart); clear(el.legend); clear(el.table);
        el.tip.hidden = true;

        if (!pts.length) {
            el.spectrumWrap.hidden = true;
            el.spectrumNote.textContent = "Bu konum için tayf ölçümü yok (merkezde ölçülebilir bir kaynak bulunamadı ya da pikseller işaretli).";
            return;
        }
        el.spectrumWrap.hidden = false;
        const unit = data.flux_unit ? String(data.flux_unit) : "";
        el.spectrumNote.textContent = "Merkezdeki kaynağın her karedeki parlaklığı. Aynı dalga boyunda farklı dönemlerin noktaları ayrışıyorsa kaynak değişkendir. Vurgulu nokta gösterilen kareye aittir.";

        // Lejant (dönem = renk + şekil)
        const usedEpochs = Array.from(new Set(pts.map((p) => p.ep))).sort((a, b) => a - b);
        if (usedEpochs.length > 1) {
            usedEpochs.forEach((i) => {
                const st = seriesStyle(i);
                const item = make("span", "legend-item");
                const sw = svg("svg", { width: 14, height: 14, viewBox: "0 0 14 14", "aria-hidden": "true" });
                sw.appendChild(markNode(st.shape, 7, 7, 4.5, st.color));
                item.appendChild(sw);
                const ep = i >= 0 ? state.epochs[i] : null;
                item.appendChild(document.createTextNode(ep ? "Dönem " + String(ep.label != null ? ep.label : ep.id) : "Diğer"));
                el.legend.appendChild(item);
            });
        }

        // Ölçekler
        const W = Math.round(Math.max(320, Math.min(980, el.spectrumWrap.clientWidth || 720)));
        const H = W < 480 ? 260 : 320, M = { l: 64, r: 16, t: 14, b: 46 };
        let xmin = Math.min.apply(null, pts.map((p) => p.w));
        let xmax = Math.max.apply(null, pts.map((p) => p.w));
        let ymin = Math.min.apply(null, pts.map((p) => p.f - (p.e || 0)));
        let ymax = Math.max.apply(null, pts.map((p) => p.f + (p.e || 0)));
        const xpad = (xmax - xmin) * 0.06 || 0.05;
        const ypad = (ymax - ymin) * 0.1 || Math.abs(ymax) * 0.1 || 1;
        xmin -= xpad; xmax += xpad; ymin -= ypad; ymax += ypad;
        const X = (v) => M.l + (v - xmin) / (xmax - xmin) * (W - M.l - M.r);
        const Y = (v) => H - M.b - (v - ymin) / (ymax - ymin) * (H - M.t - M.b);

        const s = svg("svg", { viewBox: "0 0 " + W + " " + H, role: "img",
            "aria-label": "Tayf: akı – dalga boyu grafiği" });
        const xt = niceTicks(xmin, xmax, W < 480 ? 4 : 7), yt = niceTicks(ymin, ymax, 5);
        yt.ticks.forEach((v) => {
            s.appendChild(svg("line", { x1: M.l, x2: W - M.r, y1: Y(v), y2: Y(v), class: v === 0 ? "grid zero" : "grid" }));
            const t = svg("text", { x: M.l - 8, y: Y(v) + 4, "text-anchor": "end", class: "tick" });
            t.textContent = formatTick(v, yt.step);
            s.appendChild(t);
        });
        xt.ticks.forEach((v) => {
            s.appendChild(svg("line", { x1: X(v), x2: X(v), y1: H - M.b, y2: H - M.b + 5, class: "axis" }));
            const t = svg("text", { x: X(v), y: H - M.b + 19, "text-anchor": "middle", class: "tick" });
            t.textContent = formatTick(v, xt.step);
            s.appendChild(t);
        });
        s.appendChild(svg("line", { x1: M.l, x2: W - M.r, y1: H - M.b, y2: H - M.b, class: "axis" }));
        const xl = svg("text", { x: (M.l + W - M.r) / 2, y: H - 8, "text-anchor": "middle", class: "axis-label" });
        xl.textContent = "Dalga boyu (µm)";
        s.appendChild(xl);
        const yl = svg("text", { x: 14, y: (M.t + H - M.b) / 2, "text-anchor": "middle", class: "axis-label",
            transform: "rotate(-90 14 " + (M.t + H - M.b) / 2 + ")" });
        yl.textContent = "Akı" + (unit ? " (" + unit + ")" : "");
        s.appendChild(yl);

        // Hata çubukları, sonra işaretler
        pts.forEach((p) => {
            if (p.e) {
                s.appendChild(svg("line", { x1: X(p.w), x2: X(p.w), y1: Y(p.f - p.e), y2: Y(p.f + p.e),
                    class: "errbar", stroke: seriesStyle(p.ep).color }));
            }
        });
        const ring = svg("circle", { r: 10, class: "hl-ring", visibility: "hidden" });
        s.appendChild(ring);
        state.spectrum.ring = ring;
        pts.forEach((p, i) => {
            const st = seriesStyle(p.ep);
            const x = X(p.w), y = Y(p.f);
            p.x = x; p.y = y;
            s.appendChild(markNode(st.shape, x, y, 4.5, st.color));
            const hit = svg("circle", { cx: x, cy: y, r: 12, class: "hit", tabindex: "0" });
            hit.addEventListener("mouseenter", () => showTip(i));
            hit.addEventListener("focus", () => showTip(i));
            hit.addEventListener("mouseleave", hideTip);
            hit.addEventListener("blur", hideTip);
            hit.addEventListener("click", () => jumpToSpectrumPoint(i));
            s.appendChild(hit);
        });
        state.spectrum.size = { W: W, H: H };
        el.chart.appendChild(s);

        // Tablo görünümü
        const thead = make("thead"), hr = make("tr");
        ["Tarih", "Dönem", "Dalga boyu (µm)", "Akı" + (unit ? " (" + unit + ")" : ""), "Hata"].forEach((h) => hr.appendChild(make("th", null, h)));
        thead.appendChild(hr);
        const tbody = make("tbody");
        pts.forEach((p) => {
            const tr = make("tr");
            const ep = p.ep >= 0 ? state.epochs[p.ep] : null;
            [p.date || "—", ep ? String(ep.label != null ? ep.label : ep.id) : "—", p.w.toFixed(3),
                formatNumber(p.f), p.e ? formatNumber(p.e) : "—"].forEach((v) => tr.appendChild(make("td", null, v)));
            tbody.appendChild(tr);
        });
        el.table.appendChild(thead);
        el.table.appendChild(tbody);
    }

    function showTip(i) {
        const p = state.spectrum.points[i];
        if (!p) return;
        const unit = state.data && state.data.flux_unit ? " " + String(state.data.flux_unit) : "";
        clear(el.tip);
        el.tip.appendChild(make("strong", null, p.w.toFixed(3) + " µm"));
        el.tip.appendChild(make("div", null, "Akı: " + formatNumber(p.f) + (p.e ? " ± " + formatNumber(p.e) : "") + unit));
        if (p.date) el.tip.appendChild(make("div", null, "Tarih: " + p.date));
        const ep = p.ep >= 0 ? state.epochs[p.ep] : null;
        if (ep) el.tip.appendChild(make("div", null, "Dönem: " + String(ep.label != null ? ep.label : ep.id)));
        el.tip.hidden = false;
        const box = el.chart.getBoundingClientRect();
        const wrap = el.spectrumWrap.getBoundingClientRect();
        const sz = state.spectrum.size;
        const px = box.left - wrap.left + p.x / sz.W * box.width;
        const py = box.top - wrap.top + p.y / sz.H * box.height;
        const tw = el.tip.offsetWidth, th = el.tip.offsetHeight;
        let left = px + 12;
        if (left + tw > wrap.width) left = px - tw - 12;
        el.tip.style.left = Math.max(0, left) + "px";
        el.tip.style.top = Math.max(0, py - th - 8) + "px";
    }
    function hideTip() { el.tip.hidden = true; }

    function spectrumIndexForFrame(f) {
        const pts = state.spectrum.points;
        if (!f || typeof f.wavelength_um !== "number") return -1;
        let best = -1, bestD = Infinity;
        for (let i = 0; i < pts.length; i++) {
            if (f.date != null && pts[i].date != null && String(f.date) !== pts[i].date) continue;
            const d = Math.abs(pts[i].w - f.wavelength_um);
            if (d < bestD) { bestD = d; best = i; }
        }
        return bestD <= 0.03 ? best : -1;
    }

    function highlightSpectrum(i) {
        const ring = state.spectrum.ring;
        if (!ring) return;
        const p = state.spectrum.points[i];
        if (!p) { ring.setAttribute("visibility", "hidden"); return; }
        ring.setAttribute("cx", p.x);
        ring.setAttribute("cy", p.y);
        ring.setAttribute("visibility", "visible");
    }

    function jumpToSpectrumPoint(i) {
        const pl = state.playlists.frames;
        for (let k = 0; k < pl.length; k++) {
            if (spectrumIndexForFrame(pl[k].frame) === i) {
                if (state.mode !== "frames") setMode("frames");
                pause();
                state.index = k;
                render();
                return;
            }
        }
    }

    // ---------------- Aladin Lite (tembel, hataya dayanıklı) ----------------

    function aladinMessage(text) {
        el.aladinMsg.textContent = text || "";
        el.aladinMsg.hidden = !text;
    }

    function loadScript(src) {
        return new Promise((resolve, reject) => {
            const sc = document.createElement("script");
            const timeout = setTimeout(() => reject(new Error("zaman aşımı: " + src)), 15000);
            sc.src = src;
            sc.charset = "utf-8";
            sc.onload = () => {
                clearTimeout(timeout);
                if (root.A && root.A.aladin) resolve(); else reject(new Error("A.aladin tanımsız: " + src));
            };
            sc.onerror = () => { clearTimeout(timeout); reject(new Error("betik yüklenemedi: " + src)); };
            document.head.appendChild(sc);
        });
    }

    /** npm paketi ES modülüdür; dinamik import ile yüklenir. */
    function loadModule(src) {
        return import(src).then((m) => {
            const A = (m && (m.default || m.A)) || root.A;
            if (!A || !A.aladin) throw new Error("A.aladin tanımsız: " + src);
            root.A = A;
        });
    }

    function loadAladinScript() {
        if (root.A && root.A.aladin) return Promise.resolve();
        return loadScript(ALADIN_JS[0]).catch(() => loadModule(ALADIN_JS[1]));
    }

    function aladinFailed() {
        state.aladin.status = "failed";
        aladinMessage("Aladin Lite yüklenemedi (CDS sunucusuna ulaşılamıyor olabilir). SPHEREx görüntüleyicisi bundan etkilenmez.");
        el.survey.disabled = true;
        el.gaia.disabled = true;
    }

    function initAladin() {
        const a = state.aladin;
        if (a.status === "ready") { updateAladin(); return; }
        if (a.status === "loading" || a.status === "failed") return;
        a.status = "loading";
        aladinMessage("Aladin Lite yükleniyor…");
        loadAladinScript().then(() => {
            const A = root.A;
            const ready = (A.init && typeof A.init.then === "function") ? A.init : Promise.resolve();
            return ready.then(() => {
                const v = state.view;
                a.instance = A.aladin("#aladin-lite-div", {
                    survey: el.survey.value,
                    fov: v.fov / 60,
                    target: v.ra + " " + v.dec,
                    cooFrame: "ICRSd",
                    showReticle: true,
                    showFullscreenControl: false,
                    showLayersControl: false,
                    showProjectionControl: false,
                    showGotoControl: false,
                    showFrame: false
                });
                a.status = "ready";
                aladinMessage("");
                if (el.gaia.checked) updateGaia();
            });
        }).catch((e) => {
            try { console.warn("Aladin Lite:", e); } catch (_) { /* yok say */ }
            aladinFailed();
        });
    }

    function updateAladin() {
        const a = state.aladin, v = state.view;
        if (a.status !== "ready" || !a.instance || !v) return;
        try {
            a.instance.gotoRaDec(v.ra, v.dec);
            a.instance.setFoV(v.fov / 60);
            if (el.gaia.checked) updateGaia();
        } catch (e) { try { console.warn("Aladin Lite:", e); } catch (_) { /* yok say */ } }
    }

    function setSurvey() {
        const a = state.aladin;
        if (a.status !== "ready" || !a.instance) return;
        try {
            if (typeof a.instance.setBaseImageLayer === "function") a.instance.setBaseImageLayer(el.survey.value);
            else a.instance.setImageSurvey(el.survey.value);
        } catch (e) { try { console.warn("Aladin Lite:", e); } catch (_) { /* yok say */ } }
    }

    function updateGaia() {
        const a = state.aladin, v = state.view;
        if (a.status !== "ready" || !a.instance || !v) return;
        try {
            const key = v.ra + "," + v.dec + "," + v.fov;
            if (!el.gaia.checked) { if (a.gaia) a.gaia.hide(); return; }
            if (a.gaia && a.gaiaKey === key) { a.gaia.show(); return; }
            if (a.gaia) a.gaia.hide();
            const radiusDeg = Math.max(v.fov / 60 * 0.75, 0.02);
            a.gaia = root.A.catalogFromVizieR("I/355/gaiadr3", v.ra + " " + v.dec, radiusDeg, {
                onClick: "showTable", name: "Gaia DR3", color: "#e66767", sourceSize: 12
            });
            a.gaiaKey = key;
            a.instance.addCatalog(a.gaia);
        } catch (e) {
            try { console.warn("Aladin Lite Gaia:", e); } catch (_) { /* yok say */ }
            el.gaia.checked = false;
        }
    }

    // ---------------- URL / paylaşım ----------------

    function updateUrl(params) {
        try {
            const cur = new URLSearchParams(root.location.search);
            const q = new URLSearchParams();
            if (state.nameForCoords) q.set("name", state.nameForCoords);
            q.set("ra", String(params.ra));
            q.set("dec", String(params.dec));
            q.set("fov", String(params.fov));
            q.set("band", String(params.band));
            q.set("frames", String(params.frames));
            if (cur.get("api")) q.set("api", cur.get("api"));
            root.history.replaceState(null, "", root.location.pathname + "?" + q.toString());
        } catch (e) { /* file:// vb. ortamlarda sessizce geç */ }
    }

    function copyLink() {
        const url = root.location.href;
        const done = (ok) => {
            el.copyFeedback.textContent = ok ? "Kopyalandı ✔" : "Kopyalanamadı; adres çubuğundan kopyalayın.";
            setTimeout(() => { el.copyFeedback.textContent = ""; }, 3000);
        };
        const fallback = () => {
            try {
                const ta = make("textarea");
                ta.value = url;
                ta.style.position = "fixed";
                ta.style.opacity = "0";
                document.body.appendChild(ta);
                ta.select();
                const ok = document.execCommand("copy");
                document.body.removeChild(ta);
                done(ok);
            } catch (e) { done(false); }
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(url).then(() => done(true), fallback);
        } else fallback();
    }

    function applyUrlParams() {
        let q;
        try { q = new URLSearchParams(root.location.search); } catch (e) { return; }
        const name = (q.get("name") || "").trim();
        const ra = q.get("ra"), dec = q.get("dec");
        if (name) el.name.value = name;
        if (q.get("fov")) el.fov.value = q.get("fov");
        if (q.get("frames")) el.frames.value = q.get("frames");
        if (q.get("band")) {
            const b = Number(q.get("band"));
            if (!applyBand(b)) {
                el.bandHint.textContent = "URL’deki dalga boyu (" + q.get("band") + ") 0.75–5.00 µm dışında; varsayılan kullanıldı.";
            }
        }
        if (ra != null && dec != null && ra.trim() && dec.trim()) {
            el.ra.value = ra;
            el.dec.value = dec;
            state.nameForCoords = name;
            run();
        } else if (name) {
            resolveName(true);
        }
    }

    // ---------------- Hazır hedefler ----------------

    async function loadTargets() {
        try {
            const response = await fetch("targets.json");
            const targets = await response.json();
            if (!Array.isArray(targets)) return;
            clear(el.targets);
            targets.forEach((t) => {
                if (!t || typeof t !== "object" || t.name == null) return;
                const btn = make("button", null, String(t.name));
                btn.type = "button";
                const desc = t.description != null ? String(t.description) : "";
                btn.title = desc;
                btn.disabled = state.busy;
                const show = () => { el.targetDesc.textContent = desc ? String(t.name) + ": " + desc : ""; };
                btn.addEventListener("mouseenter", show);
                btn.addEventListener("focus", show);
                btn.addEventListener("click", () => {
                    show();
                    el.name.value = String(t.name);
                    el.ra.value = String(t.ra);
                    el.dec.value = String(t.dec);
                    el.fov.value = String(t.fov != null ? t.fov : FOV_DEFAULT);
                    state.nameForCoords = String(t.name);
                    if (typeof t.band !== "number" || !applyBand(t.band)) {
                        state.customBand = null;
                        el.band.value = DEFAULT_DETECTOR;
                        updateBandHint();
                    }
                    run();
                });
                el.targets.appendChild(btn);
            });
        } catch (e) {
            try { console.warn("Hedefler yüklenemedi:", e); } catch (_) { /* yok say */ }
        }
    }

    // ---------------- Başlatma ----------------

    function init() {
        Object.assign(el, {
            targets: $("target-buttons"), targetDesc: $("target-desc"), form: $("search-form"),
            name: $("name-input"), resolveBtn: $("resolve-btn"), ra: $("ra-input"), dec: $("dec-input"),
            fov: $("fov-input"), band: $("band-select"), bandHint: $("band-hint"), frames: $("frames-input"),
            loadBtn: $("load-btn"), status: $("status"), statusText: $("status-text"),
            statusElapsed: $("status-elapsed"), statusBar: $("status-bar"), statusSub: $("status-sub"),
            empty: $("empty-state"), results: $("results"), summary: $("result-summary"),
            copyBtn: $("copy-link-btn"), copyFeedback: $("copy-feedback"), tabs: document.querySelector(".tabs"),
            stage: $("stage"), canvas: $("stage-canvas"), gif: $("stage-gif"), reticle: $("reticle"),
            stageFov: $("stage-fov"), stageLabel: $("stage-label"), controls: $("player-controls"),
            prev: $("prev-btn"), play: $("play-btn"), next: $("next-btn"), scrubber: $("scrubber"),
            counter: $("frame-counter"), speed: $("speed-select"), loop: $("loop-check"),
            reticleCheck: $("reticle-check"), caption: $("mode-caption"), gifLinks: $("gif-links"),
            metaList: $("meta-list"), targetList: $("target-list"),
            spectrumNote: $("spectrum-note"), spectrumWrap: $("spectrum-wrap"), legend: $("spectrum-legend"),
            chart: $("spectrum-chart"), tip: $("spectrum-tip"), table: $("spectrum-table"),
            survey: $("survey-select"), gaia: $("gaia-check"), aladinMsg: $("skyref-msg")
        });

        buildBandSelect();

        el.form.addEventListener("submit", (ev) => { ev.preventDefault(); run(); });
        el.resolveBtn.addEventListener("click", () => resolveName(false));
        el.name.addEventListener("keydown", (ev) => {
            if (ev.key === "Enter") { ev.preventDefault(); resolveName(true); }
        });
        const coordEdited = () => { state.nameForCoords = ""; };
        el.ra.addEventListener("input", () => { coordEdited(); setError("ra", ""); });
        el.dec.addEventListener("input", () => { coordEdited(); setError("dec", ""); });
        el.name.addEventListener("input", () => setError("name", ""));
        el.fov.addEventListener("input", () => setError("fov", ""));
        el.frames.addEventListener("input", () => setError("frames", ""));
        el.band.addEventListener("change", () => { state.customBand = null; updateBandHint(); });

        el.tabs.addEventListener("click", (ev) => {
            const b = ev.target.closest(".tab");
            if (b && !b.disabled && state.data) setMode(b.dataset.mode);
        });
        el.prev.addEventListener("click", () => { pause(); step(-1); });
        el.next.addEventListener("click", () => { pause(); step(1); });
        el.play.addEventListener("click", togglePlay);
        el.scrubber.addEventListener("input", () => {
            pause();
            state.index = Number(el.scrubber.value) || 0;
            render();
        });
        el.speed.addEventListener("change", () => { if (state.playing) schedule(); });
        el.reticleCheck.addEventListener("change", () => { el.reticle.style.display = el.reticleCheck.checked ? "" : "none"; });
        el.copyBtn.addEventListener("click", copyLink);
        el.survey.addEventListener("change", setSurvey);
        el.gaia.addEventListener("change", updateGaia);

        document.addEventListener("keydown", (ev) => {
            if (!state.data || el.results.hidden || ev.ctrlKey || ev.metaKey || ev.altKey) return;
            const t = ev.target || {};
            const tag = t.tagName || "";
            const type = (t.type || "").toLowerCase();
            const inPlayer = !!(t.closest && t.closest(".player, .tabs"));
            // metin alanlarında ve Aladin içinde klavyeye karışma
            if (tag === "TEXTAREA" || tag === "SELECT") return;
            if (tag === "INPUT" && type !== "range" && type !== "checkbox") return;
            if (t.closest && t.closest("#skyref-box")) return;
            if (ev.key === " " || ev.code === "Space") {
                // oynatıcı dışındaki düğme/bağlantılarda boşluk kendi işini yapsın
                if (!inPlayer && (tag === "BUTTON" || tag === "A" || tag === "SUMMARY" || tag === "INPUT")) return;
                if (tag === "INPUT" && type === "checkbox") return;
                ev.preventDefault();
                togglePlay();
            } else if (ev.key === "ArrowLeft" || ev.key === "ArrowRight") {
                if (tag === "INPUT" && type === "range") return; // kaydırıcı kendi yönetir
                ev.preventDefault();
                pause();
                step(ev.key === "ArrowLeft" ? -1 : 1);
            }
        });

        loadTargets();
        $("diff-btn").addEventListener("click", computeDiff);
        $("diff-swap").addEventListener("click", () => {
            const sa = $("diff-a"), sb = $("diff-b"), t = sa.value;
            sa.value = sb.value; sb.value = t;
        });
        applyUrlParams();
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
    else init();

})(typeof window !== "undefined" ? window : globalThis);
