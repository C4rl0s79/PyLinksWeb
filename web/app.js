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
  const be = el("btnExport");
  if (be) be.textContent = MODE === "steam" ? "📦 Do Steam" : "📦 Utwórz skróty";
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
  el("selbar").hidden = true;
  el("gridTitle").textContent = "LINKS / " + name;
  const grid = el("grid");
  try{
    const games = await api("list_games", name);
    el("gridSub").textContent = `${games.length} skrótów`;
    grid.innerHTML = games.length ? "" : `<div class="welcome muted">Brak skrótów.</div>`;
    for (const g of games){
      const c = card(g.name, g.platform, (g.platform || "link").toLowerCase());
      if (g.icon){
        const thumb = c.querySelector(".thumb");
        const ph = thumb.querySelector(".ph"); if (ph) ph.style.display = "none";
        const img = document.createElement("img"); img.className = "cover";
        img.src = g.icon; img.style.objectFit = "contain"; img.style.objectPosition = "center";
        thumb.insertBefore(img, ph);
      }
      grid.appendChild(c);
    }
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
  await scanRomsUI();   // skan ROM-ów
  checkOrphans();       // monit o osieroconych skrótach
  // UWAGA: skan NIE dociąga kompletu grafik. Okładki widoczne w siatce doczytują
  // się leniwie (loadCovers) przy wejściu na platformę; komplet grid+hero+logo
  // pobiera się WYŁĄCZNIE na żądanie — „⬇ Pobierz wszystko" albo prawy klik na
  // grupie PC/ROM. Automat po każdym skanie bił po SGDB i puchł cache.
}

// ── zadania w tle (pobierz/stwórz wszystko) + pasek postępu ──────────────────
let PF_TIMER = null;
const SCOPE_LABEL = {all:"wszystko", pc:"PC", rom:"ROM-y"};

// uruchamia zadanie i pokazuje pasek; label = tekst wiodący. Zwraca reason||"".
async function startJob(method, args, label){
  let r;
  try{ r = await api(method, args.scope, args.icon, args.steam); }
  catch(e){ toast("Błąd: " + e.message); return "error"; }
  if (!r || !r.started){
    const reason = (r && r.reason) || "nie uruchomiono";
    toast("Nie uruchomiono: " + reason);
    return reason;
  }
  el("pfBar").hidden = false;
  el("pfFill").style.width = "0%";
  el("pfTxt").textContent = label + "…";
  PF_LABEL = label;
  if (PF_TIMER) clearInterval(PF_TIMER);
  PF_TIMER = setInterval(pollJob, 700);
  return "";
}
let PF_LABEL = "";
async function pollJob(){
  let s;
  try{ s = await api("job_status"); }catch(e){ return; }
  const pct = s.total ? Math.round(100 * s.done / s.total) : 0;
  el("pfFill").style.width = pct + "%";
  const ph = s.phase ? ` [${s.phase}]` : "";
  const cur = s.current ? " • " + s.current : "";
  el("pfTxt").textContent = `${PF_LABEL}${ph}: ${s.done}/${s.total}${cur}`;
  if (!s.running){
    clearInterval(PF_TIMER); PF_TIMER = null;
    finishJob(s);
  }
}
function finishJob(s){
  const res = s.result || {};
  let msg = `${PF_LABEL} — gotowe: ${s.done}/${s.total}`;
  if (res.kind === "download") msg += ` (nowe ${s.new}, w cache ${s.cached})`;
  if (res.kind === "create"){
    const l = res.links || {}, st = res.steam || {};
    const parts = [];
    if (res.links){
      const nf = Array.isArray(l.failed) ? l.failed.length : (l.failed || 0);
      parts.push(`skróty: ${l.created ?? "?"}` + (nf ? `, błędy ${nf}` : ""));
    }
    if (res.links_error) parts.push(`błąd skrótów: ${res.links_error}`);
    if (res.steam){
      if (st.ok === false) parts.push(`Steam: ${st.reason || "błąd"}`);
      else parts.push(`Steam: +${st.added ?? "?"} (grafiki ${st.art ?? 0})`);
    }
    if (res.steam_error) parts.push(`błąd Steam: ${res.steam_error}`);
    if (parts.length) msg += " — " + parts.join(", ");
    loadPlatforms();
  }
  el("pfTxt").textContent = msg;
  setTimeout(() => { el("pfBar").hidden = true; }, 3500);
}
function toast(t){ el("pfBar").hidden = false; el("pfTxt").textContent = t;
  el("pfFill").style.width = "0%"; setTimeout(()=>{ el("pfBar").hidden = true; }, 3000); }

// osierocone skróty (gra już nie istnieje) → propozycja usunięcia
async function checkOrphans(){
  try{
    const r = await api("find_orphans");
    const o = (r && r.orphans) || [];
    if (!o.length) return;
    const list = o.slice(0, 15).map(x => `  [${x.platform}] ${x.name}`).join("\n");
    const more = o.length > 15 ? `\n  …i ${o.length - 15} więcej` : "";
    if (confirm(`Znaleziono ${o.length} osieroconych skrótów (gra już nie istnieje):\n${list}${more}\n\nUsunąć je wraz z ikonami?`)){
      const rr = await api("delete_orphans", o.map(x => x.path));
      alert(`Usunięto ${rr.deleted} skrótów i ${rr.icons} ikon.`);
      loadPlatforms();
    }
  }catch(e){}
}

// ── ROM-y ────────────────────────────────────────────────────────────────
let ROM_GAMES = [];
let SPINE = {enabled:false, side:"left", frac:0.22};
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
  clearActive();
  if (!node) node = [...document.querySelectorAll("#romSystems .plat-item")]
    .find(b => b.querySelector("span") && b.querySelector("span").textContent === system);
  if (node) node.classList.add("active"); LOAD_TOK++;
  el("filter").hidden = true;
  el("gridTitle").textContent = "ROM / " + system;
  const games = ROM_GAMES.filter(g => g.system === system);
  el("gridSub").textContent = `${games.length} gier`;
  CURRENT_VIEW = {type: "rom", filter: system};
  el("btnExport").hidden = games.length === 0;
  el("selbar").hidden = games.length === 0;
  SELECTED = new Set(games.map(g => g.key));   // domyślnie wszystkie
  const grid = el("grid"); grid.innerHTML = "";
  for (const g of games){
    const c = card(g.name, g.system, "rom", g);
    // grzbiet tylko w trybie IKON (Steam = portret bez grzbietu)
    if (MODE === "icon" && SPINE.enabled && SYS_LOGO[g.system]) addSpine(c, SYS_LOGO[g.system], SPINE.side);
    grid.appendChild(c);
  }
  updateSelCount();
  loadCovers(games);
}

