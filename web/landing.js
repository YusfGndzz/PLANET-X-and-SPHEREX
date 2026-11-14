(function () {
    "use strict";
    const $ = (id) => document.getElementById(id);
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const D2R = Math.PI / 180, TAU = Math.PI * 2;
    const INK = "236,232,223", HOT = "#ff5a1f";
    const MONO = '"IBM Plex Mono", ui-monospace, Consolas, monospace';
    const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
    const lerp = (a, b, t) => a + (b - a) * t;
    const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
    const tr = (n, d) => n.toFixed(d).replace(".", ",");

    // ---------- ortak: tuval boyutu ve görünürlük ----------
    function fit(canvas) {
        const r = Math.min(devicePixelRatio || 1, 2), W = canvas.clientWidth, H = canvas.clientHeight;
        if (canvas.width !== Math.round(W * r) || canvas.height !== Math.round(H * r)) {
            canvas.width = Math.round(W * r); canvas.height = Math.round(H * r);
        }
        const ctx = canvas.getContext("2d");
        ctx.setTransform(r, 0, 0, r, 0, 0);
        return { ctx, W, H };
    }
    const seen = new Map();
    const visIO = new IntersectionObserver((es) => es.forEach((e) => {
        const s = seen.get(e.target); if (!s) return;
        s.on = e.isIntersecting;
        if (s.on && !s.first) { s.first = true; if (s.enter) s.enter(); }
    }), { threshold: 0.05 });
    /* Yalnızca ekranda görünen şekiller çizilir. */
    function animate(el, frame, enter) {
        const s = { on: false, first: false, enter };
        seen.set(el, s); visIO.observe(el);
        let last = performance.now();
        function loop(t) {
            const dt = Math.min(0.1, (t - last) / 1000); last = t;
            if (s.on && !document.hidden) frame(dt, t / 1000);
            requestAnimationFrame(loop);
        }
        requestAnimationFrame(loop);
    }

    // ---------- gezinme ve görünme ----------
    const nav = $("nav");
    addEventListener("scroll", () => nav.classList.toggle("is-stuck", scrollY > 40), { passive: true });
    const io = new IntersectionObserver((es) => es.forEach((e) => {
        if (e.isIntersecting) { e.target.classList.add("is-in"); io.unobserve(e.target); }
    }), { threshold: 0.12 });
    document.querySelectorAll(".reveal").forEach((el) => io.observe(el));

    // ---------- görev sayaçları ----------
    const now = new Date();
    const launch = Date.UTC(2025, 2, 12, 3, 10), surveyStart = Date.UTC(2025, 4, 1);
    const yr = 2000 + (now - Date.UTC(2000, 0, 1, 12)) / 864e5 / 365.25;
    $("ro-day").textContent = Math.floor((now - launch) / 864e5) + ". gün";
    $("ro-survey").textContent = (Math.floor((now - surveyStart) / 864e5 / 182.6) + 1) + ". tur sürüyor";

    function hms(ra) {
        const h = ((ra % 360) + 360) % 360 / 15, Hh = Math.floor(h), m = (h - Hh) * 60, M = Math.floor(m);
        return String(Hh).padStart(2, "0") + "h" + String(M).padStart(2, "0") + "m" + ((m - M) * 60).toFixed(1).padStart(4, "0") + "s";
    }
    function dms(d) {
        const s = d < 0 ? "−" : "+", a = Math.abs(d), Dd = Math.floor(a), m = (a - Dd) * 60, M = Math.floor(m);
        return s + String(Dd).padStart(2, "0") + "°" + String(M).padStart(2, "0") + "′" + String(Math.floor((m - M) * 60)).padStart(2, "0") + "″";
    }

    // ---------- gökyüzü çizici (stereografik izdüşüm, doğu solda) ----------
    const sky = { stars: [], lines: [], grat: [] };
    for (let ra = 0; ra < 360; ra += 30) { const l = []; for (let d = -84; d <= 84; d += 4) l.push([ra, d]); sky.grat.push(l); }
    for (let d = -60; d <= 60; d += 20) { const l = []; for (let ra = 0; ra <= 360; ra += 4) l.push([ra, d]); sky.grat.push(l); }

    function projector(cam, W, H, fitDim) {
        const k = fitDim / 2 / (2 * Math.tan(cam.fov * D2R / 4));
        const sd0 = Math.sin(cam.dec * D2R), cd0 = Math.cos(cam.dec * D2R);
        return {
            k,
            pt(ra, dec) {
                const dr = (ra - cam.ra) * D2R, sd = Math.sin(dec * D2R), cd = Math.cos(dec * D2R);
                const cc = sd0 * sd + cd0 * cd * Math.cos(dr);
                if (cc < -0.2) return null;
                const f = 2 / (1 + cc);
                return [W / 2 - k * f * cd * Math.sin(dr), H / 2 - k * f * (cd0 * sd - sd0 * cd * Math.cos(dr))];
            }
        };
    }
    function strokeLines(ctx, P, lines, W, H) {
        ctx.beginPath();
        for (const l of lines) {
            let prev = null;
            for (const c of l) {
                const p = P.pt(c[0], c[1]);
                if (p && prev && Math.abs(p[0] - prev[0]) < W && Math.abs(p[1] - prev[1]) < H) {
                    ctx.moveTo(prev[0], prev[1]); ctx.lineTo(p[0], p[1]);
                }
                prev = p;
            }
        }
        ctx.stroke();
    }
    function drawSky(ctx, W, H, cam, fitDim, o) {
        const P = projector(cam, W, H, fitDim);
        ctx.lineWidth = 1;
        if (o.grat > 0.004) { ctx.strokeStyle = "rgba(" + INK + "," + o.grat + ")"; strokeLines(ctx, P, sky.grat, W, H); }
        if (o.lines > 0.004) { ctx.strokeStyle = "rgba(" + INK + "," + o.lines + ")"; strokeLines(ctx, P, sky.lines, W, H); }
        if (o.stars > 0.004) {
            const boost = clamp(Math.pow(70 / cam.fov, 0.3), 0.8, 3.2);
            for (const s of sky.stars) {
                const p = P.pt(s[0], s[1]);
                if (!p || p[0] < -4 || p[0] > W + 4 || p[1] < -4 || p[1] > H + 4) continue;
                ctx.globalAlpha = o.stars * clamp(0.34 + (6.5 - s[2]) * 0.2, 0, 1);
                ctx.fillStyle = s[3] > 1.1 ? "#ffcfa8" : s[3] < 0.2 ? "#cfdcff" : "#f1ede4";
                ctx.beginPath(); ctx.arc(p[0], p[1], Math.max(0.6, (6.9 - s[2]) * 0.5 * boost), 0, TAU); ctx.fill();
            }
            ctx.globalAlpha = 1;
        }
        return P;
    }

    // ---------- açılış ----------
    const heroC = $("hero-sky"), heroCam = { ra: 262, dec: 12, fov: 78 };
    function heroFrame(dt) {
        const { ctx, W, H } = fit(heroC);
        ctx.clearRect(0, 0, W, H);
        heroCam.ra = (heroCam.ra + dt * 0.55) % 360;
        drawSky(ctx, W, H, heroCam, Math.max(W, H), { stars: 1, lines: 0.07, grat: 0.035 });
        $("ro-center").textContent = hms(heroCam.ra) + " " + dms(heroCam.dec);
    }
    animate(heroC, (dt) => heroFrame(reduce ? 0 : dt));

    const CEL = "https://cdn.jsdelivr.net/npm/d3-celestial@0.7.35/data/";
    fetch(CEL + "stars.6.json").then((r) => r.json()).then((d) => {
        sky.stars = d.features.map((f) => [f.geometry.coordinates[0], f.geometry.coordinates[1], f.properties.mag, parseFloat(f.properties.bv) || 0.6]);
        $("hero-credit").textContent = "Arka plan: gerçek yıldız konumları, 6 kadire kadar " + sky.stars.length.toLocaleString("tr-TR") + " yıldız";
    }).catch(() => { $("hero-credit").textContent = "Yıldız kataloğu yüklenemedi."; });
    fetch(CEL + "constellations.lines.json").then((r) => r.json()).then((d) => {
        d.features.forEach((f) => f.geometry.coordinates.forEach((l) => sky.lines.push(l)));
    }).catch(() => {});

    // ---------- Şek. 1: canlı yörünge ----------
    const MU = 398600.4418, RE = 6371;
    const orbit = { satrec: null, land: null, simT: Date.now(), speed: 1, lastRO: 0 };
    document.querySelectorAll("#fig-orbit [data-speed]").forEach((b) => b.addEventListener("click", () => {
        document.querySelectorAll("#fig-orbit [data-speed]").forEach((x) => x.classList.toggle("is-on", x === b));
        orbit.speed = +b.dataset.speed;
        if (orbit.speed === 1) orbit.simT = Date.now();
    }));
    fetch("https://celestrak.org/NORAD/elements/gp.php?CATNR=63182&FORMAT=JSON").then((r) => r.json()).then((d) => {
        const o = d[0], per = 1440 / o.MEAN_MOTION, a = Math.cbrt(MU * Math.pow(per * 60 / TAU, 2));
        $("ro-alt").textContent = (a - RE).toFixed(0) + " km";
        $("or-alt").textContent = (a - RE).toFixed(0) + " km";
        $("or-vel").textContent = tr(Math.sqrt(MU / a), 2) + " km/sn";
        $("or-per").textContent = tr(per, 1) + " dk";
        $("or-inc").textContent = tr(o.INCLINATION, 2) + "°";
        $("orbit-src").textContent = "Veri: CelesTrak, " + o.EPOCH.slice(0, 10) + " tarihli yörünge elemanları.";
    }).catch(() => { $("orbit-src").textContent = "Yörünge verisine ulaşılamadı; yalnızca Dünya gösteriliyor."; });
    fetch("https://celestrak.org/NORAD/elements/gp.php?CATNR=63182&FORMAT=TLE").then((r) => r.text()).then((t) => {
        const ls = t.split(/\r?\n/).map((x) => x.trimEnd());
        const l1 = ls.find((x) => x.startsWith("1 ")), l2 = ls.find((x) => x.startsWith("2 "));
        if (l1 && l2 && window.satellite) orbit.satrec = satellite.twoline2satrec(l1, l2);
    }).catch(() => {});
    fetch("https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/land-110m.json").then((r) => r.json()).then((w) => {
        if (window.topojson) orbit.land = topojson.feature(w, w.objects.land);
    }).catch(() => {});

    const globeC = $("globe");
    function globeFrame(dt, tsec) {
        if (!window.d3) return;
        const { ctx, W, H } = fit(globeC);
        ctx.clearRect(0, 0, W, H);
        orbit.simT = orbit.speed === 1 ? Date.now() : orbit.simT + dt * 1000 * orbit.speed;
        const date = new Date(orbit.simT), c = W / 2, cy = H / 2, R = Math.min(W, H) * 0.33;

        // Güneş'in tam tepede olduğu nokta (yaklaşık)
        const doy = (date - Date.UTC(date.getUTCFullYear(), 0, 0)) / 864e5;
        const sunLat = -23.44 * Math.cos(TAU * (doy + 10) / 365.24);
        const sunLon = -15 * (date.getUTCHours() + date.getUTCMinutes() / 60 + date.getUTCSeconds() / 3600 - 12);
        const view = [sunLon - 62, 16];
        const proj = d3.geoOrthographic().translate([c, cy]).scale(R).rotate([-view[0], -view[1]]).clipAngle(90);
        const path = d3.geoPath(proj, ctx);

        ctx.beginPath(); path({ type: "Sphere" }); ctx.fillStyle = "#1b1b1e"; ctx.fill();
        ctx.beginPath(); path(d3.geoGraticule10()); ctx.strokeStyle = "rgba(" + INK + ",0.07)"; ctx.lineWidth = 1; ctx.stroke();
        if (orbit.land) { ctx.beginPath(); path(orbit.land); ctx.fillStyle = "#4a463c"; ctx.fill(); }
        ctx.beginPath(); path(d3.geoCircle().center([sunLon + 180, -sunLat]).radius(90)());
        ctx.fillStyle = "rgba(6,6,8,0.62)"; ctx.fill();
        ctx.beginPath(); path({ type: "Sphere" }); ctx.strokeStyle = "rgba(" + INK + ",0.35)"; ctx.stroke();

        if (!orbit.satrec) return;
        const gmst = satellite.gstime(date);
        const at = (ms) => {
            const pv = satellite.propagate(orbit.satrec, new Date(ms));
            if (!pv.position) return null;
            const g = satellite.eciToGeodetic(pv.position, gmst);
            const ll = [g.longitude / D2R, g.latitude / D2R], f = (RE + g.height) / RE, p = proj(ll);
            const back = d3.geoDistance(ll, view) > Math.PI / 2;
            const x = c + (p[0] - c) * f, y = cy + (p[1] - cy) * f;
            return { x, y, p, f, ll, h: g.height, back, hidden: back && Math.hypot(x - c, y - cy) < R, v: pv.velocity };
        };
        const per = TAU / orbit.satrec.no * 60000, N = 160;
        let prev = at(orbit.simT);
        for (let i = 1; i <= N; i++) {
            const q = at(orbit.simT + per * i / N);
            if (prev && q && !prev.hidden && !q.hidden) {
                ctx.beginPath(); ctx.moveTo(prev.x, prev.y); ctx.lineTo(q.x, q.y);
                ctx.strokeStyle = "rgba(" + INK + "," + (q.back ? 0.22 : 0.75) + ")"; ctx.lineWidth = q.back ? 1 : 1.3; ctx.stroke();
            }
            prev = q;
        }
        const s = at(orbit.simT);
        if (!s) return;
        if (!s.hidden) {
            // bakış yönü: Dünya'nın tersine
            let lk = 1.42;
            const inside = (k) => { const x = c + (s.p[0] - c) * s.f * k, y = cy + (s.p[1] - cy) * s.f * k; return x > 70 && x < W - 70 && y > 44 && y < H - 30; };
            while (lk > 1.08 && !inside(lk)) lk -= 0.04;
            const ox = c + (s.p[0] - c) * s.f * lk, oy = cy + (s.p[1] - cy) * s.f * lk;
            ctx.setLineDash([3, 5]); ctx.beginPath(); ctx.moveTo(s.x, s.y); ctx.lineTo(ox, oy);
            ctx.strokeStyle = HOT; ctx.lineWidth = 1; ctx.stroke(); ctx.setLineDash([]);
            if (!s.back) { ctx.beginPath(); ctx.moveTo(s.x, s.y); ctx.lineTo(s.p[0], s.p[1]); ctx.strokeStyle = "rgba(" + INK + ",0.3)"; ctx.stroke(); }
            const pulse = 7 + 3 * Math.sin(tsec * 3);
            ctx.beginPath(); ctx.arc(s.x, s.y, pulse, 0, TAU); ctx.strokeStyle = "rgba(255,90,31,0.45)"; ctx.stroke();
            ctx.beginPath(); ctx.arc(s.x, s.y, 3.5, 0, TAU); ctx.fillStyle = HOT; ctx.fill();
            ctx.font = "11px " + MONO; ctx.fillStyle = "rgb(" + INK + ")"; ctx.textAlign = s.x > c ? "left" : "right";
            ctx.fillText("SPHEREx", s.x + (s.x > c ? 12 : -12), s.y + 4);
            ctx.fillStyle = HOT; ctx.fillText("bakış yönü", ox + (s.x > c ? 8 : -8), oy + 4);
        }
        ctx.font = "11px " + MONO; ctx.fillStyle = "rgba(" + INK + ",0.5)"; ctx.textAlign = "right";
        ctx.fillText(date.toISOString().slice(0, 19).replace("T", " ") + " UTC", W - 12, H - 12);
        if (tsec - orbit.lastRO > 0.2) {
            orbit.lastRO = tsec;
            $("or-lat").textContent = tr(Math.abs(s.ll[1]), 1) + "° " + (s.ll[1] >= 0 ? "K" : "G");
            $("or-lon").textContent = tr(Math.abs(s.ll[0]), 1) + "° " + (s.ll[0] >= 0 ? "D" : "B");
            $("or-alt").textContent = s.h.toFixed(0) + " km";
            if (s.v) $("or-vel").textContent = tr(Math.hypot(s.v.x, s.v.y, s.v.z), 2) + " km/sn";
        }
    }
    // Üç boyutlu sahne kurulabiliyorsa onu kullan; olmazsa düz çizime dön
    let use3d = false;
    try { use3d = !!(window.Orbit3D && window.Orbit3D.start({ orbit, sky, $, tr, animate })); } catch (e) { use3d = false; }
    if (use3d) $("view-tools").hidden = false; else animate(globeC, globeFrame);

    // ---------- Şek. 2: 102 bant ----------
    const DET = [[0.75, 1.11], [1.10, 1.65], [1.64, 2.42], [2.42, 3.82], [3.81, 4.42], [4.42, 5.00]];
    const STOPS = [[255, 233, 168], [255, 138, 60], [214, 48, 26], [107, 20, 20]];
    function ramp(l, a) {
        const t = clamp((l - 0.75) / 4.25, 0, 1) * (STOPS.length - 1), i = Math.min(STOPS.length - 2, Math.floor(t)), f = t - i;
        const c = STOPS[i].map((v, j) => Math.round(lerp(v, STOPS[i + 1][j], f)));
        return "rgba(" + c.join(",") + "," + (a == null ? 1 : a) + ")";
    }
    const bandL = (n) => { const d = DET[Math.floor(n / 17)]; return d[0] + ((n % 17) + 0.5) / 17 * (d[1] - d[0]); };
    const dip = (l, c, w, d) => d * Math.exp(-Math.pow((l - c) / w, 2));
    const flux = (l) => (0.28 + 0.9 * Math.exp(-Math.pow(l - 1.25, 2) / 5.5)) *
        (1 - dip(l, 1.4, 0.07, 0.22) - dip(l, 1.9, 0.08, 0.2) - dip(l, 3.0, 0.2, 0.5) - dip(l, 4.27, 0.07, 0.55) - dip(l, 4.67, 0.05, 0.3)) +
        0.012 * Math.sin(l * 37);
    const lvfC = $("lvf"), lvf = { n: 0, acc: 0, hold: 0 };
    function lvfFrame(dt) {
        const { ctx, W, H } = fit(lvfC);
        ctx.clearRect(0, 0, W, H);
        if (reduce) lvf.n = 102;
        else if (lvf.n >= 102) { lvf.hold += dt; if (lvf.hold > 3.2) { lvf.n = 0; lvf.hold = 0; } }
        else { lvf.acc += dt; while (lvf.acc > 0.1 && lvf.n < 102) { lvf.acc -= 0.1; lvf.n++; } }
        const cur = Math.min(lvf.n, 101), di = Math.floor(cur / 17), bi = cur % 17, m = 18;
        const narrow = W < 520, dS = narrow ? Math.min(W - 2 * m, H * 0.4) : Math.min(W * 0.36, H - 2 * m - 46);
        const dx = m, dy = m + 20;
        ctx.font = "11px " + MONO; ctx.textBaseline = "alphabetic";

        // dedektör: renk filtrenin üstünden altına değişir
        ctx.textAlign = "left"; ctx.fillStyle = "rgba(" + INK + ",0.55)";
        ctx.fillText("DEDEKTÖR D" + (di + 1), dx, m + 8);
        ctx.textAlign = "right"; ctx.fillText(tr(DET[di][0], 2) + " – " + tr(DET[di][1], 2) + " µm", dx + dS, m + 8);
        const bh = dS / 17;
        for (let b = 0; b < 17; b++) {
            ctx.fillStyle = ramp(DET[di][0] + (b + 0.5) / 17 * (DET[di][1] - DET[di][0]), b === bi ? 0.95 : 0.3);
            ctx.fillRect(dx, dy + b * bh, dS, bh - 1);
        }
        const sx = dx + dS * (0.3 + 0.4 * (di % 2)), syy = dy + (bi + 0.5) * bh;
        for (let b = 0; b < bi; b++) { ctx.beginPath(); ctx.arc(sx, dy + (b + 0.5) * bh, 1.5, 0, TAU); ctx.fillStyle = "rgba(12,12,13,0.55)"; ctx.fill(); }
        ctx.beginPath(); ctx.arc(sx, syy, Math.max(3, bh * 0.36), 0, TAU); ctx.fillStyle = "#0c0c0d"; ctx.fill();
        ctx.beginPath(); ctx.arc(sx, syy, Math.max(1.5, bh * 0.18), 0, TAU); ctx.fillStyle = "#fff"; ctx.fill();
        // altı dedektör
        const cw = dS / 6, cyy = dy + dS + 12;
        for (let d = 0; d < 6; d++) {
            ctx.fillStyle = ramp((DET[d][0] + DET[d][1]) / 2, d === di ? 1 : d < di ? 0.45 : 0.14);
            ctx.fillRect(dx + d * cw, cyy, cw - 3, 6);
        }

        // tayf
        const gx = narrow ? m + 8 : dx + dS + 46, gy = narrow ? cyy + 40 : dy, gw = W - gx - m, gh = narrow ? H - gy - 34 : dS;
        ctx.strokeStyle = "rgba(" + INK + ",0.22)"; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(gx, gy); ctx.lineTo(gx, gy + gh); ctx.lineTo(gx + gw, gy + gh); ctx.stroke();
        const X = (l) => gx + (l - 0.75) / 4.25 * gw, Y = (f) => gy + gh - f / 1.25 * gh;
        ctx.textAlign = "center"; ctx.fillStyle = "rgba(" + INK + ",0.5)";
        for (let l = 1; l <= 5; l++) { ctx.fillText(l + " µm", X(l), gy + gh + 16); ctx.beginPath(); ctx.moveTo(X(l), gy + gh); ctx.lineTo(X(l), gy + gh + 4); ctx.stroke(); }
        ctx.save(); ctx.translate(gx - 12, gy + gh / 2); ctx.rotate(-Math.PI / 2); ctx.fillText("parlaklık", 0, 0); ctx.restore();
        ctx.textAlign = "left"; ctx.fillText("ÖLÇÜLEN NOKTA  " + String(Math.min(lvf.n, 102)).padStart(3, "0") + " / 102", gx, narrow ? gy - 14 : m + 8);
        ctx.beginPath();
        for (let n = 0; n < lvf.n; n++) { const l = bandL(n); if (n) ctx.lineTo(X(l), Y(flux(l))); else ctx.moveTo(X(l), Y(flux(l))); }
        ctx.strokeStyle = "rgba(" + INK + ",0.25)"; ctx.stroke();
        for (let n = 0; n < lvf.n; n++) {
            const l = bandL(n);
            ctx.beginPath(); ctx.arc(X(l), Y(flux(l)), n === lvf.n - 1 && lvf.n < 102 ? 4.5 : 2.4, 0, TAU);
            ctx.fillStyle = ramp(l); ctx.fill();
        }
        if (lvf.n < 102) {
            const l = bandL(cur);
            ctx.setLineDash([2, 4]); ctx.beginPath(); ctx.moveTo(X(l), gy); ctx.lineTo(X(l), gy + gh);
            ctx.strokeStyle = "rgba(" + INK + ",0.3)"; ctx.stroke(); ctx.setLineDash([]);
        }
        const a = clamp((lvf.n - 60) / 40, 0, 1);
        if (a > 0) {
            ctx.textAlign = "center"; ctx.fillStyle = "rgba(" + INK + "," + 0.85 * a + ")";
            [[3.0, "su buzu"], [4.27, "CO₂"], [4.67, "CO"]].forEach(([l, name], i) => {
                if (bandL(Math.min(lvf.n, 102) - 1) < l) return;
                const y = Y(flux(l)) + 20 + (i === 2 ? 14 : 0);
                ctx.fillText(name, X(l), Math.min(y, gy + gh - 6));
            });
        }
    }
    animate(lvfC, lvfFrame);

    // ---------- Şek. 3: yıldıza iniş ----------
    const T = [
        { id: "NAME Barnard's star", name: "Barnard Yıldızı", tag: "hızlı yıldız", band: 2.5, ra: 269.45208, dec: 4.69336, pr: -801.551, pd: 10362.394, plx: 546.98, desc: "Gökyüzünde konumu en hızlı değişen yıldız. Güneş'e en yakın ikinci yıldız sistemi." },
        { id: "eps Ind B", name: "Epsilon Indi B", tag: "kahverengi cüce çifti", band: 2.5, ra: 331.04415, dec: -56.78284, pr: 3981.977, pd: -2466.832, plx: 270.66, desc: "Yakın bir yıldızın çevresinde dolanan iki kahverengi cüce." },
        { id: "WISE J085510.83-071442.5", name: "WISE 0855", tag: "en soğuk cüce", band: 4.6, ra: 133.81891, dec: -7.24705, pr: -8123.7, pd: 673.2, plx: 439, desc: "Bilinen en soğuk kahverengi cüce. Çok sönük; yalnızca 4,6 µm civarında seçilebiliyor." },
        { id: "V* RW Aur", name: "RW Aurigae", tag: "değişen yıldız", band: 2.5, ra: 76.95653, dec: 30.40144, pr: 7.23, pd: -20.33, plx: 15.42, desc: "Genç bir yıldız. Yerinde duruyor; değişen şey parlaklığı, çevresindeki toz yüzünden zaman zaman kararıyor." },
        { id: "V* R Dra", name: "R Draconis", tag: "değişen yıldız", band: 2.5, ra: 248.16761, dec: 66.75498, pr: -20.643, pd: 6.149, plx: 1.2509, desc: "Parlaklığı aylar süren bir döngüyle değişen dev yıldız. SPHEREx'in en sık çektiği bölgede." }
    ];
    const PIX = 6.15, MISSION0 = 2025.33;
    const posAt = (t, y) => ({ ra: t.ra + t.pr * (y - 2000) / 3.6e6 / Math.cos(t.dec * D2R), dec: t.dec + t.pd * (y - 2000) / 3.6e6 });
    const pmOf = (t) => Math.hypot(t.pr, t.pd) / 1000;
    const list = $("fly-targets"), flyC = $("fly-sky");
    const fly = { i: 0, t: 0, run: false, cam: { ra: 300, dec: 10, fov: 110 }, from: null };

    let presets = [];
    fetch("targets.json").then((r) => r.json()).then((d) => { presets = d; show(fly.i); }).catch(() => {});
    function show(i) {
        const t = T[i], p = posAt(t, yr), pm = pmOf(t);
        list.querySelectorAll("button").forEach((b, j) => b.classList.toggle("is-on", j === i));
        $("fi-name").textContent = t.name; $("fi-desc").textContent = t.desc;
        $("fi-pos").textContent = hms(p.ra) + " " + dms(p.dec);
        $("fi-pm").textContent = tr(pm, 2) + "″ / yıl";
        $("fi-dist").textContent = tr(3261.56 / t.plx, 1) + " ışık yılı";
        $("fi-shift").textContent = tr(pm * (yr - 2000), pm > 1 ? 0 : 1) + "″ kaydı";
        $("fi-px").textContent = tr(pm * (yr - MISSION0) / PIX, pm > 1 ? 1 : 3) + " piksel kaydı";
        // Araçtaki hazır hedeflerle aynı değerler kullanılır ki önbellekteki sonuç eşleşsin
        const pre = presets[i];
        $("fi-open").href = pre
            ? "explorer.html?ra=" + pre.ra + "&dec=" + pre.dec + "&fov=" + pre.fov + "&band=" + pre.band
            : "explorer.html?ra=" + p.ra.toFixed(4) + "&dec=" + p.dec.toFixed(4) + "&fov=4&band=" + t.band;
    }
    function startFly(i) {
        fly.i = i; fly.t = 0; fly.run = true;
        const t = T[i];
        fly.from = { ra: t.ra + 58, dec: clamp(t.dec * 0.35 + 8, -60, 60) };
        show(i);
    }
    T.forEach((t, i) => {
        const li = document.createElement("li"), b = document.createElement("button"), n = document.createElement("span"), s = document.createElement("small");
        b.type = "button"; n.textContent = t.name; s.textContent = t.tag; b.append(n, s);
        b.addEventListener("click", () => startFly(i)); li.append(b); list.append(li);
    });
    show(0);
    $("fly-replay").addEventListener("click", () => startFly(fly.i));
    $("fly-src").textContent = "Konumlar: yerleşik değerler";
    const q = "SELECT id, ra, dec, pmra, pmdec, plx_value FROM basic JOIN ident ON oidref=oid WHERE id IN (" + T.map((t) => "'" + t.id.replace(/'/g, "''") + "'").join(",") + ")";
    fetch("https://simbad.harvard.edu/simbad/sim-tap/sync?request=doQuery&lang=adql&format=json&query=" + encodeURIComponent(q)).then((r) => r.json()).then((d) => {
        d.data.forEach((row) => {
            const t = T.find((x) => x.id === row[0]);
            if (t && row[1] != null && row[3] != null) { t.ra = row[1]; t.dec = row[2]; t.pr = row[3]; t.pd = row[4]; if (row[5]) t.plx = row[5]; }
        });
        $("fly-src").textContent = "Konum ve öz hareket: SIMBAD, canlı";
        show(fly.i);
    }).catch(() => {});

    const vec = (ra, dec) => [Math.cos(dec * D2R) * Math.cos(ra * D2R), Math.cos(dec * D2R) * Math.sin(ra * D2R), Math.sin(dec * D2R)];
    function slerp(a, b, t) {
        const A = vec(a.ra, a.dec), B = vec(b.ra, b.dec);
        const om = Math.acos(clamp(A[0] * B[0] + A[1] * B[1] + A[2] * B[2], -1, 1));
        if (om < 1e-6) return { ra: b.ra, dec: b.dec };
        const s = Math.sin(om), k1 = Math.sin((1 - t) * om) / s, k2 = Math.sin(t * om) / s;
        const v = [k1 * A[0] + k2 * B[0], k1 * A[1] + k2 * B[1], k1 * A[2] + k2 * B[2]];
        return { ra: (Math.atan2(v[1], v[0]) / D2R + 360) % 360, dec: Math.asin(clamp(v[2], -1, 1)) / D2R };
    }
    function fovText(f) {
        if (f >= 1) return tr(f, f >= 10 ? 0 : 1) + "°";
        if (f * 60 >= 1) return tr(f * 60, f * 60 >= 10 ? 0 : 1) + "′";
        return (f * 3600).toFixed(0) + "″";
    }
    const FLY = 8.5, TRACK = 4;
    function flyFrame(dt, tsec) {
        const { ctx, W, H } = fit(flyC);
        ctx.clearRect(0, 0, W, H);
        const t = T[fly.i], pm = pmOf(t), mover = pm > 0.5, fitDim = Math.min(W, H);
        const ym = (2000 + yr) / 2, mid = mover ? posAt(t, ym) : posAt(t, yr);
        const span = pm * (yr - 2000);
        const fovEnd = (mover ? Math.max(span * 1.55, 90) : 80) / 3600;
        if (fly.run) fly.t += dt;
        if (reduce && fly.run) fly.t = FLY + TRACK;
        const p = fly.from ? clamp(fly.t / FLY, 0, 1) : 0;
        if (fly.from) {
            const c = slerp(fly.from, mid, ease(clamp(p / 0.5, 0, 1)));
            fly.cam.ra = c.ra; fly.cam.dec = c.dec;
            fly.cam.fov = Math.exp(p < 0.45 ? lerp(Math.log(110), Math.log(34), ease(p / 0.45)) : lerp(Math.log(34), Math.log(fovEnd), ease((p - 0.45) / 0.55)));
        }
        const fov = fly.cam.fov;
        const wide = clamp((Math.log(fov) - Math.log(0.8)) / (Math.log(5) - Math.log(0.8)), 0, 1);
        const cons = clamp((fov - 6) / 20, 0, 1);
        const P = drawSky(ctx, W, H, fly.cam, fitDim, { stars: wide, lines: 0.2 * cons, grat: 0.06 * cons });
        const deep = clamp((Math.log(0.5) - Math.log(fov)) / (Math.log(0.5) - Math.log(fovEnd * 1.4)), 0, 1);
        const cx = W / 2, cy = H / 2, s = P.k * D2R / 3600;   // piksel / yay saniyesi
        $("fly-fov").textContent = "Alan genişliği " + fovText(fov);
        ctx.font = "11px " + MONO;

        if (fly.from && deep < 1) {
            const m = P.pt(mid.ra, mid.dec);
            if (m) {
                ctx.globalAlpha = 1 - deep;
                ctx.beginPath(); ctx.arc(m[0], m[1], 15 + 2 * Math.sin(tsec * 3), 0, TAU); ctx.strokeStyle = HOT; ctx.lineWidth = 1.2; ctx.stroke();
                ctx.beginPath(); ctx.moveTo(m[0] + 20, m[1]); ctx.lineTo(m[0] + 46, m[1]); ctx.stroke();
                ctx.fillStyle = "rgb(" + INK + ")"; ctx.textAlign = "left"; ctx.fillText(t.name, m[0] + 52, m[1] + 4);
                ctx.globalAlpha = 1;
            }
        }
        if (deep <= 0) return;

        // en yakın görünüm: SPHEREx piksel ızgarası
        const pitch = PIX * s;
        ctx.globalAlpha = deep;
        if (pitch > 4) {
            ctx.strokeStyle = "rgba(" + INK + "," + clamp((pitch - 4) / 30, 0.05, 0.14) + ")"; ctx.lineWidth = 1; ctx.beginPath();
            for (let x = cx % pitch; x < W; x += pitch) { ctx.moveTo(Math.round(x) + 0.5, 0); ctx.lineTo(Math.round(x) + 0.5, H); }
            for (let y = cy % pitch; y < H; y += pitch) { ctx.moveTo(0, Math.round(y) + 0.5); ctx.lineTo(W, Math.round(y) + 0.5); }
            ctx.stroke();
        }
        const k = ease(clamp((fly.t - FLY) / TRACK, 0, 1));
        const ye = mover ? lerp(2000, yr, k) : yr;
        const off = (y) => [cx - t.pr / 1000 * (y - (mover ? ym : yr)) * s, cy - t.pd / 1000 * (y - (mover ? ym : yr)) * s];
        const star = off(ye);
        // yıldız: dedektörün gördüğü gibi, piksellere dağılmış ışık
        const bright = mover ? 1 : 0.5 + 0.5 * Math.sin(tsec * 1.5);
        const gi = Math.round((star[0] - cx) / pitch), gj = Math.round((star[1] - cy) / pitch);
        for (let i = gi - 3; i <= gi + 3; i++) for (let j = gj - 3; j <= gj + 3; j++) {
            const px = cx + i * pitch, py = cy + j * pitch;
            const d2 = (Math.pow(px - star[0], 2) + Math.pow(py - star[1], 2)) / (s * s);
            const I = Math.exp(-d2 / (2 * 4.2 * 4.2)) * (0.25 + 0.75 * bright);
            if (I < 0.02) continue;
            ctx.fillStyle = "rgba(" + INK + "," + I * 0.92 + ")";
            ctx.fillRect(px - pitch / 2 + 1, py - pitch / 2 + 1, pitch - 1, pitch - 1);
        }
        ctx.textAlign = "left"; ctx.fillStyle = "rgba(" + INK + ",0.5)";
        ctx.fillText("Izgara: SPHEREx pikselleri (6,2″)", 14, 22);
        if (mover) {
            const a = off(2000);
            ctx.setLineDash([4, 5]); ctx.strokeStyle = "rgba(" + INK + ",0.55)"; ctx.lineWidth = 1;
            ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(star[0], star[1]); ctx.stroke(); ctx.setLineDash([]);
            if (ye > MISSION0) {
                const m0 = off(MISSION0);
                ctx.beginPath(); ctx.moveTo(m0[0], m0[1]); ctx.lineTo(star[0], star[1]); ctx.strokeStyle = HOT; ctx.lineWidth = 3; ctx.stroke();
            }
            let nx = (star[1] - a[1]), ny = -(star[0] - a[0]); if (nx < 0) { nx = -nx; ny = -ny; }
            const nl = Math.hypot(nx, ny) || 1, ux = nx / nl, uy = ny / nl;
            for (let y = 2000; y <= ye; y++) {
                const q = off(y), big = y % 5 === 0, L = big ? 7 : 3;
                ctx.beginPath(); ctx.moveTo(q[0] - ux * L, q[1] - uy * L); ctx.lineTo(q[0] + ux * L, q[1] + uy * L);
                ctx.strokeStyle = "rgba(" + INK + "," + (big ? 0.8 : 0.4) + ")"; ctx.lineWidth = 1; ctx.stroke();
                if (y % 10 === 0 && y > 2000) { ctx.fillStyle = "rgba(" + INK + ",0.6)"; ctx.textAlign = "left"; ctx.fillText(String(y), q[0] + ux * 16 + 4, q[1] + uy * 16 + 4); }
            }
            ctx.beginPath(); ctx.arc(a[0], a[1], 9, 0, TAU); ctx.strokeStyle = "rgb(" + INK + ")"; ctx.lineWidth = 1.2; ctx.stroke();
            ctx.fillStyle = "rgba(" + INK + ",0.8)"; ctx.textAlign = "left"; ctx.fillText("2000", a[0] + 16, a[1] + 4);
            ctx.beginPath(); ctx.arc(star[0], star[1], 4, 0, TAU); ctx.fillStyle = HOT; ctx.fill();
            ctx.beginPath(); ctx.arc(star[0], star[1], 10 + 3 * Math.sin(tsec * 3), 0, TAU); ctx.strokeStyle = "rgba(255,90,31,0.5)"; ctx.stroke();
            ctx.fillStyle = HOT; ctx.fillText(k >= 1 ? "bugün" : String(Math.floor(ye)), star[0] + 18, star[1] + 4);
        } else {
            ctx.textAlign = "center"; ctx.fillStyle = "rgba(" + INK + ",0.75)";
            ctx.fillText("konum sabit · parlaklık değişiyor", cx, cy + pitch * 3.2);
            const bw = 120, bx = cx - bw / 2, by = cy + pitch * 3.2 + 14;
            ctx.strokeStyle = "rgba(" + INK + ",0.3)"; ctx.strokeRect(bx + 0.5, by + 0.5, bw, 5);
            ctx.fillStyle = HOT; ctx.fillRect(bx + 1, by + 1, (bw - 1) * bright, 4);
        }
        ctx.globalAlpha = 1;
    }
    animate(flyC, flyFrame, () => startFly(0));

    // ---------- NASA görsel arşivi ----------
    const gal = $("gallery");
    fetch("https://images-api.nasa.gov/search?q=SPHEREx&media_type=image&page_size=14").then((r) => r.json()).then((d) => {
        const items = d.collection.items.filter((it) => it.links && it.links[0]);
        if (!items.length) throw new Error("boş");
        gal.textContent = "";
        items.forEach((it) => {
            const m = it.data[0], a = document.createElement("a"), w = document.createElement("div"), img = document.createElement("img"), b = document.createElement("b"), s = document.createElement("span");
            a.className = "shot"; a.target = "_blank"; a.rel = "noopener"; a.href = "https://images.nasa.gov/details/" + encodeURIComponent(m.nasa_id);
            w.className = "shot-img"; img.loading = "lazy"; img.alt = m.title; img.src = it.links[0].href.replace("~thumb", "~medium");
            b.textContent = m.title.trim(); s.textContent = m.date_created.slice(0, 10) + " · NASA/JPL-Caltech";
            w.append(img); a.append(w, b, s); gal.append(a);
        });
    }).catch(() => { $("gallery-msg").textContent = "NASA görsel arşivine ulaşılamadı."; });
})();
