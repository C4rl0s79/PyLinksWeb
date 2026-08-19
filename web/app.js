"use strict";

// Most pywebview: window.pywebview.api pojawia się po zdarzeniu 'pywebviewready'.
// W zwykłej przeglądarce (podgląd) mostu nie ma — degradujemy się łagodnie.
const el = (id) => document.getElementById(id);
const hasBridge = () => !!(window.pywebview && window.pywebview.api);

let PC_GAMES = [];      // ostatni skan PC (odchudzone wpisy z backendu)
let CUR_SRC  = "";      // aktywny filtr źródła ("" = wszystkie)
let FILTER   = "";      // filtr tekstowy nazwy
let LOAD_TOK = 0;       // token anulowania leniwego ładowania okładek
let FIT      = "pad";   // tryb dopasowania ikony (kwadrat): "pad" | "crop"
let MODE     = "icon";  // tryb grafiki: "icon" (kwadrat) | "steam" (portret)
let CURRENT_VIEW = null; // {type:"pc"|"rom", filter} — do eksportu

function applyCardSize(px){
  document.documentElement.style.setProperty("--card-size", px + "px");
}

function applyFit(){
  // klasa siatki niesie i tryb grafiki, i dopasowanie (istotne tylko dla ikon)
  el("grid").className = "grid mode-" + MODE + " fit-" + FIT;
  const bf = el("btnFit");
  if (bf){ bf.textContent = FIT === "crop" ? "⬛ Wypełnij" : "⬚ Całość"; bf.hidden = (MODE !== "icon"); }
  const bm = el("btnMode");
  if (bm) bm.textContent = MODE === "steam" ? "🖼 Tryb: Steam" : "🖼 Tryb: Ikony";
}

function setBridge(ok, txt){
  const b = el("statBridge");
  b.textContent = "most: " + txt;
  b.className = "pill " + (ok ? "ok" : "err");
  const eb = el("engineBadge");
  eb.textContent = ok ? "silnik OK" : "brak mostu";
  eb.className = "badge " + (ok ? "ok" : "err");
}

async function api(method, ...args){
  if (!hasBridge()) throw new Error("brak mostu pywebview");
  return await window.pywebview.api[method](...args);
}

async function init(){
  if (!hasBridge()){
    setBridge(false, "podgląd (uruchom: python main.py)");
    el("statPaths").textContent = "Uruchom przez main.py, aby wczytać bibliotekę.";
    return;
  }
  try{
    const info = await api("get_info");
    el("ver").textContent = "v" + info.version;
    el("statPaths").textContent =
      `root: ${info.paths.root}   •   config: ${info.config_exists ? "✓" : "domyślny"}`;
    setBridge(true, "OK");
    try{ FIT = await api("get_fit_mode"); }catch(_){}
    applyFit();
    const sz = info.thumb_size || 160;
    el("cardSize").value = sz; applyCardSize(sz);
    await loadPlatforms();
  }catch(e){
    setBridge(false, "błąd: " + e.message);
  }
}

// ── skróty LINKS (istniejące) ──────────────────────────────────────────────
async function loadPlatforms(){
  const box = el("platforms");
  try{
    const plats = await api("list_platforms");
    el("platCount").textContent = plats.length ? `(${plats.length})` : "";
    if (!plats.length){ box.innerHTML = `<div class="muted pad">— brak —</div>`; return; }
    box.innerHTML = "";
    for (const p of plats){
      const b = document.createElement("button");
      b.className = "plat-item";
      b.innerHTML = `<span>${p.name}</span><span class="count">${p.count}</span>`;
      b.onclick = () => showLinks(p.name, b);
      box.appendChild(b);
    }
  }catch(e){ box.innerHTML = `<div class="muted pad">Błąd: ${e.message}</div>`; }
}