function addSpine(cardEl, logoUri, side){
  const thumb = cardEl.querySelector(".thumb");
  if (!thumb) return;
  thumb.classList.add(side === "right" ? "spine-right" : "spine-left");
  thumb.style.setProperty("--spine-w", Math.round((SPINE.frac||0.22)*100) + "%");
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
  el("selbar").hidden = games.length === 0;
  if (!games.length){ grid.innerHTML = `<div class="welcome muted">Brak gier.</div>`; return; }
  SELECTED = new Set(games.map(g => g.key));   // domyślnie wszystkie
  grid.innerHTML = "";
  for (const g of games) grid.appendChild(card(g.name, g.source, g.source, g));
  updateSelCount();
  loadCovers(games);   // leniwe doczytanie okładek (miniatury upieczone na kwadrat/portret w PIL)
}

function card(name, sub, src, game){
  const c = document.createElement("div");
  c.className = "card";
  const hasKey = !!(game && game.key);
  if (hasKey){
    c.dataset.key = game.key;
    if (SELECTED.has(game.key)) c.classList.add("sel");
    c.onclick = (e) => { if (!e.target.closest(".pick")) openPicker(game); };  // lewy klik = wybór ikony
    c.oncontextmenu = (e) => { e.preventDefault(); showCtx(e.clientX, e.clientY, game); };
  }
  c.title = (game && game.game_dir) ? game.game_dir : name;
  c.innerHTML =
    `<div class="thumb">${hasKey ? '<span class="pick" title="Zaznacz do eksportu">✓</span>' : ''}` +
    `<span class="src src-${src}">${src}</span><span class="ph">🎮</span></div>
     <div class="cap"><span class="t">${name}</span><span class="p">${sub}</span></div>`;
  if (hasKey){
    const pk = c.querySelector(".pick");
    if (pk) pk.onclick = (e) => { e.stopPropagation(); toggleSel(game.key, c); };
  }
  return c;
}

