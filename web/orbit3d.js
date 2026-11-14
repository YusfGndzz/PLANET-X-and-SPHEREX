/* Şek. 1 — SPHEREx'in gerçek konumu, üç boyutlu.
   Her şey gök eylemsiz çerçevesinde (ECI) kurulur: Dünya kendi ekseninde gerçek açısıyla döner,
   Güneş gerçek yönünden aydınlatır, uydu CelesTrak yörünge elemanlarından SGP4 ile hesaplanır,
   arka plandaki yıldızlar gerçek katalog konumlarındadır.  three: (x, y, z) = ECI (x, z, -y), birim = Dünya yarıçapı. */
(function () {
    "use strict";
    const RE = 6371, D2R = Math.PI / 180, TAU = Math.PI * 2;
    const IMG = "https://cdn.jsdelivr.net/npm/three-globe@2.31.0/example/img/";

    const EARTH_VS = `
        varying vec2 vUv; varying vec3 vN; varying vec3 vP;
        void main() {
            vUv = uv;
            vN = normalize(mat3(modelMatrix) * normal);
            vec4 w = modelMatrix * vec4(position, 1.0);
            vP = w.xyz;
            gl_Position = projectionMatrix * viewMatrix * w;
        }`;
    const EARTH_FS = `
        uniform sampler2D dayMap; uniform sampler2D nightMap; uniform sampler2D waterMap;
        uniform vec3 sunDir; uniform float ready;
        varying vec2 vUv; varying vec3 vN; varying vec3 vP;
        void main() {
            vec3 n = normalize(vN);
            vec3 v = normalize(cameraPosition - vP);
            float d = dot(n, sunDir);
            float lit = smoothstep(-0.10, 0.22, d);
            vec3 day = mix(vec3(0.05, 0.06, 0.08), texture2D(dayMap, vUv).rgb, ready);
            vec3 col = day * (0.02 + 1.25 * max(d, 0.0));
            float water = texture2D(waterMap, vUv).r * ready;
            vec3 h = normalize(v + sunDir);
            col += vec3(1.0, 0.93, 0.8) * pow(max(dot(n, h), 0.0), 320.0) * water * 0.5 * lit;
            vec3 night = texture2D(nightMap, vUv).rgb * ready;
            col = mix(night * vec3(1.25, 1.0, 0.7) + day * 0.012, col, lit);
            float rim = pow(1.0 - max(dot(n, v), 0.0), 3.2);
            col += vec3(0.22, 0.45, 1.0) * rim * 0.7 * smoothstep(-0.25, 0.35, d);
            gl_FragColor = vec4(col, 1.0);
            #include <colorspace_fragment>
        }`;
    const ATM_VS = `
        varying vec3 vNv; varying vec3 vNw;
        void main() {
            vNv = normalize(normalMatrix * normal);
            vNw = normalize(mat3(modelMatrix) * normal);
            gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }`;
    const ATM_FS = `
        uniform vec3 sunDir; varying vec3 vNv; varying vec3 vNw;
        void main() {
            float i = pow(max(0.0, 0.66 - dot(vNv, vec3(0.0, 0.0, 1.0))), 5.0);
            float dayside = smoothstep(-0.35, 0.45, dot(normalize(vNw), sunDir));
            gl_FragColor = vec4(vec3(0.28, 0.55, 1.0) * i * (0.06 + 0.94 * dayside), 1.0);
        }`;

    function sunEci(date) {
        const n = date.getTime() / 864e5 + 2440587.5 - 2451545.0;
        const L = (280.460 + 0.9856474 * n) * D2R, g = (357.528 + 0.9856003 * n) * D2R;
        const lam = L + (1.915 * Math.sin(g) + 0.020 * Math.sin(2 * g)) * D2R, eps = (23.439 - 4e-7 * n) * D2R;
        return [Math.cos(lam), Math.cos(eps) * Math.sin(lam), Math.sin(eps) * Math.sin(lam)];
    }

    function buildSatellite(THREE) {
        const g = new THREE.Group();
        const white = new THREE.MeshStandardMaterial({ color: 0xece9e2, metalness: 0.25, roughness: 0.45, side: THREE.DoubleSide });
        const inner = new THREE.MeshStandardMaterial({ color: 0xbfc4cc, metalness: 0.85, roughness: 0.25, side: THREE.DoubleSide });
        // iç içe üç foton kalkanı (koni), ağzı Dünya'nın tersine bakar
        [[1.0, 0.34, 1.25], [0.78, 0.30, 1.12], [0.58, 0.26, 1.0]].forEach(([rt, rb, h], i) => {
            const m = new THREE.Mesh(new THREE.CylinderGeometry(rt, rb, h, 56, 1, true), i === 0 ? white : inner);
            m.position.y = h / 2 - 0.1; g.add(m);
        });
        const scope = new THREE.Mesh(new THREE.CylinderGeometry(0.2, 0.2, 0.7, 24), new THREE.MeshStandardMaterial({ color: 0x23262b, metalness: 0.6, roughness: 0.5 }));
        scope.position.y = 0.3; g.add(scope);
        const bus = new THREE.Mesh(new THREE.CylinderGeometry(0.36, 0.36, 0.5, 6), new THREE.MeshStandardMaterial({ color: 0xa48a4e, metalness: 0.7, roughness: 0.45 }));
        bus.position.y = -0.36; g.add(bus);
        const panel = new THREE.Mesh(new THREE.BoxGeometry(1.25, 0.95, 0.03), new THREE.MeshStandardMaterial({ color: 0x141c3a, metalness: 0.5, roughness: 0.3 }));
        panel.position.set(0, -0.3, 0.42); g.add(panel);
        return g;
    }

    function start(dep) {
        const THREE = window.THREE;
        if (!THREE || !window.satellite) return false;
        const frame = document.querySelector("#fig-orbit .fig-frame"), old = document.getElementById("globe");
        let renderer;
        try { renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" }); }
        catch (e) { return false; }
        const cv = renderer.domElement;
        cv.id = "globe3d";
        frame.insertBefore(cv, old);
        old.style.display = "none";
        renderer.setClearColor(0x040406, 1);

        const label = document.createElement("div"); label.className = "sat-label"; label.textContent = "SPHEREx";
        const clock = document.createElement("div"); clock.className = "sat-clock";
        frame.append(label, clock);

        const scene = new THREE.Scene();
        const cam = new THREE.PerspectiveCamera(33, 1, 0.0008, 400);
        const V = (x, y, z) => new THREE.Vector3(x, y, z);
        const eci = (p, k) => V(p.x * k, p.z * k, -p.y * k);

        // Dünya
        const uni = {
            dayMap: { value: null }, nightMap: { value: null }, waterMap: { value: null },
            sunDir: { value: V(1, 0, 0) }, ready: { value: 0 }
        };
        const earth = new THREE.Mesh(new THREE.SphereGeometry(1, 128, 80),
            new THREE.ShaderMaterial({ uniforms: uni, vertexShader: EARTH_VS, fragmentShader: EARTH_FS }));
        scene.add(earth);
        const atm = new THREE.Mesh(new THREE.SphereGeometry(1.075, 96, 64), new THREE.ShaderMaterial({
            uniforms: { sunDir: uni.sunDir }, vertexShader: ATM_VS, fragmentShader: ATM_FS,
            side: THREE.BackSide, blending: THREE.AdditiveBlending, transparent: true, depthWrite: false
        }));
        scene.add(atm);
        const loader = new THREE.TextureLoader(); loader.setCrossOrigin("anonymous");
        const aniso = Math.min(8, renderer.capabilities.getMaxAnisotropy());
        const load = (name, srgb) => new Promise((ok, no) => loader.load(IMG + name, (t) => {
            if (srgb) t.colorSpace = THREE.SRGBColorSpace;
            t.anisotropy = aniso; ok(t);
        }, undefined, no));
        const blank = new THREE.DataTexture(new Uint8Array([0, 0, 0, 255]), 1, 1); blank.needsUpdate = true;
        uni.dayMap.value = uni.nightMap.value = uni.waterMap.value = blank;
        load("earth-blue-marble.jpg", true).then((t) => { uni.dayMap.value = t; uni.ready.value = 1; }).catch(() => {});
        load("earth-night.jpg", true).then((t) => { uni.nightMap.value = t; }).catch(() => {});
        load("earth-water.png", false).then((t) => { uni.waterMap.value = t; }).catch(() => {});

        // ışık (yalnızca uydu modeli için; Dünya kendi gölgelendiricisini kullanır)
        const sun = new THREE.DirectionalLight(0xffffff, 3.2); scene.add(sun);
        scene.add(new THREE.AmbientLight(0x8fa6c8, 0.35));

        // gerçek yıldızlar
        let starsBuilt = false;
        function buildStars() {
            const st = dep.sky.stars; if (!st.length) return;
            starsBuilt = true;
            const pos = new Float32Array(st.length * 3), col = new Float32Array(st.length * 3);
            st.forEach((s, i) => {
                const ra = s[0] * D2R, de = s[1] * D2R, R = 180;
                pos[i * 3] = R * Math.cos(de) * Math.cos(ra); pos[i * 3 + 1] = R * Math.sin(de); pos[i * 3 + 2] = -R * Math.cos(de) * Math.sin(ra);
                const b = Math.min(1, 0.16 + (6.5 - s[2]) * 0.16), warm = s[3] > 1.1, cool = s[3] < 0.2;
                col[i * 3] = b * (cool ? 0.82 : 1); col[i * 3 + 1] = b * (warm ? 0.85 : 0.93); col[i * 3 + 2] = b * (warm ? 0.7 : 1);
            });
            const g = new THREE.BufferGeometry();
            g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
            g.setAttribute("color", new THREE.BufferAttribute(col, 3));
            scene.add(new THREE.Points(g, new THREE.PointsMaterial({ size: 1.7, sizeAttenuation: false, vertexColors: true, depthWrite: false })));
        }

        // yörünge halkası ve uydu
        const NP = 240, ringPos = new Float32Array((NP + 1) * 3);
        const ringGeo = new THREE.BufferGeometry(); ringGeo.setAttribute("position", new THREE.BufferAttribute(ringPos, 3));
        const ring = new THREE.Line(ringGeo, new THREE.LineBasicMaterial({ color: 0xece8df, transparent: true, opacity: 0.5 }));
        ring.frustumCulled = false; scene.add(ring);
        const lookGeo = new THREE.BufferGeometry(); lookGeo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(6), 3));
        const look = new THREE.Line(lookGeo, new THREE.LineDashedMaterial({ color: 0xff5a1f, dashSize: 0.02, gapSize: 0.025, transparent: true, opacity: 0.9 }));
        look.frustumCulled = false; scene.add(look);
        const sat = buildSatellite(THREE); scene.add(sat);
        let ringAt = -1e18;

        // kamera
        const view = { az: 58 * D2R, el: 17 * D2R, mode: 0, mix: 0, drag: null };
        cv.addEventListener("pointerdown", (e) => { view.drag = { x: e.clientX, y: e.clientY, az: view.az, el: view.el }; cv.setPointerCapture(e.pointerId); cv.classList.add("is-drag"); });
        cv.addEventListener("pointermove", (e) => {
            if (!view.drag) return;
            view.az = view.drag.az - (e.clientX - view.drag.x) * 0.006;
            view.el = Math.max(-1.3, Math.min(1.3, view.drag.el + (e.clientY - view.drag.y) * 0.006));
        });
        const endDrag = () => { view.drag = null; cv.classList.remove("is-drag"); };
        cv.addEventListener("pointerup", endDrag); cv.addEventListener("pointercancel", endDrag);
        document.querySelectorAll("#fig-orbit [data-view]").forEach((b) => b.addEventListener("click", () => {
            document.querySelectorAll("#fig-orbit [data-view]").forEach((x) => x.classList.toggle("is-on", x === b));
            view.mode = +b.dataset.view;
        }));

        const tmp = V(0, 0, 0), up = V(0, 1, 0), pos0 = V(0, 0, 0), pos1 = V(0, 0, 0), tg1 = V(0, 0, 0), mat = new THREE.Matrix4();
        let lastRO = 0;
        const $ = dep.$, tr = dep.tr, orbit = dep.orbit;

        function frameFn(dt, tsec) {
            const w = cv.clientWidth, h = cv.clientHeight;
            if (!w || !h) return;
            const pr = Math.min(devicePixelRatio || 1, 2);
            if (cv.width !== Math.round(w * pr) || cv.height !== Math.round(h * pr)) { renderer.setPixelRatio(pr); renderer.setSize(w, h, false); }
            cam.aspect = w / h;
            if (!starsBuilt) buildStars();

            orbit.simT = orbit.speed === 1 ? Date.now() : orbit.simT + dt * 1000 * orbit.speed;
            const date = new Date(orbit.simT), gmst = satellite.gstime(date);
            earth.rotation.y = gmst;
            const s = sunEci(date), sd = V(s[0], s[2], -s[1]).normalize();
            uni.sunDir.value.copy(sd); sun.position.copy(sd).multiplyScalar(50);
            clock.textContent = date.toISOString().slice(0, 19).replace("T", " ") + " UTC" + (orbit.speed === 1 ? " · canlı" : " · ×" + orbit.speed);

            let sp = null, vel = null, geo = null;
            if (orbit.satrec) {
                const pv = satellite.propagate(orbit.satrec, date);
                if (pv.position) {
                    sp = eci(pv.position, 1 / RE); vel = eci(pv.velocity, 1).normalize();
                    geo = satellite.eciToGeodetic(pv.position, gmst);
                    if (Math.abs(orbit.simT - ringAt) > 20000) {
                        ringAt = orbit.simT;
                        const per = TAU / orbit.satrec.no * 60000;
                        for (let i = 0; i <= NP; i++) {
                            const q = satellite.propagate(orbit.satrec, new Date(orbit.simT + per * i / NP)).position;
                            if (q) { ringPos[i * 3] = q.x / RE; ringPos[i * 3 + 1] = q.z / RE; ringPos[i * 3 + 2] = -q.y / RE; }
                        }
                        ringGeo.attributes.position.needsUpdate = true;
                    }
                    if (tsec - lastRO > 0.2) {
                        lastRO = tsec;
                        const la = geo.latitude / D2R, lo = geo.longitude / D2R;
                        $("or-lat").textContent = tr(Math.abs(la), 1) + "° " + (la >= 0 ? "K" : "G");
                        $("or-lon").textContent = tr(Math.abs(lo), 1) + "° " + (lo >= 0 ? "D" : "B");
                        $("or-alt").textContent = geo.height.toFixed(0) + " km";
                        $("or-vel").textContent = tr(Math.hypot(pv.velocity.x, pv.velocity.y, pv.velocity.z), 2) + " km/sn";
                    }
                }
            }
            sat.visible = ring.visible = look.visible = !!sp;

            // kamera: genel görünüm (Güneş'e göre sabit açı) <-> uyduyu izleme
            view.mix += ((view.mode && sp ? 1 : 0) - view.mix) * Math.min(1, dt * 2.6);
            const a = Math.atan2(s[1], s[0]) + view.az, ce = Math.cos(view.el), dist = 4.15;
            pos0.set(ce * Math.cos(a), Math.sin(view.el), -ce * Math.sin(a)).multiplyScalar(dist);
            let upv = up;
            if (sp) {
                const rad = tmp.copy(sp).normalize();
                const side = V(0, 0, 0).crossVectors(vel, rad).normalize();
                pos1.copy(sp).addScaledVector(rad, 0.035).addScaledVector(vel, -0.105).addScaledVector(side, 0.04);
                tg1.copy(sp).addScaledVector(vel, 0.03).addScaledVector(rad, -0.012);
                const k = view.mix * view.mix * (3 - 2 * view.mix);
                cam.position.lerpVectors(pos0, pos1, k);
                const tg = V(0, 0, 0).lerp(tg1, k);
                upv = V(0, 1, 0).lerp(rad, k).normalize();
                cam.up.copy(upv); cam.lookAt(tg);

                // uydu: koninin ağzı Dünya'nın tersine, güneş paneli Güneş'e
                const y = rad.clone(), z = sd.clone().addScaledVector(y, -sd.dot(y)).normalize(), x = V(0, 0, 0).crossVectors(y, z);
                sat.quaternion.setFromRotationMatrix(mat.makeBasis(x, y, z));
                sat.position.copy(sp);
                sat.scale.setScalar(0.03 + (0.0085 - 0.03) * k);
                const lp = lookGeo.attributes.position.array, far = 0.34 * (1 - k) + 0.03;
                lp[0] = sp.x; lp[1] = sp.y; lp[2] = sp.z; lp[3] = sp.x + rad.x * far; lp[4] = sp.y + rad.y * far; lp[5] = sp.z + rad.z * far;
                lookGeo.attributes.position.needsUpdate = true; look.computeLineDistances();
                look.material.opacity = 0.9 * (1 - k); ring.material.opacity = 0.5 - 0.25 * k;

                // etiket: uydu Dünya'nın arkasındaysa gizle
                const p = sp.clone().project(cam);
                const toSat = sp.clone().sub(cam.position), L = toSat.length(); toSat.divideScalar(L);
                const tca = -cam.position.dot(toSat), d2 = cam.position.lengthSq() - tca * tca;
                const hidden = tca > 0 && tca < L && d2 < 1;
                label.style.opacity = hidden || p.z > 1 ? 0 : String(1 - k);
                label.style.transform = "translate(" + ((p.x + 1) / 2 * w).toFixed(1) + "px," + ((1 - p.y) / 2 * h).toFixed(1) + "px)";
            } else {
                cam.position.copy(pos0); cam.up.copy(up); cam.lookAt(0, 0, 0); label.style.opacity = 0;
            }
            cam.updateProjectionMatrix();
            renderer.render(scene, cam);
        }
        dep.animate(cv, frameFn);
        return true;
    }

    window.Orbit3D = { start };
})();