async function showLinks(name, node){
  clearActive(); node.classList.add("active");
  LOAD_TOK++;                       // anuluj ładowanie okładek PC
  el("filter").hidden = true;
  el("btnExport").hidden = true;    // LINKS = gotowe skróty, nie eksportujemy
  el("gridTitle").textContent = "LINKS / " + name;
  const grid = el("grid");
  try{
    const games = await api("list_games", name);
    el("gridSub").textContent = `${games.length} skrótów`;
    grid.innerHTML = games.length ? "" : `<div class="welcome muted">Brak skrótów.</div>`;
    for (const g of games) grid.appendChild(card(g.name, g.platform, "extra"));
    squareThumbs();
  }catch(e){ grid.innerHTML = `<div class="welcome muted">Błąd: ${e.message}</div>`; }
}

// ── skan PC (Steam/GOG/Epic/Extra) ─────────────────────────────────────────
async function runScan(){
  clearActive(); LOAD_TOK++;         // anuluj poprzednie ładowanie okładek
  document.querySelector('.src[data-src=""]').classList.add("active");
  CUR_SRC = "";
  el("gridTitle").textContent = "Biblioteka PC";
  el("gridSub").textContent = "skanuję…";
  el("grid").innerHTML = `<div class="scanning"><div class="spin"></div>
    Skanuję Steam / GOG / Epic / Extra…</div>`;
  try{
    const r = await api("scan_pc");
    PC_GAMES = r.games || [];
    updateCounts(r.counts, r.total);
    el("filter").hidden = false;
    renderPC();
  }catch(e){
    el("grid").innerHTML = `<div class="welcome muted">Błąd skanu: ${e.message}</div>`;
  }
  scanRomsUI();   // skan ROM-ów w tle
}

// ── ROM-y ────────────────────────────────────────────────────────────────
let ROM_GAMES = [];
let SPINE = {enabled:false, side:"left"};
let SYS_LOGO = {};      // system → logo (data-URI)
async function scanRomsUI(){
  const box = el("romSystems");
  try{
    const r = await api("scan_roms");
    if (!r.enabled){ box.innerHTML = `<div class="muted pad">wyłączone w configu</div>`; return; }
    ROM_GAMES = []; SYS_LOGO = {}; SPINE = r.spine || SPINE;
    el("romTotal").textContent = r.total ? `(${r.total})` : "";
    box.innerHTML = "";
    for (const s of r.systems){
      if (!s.count) continue;
      ROM_GAMES.push(...s.games);
      if (s.logo) SYS_LOGO[s.name] = s.logo;
      const b = document.createElement("button");
      b.className = "plat-item";
      b.innerHTML = `<span>${s.name}</span><span class="count">${s.count}</span>`;
      b.onclick = () => showRoms(s.name, b);
      box.appendChild(b);
    }
    if (!box.children.length) box.innerHTML = `<div class="muted pad">brak ROM-ów</div>`;
  }catch(e){ box.innerHTML = `<div class="muted pad">Błąd: ${e.message}</div>`; }
}

function showRoms(system, node){
  clearActive(); if (node) node.classList.add("active"); LOAD_TOK++;
  el("filter").hidden = true;
  el("gridTitle").textContent = "ROM / " + system;
  const games = ROM_GAMES.filter(g => g.system === system);
  el("gridSub").textContent = `${games.length} gier`;
  CURRENT_VIEW = {type: "rom", filter: system};
  el("btnExport").hidden = games.length === 0;
  const grid = el("grid"); grid.innerHTML = "";
  for (const g of games){
    const c = card(g.name, g.system, "rom", g);
    if (SPINE.enabled && SYS_LOGO[g.system]) addSpine(c, SYS_LOGO[g.system], SPINE.side);
    grid.appendChild(c);
  }
  loadCovers(games);
}

function addSpine(cardEl, logoUri, side){
  const thumb = cardEl.querySelector(".thumb");
  if (!thumb) return;
  thumb.classList.add(side === "right" ? "spine-right" : "spine-left");
  const sp = document.createElement("div");
  sp.className = "spine";
  const img = document.createElement("img"); img.src = logoUri; img.alt = "";
  sp.appendChild(img);
  thumb.appendChild(sp);
}