// ── zaznaczanie do eksportu ─────────────────────────────────────────────────
let SELECTED = new Set();
function toggleSel(key, cardEl){
  if (SELECTED.has(key)){ SELECTED.delete(key); cardEl.classList.remove("sel"); }
  else { SELECTED.add(key); cardEl.classList.add("sel"); }
  updateSelCount();
}
function selectKeys(keys){
  SELECTED = new Set(keys);
  document.querySelectorAll("#grid .card[data-key]").forEach(c =>
    c.classList.toggle("sel", SELECTED.has(c.dataset.key)));
  updateSelCount();
}
function currentKeys(){
  return [...document.querySelectorAll("#grid .card[data-key]")].map(c => c.dataset.key);
}
function updateSelCount(){
  el("selCount").textContent = `zaznaczono ${SELECTED.size}`;
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
  // karta z grzbietem: lekko poszerz do wypełnienia (fill) gdy proporcje bliskie
  // obszarowi (100%-22%); przy dużej różnicy (kwadrat) contain — bez ucinania.
  if (c.classList.contains("spine-left") || c.classList.contains("spine-right")){
    const decide = () => {
      const a = img.naturalWidth / img.naturalHeight;
      const area = 0.78;   // (100% − grzbiet 22%)
      img.style.objectFit = (Math.abs(a - area) / area <= 0.20) ? "fill" : "contain";
      img.style.objectPosition = "center";
    };
    if (img.complete && img.naturalWidth) decide(); else img.addEventListener("load", decide, {once:true});
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
  // „Wskaż .exe" tylko dla gier Extra/GOG (ich skrót celuje w plik)
  const exeBtn = m.querySelector('[data-act="exe"]');
  if (exeBtn) exeBtn.hidden = !(game.source === "extra" || game.source === "gog");
  m.hidden = false;
  // trzymaj w oknie
  const w = m.offsetWidth || 200, h = m.offsetHeight || 80;
  m.style.left = Math.min(x, innerWidth - w - 8) + "px";
  m.style.top  = Math.min(y, innerHeight - h - 8) + "px";
}
function hideCtx(){ el("ctxMenu").hidden = true; CTX_GAME = null; }

el("ctxMenu").addEventListener("click", (e) => {
  const act = e.target.dataset.act;
  const g = CTX_GAME;
  hideCtx();
  if (!g) return;
  if (act === "pick") openPicker(g);
  else if (act === "crop") openCropWindow(g);             // okno z przeciąganiem
  else if (act === "frame") setCropMode(g, "pad", 0.5);   // Ramki
  else if (act === "default") cropDefault(g);             // Domyślne
  else if (act === "exe") pickExe(g);                     // ręczny .exe
});

async function pickExe(game){
  try{
    const r = await api("pick_file", game.game_dir || "");
    if (r.path){ await api("set_exe_override", game.key, r.path);
      alert("Plik uruchamiający ustawiony:\n" + r.path); }
  }catch(e){}
}

// Ramki / ustawienie fit bez okna
async function setCropMode(game, fit, offset){
  try{
    const r = await api("crop_apply", game.key, fit, offset, MODE);
    restyleCover(game.key, r.fit, r.offset);
  }catch(e){}
}
async function cropDefault(game){
  try{
    const r = await api("crop_reset", game.key, MODE);
    restyleCover(game.key, r.fit, r.offset);
  }catch(e){}
}

// ── Kadruj w OSOBNYM oknie: chwyć grafikę i przeciągnij (grab-drag) ─────────
let CROP_W = null;
function openCropWindow(game){
  el("cropTitle").textContent = "Kadrowanie: " + game.name;
  el("cropModal").hidden = false;
  const img = el("cropImg");
  CROP_W = {key: game.key, offset: 0.5, portrait: true};
  api("crop_get", game.key, MODE).then(r => {
    CROP_W.offset = (r.offset != null ? +r.offset : 0.5);
    img.onload = () => {
      CROP_W.portrait = img.naturalHeight >= img.naturalWidth;
      applyCropPos();
    };
    img.src = r.raw || "";
    if (!r.raw){ el("cropImg").removeAttribute("src"); }
  }).catch(()=>{});
}
function closeCropWindow(){ el("cropModal").hidden = true; CROP_W = null; }
function applyCropPos(){
  const p = Math.round(Math.max(0, Math.min(1, CROP_W.offset)) * 100);
  el("cropImg").style.objectPosition = p + "% " + p + "%";
}

(function initCropDrag(){
  const frame = () => el("cropFrame");
  let dragging = false, startPos = 0, startOff = 0;
  const down = (e) => {
    if (!CROP_W) return;
    dragging = true; startOff = CROP_W.offset;
    startPos = CROP_W.portrait ? e.clientY : e.clientX;
    frame().classList.add("drag");
    e.preventDefault();
  };
  const move = (e) => {
    if (!dragging || !CROP_W) return;
    const rect = frame().getBoundingClientRect();
    const size = CROP_W.portrait ? rect.height : rect.width;
    const cur = CROP_W.portrait ? e.clientY : e.clientX;
    // GRAB: przeciągasz w dół → grafika w dół → widać górę → offset maleje
    CROP_W.offset = Math.max(0, Math.min(1, startOff - (cur - startPos) / size));
    applyCropPos();
  };
  const up = () => { dragging = false; const f = el("cropFrame"); if (f) f.classList.remove("drag"); };
  document.addEventListener("mousedown", (e) => { if (e.target.closest("#cropFrame")) down(e); });
  document.addEventListener("mousemove", move);
  document.addEventListener("mouseup", up);
})();

el("cropClose").onclick = closeCropWindow;
// zamyka modal klikiem w tło, ale TYLKO gdy mousedown też był na tle
// (żeby resize/przeciąganie kończące się poza oknem nie zamykało go)
function bindBackdropClose(id, closeFn){
  const m = el(id); if (!m) return;
  let downBg = false;
  m.addEventListener("mousedown", (e) => { downBg = (e.target === m); });
  m.addEventListener("click", (e) => { if (e.target === m && downBg) closeFn(); });
}
bindBackdropClose("cropModal", closeCropWindow);

// przeciąganie okna modala za nagłówek (transform translate — nie psuje resize);
// pozycja zapamiętywana w localStorage, z lekkim ograniczeniem do ekranu
function makeModalDraggable(modalId, storeKey){
  const modal = el(modalId); if (!modal) return;
  const box = modal.querySelector(".modal-box");
  const head = modal.querySelector(".modal-head");
  if (!box || !head) return;
  head.style.cursor = "move";
  let dx = 0, dy = 0;
  try{ const s = JSON.parse(localStorage.getItem(storeKey) || "null");
       if (s){ dx = s.dx||0; dy = s.dy||0; } }catch(_){}
  const clamp = () => {
    const mx = Math.max(120, innerWidth * 0.45), my = Math.max(120, innerHeight * 0.42);
    dx = Math.max(-mx, Math.min(mx, dx)); dy = Math.max(-my, Math.min(my, dy));
  };
  const apply = () => { clamp(); box.style.transform = `translate(${dx}px,${dy}px)`; };
  apply();
  let sx, sy, sdx, sdy, drag = false;
  head.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    if (e.target.closest("button,input,select,textarea,a,.pk-suggest")) return;
    drag = true; sx = e.clientX; sy = e.clientY; sdx = dx; sdy = dy;
    document.body.style.userSelect = "none"; e.preventDefault();
  });
  document.addEventListener("mousemove", (e) => {
    if (!drag) return;
    dx = sdx + (e.clientX - sx); dy = sdy + (e.clientY - sy); apply();
  });
  document.addEventListener("mouseup", () => {
    if (!drag) return;
    drag = false; document.body.style.userSelect = "";
    try{ localStorage.setItem(storeKey, JSON.stringify({dx, dy})); }catch(_){}
  });
}
["picker:pk_modal_pos", "cropModal:crop_modal_pos", "collModal:coll_modal_pos",
 "setModal:set_modal_pos", "logoModal:logo_modal_pos"].forEach(s => {
  const [id, key] = s.split(":"); makeModalDraggable(id, key);
});
el("cropSave").onclick = async () => {
  if (!CROP_W) return;
  try{
    const r = await api("crop_apply", CROP_W.key, "crop", CROP_W.offset, MODE);
    restyleCover(CROP_W.key, r.fit, r.offset);
  }catch(e){}
  closeCropWindow();
};
el("cropFrameBtn").onclick = async () => {
  if (!CROP_W) return;
  try{ const r = await api("crop_apply", CROP_W.key, "pad", 0.5, MODE);
       restyleCover(CROP_W.key, r.fit, r.offset); }catch(e){}
  closeCropWindow();
};
el("cropDefBtn").onclick = async () => {
  if (!CROP_W) return;
  try{ const r = await api("crop_reset", CROP_W.key, MODE);
       restyleCover(CROP_W.key, r.fit, r.offset); }catch(e){}
  closeCropWindow();
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
function closePicker(){ el("picker").hidden = true; PK_GAME = null; PK_TOK++; hideSuggest(); el("pkProgress").hidden = true; }

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
  renderSteamCurrent(box, opt.cached || {});
  pkProgReset(STEAM_SECTIONS.reduce((n,[a]) => n + ((opt[a]||[]).length), 0));
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
        it.appendChild(pkImg(v.thumb));
        if (dims){ const m=document.createElement("div"); m.className="meta"; m.textContent=dims; it.appendChild(m); }
        it.onclick = () => chooseSteamAsset(asset, v.url, it);
        row.appendChild(it);
      }
    }
    sec.appendChild(row);
    box.appendChild(sec);
  }
  // przewiń wybrany wariant do widoku w każdym wierszu
  box.querySelectorAll(".pk-row .pk-item.chosen").forEach(it => {
    try{ it.scrollIntoView({inline:"center", block:"nearest"}); }catch(_){}
  });
}

