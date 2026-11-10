/* ML Talks — static front end. Data lives in data/ (built by scraper/build.py). */
(() => {
  "use strict";
  const DATA = "data/";
  let CONF_NAMES = { neurips: "NeurIPS", icml: "ICML", iclr: "ICLR" };
  let CONF_FIELD = { neurips: "ml", icml: "ml", iclr: "ml" };
  const FIELDS = { all: "All fields", ml: "Machine learning", cv: "Computer vision", nlp: "NLP" };
  const KIND_ORDER = ["Oral", "Spotlight", "Poster", "Long", "Main", "Short", "Findings", "Tutorial", "Invited Talk", "Workshop", "Demo", "Industry", "Student", "Competition"];
  const PAGE = 48;

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // per-viewer storage (favorites, history, last filters); always optional
  const store = {
    get(k, d) { try { const v = localStorage.getItem("mlt:" + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem("mlt:" + k, JSON.stringify(v)); } catch { /* ignore */ } },
  };

  const state = Object.assign({ field: "all", conf: "all", year: null, kind: "all", onlyVideo: true, q: "" }, store.get("filters", {}));
  state.q = "";
  let manifest = { items: [] };
  const cache = new Map();      // "conf-year" -> rows
  const absCache = new Map();
  let results = [];
  let shown = 0;
  let viewMode = "home";        // home | library

  // ------------------------------------------------------------- data
  async function getJSON(path) {
    const r = await fetch(DATA + path, { cache: "default" });
    if (!r.ok) throw new Error(path + " " + r.status);
    return r.json();
  }
  async function loadSet(conf, year) {
    const key = conf + "-" + year;
    if (!cache.has(key)) {
      cache.set(key, getJSON("papers/" + key + ".json").then((rows) => {
        rows.forEach((r) => { r.c = conf; r.y = year; r._s = (r.t + " " + (r.a || []).join(" ") + " " + (r.tp || "")).toLowerCase(); });
        return rows;
      }).catch(() => []));
    }
    return cache.get(key);
  }
  function inField(m) { return state.field === "all" || CONF_FIELD[m.conf] === state.field; }
  function wantedSets() {
    return manifest.items.filter((m) => inField(m) && (state.conf === "all" || m.conf === state.conf) && (state.year === "all" || m.year === state.year));
  }

  // ------------------------------------------------------------- media helpers
  function firstVideo(r) { return r.vs && r.vs[0]; }
  function thumbURL(r) {
    if (r.th) return r.th;
    const v = firstVideo(r);
    if (v && v[0] === "yt") return "https://i.ytimg.com/vi/" + v[1] + "/mqdefault.jpg";
    return null;
  }
  function placeholder(r) {
    return `<div class="ph ${r.c} f-${CONF_FIELD[r.c] || "ml"}"><div class="pt">${esc(r.t)}</div><div class="pc">${CONF_NAMES[r.c]} ${r.y} · ${esc(r.k)}</div></div>`;
  }
  function thumbHTML(r) {
    const src = thumbURL(r);
    const n = r.vs ? r.vs.length : 0;
    const badge = n ? `<span class="badge">${n > 1 ? n + " videos" : srcName(r.vs[0][0])}</span>` : `<span class="badge none">No video</span>`;
    const img = src ? `<img loading="lazy" decoding="async" src="${esc(src)}" alt="" onerror="this.remove()">` : "";
    return `<div class="thumb">${placeholder(r)}${img}${badge}</div>`;
  }
  function srcName(s) { return { sl: "SlidesLive", yt: "YouTube", vm: "Vimeo", fb: "Facebook", mp4: "ACL Anthology" }[s] || s; }
  function link(r) { return `#/w/${r.c}/${r.y}/${r.id}`; }
  function authorsShort(a) { if (!a || !a.length) return ""; return a.length > 3 ? a.slice(0, 3).join(", ") + " et al." : a.join(", "); }

  function cardHTML(r) {
    return `<a class="card" href="${link(r)}">${thumbHTML(r)}<div class="info"><h3>${esc(r.t)}</h3>
      <div class="sub"><span class="tag ${r.c}">${CONF_NAMES[r.c]} ${r.y}</span> · ${esc(r.k)}${r.a && r.a.length ? " · " + esc(authorsShort(r.a)) : ""}</div></div></a>`;
  }
  function itemHTML(r) {
    return `<a class="li" href="${link(r)}">${thumbHTML(r)}<div><h3>${esc(r.t)}</h3>
      <div class="sub"><span class="tag ${r.c}">${CONF_NAMES[r.c]} ${r.y}</span> · ${esc(r.k)}</div>
      <div class="sub">${esc(authorsShort(r.a))}</div></div></a>`;
  }

  // ------------------------------------------------------------- filters UI
  let availableKinds = [];
  function renderFilters() {
    const present = new Set(manifest.items.map((m) => m.conf));
    const confs = Object.keys(CONF_NAMES).filter((c) => present.has(c) && (state.field === "all" || CONF_FIELD[c] === state.field));
    $("fieldChips").innerHTML = Object.keys(FIELDS).map((f) => `<button class="chip ${state.field === f ? "on" : ""}" data-field="${f}">${FIELDS[f]}</button>`).join("");
    $("confChips").innerHTML = ["all", ...confs].map((c) => `<button class="chip ${state.conf === c ? "on" : ""}" data-conf="${c}">${c === "all" ? "All conferences" : CONF_NAMES[c]}</button>`).join("");
    const years = [...new Set(manifest.items.filter((m) => inField(m) && (state.conf === "all" || m.conf === state.conf)).map((m) => m.year))].sort((a, b) => b - a);
    $("yearSel").innerHTML = `<option value="all">All years</option>` + years.map((y) => `<option value="${y}">${y}</option>`).join("");
    $("yearSel").value = String(state.year);
    renderKinds();
    $("onlyVideo").checked = state.onlyVideo;
  }
  function renderKinds() {
    const kinds = availableKinds.length ? availableKinds : (state.kind !== "all" ? [state.kind] : []);
    $("kindChips").innerHTML = ["all", ...kinds].map((k) => `<button class="chip ${state.kind === k ? "on" : ""}" data-kind="${esc(k)}">${k === "all" ? "All types" : kindZh(k)}</button>`).join("");
  }
  function kindZh(k) {
    return { Long: "Long paper", Short: "Short paper", Main: "Main track", Student: "Student research", Paper: "Paper" }[k] || k;
  }

  $("fieldChips").addEventListener("click", (e) => { const b = e.target.closest("[data-field]"); if (b) { state.field = b.dataset.field; state.conf = "all"; state.kind = "all"; changed(); } });
  $("confChips").addEventListener("click", (e) => { const b = e.target.closest("[data-conf]"); if (b) { state.conf = b.dataset.conf; changed(); } });
  $("kindChips").addEventListener("click", (e) => { const b = e.target.closest("[data-kind]"); if (b) { state.kind = b.dataset.kind; changed(); } });
  $("yearSel").addEventListener("change", (e) => { state.year = e.target.value === "all" ? "all" : +e.target.value; changed(); });
  $("onlyVideo").addEventListener("change", (e) => { state.onlyVideo = e.target.checked; changed(); });
  let qTimer;
  $("q").addEventListener("input", (e) => {
    clearTimeout(qTimer);
    qTimer = setTimeout(() => {
      state.q = e.target.value.trim();
      // searching from a single year is surprising; widen to all years
      if (state.q && state.year !== "all") { state.year = "all"; }
      changed();
    }, 250);
  });
  $("searchForm").addEventListener("submit", (e) => { e.preventDefault(); $("q").blur(); if (location.hash.startsWith("#/w/")) location.hash = "#/"; });
  $("libBtn").addEventListener("click", () => { location.hash = location.hash === "#/lib" ? "#/" : "#/lib"; });

  // keep the year on one that actually has videos for the current field/conference
  function ensureYear() {
    if (state.year === "all") return;
    const ys = manifest.items.filter((m) => inField(m) && (state.conf === "all" || m.conf === state.conf) && m.videos > 0).map((m) => m.year);
    if (ys.length && !ys.includes(state.year)) state.year = Math.max(...ys);
  }
  function changed() {
    ensureYear();
    store.set("filters", { field: state.field, conf: state.conf, year: state.year, kind: state.kind, onlyVideo: state.onlyVideo });
    renderFilters();
    if (!location.hash.startsWith("#/") || location.hash.startsWith("#/w/") || location.hash === "#/lib") location.hash = "#/";
    else refresh();
  }

  // ------------------------------------------------------------- home
  let refreshToken = 0;
  async function refresh() {
    const token = ++refreshToken;
    viewMode = "home";
    shown = 0;
    $("grid").innerHTML = "";
    $("sentinel").textContent = "Loading…";
    const sets = wantedSets();
    const terms = state.q.toLowerCase().split(/\s+/).filter(Boolean);
    const out = [];
    const kinds = new Set();
    // load newest first so results appear quickly on slow mobile connections
    for (const m of sets) {
      const rows = await loadSet(m.conf, m.year);
      if (token !== refreshToken) return;
      for (const r of rows) {
        if (state.onlyVideo && !r.vs) continue;
        kinds.add(r.k);
        if (state.kind !== "all" && r.k !== state.kind) continue;
        if (terms.length && !terms.every((t) => r._s.includes(t))) continue;
        out.push(r);
      }
      if (out.length >= PAGE && shown === 0 && !terms.length) { results = out; paint(true); }
    }
    if (token !== refreshToken) return;
    availableKinds = [...kinds].sort((a, b) => ((KIND_ORDER.indexOf(a) + 1) || 99) - ((KIND_ORDER.indexOf(b) + 1) || 99));
    renderKinds();
    if (terms.length) out.sort((a, b) => score(b, terms) - score(a, terms));
    results = out;
    $("count").textContent = `${out.length.toLocaleString()} talks`;
    paint(true);
  }
  function score(r, terms) {
    const t = r.t.toLowerCase();
    let s = 0;
    for (const w of terms) if (t.includes(w)) s += 2;
    if (r.vs) s += 1;
    return s + r.y / 10000;
  }
  function paint(reset) {
    if (reset) { $("grid").innerHTML = ""; shown = 0; }
    const next = results.slice(shown, shown + PAGE);
    $("grid").insertAdjacentHTML("beforeend", next.map(cardHTML).join(""));
    shown += next.length;
    if (!results.length) $("grid").innerHTML = `<div class="empty">No matching talks. Try turning off “Only with video” or another search.</div>`;
    $("sentinel").textContent = shown < results.length ? "Loading more…" : "";
  }
  new IntersectionObserver((es) => {
    if (es[0].isIntersecting && viewMode === "home" && shown < results.length && !$("home").hidden) paint(false);
  }, { rootMargin: "800px" }).observe($("sentinel"));

  // ------------------------------------------------------------- library (favorites + history)
  async function showLibrary() {
    viewMode = "library";
    $("libBtn").classList.add("on");
    const favs = store.get("favs", []);
    const hist = store.get("hist", []);
    const rowsFor = async (keys) => {
      const out = [];
      for (const k of keys) {
        const [c, y, id] = k.split("/");
        const rows = await loadSet(c, +y);
        const r = rows.find((x) => x.id === id);
        if (r) out.push(r);
      }
      return out;
    };
    const [f, h] = await Promise.all([rowsFor(favs), rowsFor(hist.slice(0, 60))]);
    $("grid").innerHTML =
      `<div class="libhead" style="grid-column:1/-1"><h2>Saved (${f.length})</h2></div>` +
      (f.length ? f.map(cardHTML).join("") : `<div class="empty" style="grid-column:1/-1">Nothing saved yet. Tap “Save” on a talk page.</div>`) +
      `<div class="libhead" style="grid-column:1/-1"><h2>History</h2>${h.length ? '<button class="chip" id="clearHist">Clear</button>' : ""}</div>` +
      (h.length ? h.map(cardHTML).join("") : `<div class="empty" style="grid-column:1/-1">No watch history yet.</div>`);
    $("sentinel").textContent = "";
    const ch = $("clearHist");
    if (ch) ch.onclick = () => { store.set("hist", []); showLibrary(); };
  }

  // ------------------------------------------------------------- watch
  function outLink(text, href, label) {
    return `<div class="out"><div>${text}<br><a href="${esc(href)}" target="_blank" rel="noopener">${label}</a></div></div>`;
  }
  async function play(r, v) {
    const p = $("player");
    if (!v) { p.innerHTML = outLink("No video found for this talk yet", r.u, "Check the conference page"); return; }
    const [src, id] = v;
    if (src === "yt") {
      p.innerHTML = `<iframe src="https://www.youtube-nocookie.com/embed/${encodeURIComponent(id)}?playsinline=1&rel=0" allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowfullscreen></iframe>`;
    } else if (src === "mp4") {
      p.innerHTML = `<video controls playsinline preload="metadata" src="${esc(id)}"></video>`;
    } else if (src === "vm") {
      p.innerHTML = `<iframe src="https://player.vimeo.com/video/${encodeURIComponent(id)}?playsinline=1" allow="autoplay; fullscreen; picture-in-picture" allowfullscreen></iframe>`;
    } else if (src === "fb") {
      p.innerHTML = `<iframe src="https://www.facebook.com/plugins/video.php?href=${encodeURIComponent(id)}&show_text=false" allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowfullscreen></iframe>`;
    } else if (src === "sl") {
      // Conferences restrict SlidesLive embeds to their own domains ("Domain not allowed"),
      // so we hand off to SlidesLive's own page, where the talk is publicly viewable.
      const bg = r.th ? `<img src="${esc(r.th)}" alt="" onerror="this.remove()">` : "";
      p.innerHTML = `<div class="handoff ${r.c} f-${CONF_FIELD[r.c] || "ml"}">${bg}<div class="scrim"></div>
        <div class="hcontent">
          <a class="playbig" href="https://slideslive.com/${esc(id)}" target="_blank" rel="noopener" aria-label="Watch on SlidesLive">
            <svg viewBox="0 0 24 24" width="34" height="34"><path fill="currentColor" d="M8 5v14l11-7z"/></svg></a>
          <a class="hbtn" href="https://slideslive.com/${esc(id)}" target="_blank" rel="noopener">Watch on SlidesLive</a>
          <a class="hlink" href="${esc(r.u)}" target="_blank" rel="noopener">or watch on the conference site</a>
          <div class="hnote">The conference only allows this video to play on official sites, so it opens in a new tab</div>
        </div></div>`;
    }
  }
  async function showWatch(conf, year, id) {
    $("libBtn").classList.remove("on");
    const rows = await loadSet(conf, year);
    const r = rows.find((x) => x.id === id);
    if (!r) { $("player").innerHTML = outLink("This talk could not be found", "#/", "Back to home"); return; }
    document.title = r.t + " · ML Talks";
    const key = `${conf}/${year}/${id}`;
    store.set("hist", [key, ...store.get("hist", []).filter((k) => k !== key)].slice(0, 200));

    const vs = r.vs || [];
    $("vidChips").innerHTML = vs.length > 1 ? vs.map((v, i) => `<button class="chip ${i === 0 ? "on" : ""}" data-i="${i}">Video ${i + 1}</button>`).join("") : "";
    $("vidChips").onclick = (e) => {
      const b = e.target.closest("[data-i]"); if (!b) return;
      [...$("vidChips").children].forEach((c) => c.classList.toggle("on", c === b));
      play(r, vs[+b.dataset.i]);
    };
    play(r, vs[0]);

    $("wTitle").textContent = r.t;
    $("wMeta").innerHTML = `<span class="tag ${conf}">${CONF_NAMES[conf]} ${year}</span> · ${esc(kindZh(r.k))}${r.tp ? " · " + esc(r.tp) : ""}${vs.length ? " · " + srcName(vs[0][0]) : ""}`;
    $("wAuthors").textContent = (r.a || []).join(", ");
    const favs = store.get("favs", []);
    const isFav = favs.includes(key);
    const links = [
      `<button id="favBtn" class="${isFav ? "primary" : ""}">${isFav ? "Saved" : "Save"}</button>`,
      `<a href="${esc(r.u)}" target="_blank" rel="noopener">Conference page</a>`,
      r.or ? `<a href="${esc(r.or)}" target="_blank" rel="noopener">OpenReview</a>` : "",
      r.pdf ? `<a href="${esc(r.pdf)}" target="_blank" rel="noopener">PDF</a>` : "",
      r.proj ? `<a href="${esc(r.proj)}" target="_blank" rel="noopener">Project page</a>` : "",
      vs[0] && vs[0][0] === "sl" ? `<a href="https://slideslive.com/${esc(vs[0][1])}" target="_blank" rel="noopener">Open on SlidesLive</a>` : "",
      navigator.share ? `<button id="shareBtn">Share</button>` : "",
    ];
    $("wLinks").innerHTML = links.join("");
    $("favBtn").onclick = () => {
      const f = store.get("favs", []);
      const on = !f.includes(key);
      store.set("favs", on ? [key, ...f] : f.filter((k) => k !== key));
      $("favBtn").textContent = on ? "Saved" : "Save";
      $("favBtn").classList.toggle("primary", on);
    };
    if ($("shareBtn")) $("shareBtn").onclick = () => navigator.share({ title: r.t, url: location.href }).catch(() => {});

    $("wAbs").textContent = "";
    $("wAbsBox").hidden = true;
    const absKey = conf + "-" + year;
    if (!absCache.has(absKey)) absCache.set(absKey, getJSON("abstracts/" + absKey + ".json").catch(() => ({})));
    absCache.get(absKey).then((abs) => {
      if (location.hash !== link(r)) return;
      if (abs[id]) { $("wAbs").textContent = abs[id]; $("wAbsBox").hidden = false; }
    });

    // related: same conference-year, same topic first, with video
    const topic = r.tp && r.tp.split("->")[0];
    const rel = rows.filter((x) => x !== r && x.vs);
    rel.sort((a, b) => ((b.tp || "").startsWith(topic || "\0") - (a.tp || "").startsWith(topic || "\0")) || (Math.random() - 0.5));
    $("related").innerHTML = rel.slice(0, 20).map(itemHTML).join("");
  }

  // ------------------------------------------------------------- router
  async function route() {
    const h = location.hash || "#/";
    const m = h.match(/^#\/w\/([a-z]+)\/(\d{4})\/([^/?#]+)/);
    $("home").hidden = !!m;
    $("filters").hidden = !!m || h === "#/lib";
    $("watch").hidden = !m;
    if (!m) { $("player").innerHTML = ""; document.title = "ML Talks"; }
    window.scrollTo(0, 0);
    if (m) return showWatch(m[1], +m[2], decodeURIComponent(m[3]));
    if (h === "#/lib") return showLibrary();
    $("libBtn").classList.remove("on");
    return refresh();
  }
  window.addEventListener("hashchange", route);

  // ------------------------------------------------------------- boot
  (async () => {
    try { manifest = await getJSON("manifest.json"); } catch { manifest = { items: [] }; }
    manifest.items = manifest.items.filter((m) => m.events > 0);
    if (manifest.confs) {
      CONF_NAMES = {}; CONF_FIELD = {};
      for (const [k, v] of Object.entries(manifest.confs)) { CONF_NAMES[k] = v.name; CONF_FIELD[k] = v.field; }
    }
    $("updated").textContent = manifest.updated || "—";
    if (manifest.repo) $("repoLink").href = manifest.repo;
    const years = [...new Set(manifest.items.filter((m) => m.videos > 0).map((m) => m.year))].sort((a, b) => b - a);
    if (state.year !== "all" && !years.includes(state.year)) state.year = years[0] ?? "all";
    ensureYear();
    renderFilters();
    route();
  })();

  if ("serviceWorker" in navigator && location.protocol === "https:") {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }
})();