function updateCounts(counts, total){
  el("pcTotal").textContent = total ? `(${total})` : "";
  const set = (k,v) => { const n=document.querySelector(`.count[data-c="${k}"]`); if(n) n.textContent=v; };
  set("all", total||0);
  set("steam", counts.steam||0); set("gog", counts.gog||0);
  set("epic", counts.epic||0);   set("extra", counts.extra||0);
}

function renderPC(){
  const grid = el("grid");
  let games = CUR_SRC ? PC_GAMES.filter(g => g.source === CUR_SRC) : PC_GAMES;
  if (FILTER){
    const q = FILTER.toLowerCase();
    games = games.filter(g => g.name.toLowerCase().includes(q));
  }
  el("gridTitle").textContent = CUR_SRC ? "PC / " + CUR_SRC.toUpperCase() : "Biblioteka PC";
  el("gridSub").textContent = `${games.length} gier` + (FILTER ? ` (filtr)` : "");
  CURRENT_VIEW = {type: "pc", filter: CUR_SRC};
  el("btnExport").hidden = games.length === 0;
  if (!games.length){ grid.innerHTML = `<div class="welcome muted">Brak gier.</div>`; return; }
  grid.innerHTML = "";
  for (const g of games) grid.appendChild(card(g.name, g.source, g.source, g));
  loadCovers(games);   // leniwe doczytanie okładek (miniatury upieczone na kwadrat/portret w PIL)
}

function card(name, sub, src, game){
  const c = document.createElement("div");
  c.className = "card";
  if (game && game.key){
    c.dataset.key = game.key;
    c.onclick = () => openPicker(game);
    c.oncontextmenu = (e) => { e.preventDefault(); showCtx(e.clientX, e.clientY, game); };
  }
  c.title = (game && game.game_dir) ? game.game_dir : name;
  c.innerHTML =
    `<div class="thumb"><span class="src src-${src}">${src}</span><span class="ph">🎮</span></div>
     <div class="cap"><span class="t">${name}</span><span class="p">${sub}</span></div>`;
  return c;
}

// Kształt robi CSS: pad = contain, crop = cover + object-position (offset).
function styleCover(img, fit, offset){
  if (MODE === "steam"){   // portret Steam zawsze wypełnia ramkę
    img.style.objectFit = "cover"; img.style.objectPosition = "center"; return;
  }
  if (fit === "crop"){
    img.style.objectFit = "cover";
    const p = Math.round((offset == null ? 0.5 : offset) * 100);
    img.style.objectPosition = p + "% " + p + "%";
  } else {
    img.style.objectFit = "contain";
    img.style.objectPosition = "center";
  }
}

function setCover(key, dataUri, fit, offset){
  const c = document.querySelector(`.card[data-key="${cssEsc(key)}"] .thumb`);
  if (!c) return;
  c.classList.remove("loading");
  if (!dataUri) return;
  const old = c.querySelector("img.cover"); if (old) old.remove();  // NIE ruszaj logo grzbietu
  const ph = c.querySelector(".ph"); if (ph) ph.style.display = "none";  // schowaj 🎮 pod okładką
  const img = document.createElement("img");
  img.className = "cover";
  img.alt = ""; styleCover(img, fit, offset); img.src = dataUri;
  c.insertBefore(img, ph);
  // karta z grzbietem: plakat wypełnia wcięty obszar (portret + grzbiet = kwadrat)
  if (c.classList.contains("spine-left") || c.classList.contains("spine-right")){
    img.style.objectFit = "cover"; img.style.objectPosition = "center";
  }
}