// pasek „Wybrane" — pokazuje aktualnie zapisane grafiki (grid/tło/logo) z cache
function renderSteamCurrent(box, cached){
  const strip = document.createElement("div");
  strip.className = "pk-current";
  strip.id = "pkCurrent";
  strip.innerHTML = `<div class="pk-cur-h">Wybrane grafiki (trafią do Steam)</div>`;
  const wrap = document.createElement("div"); wrap.className = "pk-cur-row";
  for (const [asset, label] of STEAM_SECTIONS){
    const slot = document.createElement("div");
    slot.className = "pk-cur-slot"; slot.dataset.slot = asset;
    const uri = cached[asset];
    slot.innerHTML = `<div class="pk-cur-lbl">${label}</div>`
      + (uri ? `<img class="pk-cur-img pk-cur-${asset}" src="${uri}" alt="">`
             : `<div class="pk-cur-none">— brak —</div>`);
    wrap.appendChild(slot);
  }
  strip.appendChild(wrap);
  box.appendChild(strip);
}
function updateSteamCurrent(asset, dataUri){
  const slot = document.querySelector(`#pkCurrent .pk-cur-slot[data-slot="${asset}"]`);
  if (!slot) return;
  const lbl = slot.querySelector(".pk-cur-lbl");
  slot.innerHTML = ""; if (lbl) slot.appendChild(lbl);
  if (dataUri){
    const img = document.createElement("img");
    img.className = "pk-cur-img pk-cur-" + asset; img.src = dataUri; img.alt = "";
    slot.appendChild(img);
  }else{
    const d = document.createElement("div"); d.className = "pk-cur-none";
    d.textContent = "— brak —"; slot.appendChild(d);
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
      updateSteamCurrent(asset, r.data_uri);   // odśwież pasek „Wybrane"
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

// ── pasek postępu ładowania miniatur ────────────────────────────────────────
let PK_PROG = {total: 0, loaded: 0};
function pkProgReset(total){
  PK_PROG = {total, loaded: 0};
  el("pkProgress").hidden = total <= 0;
  pkProgUpdate();
}
function pkProgUpdate(){
  const {total, loaded} = PK_PROG;
  el("pkBarFill").style.width = total ? Math.round(loaded / total * 100) + "%" : "0%";
  el("pkBarTxt").textContent = total ? `${loaded}/${total} grafik` : "";
  if (total && loaded >= total) setTimeout(() => { el("pkProgress").hidden = true; }, 500);
}
function pkImg(url){
  const img = document.createElement("img"); img.alt = "";
  img.onload = () => { PK_PROG.loaded++; pkProgUpdate(); };
  img.onerror = async () => {
    try{ const r = await api("fetch_thumb", url);   // fallback serwerowy
         if (r && r.data_uri){ img.onerror = null; img.src = r.data_uri; return; } }catch(_){}
    const it = img.closest(".pk-item"); if (it) it.classList.add("broken");
    PK_PROG.loaded++; pkProgUpdate();
  };
  img.src = url;
  return img;
}

function renderCandidates(r){
  const box = el("pkGrid");
  box.classList.remove("steam");
  const cs = (r && r.candidates) || [];
  if (!cs.length){
    pkProgReset(0);
    box.innerHTML = `<div class="pk-empty">${(r && r.reason) || "Brak wyników."}</div>`;
    return;
  }
  box.innerHTML = "";
  pkProgReset(cs.length);
  for (const c of cs){
    const it = document.createElement("div");
    it.className = "pk-item" + (c.square ? " sq" : "");
    const dims = (c.w && c.h) ? `${c.w}×${c.h}` : "";
    it.title = `${c.kind||""} ${dims} ${c.style||""}`.replace(/\s+/g," ").trim();
    it.appendChild(pkImg(c.thumb));
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.innerHTML = `<span class="kind kind-${c.kind||""}">${c.kind||""}</span>${dims?" "+dims:""}`;
    it.appendChild(meta);
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
bindBackdropClose("picker", closePicker);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !el("picker").hidden && el("pkSuggest").hidden) closePicker();
});

// ── zdarzenia ──────────────────────────────────────────────────────────────
el("btnScan").onclick = runScan;
el("pfStop").onclick = async () => {
  try{ await api("job_stop"); }catch(e){}
  el("pfTxt").textContent = "Zatrzymywanie…";
};

// ── Pobierz wszystko / Stwórz wszystko (globalnie lub per grupa) ──────────────
async function doGetAll(scope){
  await startJob("job_download", {scope, icon:true, steam:true},
                 `Pobieram grafiki (${SCOPE_LABEL[scope]||scope})`);
}
async function doMakeAll(scope){
  let running = false;
  try{ running = (await api("steam_running")).running; }catch(_){}
  let m = `Utworzyć WSZYSTKO dla: ${SCOPE_LABEL[scope]||scope}?\n`
        + `• skróty .lnk/.url w LINKS/\n`
        + `• wpisy w bibliotece Steam (grafiki + kolekcje per platforma)`;
  if (running) m += `\n\n⚠ Steam JEST URUCHOMIONY — zamknij go CAŁKOWICIE, inaczej `
                  + `zmiany w bibliotece Steam zostaną nadpisane.`;
  if (!confirm(m)) return;
  await startJob("job_create", {scope, icon:true, steam:true},
                 `Tworzę wszystko (${SCOPE_LABEL[scope]||scope})`);
}
el("btnGetAll").onclick  = () => doGetAll("all");
el("btnMakeAll").onclick = () => doMakeAll("all");

// prawy klik na grupie PC/ROM → menu z tymi samymi opcjami dla danej grupy
let GRP_SCOPE = "all";
document.querySelector(".sidebar").addEventListener("contextmenu", (e) => {
  const grp = e.target.closest(".side-group[data-scope]");
  if (!grp) return;
  e.preventDefault();
  GRP_SCOPE = grp.dataset.scope;
  const m = el("grpMenu");
  el("grpMenuHead").textContent = GRP_SCOPE === "pc" ? "Grupa: PC" : "Grupa: ROM-y";
  m.hidden = false;
  const w = m.offsetWidth || 240, h = m.offsetHeight || 90;
  m.style.left = Math.min(e.clientX, innerWidth - w - 8) + "px";
  m.style.top  = Math.min(e.clientY, innerHeight - h - 8) + "px";
});
el("grpMenu").addEventListener("click", (e) => {
  const b = e.target.closest("[data-gact]"); if (!b) return;
  el("grpMenu").hidden = true;
  if (b.dataset.gact === "get") doGetAll(GRP_SCOPE);
  else doMakeAll(GRP_SCOPE);
});
document.addEventListener("click", (e) => {
  if (!e.target.closest("#grpMenu")) el("grpMenu").hidden = true;
});
el("btnPing").onclick = async () => {
  try{ const r = await api("ping","hi"); setBridge(true,"OK (ping "+(r.echo||"")+")"); }
  catch(e){ setBridge(false, e.message); }
};
// ── Ustawienia ──────────────────────────────────────────────────────────────
const sv = (id) => el(id).value.trim();
const sc = (id) => el(id).checked;
async function openSettings(){
  let cfg;
  try{ cfg = await api("get_config"); }catch(e){ alert("Brak mostu."); return; }
  const ak = cfg.api_keys || {}, es = cfg.extra_sources || {}, rs = cfg.rom_support || {};
  el("s_usewebapi").checked = !!cfg.use_steam_web_api;
  el("s_steamexe").value = cfg.steam_exe || "";
  el("s_extradir").value = cfg.extra_dir || "";
  renderExtraDirs(cfg.extra_dirs_list || []);
  el("s_epic").checked = cfg.scan_epic !== false;
  el("s_gog").checked = cfg.scan_gog !== false;
  el("s_owned").checked = !!cfg.steam_include_owned;
  el("s_libretro").checked = (cfg.extra_sources || {}).libretro !== false;
  el("s_spine").checked = !!cfg.icon_platform_spine;
  el("s_spineside").value = cfg.icon_spine_side || "left";
  el("s_logodir").value = cfg.platform_logo_dir || "";
  el("s_steamcdn").checked = es.steam_cdn !== false;
  el("s_igdb").checked = !!es.igdb;
  el("s_tgdb").checked = !!es.tgdb;
  el("s_romen").checked = !!rs.enabled;
  el("s_baserom").value = rs.base_rom_dir || "";
  el("s_baseemu").value = rs.base_emu_dir || "";
  romTblRender(rs.systems || []);
  // sekrety zamaskowane — placeholder informuje, że są zapisane
  const sset = cfg._secrets_set || {};
  const ph = (id, set) => { el(id).value = ""; el(id).placeholder = set ? "●●●●●● zapisany — wpisz, by zmienić" : "(brak)"; };
  ph("s_sgdb", sset.sgdb_key); ph("s_steamkey", sset.steam_api_key);
  ph("s_steamid", sset.steam_id64); ph("s_igdbid", sset.igdb_client_id);
  ph("s_igdbsec", sset.igdb_client_secret); ph("s_tgdbkey", sset.tgdb_key);
  el("s_secnote").textContent = "🔒 szyfrowane lokalnie";
  api("security_status").then(s => {
    const b = s.backend === "tpm" ? "TPM (sprzętowy)" : (s.backend === "dpapi" ? "Windows DPAPI" : "brak");
    let t = "";
    if (s.tpm_present != null) t = ` • TPM: ${s.tpm_present ? (s.tpm_ready ? "gotowy" : "obecny") : "brak"}`;
    el("s_secnote").textContent = `🔒 szyfrowane: ${b}${t}`;
  }).catch(()=>{});
  el("setMsg").textContent = "";
  el("setModal").hidden = false;
}
function closeSettings(){ el("setModal").hidden = true; }

// ── dodatkowe foldery Extra: lista z przyciskiem wyboru folderu ──────────────
function renderExtraDirs(dirs){
  const box = el("s_extradirsList"); box.innerHTML = "";
  for (const d of dirs){
    if (!d) continue;
    const row = document.createElement("div"); row.className = "dir-item";
    const span = document.createElement("span"); span.className = "dir-path";
    span.textContent = d; span.title = d;
    const del = document.createElement("button");
    del.className = "dir-del"; del.type = "button"; del.textContent = "✕";
    del.title = "Usuń"; del.onclick = () => row.remove();
    row.appendChild(span); row.appendChild(del); box.appendChild(row);
  }
}
function collectExtraDirs(){
  return [...el("s_extradirsList").querySelectorAll(".dir-path")]
    .map(s => s.textContent.trim()).filter(Boolean);
}

function romRow(s){
  const tr = document.createElement("tr");
  const esc = (v) => (v||'').replace(/"/g,'&quot;');
  const cell = (val, cls="") => `<td class="${cls}"><input type="text" value="${esc(val)}"></td>`;
  tr.innerHTML =
    cell(s.name) + cell(s.rom_dir) +
    `<td class="emu"><input type="text" value="${esc(s.emulator)}">` +
      `<button class="btn ghost mini emu-pick" title="Wybierz emulator/rdzeń">⚙</button>` +
      `<button class="btn ghost mini emu-file" title="Wskaż .exe">📁</button></td>` +
    cell(s.primary_ext) + cell(s.launch_args) +
    `<td class="sub"><input type="checkbox" ${s.roms_in_subdirs?'checked':''}></td>` +
    `<td><button class="del" title="Usuń">✕</button></td>`;
  tr.querySelector(".del").onclick = () => tr.remove();
  tr.querySelector(".emu-pick").onclick = (e) => emuPickOpen(e.currentTarget, tr);
  tr.querySelector(".emu-file").onclick = async () => {
    const r = await api("pick_file", sv("s_baseemu")); if (r.path) romRowEmu(tr, r.path, null);
  };
  return tr;
}

function romRowInputs(tr){ return tr.querySelectorAll("input[type=text]"); }
function romRowEmu(tr, exe, args){
  const inp = romRowInputs(tr);
  inp[2].value = exe;                 // emulator (kol. 3)
  if (args != null) inp[4].value = args;   // args (kol. 5)
}

// popup wyboru emulatora/rdzenia dla wiersza
async function emuPickOpen(btn, tr){
  const sys = romRowInputs(tr)[0].value.trim();
  const base = sv("s_baseemu");
  const pop = el("emuPop");
  pop.innerHTML = `<div class="muted" style="padding:6px 10px">Szukam…</div>`;
  const r = btn.getBoundingClientRect();
  pop.style.left = Math.min(r.left, innerWidth-240) + "px";
  pop.style.top = Math.min(r.bottom+4, innerHeight-290) + "px";
  pop.hidden = false;
  let opts = [];
  try{ opts = await api("scan_emulators", sys, base); }catch(e){}
  if (!opts.length){ pop.innerHTML = `<div class="muted" style="padding:6px 10px">Brak w folderze emulatorów.</div>`; return; }
  pop.innerHTML = "";
  for (const o of opts){
    const b = document.createElement("button");
    if (o.type === "retroarch") b.className = "ra";
    b.textContent = o.label;
    b.onclick = () => { romRowEmu(tr, o.exe, o.args); pop.hidden = true; };
    pop.appendChild(b);
  }
}
function romTblRender(systems){
  const b = el("romTblBody"); b.innerHTML = "";
  for (const s of systems) b.appendChild(romRow(s));
}
function romTblCollect(){
  const out = [];
  for (const tr of el("romTblBody").querySelectorAll("tr")){
    const inp = tr.querySelectorAll("input[type=text]");
    const name = inp[0].value.trim();
    if (!name) continue;
    out.push({
      name, rom_dir: inp[1].value.trim(), emulator: inp[2].value.trim(),
      primary_ext: inp[3].value.trim() || "m3u,cue,iso,chd,bin",
      launch_args: inp[4].value.trim(),
      roms_in_subdirs: tr.querySelector("input[type=checkbox]").checked,
    });
  }
  return out;
}

// ── Kolekcje serii ──────────────────────────────────────────────────────────
let COLL = {}, COLL_GAMES = [], COLL_CUR = null;
async function openColl(){
  try{ const r = await api("collections_data"); COLL = r.collections || {}; COLL_GAMES = r.games || []; }
  catch(e){ alert("Brak mostu."); return; }
  COLL_CUR = Object.keys(COLL)[0] || null;
  renderCollList(); selectColl(COLL_CUR);
  el("collMsg").textContent = "";
  el("collModal").hidden = false;
}
function closeColl(){ el("collModal").hidden = true; }
function saveColl(){ try{ api("save_collections", COLL); }catch(e){} }

function renderCollList(){
  const box = el("collList"); box.innerHTML = "";
  for (const name of Object.keys(COLL)){
    const b = document.createElement("button");
    if (name === COLL_CUR) b.classList.add("active");
    b.innerHTML = `<span>${name}</span><span class="n">${(COLL[name]||[]).length}</span>`;
    b.onclick = () => { COLL_CUR = name; renderCollList(); selectColl(name); };
    box.appendChild(b);
  }
  if (!Object.keys(COLL).length) box.innerHTML = `<div class="muted pad">brak kolekcji</div>`;
}
function selectColl(name){
  el("collTitle").textContent = name ? "Kolekcja: " + name : "Wybierz kolekcję po lewej";
  renderMembers(); renderPool();
}
function renderMembers(){
  const box = el("collMembers"); box.innerHTML = "";
  const mem = (COLL_CUR && COLL[COLL_CUR]) || [];
  el("collMemCount").textContent = COLL_CUR ? `(${mem.length})` : "";
  if (!COLL_CUR){ return; }
  for (const m of mem){
    const it = document.createElement("div");
    it.className = "coll-item";
    it.innerHTML = `<span>${m.name}</span><span class="plat">${m.platform}</span><span class="x">✕</span>`;
    it.querySelector(".x").onclick = () => {
      COLL[COLL_CUR] = mem.filter(x => !(x.name===m.name && x.platform===m.platform));
      saveColl(); renderCollList(); renderMembers(); renderPool();
    };
    box.appendChild(it);
  }
}
function renderPool(){
  const box = el("collPool"); box.innerHTML = "";
  if (!COLL_CUR) return;
  const q = el("collFilter").value.trim().toLowerCase();
  const have = new Set(((COLL[COLL_CUR]||[]).map(m => m.platform+"|"+m.name)));
  for (const g of COLL_GAMES){
    if (have.has(g.platform+"|"+g.name)) continue;
    if (q && !g.name.toLowerCase().includes(q)) continue;
    const it = document.createElement("div");
    it.className = "coll-item";
    it.innerHTML = `<span>${g.name}</span><span class="plat">${g.platform}</span>`;
    it.onclick = () => {
      (COLL[COLL_CUR] = COLL[COLL_CUR] || []).push({name:g.name, platform:g.platform});
      saveColl(); renderCollList(); renderMembers(); renderPool();
    };
    box.appendChild(it);
  }
}
el("btnColl").onclick = openColl;
el("collClose").onclick = closeColl;
bindBackdropClose("collModal", closeColl);
el("collFilter").oninput = renderPool;
el("collAdd").onclick = () => {
  const n = el("collNew").value.trim(); if (!n) return;
  if (!COLL[n]) COLL[n] = [];
  el("collNew").value = ""; COLL_CUR = n; saveColl(); renderCollList(); selectColl(n);
};
el("collDel").onclick = () => {
  if (!COLL_CUR) return;
  if (!confirm(`Usunąć kolekcję „${COLL_CUR}"? (folder na dysku zostaje — usuń ręcznie)`)) return;
  delete COLL[COLL_CUR]; COLL_CUR = Object.keys(COLL)[0] || null;
  saveColl(); renderCollList(); selectColl(COLL_CUR);
};
el("collGenAll").onclick = async () => {
  el("collMsg").textContent = "Tworzę foldery…";
  try{
    const r = await api("generate_all_collections");
    let m = `Utworzono ${r.made} skrótów w ${r.count} kolekcjach (LINKS/Kolekcje/).`;
    if (r.missing && r.missing.length) m += `\nBrakujące: ${r.missing.slice(0,6).join(", ")}`;
    el("collMsg").textContent = "Gotowe ✓"; alert(m); loadPlatforms();
  }catch(e){ el("collMsg").textContent = "Błąd: " + e.message; }
};

el("btnSettings").onclick = openSettings;
el("setClose").onclick = closeSettings;
el("setCancel").onclick = closeSettings;
bindBackdropClose("setModal", closeSettings);
el("s_detect").onclick = async () => {
  try{ const r = await api("detect_steam"); if (r.steam_exe) el("s_steamexe").value = r.steam_exe;
       else el("setMsg").textContent = "Nie wykryto Steam.exe"; }catch(e){}
};
el("s_testsgdb").onclick = async () => {
  el("s_sgdbres").textContent = "…";
  try{ const r = await api("test_sgdb"); el("s_sgdbres").textContent = r.ok ? "✓ klucz OK" : ("✗ " + (r.reason||"błąd")); }
  catch(e){ el("s_sgdbres").textContent = "✗ błąd"; }
};
el("s_romadd").onclick = () => el("romTblBody").appendChild(romRow({primary_ext:"m3u,cue,iso,chd,bin"}));
el("s_extradd").onclick = async () => {
  try{
    const r = await api("pick_folder", "");
    if (r && r.path){
      const cur = collectExtraDirs();
      if (!cur.includes(r.path)){ cur.push(r.path); renderExtraDirs(cur); }
    }
  }catch(e){ el("setMsg").textContent = "Błąd wyboru folderu: " + e.message; }
};
// przyciski wyboru folderu / pliku (📁)
el("setBody").addEventListener("click", async (e) => {
  const fb = e.target.closest("[data-folder]"), fl = e.target.closest("[data-file]");
  if (fb){ e.preventDefault(); const r = await api("pick_folder", el(fb.dataset.folder).value);
           if (r.path) el(fb.dataset.folder).value = r.path; }
  else if (fl){ e.preventDefault(); const r = await api("pick_file", el(fl.dataset.file).value);
                if (r.path) el(fl.dataset.file).value = r.path; }
});
el("s_romauto").onclick = async () => {
  const br = sv("s_baserom"), be = sv("s_baseemu");
  if (!br){ el("s_romautores").textContent = "Podaj wspólny folder ROM"; return; }
  el("s_romautores").textContent = "Skanuję…";
  try{
    const r = await api("rom_autodetect", br, be);
    romTblRender(r.systems || []);
    el("s_romautores").textContent = `Wykryto ${(r.systems||[]).length} systemów`;
  }catch(e){ el("s_romautores").textContent = "Błąd: " + e.message; }
};
document.addEventListener("click", (e) => {
  if (!e.target.closest("#emuPop") && !e.target.closest(".emu-pick")) el("emuPop").hidden = true;
});
el("setSave").onclick = async () => {
  const patch = {
    api_keys: {sgdb_key: sv("s_sgdb"), steam_api_key: sv("s_steamkey"), steam_id64: sv("s_steamid")},
    use_steam_web_api: sc("s_usewebapi"),
    steam_exe: sv("s_steamexe"), extra_dir: sv("s_extradir"),
    extra_dirs_list: collectExtraDirs(),
    scan_epic: sc("s_epic"), scan_gog: sc("s_gog"),
    steam_include_owned: sc("s_owned"),
    icon_platform_spine: sc("s_spine"), icon_spine_side: el("s_spineside").value,
    platform_logo_dir: sv("s_logodir"),
    extra_sources: {steam_cdn: sc("s_steamcdn"), libretro: sc("s_libretro"),
      igdb: sc("s_igdb"),
      igdb_client_id: sv("s_igdbid"), igdb_client_secret: sv("s_igdbsec"),
      tgdb: sc("s_tgdb"), tgdb_key: sv("s_tgdbkey")},
    rom_support: {enabled: sc("s_romen"), base_rom_dir: sv("s_baserom"),
      base_emu_dir: sv("s_baseemu"), systems: romTblCollect()},
  };
  el("setMsg").textContent = "Zapisuję…";
  try{
    const r = await api("save_config", patch);
    if (r.ok){ el("setMsg").textContent = "Zapisano ✓"; setTimeout(closeSettings, 500); }
    else el("setMsg").textContent = "Błąd: " + (r.reason||"");
  }catch(e){ el("setMsg").textContent = "Błąd: " + e.message; }
};
// ── Edytor logo grzbietu (wariant + rozmiar %) ──────────────────────────────
let LG = { platform: "", style: "", scale: 85, frac: 22, chosen: "" };
let _lgPrevTimer = null;

async function openLogoEditor(){
  let info;
  try{ info = await api("spine_logo_info"); }catch(e){ alert("Brak mostu."); return; }
  LG.style = info.style || "Light - Color";
  LG.scale = Math.round((info.scale || 0.85) * 100);
  LG.frac = Math.round((info.frac || 0.22) * 100);
  // kolorystyki (styl); oznacz te z pobranym cache
  const cached = new Set(info.cached_styles || []);
  const st = el("lgStyle"); st.innerHTML = "";
  for (const [label, val] of Object.entries(info.styles || {})){
    const o = document.createElement("option");
    o.value = val; o.textContent = label + (cached.has(val) ? " ✓" : " (brak cache)");
    if (val === LG.style) o.selected = true;
    st.appendChild(o);
  }
  // platformy
  const pl = el("lgPlat"); pl.innerHTML = "";
  for (const p of (info.platforms || [])){
    const o = document.createElement("option");
    o.value = p.key; o.textContent = p.label;
    pl.appendChild(o);
  }
  LG.platform = (info.platforms && info.platforms[0]) ? info.platforms[0].key : "";
  el("lgScale").value = LG.scale;
  el("lgScaleVal").textContent = LG.scale + "%";
  el("lgFrac").value = LG.frac;
  el("lgFracVal").textContent = LG.frac + "%";
  el("lgMsg").textContent = ""; el("lgDlMsg").textContent = "";
  el("logoModal").hidden = false;
  await refreshVariants();
  refreshPreview();
}
function closeLogoEditor(){ el("logoModal").hidden = true; }

async function refreshVariants(){
  const grid = el("lgVarGrid"); grid.innerHTML = "<div class='muted'>Ładuję…</div>";
  let vs = [];
  try{ vs = await api("spine_logo_variants", LG.platform, LG.style); }catch(e){}
  el("lgVarCount").textContent = vs.length;
  grid.innerHTML = "";
  if (!vs.length){
    grid.innerHTML = "<div class='muted'>Brak wariantów w cache dla tej kolorystyki. Użyj „Pobierz warianty”.</div>";
    return;
  }
  for (const v of vs){
    const d = document.createElement("div");
    d.className = "logo-var"; d.title = v.name;
    d.innerHTML = `<img src="${v.thumb}" alt="">`;
    d.onclick = async () => {
      grid.querySelectorAll(".logo-var").forEach(x => x.classList.remove("sel"));
      d.classList.add("sel"); LG.chosen = v.source;
      el("lgMsg").textContent = "Instaluję…";
      try{
        const r = await api("set_platform_logo", LG.platform, v.source);
        el("lgMsg").textContent = r.ok ? "Wariant ustawiony ✓" : "Błąd instalacji";
        refreshPreview();
      }catch(e){ el("lgMsg").textContent = "Błąd: " + e.message; }
    };
    grid.appendChild(d);
  }
}

function refreshPreview(){
  if (_lgPrevTimer) clearTimeout(_lgPrevTimer);
  _lgPrevTimer = setTimeout(async () => {
    try{
      const r = await api("spine_preview", LG.platform, LG.scale / 100, LG.frac / 100);
      if (r.ok) el("lgPreview").src = r.preview;
    }catch(e){}
  }, 60);
}

el("s_logoedit").onclick = openLogoEditor;
el("logoClose").onclick = closeLogoEditor;
bindBackdropClose("logoModal", closeLogoEditor);
el("lgPlat").onchange = async () => { LG.platform = el("lgPlat").value; await refreshVariants(); refreshPreview(); };
el("lgStyle").onchange = async () => {
  LG.style = el("lgStyle").value;
  try{ await api("set_spine_logo_style", LG.style); }catch(e){}
  await refreshVariants();
};
el("lgScale").oninput = () => {
  LG.scale = parseInt(el("lgScale").value, 10);
  el("lgScaleVal").textContent = LG.scale + "%";
  refreshPreview();
};
el("lgFrac").oninput = () => {
  LG.frac = parseInt(el("lgFrac").value, 10);
  el("lgFracVal").textContent = LG.frac + "%";
  refreshPreview();
};
el("lgDownload").onclick = async () => {
  el("lgDlMsg").textContent = "Pobieram (to może potrwać)…";
  el("lgDownload").disabled = true;
  try{
    const r = await api("download_logo_variants", LG.style);
    el("lgDlMsg").textContent = r.total ? `Pobrano ${r.downloaded}/${r.total} ✓` : "Nic nie pobrano";
    await refreshVariants();
    // odśwież ✓ przy kolorystyce
    const info = await api("spine_logo_info");
    const cached = new Set(info.cached_styles || []);
    for (const o of el("lgStyle").options){
      const base = o.textContent.replace(/ ✓| \(brak cache\)$/,"");
      o.textContent = base + (cached.has(o.value) ? " ✓" : " (brak cache)");
    }
  }catch(e){ el("lgDlMsg").textContent = "Błąd: " + e.message; }
  finally{ el("lgDownload").disabled = false; }
};
el("lgSave").onclick = async () => {
  el("lgMsg").textContent = "Zapisuję…";
  try{
    await api("set_spine_frac", LG.frac / 100);
    await api("set_spine_logo_scale", LG.scale / 100);
    SPINE.frac = LG.frac / 100;
    el("lgMsg").textContent = "Zapisano ✓";
    setTimeout(closeLogoEditor, 400);
    // odśwież widok ROM, by kafelki dostały nową szerokość grzbietu
    if (CURRENT_VIEW && CURRENT_VIEW.type === "rom") showRoms(CURRENT_VIEW.filter);
  }catch(e){ el("lgMsg").textContent = "Błąd: " + e.message; }
};

el("selAll").onclick = () => selectKeys(currentKeys());
el("selNone").onclick = () => selectKeys([]);
el("selMissing").onclick = async () => {
  if (!CURRENT_VIEW) return;
  try{
    const keys = await api("missing_keys", CURRENT_VIEW.type,
                           CURRENT_VIEW.filter || "");
    // zaznacz tylko brakujące, które są w bieżącym widoku
    const inView = new Set(currentKeys());
    selectKeys((keys || []).filter(k => inView.has(k)));
  }catch(e){ alert("Błąd: " + e.message); }
};

el("btnExport").onclick = async () => {
  if (!CURRENT_VIEW) return;
  if (SELECTED.size === 0){ alert("Nie zaznaczono żadnej gry."); return; }
  const b = el("btnExport"); const old = b.textContent;
  if (MODE === "steam"){
    // ostrzeż, gdy Steam działa (nadpisze shortcuts.vdf przy zamknięciu)
    let running = false;
    try{ running = (await api("steam_running")).running; }catch(_){}
    let m = `Dodać ${SELECTED.size} gier do biblioteki Steam?\n(grafiki grid/hero/logo + kolekcje per platforma)`;
    if (running) m += `\n\n⚠ Steam JEST URUCHOMIONY — zamknij go CAŁKOWICIE, inaczej zmiany zostaną nadpisane.`;
    if (!confirm(m)) return;
    b.disabled = true; b.textContent = "⏳ Do Steam…";
    try{
      const r = await api("export_steam", [...SELECTED]);
      if (r.ok){
        let msg = `Dodano ${r.added} gier do Steam (grafik: ${r.art}).\nProfil: ${r.config_dir}`;
        const c = r.collections || {};
        if (c.created && c.created.length) msg += `\nKolekcje: +${c.created.join(", ")}`;
        if (c.updated && c.updated.length) msg += `\nZaktualizowane: ${c.updated.join(", ")}`;
        if (c.error) msg += `\nKolekcje: ${c.error}`;
        if (r.steam_running) msg += `\n\n⚠ Steam był uruchomiony — uruchom go ponownie, by zobaczyć zmiany (mogły zostać nadpisane, jeśli był otwarty).`;
        alert(msg);
      }else alert("Steam: " + (r.reason || "nie powiodło się"));
    }catch(e){ alert("Błąd eksportu Steam: " + e.message); }
    finally{ b.disabled = false; b.textContent = old; }
    return;
  }
  if (!confirm(`Utworzyć skróty (.lnk/.url) z ikonami dla ${SELECTED.size} zaznaczonych gier?\nTrafią do LINKS/<platforma>/.`)) return;
  b.disabled = true; b.textContent = "⏳ Tworzę…";
  try{
    const r = await api("export_keys", [...SELECTED]);
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
  // grafiki i grzbiety różnią się per tryb — przeładuj bieżący widok
  if (CURRENT_VIEW && CURRENT_VIEW.type === "rom") showRoms(CURRENT_VIEW.filter);
  else if (PC_GAMES.length) renderPC();
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