function restyleCover(key, fit, offset){
  const img = document.querySelector(`.card[data-key="${cssEsc(key)}"] .thumb img`);
  if (img) styleCover(img, fit, offset);
}
const cssEsc = (s) => (window.CSS && CSS.escape) ? CSS.escape(s) : s.replace(/"/g,'\\"');

// Najpierw szybki cache dyskowy dla wszystkich, potem sieć dla brakujących.
// Token LOAD_TOK anuluje ładowanie, gdy widok się zmieni (filtr/źródło/skan).
async function loadCovers(games){
  const tok = ++LOAD_TOK;
  const keys = games.map(g => g.key).filter(Boolean);
  const missing = [];
  // 1) JEDNO zbiorcze wywołanie mostu dla całego cache (zamiast N round-tripów)
  try{
    const batch = await api("covers_batch", keys, MODE);
    if (tok !== LOAD_TOK) return;
    for (const g of games){
      const r = g.key && batch[g.key];
      if (r) setCover(g.key, r.uri, r.fit, r.offset);
      else if (g.key) missing.push(g);
    }
  }catch(e){ return; }
  // 2) sieć tylko dla brakujących (sekwencyjnie)
  for (const g of missing){
    if (tok !== LOAD_TOK) return;
    const c = document.querySelector(`.card[data-key="${cssEsc(g.key)}"] .thumb`);
    if (c) c.classList.add("loading");
    try{
      const r = await api("fetch_cover", g.key, MODE);
      if (tok !== LOAD_TOK) return;
      setCover(g.key, r.found ? r.data_uri : null, r.fit, r.offset);
    }catch(e){ if (c) c.classList.remove("loading"); }
  }
}

function clearActive(){
  document.querySelectorAll(".side-item,.plat-item").forEach(n => n.classList.remove("active"));
}

// ── menu kontekstowe (prawy klik) ────────────────────────────────────────────
let CTX_GAME = null;
function showCtx(x, y, game){
  CTX_GAME = game;
  const m = el("ctxMenu");
  m.hidden = false;
  // trzymaj w oknie
  const w = m.offsetWidth || 200, h = m.offsetHeight || 80;
  m.style.left = Math.min(x, innerWidth - w - 8) + "px";
  m.style.top  = Math.min(y, innerHeight - h - 8) + "px";
}
function hideCtx(){ el("ctxMenu").hidden = true; CTX_GAME = null; }

async function resetCover(game){
  const key = game.key;
  const thumb = document.querySelector(`.card[data-key="${cssEsc(key)}"] .thumb`);
  if (thumb){ thumb.classList.add("loading"); const im=thumb.querySelector("img"); if(im) im.remove(); }
  try{
    const r = await api("reset_cover", key, MODE);
    setCover(key, r.found ? r.data_uri : null, r.fit, r.offset);
  }catch(e){ if (thumb) thumb.classList.remove("loading"); }
}

el("ctxMenu").addEventListener("click", (e) => {
  const act = e.target.dataset.act;
  const g = CTX_GAME;
  hideCtx();
  if (!g) return;
  if (act === "pick") openPicker(g);
  else if (act === "crop") openCrop(g);
  else if (act === "reset") resetCover(g);
});

// ── okno kadrowania (pad/crop + pozycja), podgląd po stronie klienta ────────
let CROP_GAME = null, CROP_IMG = null;
function openCrop(game){
  CROP_GAME = game; CROP_IMG = null;
  el("cropTitle").textContent = "Kadrowanie: " + game.name;
  el("cropModal").hidden = false;
  api("crop_get", game.key, MODE).then(r => {
    document.querySelector(`input[name=cropFit][value="${r.fit==='crop'?'crop':'pad'}"]`).checked = true;
    el("cropOffset").value = (r.offset != null ? r.offset : 0.5);
    if (r.raw){
      const im = new Image();
      im.onload = () => { CROP_IMG = im; drawCrop(); };
      im.src = r.raw;
    } else { drawCrop(); }
    updateCropOffState();
  }).catch(()=>{});
}
function closeCrop(){ el("cropModal").hidden = true; CROP_GAME = null; CROP_IMG = null; }
function cropFit(){ return document.querySelector("input[name=cropFit]:checked").value; }
function updateCropOffState(){ el("cropOffRow").classList.toggle("dis", cropFit() !== "crop"); }

function drawCrop(){
  const cv = el("cropCanvas"), ctx = cv.getContext("2d");
  ctx.clearRect(0,0,256,256);
  if (!CROP_IMG) return;
  const iw = CROP_IMG.naturalWidth, ih = CROP_IMG.naturalHeight;
  if (cropFit() === "crop"){
    const off = +el("cropOffset").value;
    const side = Math.min(iw, ih);
    let sx, sy;
    if (ih > iw){ sx = (iw-side)/2; sy = (ih-side)*off; }
    else { sx = (iw-side)*off; sy = (ih-side)/2; }
    ctx.drawImage(CROP_IMG, sx, sy, side, side, 0, 0, 256, 256);
  } else {
    const s = Math.min(256/iw, 256/ih);
    const w = iw*s, h = ih*s;
    ctx.drawImage(CROP_IMG, (256-w)/2, (256-h)/2, w, h);
  }
}

el("cropClose").onclick = closeCrop;
document.querySelectorAll("input[name=cropFit]").forEach(r =>
  r.onchange = () => { updateCropOffState(); drawCrop(); });
el("cropOffset").oninput = drawCrop;
el("cropModal").addEventListener("click", (e) => { if (e.target === el("cropModal")) closeCrop(); });
el("cropSave").onclick = async () => {
  if (!CROP_GAME) return;
  const r = await api("crop_apply", CROP_GAME.key, cropFit(), +el("cropOffset").value, MODE);
  restyleCover(CROP_GAME.key, r.fit, r.offset);   // kształt robi CSS — bez ponownego pobierania
  closeCrop();
};
el("cropResetBtn").onclick = async () => {
  if (!CROP_GAME) return;
  const r = await api("crop_reset", CROP_GAME.key, MODE);
  restyleCover(CROP_GAME.key, r.fit, r.offset);
  closeCrop();
};
document.addEventListener("click", () => hideCtx());
document.addEventListener("scroll", () => hideCtx(), true);

// ── okno wyboru okładki ─────────────────────────────────────────────────────
let PK_GAME = null;     // gra, dla której otwarto wybór
let PK_TOK  = 0;        // token anulowania ładowania kandydatów

// zapamiętany rozmiar modala (localStorage) — dotyczy każdego wyskakującego okna
function restoreModalSize(){
  const box = document.querySelector("#picker .modal-box");
  if (!box) return;
  try{
    const s = JSON.parse(localStorage.getItem("pk_modal_size") || "null");
    if (s && s.w && s.h){ box.style.width = s.w + "px"; box.style.height = s.h + "px"; }
  }catch(_){}
  if (!box._ro){
    box._ro = new ResizeObserver(() => {
      try{ localStorage.setItem("pk_modal_size",
        JSON.stringify({w: box.offsetWidth, h: box.offsetHeight})); }catch(_){}
    });
    box._ro.observe(box);
  }
}

function openPicker(game){
  PK_GAME = game;
  restoreModalSize();
  el("pkTitle").textContent = (MODE === "steam" ? "Grafiki Steam: " : "Ikona: ") + game.name;
  el("pkSub").textContent = (MODE === "steam"
    ? "wybierz grid / tło / logo"
    : (game.source ? game.source.toUpperCase() : ""));
  el("pkQuery").value = "";
  hideSuggest();
  el("picker").hidden = false;
  pkLoad("");
  el("pkQuery").focus();
}
function closePicker(){ el("picker").hidden = true; PK_GAME = null; PK_TOK++; hideSuggest(); }

let PK_QUERY = "";      // ostatnie zapytanie (do przycisku Odśwież)
async function pkLoad(query){
  if (!PK_GAME) return;
  PK_QUERY = query || "";
  const tok = ++PK_TOK;
  const box = el("pkGrid");
  box.innerHTML = `<div class="pk-loading"><div class="spin"></div>Szukam grafik…</div>`;
  try{
    if (MODE === "steam"){
      const opt = await api("steam_art_options", PK_GAME.key, query || "");
      if (tok !== PK_TOK) return;
      renderSteamOptions(opt);
    }else{
      const r = await api("cover_candidates", PK_GAME.key, query || "", MODE);
      if (tok !== PK_TOK) return;
      renderCandidates(r);
    }
  }catch(e){
    if (tok === PK_TOK) box.innerHTML = `<div class="pk-empty">Błąd: ${e.message}</div>`;
  }
}

// Steam: trzy sekcje typów grafik (grid / hero-tło / logo), po jednym wyborze.
const STEAM_SECTIONS = [
  ["grid", "Okładka (grid)"],
  ["hero", "Tło (hero)"],
  ["logo", "Logo"],
];
function renderSteamOptions(opt){
  const box = el("pkGrid");
  box.classList.add("steam");
  if (!opt || opt.ok === false){
    box.innerHTML = `<div class="pk-empty">${(opt && opt.reason) || "Brak wyników."}</div>`;
    return;
  }
  box.innerHTML = "";
  const cur = opt.current || {};
  for (const [asset, label] of STEAM_SECTIONS){
    const list = opt[asset] || [];
    const sec = document.createElement("div");
    sec.className = "pk-sec";
    sec.innerHTML = `<div class="pk-sec-h">${label} <span class="muted">${list.length}</span></div>`;
    const row = document.createElement("div");
    row.className = "pk-row pk-row-" + asset;
    if (!list.length){
      row.innerHTML = `<div class="pk-empty" style="justify-content:flex-start">— brak —</div>`;
    }else{
      for (const v of list){
        const it = document.createElement("div");
        it.className = "pk-item";
        const dims = (v.w && v.h) ? `${v.w}×${v.h}` : "";
        it.title = `${dims} ${v.style||""}`.trim();
        if (cur[asset] && cur[asset] === v.url) it.classList.add("chosen");
        it.innerHTML = `<img src="${v.thumb}" alt="" onerror="this.closest('.pk-item').classList.add('broken')">${dims?`<div class="meta">${dims}</div>`:""}`;
        it.onclick = () => chooseSteamAsset(asset, v.url, it);
        row.appendChild(it);
      }
    }
    sec.appendChild(row);
    box.appendChild(sec);
  }
}

async function chooseSteamAsset(asset, url, itEl){
  if (!PK_GAME) return;
  const row = itEl.parentElement;
  row.querySelectorAll(".pk-item").forEach(n => n.classList.remove("chosen"));
  itEl.classList.add("busy");
  try{
    const r = await api("choose_steam_asset", PK_GAME.key, asset, url);
    itEl.classList.remove("busy");
    if (r.found){
      itEl.classList.add("chosen");
      // okładka (grid) jest tym, co widać w karcie — podmień
      if (asset === "grid"){
        const thumb = document.querySelector(`.card[data-key="${cssEsc(PK_GAME.key)}"] .thumb`);
        if (thumb){
          const old = thumb.querySelector("img"); if (old) old.remove();
          const img = document.createElement("img"); img.src = r.data_uri;
          thumb.insertBefore(img, thumb.querySelector(".ph"));
        }
      }
    }
  }catch(e){ itEl.classList.remove("busy"); }
}

function renderCandidates(r){
  const box = el("pkGrid");
  box.classList.remove("steam");
  const cs = (r && r.candidates) || [];
  if (!cs.length){
    box.innerHTML = `<div class="pk-empty">${(r && r.reason) || "Brak wyników."}</div>`;
    return;
  }
  box.innerHTML = "";
  for (const c of cs){
    const it = document.createElement("div");
    it.className = "pk-item" + (c.square ? " sq" : "");
    const dims = (c.w && c.h) ? `${c.w}×${c.h}` : "";
    it.title = `${c.kind||""} ${dims} ${c.style||""}`.replace(/\s+/g," ").trim();
    it.innerHTML =
      `<img src="${c.thumb}" alt="" onerror="this.closest('.pk-item').classList.add('broken')">
       <div class="meta"><span class="kind kind-${c.kind||""}">${c.kind||""}</span>${dims?" "+dims:""}</div>`;
    it.onclick = () => chooseCover(c.url);
    box.appendChild(it);
  }
}

async function chooseCover(url){
  if (!PK_GAME) return;
  const key = PK_GAME.key;
  const box = el("pkGrid");
  box.innerHTML = `<div class="pk-loading"><div class="spin"></div>Pobieram okładkę…</div>`;
  try{
    const r = await api("choose_cover", key, url, MODE);
    if (r.found){
      setCover(key, r.data_uri, r.fit, r.offset);   // kształt przez CSS
      closePicker();
    }else{
      box.innerHTML = `<div class="pk-empty">Nie udało się pobrać okładki.</div>`;
    }
  }catch(e){ box.innerHTML = `<div class="pk-empty">Błąd: ${e.message}</div>`; }
}

// ── podpowiedzi tytułów (autouzupełnianie SGDB) ──────────────────────────────
let SUG_TOK = 0;        // token anulowania podpowiedzi
let SUG_TIMER = null;   // debounce
let SUG_ACTIVE = -1;    // indeks podświetlonej podpowiedzi

function hideSuggest(){ el("pkSuggest").hidden = true; SUG_ACTIVE = -1; SUG_TOK++; }

async function loadSuggest(q){
  const tok = ++SUG_TOK;
  if (q.length < 2){ hideSuggest(); return; }
  try{
    const list = await api("sgdb_suggest", q);
    if (tok !== SUG_TOK) return;
    renderSuggest(list || []);
  }catch(_){ hideSuggest(); }
}

function renderSuggest(list){
  const box = el("pkSuggest");
  if (!list.length){ hideSuggest(); return; }
  SUG_ACTIVE = -1;
  box.innerHTML = "";
  list.forEach((s, i) => {
    const it = document.createElement("div");
    it.className = "pk-sug"; it.dataset.i = i;
    it.innerHTML = `<span>${s.name}</span>${s.year?`<span class="yr">${s.year}</span>`:""}`;
    it.onclick = () => pickSuggest(s);
    box.appendChild(it);
  });
  box.hidden = false;
}

function pickSuggest(s){
  el("pkQuery").value = s.name;
  hideSuggest();
  pkLoadById(s.id);
}

async function pkLoadById(id){
  if (!PK_GAME) return;
  const tok = ++PK_TOK;
  const box = el("pkGrid");
  box.innerHTML = `<div class="pk-loading"><div class="spin"></div>Szukam grafik…</div>`;
  try{
    if (MODE === "steam"){
      const opt = await api("steam_art_options", PK_GAME.key, String(id));
      if (tok !== PK_TOK) return;
      renderSteamOptions(opt);
    }else{
      const r = await api("cover_candidates_by_id", PK_GAME.key, id, MODE);
      if (tok !== PK_TOK) return;
      renderCandidates(r);
    }
  }catch(e){ if (tok===PK_TOK) box.innerHTML = `<div class="pk-empty">Błąd: ${e.message}</div>`; }
}

function moveSuggest(delta){
  const items = el("pkSuggest").querySelectorAll(".pk-sug");
  if (!items.length) return;
  SUG_ACTIVE = (SUG_ACTIVE + delta + items.length) % items.length;
  items.forEach((n,i) => n.classList.toggle("active", i === SUG_ACTIVE));
  items[SUG_ACTIVE].scrollIntoView({block:"nearest"});
}

el("pkClose").onclick = closePicker;
el("pkReload").onclick = () => { hideSuggest(); pkLoad(PK_QUERY); };
el("pkSearch").onclick = () => { hideSuggest(); pkLoad(el("pkQuery").value.trim()); };
el("pkQuery").addEventListener("input", (e) => {
  clearTimeout(SUG_TIMER);
  const q = e.target.value.trim();
  SUG_TIMER = setTimeout(() => loadSuggest(q), 220);
});
el("pkQuery").addEventListener("keydown", (e) => {
  const open = !el("pkSuggest").hidden;
  if (e.key === "ArrowDown" && open){ e.preventDefault(); moveSuggest(1); }
  else if (e.key === "ArrowUp" && open){ e.preventDefault(); moveSuggest(-1); }
  else if (e.key === "Enter"){
    const items = el("pkSuggest").querySelectorAll(".pk-sug");
    if (open && SUG_ACTIVE >= 0 && items[SUG_ACTIVE]){ items[SUG_ACTIVE].click(); }
    else { hideSuggest(); pkLoad(el("pkQuery").value.trim()); }
  }
  else if (e.key === "Escape" && open){ e.stopPropagation(); hideSuggest(); }
});
el("picker").addEventListener("click", (e) => {
  if (e.target === el("picker")) closePicker();   // klik w tło zamyka
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !el("picker").hidden && el("pkSuggest").hidden) closePicker();
});

// ── zdarzenia ──────────────────────────────────────────────────────────────
el("btnScan").onclick = runScan;
el("btnPing").onclick = async () => {
  try{ const r = await api("ping","hi"); setBridge(true,"OK (ping "+(r.echo||"")+")"); }
  catch(e){ setBridge(false, e.message); }
};
el("btnSettings").onclick = () => alert("Ustawienia dojdą w Fazie 2.");
el("btnExport").onclick = async () => {
  if (!CURRENT_VIEW) return;
  const what = CURRENT_VIEW.type === "rom"
    ? `ROM / ${CURRENT_VIEW.filter}`
    : (CURRENT_VIEW.filter ? "PC / " + CURRENT_VIEW.filter.toUpperCase() : "całej biblioteki PC");
  if (!confirm(`Utworzyć skróty (.lnk/.url) z ikonami dla ${what}?\nTrafią do LINKS/<platforma>/.`)) return;
  const b = el("btnExport"); const old = b.textContent;
  b.disabled = true; b.textContent = "⏳ Tworzę…";
  try{
    const r = CURRENT_VIEW.type === "rom"
      ? await api("export_rom", CURRENT_VIEW.filter)
      : await api("export_pc", CURRENT_VIEW.filter || "");
    if (r.ok){
      let msg = `Utworzono ${r.created}/${r.total} skrótów w:\n${r.links_dir}`;
      if (r.no_icon) msg += `\n(${r.no_icon} bez ikony — brak okładki)`;
      if (r.failed && r.failed.length) msg += `\nNieudane: ${r.failed.slice(0,8).join(", ")}`;
      if (!r.win32com) msg += `\nUWAGA: brak pywin32 — .lnk mogą się nie tworzyć.`;
      alert(msg);
      loadPlatforms();   // odśwież listę LINKS
    }else{
      alert("Eksport: " + (r.reason || "nie powiodło się"));
    }
  }catch(e){ alert("Błąd eksportu: " + e.message); }
  finally{ b.disabled = false; b.textContent = old; }
};
el("btnFit").onclick = async () => {
  FIT = (FIT === "crop") ? "pad" : "crop";
  applyFit();
  try{ await api("set_fit_mode", FIT); }catch(_){}
  if (PC_GAMES.length) renderPC();   // przepiecz miniatury (pad/crop robione w PIL)
};
el("cardSize").oninput = (e) => applyCardSize(+e.target.value);
el("cardSize").onchange = (e) => { try{ api("set_thumb_size", +e.target.value); }catch(_){} };
el("btnMode").onclick = () => {
  MODE = (MODE === "steam") ? "icon" : "steam";
  applyFit();
  // grafiki różnią się per tryb — przeładuj okładki widocznych gier
  if (PC_GAMES.length) renderPC();
};
el("filter").oninput = (e) => { FILTER = e.target.value.trim(); renderPC(); };

document.querySelectorAll(".src").forEach(b => b.onclick = () => {
  clearActive(); b.classList.add("active");
  CUR_SRC = b.dataset.src || "";
  el("filter").hidden = PC_GAMES.length === 0;
  renderPC();
});

// start
if (hasBridge()) init();
else window.addEventListener("pywebviewready", init);
setTimeout(() => { if (!hasBridge()) init(); }, 800);
