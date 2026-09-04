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

let LINK_GAMES = [];      // skróty bieżącej platformy (do naprawy ikon)
async function showLinks(name, node){
  clearActive();
  if (!node) node = [...document.querySelectorAll("#platforms .plat-item")]
    .find(b => b.textContent.trim().startsWith(name));
  if (node) node.classList.add("active");
  LOAD_TOK++;                       // anuluj ładowanie okładek PC
  el("filter").hidden = true;
  el("btnExport").hidden = true;    // LINKS = gotowe skróty, nie eksportujemy
  el("selbar").hidden = false;      // pasek trzyma „Napraw ikony"
  el("btnFixIcons").hidden = false;
  el("gridTitle").textContent = "LINKS / " + name;
  CURRENT_VIEW = {type: "link", filter: name};
  const grid = el("grid");
  try{
    const games = await api("list_games", name);
    LINK_GAMES = games;
    // Bez tego widok dziedziczył zaznaczenie z poprzedniej platformy — a operacje
    // tutaj ruszają PLIKI skrótów, więc wybór ma być świadomy.
    SELECTED = new Set();
    updateSelCount();
    el("gridSub").textContent = `${games.length} skrótów`;
    grid.innerHTML = games.length ? "" : `<div class="welcome muted">Brak skrótów.</div>`;
    for (const g of games){
      // przekazujemy obiekt gry — bez niego card() nie podpinał ŻADNEGO kliknięcia
      // i kafelki w tym widoku były martwe
      const c = card(g.name, g.platform, (g.platform || "link").toLowerCase(),
                     g.key ? g : null);
      if (!g.key) c.title = "Nie rozpoznano gry — uruchom skan, by móc zmienić ikonę";
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
  scanMameUI();         // biblioteka arcade (jeśli włączona)
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

// ── grupy (kolekcje) w bibliotece Steam ─────────────────────────────────────
// Kolekcje rosną same: usunięcie gry z dysku nie rusza ani wpisu Steam, ani jego
// przynależności do kolekcji. Stąd potrzeba osobnego zarządzania.
let SG = [];
async function openSteamGroups(){
  el("sgModal").hidden = false;
  el("sgMsg").textContent = "Wczytuję…";
  try{
    const r = await api("steam_groups");
    if (!r.ok){ el("sgMsg").textContent = "✗ " + (r.reason || "błąd"); return; }
    SG = r.groups || [];
    el("sgInfo").textContent =
      `${SG.length} grup • ${r.entries} pozycji w bibliotece`
      + (r.steam_running ? " • ⚠ Steam URUCHOMIONY" : "");
    el("sgMsg").textContent = "";
    renderSteamGroups();
  }catch(e){ el("sgMsg").textContent = "✗ " + e.message; }
}
function renderSteamGroups(){
  const box = el("sgList"); box.innerHTML = "";
  for (const g of SG){
    const row = document.createElement("label");
    row.className = "cat-row";
    const uwagi = [];
    if (g.dead) uwagi.push(`<span class="mat">${g.dead} bez pliku</span>`);
    if (g.stale) uwagi.push(`<span class="mat">${g.stale} martwych</span>`);
    if (g.steam_games) uwagi.push(`<span class="n">${g.steam_games} gier Steam</span>`);
    row.innerHTML = `<input type="checkbox" data-g="${g.name}">`
      + `<span>${g.name}</span><span class="n">${g.total} poz.</span>`
      + (g.ours ? `<span class="n">nasza</span>` : "")
      + uwagi.join("");
    box.appendChild(row);
  }
}
el("btnSteamGroups").onclick = openSteamGroups;
el("sgClose").onclick = () => { el("sgModal").hidden = true; };
bindBackdropClose("sgModal", () => { el("sgModal").hidden = true; });

el("sgClean").onclick = async () => {
  el("sgMsg").textContent = "Sprawdzam…";
  let r;
  try{ r = await api("steam_clean_collections", true); }
  catch(e){ el("sgMsg").textContent = "✗ " + e.message; return; }
  if (!r.ok){ el("sgMsg").textContent = "✗ " + (r.reason || "błąd"); return; }
  if (!r.total){ el("sgMsg").textContent = "✓ nic do sprzątania"; return; }
  if (r.steam_running){
    el("sgMsg").textContent = `${r.total} martwych — ZAMKNIJ STEAM, żeby usunąć`;
    return;
  }
  const lista = (r.groups || []).map(g => `  ${g.name}: -${g.remove}`).join("\n");
  if (!confirm(`Usunąć ${r.total} martwych numerów z kolekcji?\n\n${lista}\n\nZwykłe gry ze Steama zostają nietknięte.`)) return;
  try{
    const d = await api("steam_clean_collections", false);
    el("sgMsg").textContent = d.ok ? `✓ usunięto ${d.total}` : "✗ " + (d.reason || "błąd");
    openSteamGroups();
  }catch(e){ el("sgMsg").textContent = "✗ " + e.message; }
};

el("sgDel").onclick = async () => {
  const zazn = [...el("sgList").querySelectorAll("input[data-g]:checked")].map(i => i.dataset.g);
  if (!zazn.length){ el("sgMsg").textContent = "Zaznacz grupy do usunięcia."; return; }
  const drop = el("sgDrop").checked;
  let r;
  try{ r = await api("steam_remove_groups", zazn, drop, true); }
  catch(e){ el("sgMsg").textContent = "✗ " + e.message; return; }
  if (!r.ok){ el("sgMsg").textContent = "✗ " + (r.reason || "błąd"); return; }
  if (r.steam_running){
    el("sgMsg").textContent = "ZAMKNIJ STEAM — inaczej nadpisze zmiany przy wyjściu";
    return;
  }
  const przyklad = (r.names || []).slice(0, 10).map(n => "  " + n).join("\n");
  let m = `Usunąć ${zazn.length} grup: ${zazn.join(", ")}?`;
  m += drop ? `\n\nZniknie też ${r.entries} pozycji z biblioteki:\n${przyklad}`
            : `\n\nSame kolekcje — gry zostają w bibliotece.`;
  if (!confirm(m)) return;
  try{
    const d = await api("steam_remove_groups", zazn, drop, false);
    el("sgMsg").textContent = d.ok
      ? `✓ usunięto ${(d.deleted||[]).length} grup i ${d.removed_entries} pozycji`
      : "✗ " + (d.reason || "błąd");
    openSteamGroups();
  }catch(e){ el("sgMsg").textContent = "✗ " + e.message; }
};

// wysyłka kolekcji z LINKS do biblioteki Steam
el("collSteam").onclick = async () => {
  const nazwy = Object.keys(COLL || {});
  if (!nazwy.length){ el("collMsg").textContent = "Brak kolekcji."; return; }
  if (!confirm(`Wysłać ${nazwy.length} kolekcji do biblioteki Steam?\n(${nazwy.join(", ")})\n\nGry muszą już być w bibliotece Steam.`)) return;
  el("collMsg").textContent = "Wysyłam…";
  try{
    const r = await api("steam_push_collections", nazwy);
    if (!r.ok){ el("collMsg").textContent = "✗ " + (r.reason || r.error || "błąd"); return; }
    const c = (r.created || []).length, u = (r.updated || []).length;
    let m = `✓ utworzone ${c}, zaktualizowane ${u}`;
    const brak = Object.entries(r.missing || {});
    if (brak.length) m += ` • pominięto ${brak.reduce((a,[,v])=>a+v.length,0)} gier spoza Steam`;
    el("collMsg").textContent = m;
  }catch(e){ el("collMsg").textContent = "✗ " + e.message; }
};

// osierocone skróty (gra już nie istnieje) → propozycja usunięcia
async function checkOrphans(){
  try{
    const r = await api("find_orphans");
    const o = (r && r.orphans) || [];
    const st = (r && r.steam) || [];
    if (!o.length) return;
    const list = o.slice(0, 15).map(x => `  [${x.platform}] ${x.name}`).join("\n");
    const more = o.length > 15 ? `\n  …i ${o.length - 15} więcej` : "";
    // Wpis w bibliotece Steam nie znika razem ze skrótem — pytamy o oba naraz,
    // inaczej po usunięciu gry z dysku zostaje tam martwa pozycja.
    const czySteam = st.length
      ? `\n\nW bibliotece Steam odpowiada im ${st.length} pozycji — usunę je również.`
      : "";
    if (!confirm(`Znaleziono ${o.length} osieroconych skrótów (gra już nie istnieje):\n${list}${more}${czySteam}\n\nUsunąć je wraz z ikonami?`)) return;
    const rr = await api("delete_orphans", o.map(x => x.path));
    let msg = `Usunięto ${rr.deleted} skrótów i ${rr.icons} ikon.`;
    if (st.length){
      try{
        const rs = await api("delete_steam_entries", st.map(x => x.appid));
        msg += rs.ok ? `\nZ biblioteki Steam: ${rs.removed} pozycji.`
                     : `\nSteam: ${rs.reason}`;
      }catch(e){ msg += `\nSteam: ${e.message}`; }
    }
    alert(msg);
    loadPlatforms();
  }catch(e){ console.warn("duchy:", e.message); }
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
  el("btnFixIcons").hidden = true;
  clearActive();
  if (!node) node = [...document.querySelectorAll("#romSystems .plat-item")]
    .find(b => b.querySelector("span") && b.querySelector("span").textContent === system);
  if (node) node.classList.add("active"); LOAD_TOK++;
  el("filter").hidden = false;
  el("gridTitle").textContent = "ROM / " + system;
  const games = applyFilter(ROM_GAMES.filter(g => g.system === system));
  el("gridSub").textContent = `${games.length} gier` + (FILTER ? " (filtr)" : "");
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

// Filtr nazwy działa w KAŻDYM widoku (PC / ROM / arcade), nie tylko na liście PC.
function applyFilter(games){
  if (!FILTER) return games;
  const q = FILTER.toLowerCase();
  return games.filter(g => (g.name || "").toLowerCase().includes(q));
}
// Przerysowanie bieżącego widoku — zachowuje podświetlony wpis w panelu bocznym,
// bo show*() bez węzła zgubiłoby zaznaczenie przy każdym wciśniętym klawiszu.
function refreshView(){
  const act = document.querySelector(".plat-item.active");
  const v = CURRENT_VIEW || {type: "pc"};
  if (v.type === "rom") showRoms(v.filter, act);
  else if (v.type === "mame") showMame(v.filter, act);
  else renderPC();
}

function renderPC(){
  el("btnFixIcons").hidden = true;
  const grid = el("grid");
  let games = CUR_SRC ? PC_GAMES.filter(g => g.source === CUR_SRC) : PC_GAMES;
  games = applyFilter(games);
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

// Etykiety wersji w liście: nazwa zestawu („avspa") nic nie mówi, a pełny tytuł
// się nie mieści. Odcinamy nazwę GRY (tytuł rodzica bez końcowego nawiasu), więc
// zostaje dokładnie to, co odróżnia wersje: „(Europe 940520)" / „(Asia 940520)".
// Wariant o innym tytule („Link (Korean bootleg…)" pod „Tetris") zachowuje całość
// — dlatego prefiks bierzemy z rodzica, a nie ze wspólnego początku wszystkich
// wariantów: jedna odmienna nazwa zerowałaby go dla całej listy.
function variantLabels(vars, parentTitle){
  let pre = String(parentTitle || "");
  let prev = null;
  while (prev !== pre){                      // zdejmij końcowe nawiasy
    prev = pre;
    pre = pre.replace(/\s*[\(\[][^()\[\]]*[\)\]]\s*$/, "").trim();
  }
  return vars.map(v => {
    const t = v.title || v.name;
    if (pre.length >= 3 && t.startsWith(pre)){
      const rest = t.slice(pre.length).replace(/^[\s-]+/, "").trim();
      if (rest) return rest;
    }
    return t;
  });
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
  // Arcade z kilkoma wersjami: zamiast statycznego podpisu — lista wyboru,
  // żeby od razu było widać, KTÓRA wersja jest wybrana (i dało się ją zmienić).
  const vars = (game && game.variants) || [];
  const multi = vars.length > 1;
  if (multi){
    // w interfejsie pokazujemy opis WYBRANEJ wersji — inaczej nie dałoby się
    // odróżnić „(Europe)" od „(Japan)"; plik skrótu dostanie samą nazwę gry
    const cur = vars.find(v => v.name === (game.chosen || game.set));
    if (cur) name = cur.title;
  }
  const labels = multi ? variantLabels(vars, game.name) : [];
  const subHtml = multi
    ? `<select class="ver-sel" title="Wersja gry">` +
      vars.map((v, i) => `<option value="${v.name}" title="${v.title} [${v.name}]"${
        v.name === (game.chosen || game.set) ? " selected" : ""
      }>${labels[i]}</option>`).join("") + `</select>`
    : `<span class="p">${sub}</span>`;
  c.innerHTML =
    `<div class="thumb">${hasKey ? '<span class="pick" title="Zaznacz do eksportu">✓</span>' : ''}` +
    `<span class="src src-${src}">${src}</span><span class="ph">🎮</span></div>
     <div class="cap"><span class="t" title="${name}">${name}</span>${subHtml}</div>`;
  if (hasKey){
    const pk = c.querySelector(".pick");
    if (pk) pk.onclick = (e) => { e.stopPropagation(); toggleSel(game.key, c); };
  }
  if (multi){
    const sel = c.querySelector(".ver-sel");
    // klik w listę nie może otwierać okna wyboru grafiki
    sel.onclick = (e) => e.stopPropagation();
    sel.onchange = async (e) => {
      e.stopPropagation();
      game.chosen = sel.value;
      sel.title = "Wersja: " + sel.value;
      const v = vars.find(x => x.name === sel.value);
      const t = c.querySelector(".cap .t");
      if (v && t){ t.textContent = v.title; t.title = v.title; }
      try{ await api("mame_set_version", game.key, sel.value); }catch(_){}
    };
  }
  return c;
}

// ── zaznaczanie do eksportu ─────────────────────────────────────────────────
let SELECTED = new Set();
// czy podawanie miniatur po HTTP działa (diagnostyka trafia do konsoli)
let IMG_HTTP_OK = true;
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
  // Arcade renderuje się porcjami po 300 kafelków, więc odczyt z DOM objąłby tylko
  // widoczny kawałek. Bierzemy komplet z danych — z uwzględnieniem filtra nazwy.
  if (CURRENT_VIEW && CURRENT_VIEW.type === "mame"){
    const f = CURRENT_VIEW.filter;
    const g = f ? MAME_GAMES.filter(x => {
      const c = x.cat || "(bez kategorii)";
      return f.includes(" / ") ? (c + " / " + (x.sub || "")) === f : c === f;
    }) : MAME_GAMES;
    return applyFilter(g).map(x => x.key);
  }
  return [...document.querySelectorAll("#grid .card[data-key]")].map(c => c.dataset.key);
}
function updateSelCount(){
  el("selCount").textContent = `zaznaczono ${SELECTED.size}`;
  // W widoku LINKS przycisk mówi wprost, czego dotknie — inaczej trzeba by
  // zgadywać, czy zadziała na zaznaczeniu, czy na całej platformie.
  const b = el("btnFixIcons");
  if (b && !b.hidden){
    const n = LINK_GAMES.filter(g => SELECTED.has(g.key)).length;
    b.textContent = n ? `🖼 Napraw ikony (${n})` : "🖼 Napraw ikony (wszystkie)";
  }
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

function setCoverOne(thumb, src, fit, offset){
  thumb.classList.remove("loading");
  if (!src) return;
  const old = thumb.querySelector("img.cover"); if (old) old.remove();
  const ph = thumb.querySelector(".ph"); if (ph) ph.style.display = "none";
  const img = document.createElement("img");
  img.className = "cover"; img.alt = "";
  img.loading = "lazy"; img.decoding = "async";
  styleCover(img, fit, offset); img.src = src;
  thumb.insertBefore(img, ph);
}

function setCover(key, src, fit, offset){
  // WSZYSTKIE kafelki o tym kluczu, nie pierwszy z brzegu: gdy ta sama gra trafi
  // do siatki dwa razy, pojedyncze trafienie zostawiało drugi kafelek pusty.
  const wszystkie = document.querySelectorAll(`.card[data-key="${cssEsc(key)}"] .thumb`);
  if (wszystkie.length > 1) for (let i = 1; i < wszystkie.length; i++)
    setCoverOne(wszystkie[i], src, fit, offset);
  const c = wszystkie[0];
  if (!c) return;
  c.classList.remove("loading");
  if (!src) return;
  const old = c.querySelector("img.cover"); if (old) old.remove();  // NIE ruszaj logo grzbietu
  const ph = c.querySelector(".ph"); if (ph) ph.style.display = "none";  // schowaj 🎮 pod okładką
  const img = document.createElement("img");
  img.className = "cover";
  img.alt = "";
  // `loading="lazy"` + `decoding="async"`: przeglądarka pobiera i dekoduje obrazy
  // sama, równolegle i poza wątkiem rysowania — czego most nigdy nie umiał.
  img.loading = "lazy"; img.decoding = "async";
  // Strona jest wstrzykiwana bez własnego adresu, więc gdyby silnik zablokował
  // pobieranie z pętli lokalnej, wracamy do starej drogi (obraz przez most).
  // Raz na obraz — inaczej błąd samego zapasu zapętliłby się.
  if (String(src).startsWith("http")){
    img.onerror = () => {
      img.onerror = null;
      IMG_HTTP_OK = false;
      api("cover_cached", key, MODE)
        .then(r => { if (r && r.data_uri) img.src = r.data_uri; })
        .catch(() => {});
    };
  }
  styleCover(img, fit, offset); img.src = src;
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
      if (!g.key) continue;
      const r = batch[g.key];
      if (r) setCover(g.key, r.url || r.uri, r.fit, r.offset);
      else if (r === false) setCover(g.key, null);   // wiadomo, że nic nie ma
      else missing.push(g);                          // dopiero to warto pobierać
    }
  }catch(e){ return; }
  // 2) sieć dla brakujących — JEDNO wywołanie, po stronie Pythona równolegle.
  //    Pętla po jednym kluczu dawała ~0,5 s na tytuł: porcja 300 kafelków
  //    schodziła 2,5 minuty i wyglądało to jak zawieszone ładowanie.
  if (!missing.length) return;
  for (const g of missing){
    const c = document.querySelector(`.card[data-key="${cssEsc(g.key)}"] .thumb`);
    if (c) c.classList.add("loading");
  }
  // Porcjami po 24: jedno wywołanie na 300 kafelków blokowało most na kilkadziesiąt
  // sekund i okno przestawało odpowiadać. Mniejsze paczki oddają sterowanie
  // przeglądarce, pokazują postęp i pozwalają przerwać przełączeniem widoku.
  const STEP = 24;
  let done = 0;
  for (let i = 0; i < missing.length; i += STEP){
    if (tok !== LOAD_TOK) return;
    const part = missing.slice(i, i + STEP);
    try{
      const got = await api("covers_fetch_batch", part.map(g => g.key), MODE);
      if (tok !== LOAD_TOK) return;
      for (const g of part){
        const r = got && got[g.key];
        setCover(g.key, r ? (r.url || r.uri) : null, r && r.fit, r && r.offset);
      }
    }catch(e){
      for (const g of part){
        const c = document.querySelector(`.card[data-key="${cssEsc(g.key)}"] .thumb`);
        if (c) c.classList.remove("loading");
      }
    }
    done += part.length;
    setBusy(`Pobieram okładki… ${done}/${missing.length}`);
    await new Promise(r => setTimeout(r, 0));      // oddaj klatkę na przerysowanie
  }
  setBusy("");
}

function clearActive(){
  document.querySelectorAll(".side-item,.plat-item").forEach(n => n.classList.remove("active"));
}


// ── biblioteka arcade (MAME) ────────────────────────────────────────────────
// Gatunki zachowują się jak systemy ROM: klik = filtr siatki. Tytuły są już
// pogrupowane po stronie Pythona (jedna kafelka = jeden tytuł, warianty w środku).
let MAME_GAMES = [];
let MAME_GENRE = "";

// ── kategorie arcade (catver.ini) ───────────────────────────────────────────
// catver.ini opisuje sety jako „Kategoria / Podkategoria" i sam oznacza treści
// dla dorosłych. Dwupoziomowy wybór zastępuje płaską listę gatunków: 57 kategorii
// i setki podkategorii to za dużo na panel boczny, stąd osobne okno.
let CAT_TREE = {};      // {kategoria: {titles, subs:{podkat: n}, mature}}
let CAT_SKIP = new Set();

async function openCats(){
  el("catModal").hidden = false;
  el("catMsg").textContent = "Wczytuję…";
  try{
    const r = await api("mame_categories");
    CAT_TREE = r.tree || {};
    CAT_SKIP = new Set(r.skip || []);
    el("catCount").textContent =
      `${Object.keys(CAT_TREE).length} kategorii • ${r.total} tytułów`;
    el("catMsg").textContent = "";
    renderCatTree();
  }catch(e){ el("catMsg").textContent = "✗ " + e.message; }
}
function catOn(key){ return !CAT_SKIP.has(key); }
function renderCatTree(){
  const q = (el("catFilter").value || "").trim().toLowerCase();
  const box = el("catTree"); box.innerHTML = "";
  const cats = Object.keys(CAT_TREE).sort(
    (a, b) => CAT_TREE[b].titles - CAT_TREE[a].titles);
  for (const cat of cats){
    const e = CAT_TREE[cat];
    const subs = Object.keys(e.subs).sort((a, b) => e.subs[b] - e.subs[a]);
    const pasuje = !q || cat.toLowerCase().includes(q)
                      || subs.some(x => x.toLowerCase().includes(q));
    if (!pasuje) continue;
    const grp = document.createElement("div");
    grp.className = "cat-grp";
    const top = document.createElement("div");
    top.className = "cat-row top";
    const mat = e.mature ? `<span class="mat">${e.mature} mature</span>` : "";
    top.innerHTML = `<input type="checkbox" data-cat="${cat}"${catOn(cat) ? " checked" : ""}>`
      + `<span>${cat}</span><span class="n">${e.titles}</span>${mat}`
      + (subs.length ? `<button class="cat-toggle">${subs.length} podkat. ▾</button>` : "");
    grp.appendChild(top);
    if (subs.length){
      const wrap = document.createElement("div");
      wrap.className = "cat-subs";
      wrap.hidden = !q;                       // szukanie rozwija od razu
      for (const sub of subs){
        if (q && !cat.toLowerCase().includes(q) && !sub.toLowerCase().includes(q)) continue;
        const key = cat + " / " + sub;
        const row = document.createElement("label");
        row.className = "cat-row";
        row.innerHTML = `<input type="checkbox" data-cat="${key}"${catOn(key) ? " checked" : ""}>`
          + `<span>${sub}</span><span class="n">${e.subs[sub]}</span>`;
        wrap.appendChild(row);
      }
      grp.appendChild(wrap);
      const btn = top.querySelector(".cat-toggle");
      if (btn) btn.onclick = () => {
        wrap.hidden = !wrap.hidden;
        btn.textContent = `${subs.length} podkat. ` + (wrap.hidden ? "▾" : "▴");
      };
    }
    box.appendChild(grp);
  }
  box.querySelectorAll("input[data-cat]").forEach(i => {
    i.onchange = () => {
      const key = i.dataset.cat;
      if (i.checked) CAT_SKIP.delete(key); else CAT_SKIP.add(key);
      // Odznaczenie całej kategorii wygasza jej podkategorie — inaczej dawałyby
      // złudzenie, że coś jeszcze przejdzie.
      if (!key.includes(" / ")){
        const wrap = i.closest(".cat-grp").querySelector(".cat-subs");
        if (wrap) wrap.querySelectorAll("input").forEach(x => {
          x.disabled = !i.checked;
          x.parentElement.style.opacity = i.checked ? "" : ".45";
        });
      }
    };
    if (!i.dataset.cat.includes(" / ")) i.onchange();
  });
}
el("btnCats").onclick = openCats;
el("catClose").onclick = () => { el("catModal").hidden = true; };
bindBackdropClose("catModal", () => { el("catModal").hidden = true; });
el("catFilter").oninput = () => renderCatTree();
el("catAll").onclick = () => { CAT_SKIP = new Set(); renderCatTree(); };
el("catNone").onclick = () => {
  CAT_SKIP = new Set(Object.keys(CAT_TREE));
  renderCatTree();
};
el("catSave").onclick = async () => {
  el("catMsg").textContent = "Zapisuję…";
  try{
    await api("mame_set_categories", [...CAT_SKIP]);
    el("catMsg").textContent = "✓ zapisano";
    el("catModal").hidden = true;
    scanMameUI();
  }catch(e){ el("catMsg").textContent = "✗ " + e.message; }
};

async function scanMameUI(){
  let st;
  try{ st = await api("mame_status"); }catch(e){ return; }
  const grp = el("mameGroup");
  if (!st.enabled || !st.lib_present){ grp.hidden = true; return; }
  grp.hidden = false;
  try{ MAME_GAMES = await api("mame_library"); }catch(e){ MAME_GAMES = []; }
  el("mameTotal").textContent = MAME_GAMES.length ? `(${MAME_GAMES.length})` : "";
  const box = el("mameGenres"); box.innerHTML = "";
  // Panel boczny idzie za catver.ini: kategoria, a pod nią jej podkategorie.
  // Rozwijamy dopiero po kliknięciu — 57 kategorii z setkami podkategorii naraz
  // byłoby nie do przejrzenia.
  const drzewo = {};
  for (const g of MAME_GAMES){
    const c = g.cat || "(bez kategorii)";
    const e = drzewo[c] || (drzewo[c] = {n: 0, subs: {}});
    e.n++;
    if (g.sub) e.subs[g.sub] = (e.subs[g.sub] || 0) + 1;
  }
  const mk = (label, cat, sub, n, klasa) => {
    const b = document.createElement("button");
    b.className = "plat-item" + (klasa ? " " + klasa : "");
    b.innerHTML = `<span>${label}</span><span class="count">${n}</span>`;
    b.onclick = () => showMame(sub ? cat + " / " + sub : cat, b);
    return b;
  };
  box.appendChild(mk("Wszystkie", "", "", MAME_GAMES.length));
  for (const [cat, e] of Object.entries(drzewo).sort((a, b) => b[1].n - a[1].n)){
    const top = mk(cat, cat, "", e.n);
    box.appendChild(top);
    const subs = Object.keys(e.subs).sort((a, b) => e.subs[b] - e.subs[a]);
    if (!subs.length) continue;
    const wrap = document.createElement("div");
    wrap.hidden = true;
    for (const sub of subs) wrap.appendChild(mk(sub, cat, sub, e.subs[sub], "sub"));
    box.appendChild(wrap);
    const strzalka = document.createElement("span");
    strzalka.className = "cat-toggle";
    strzalka.textContent = "▾";
    strzalka.onclick = (ev) => {
      ev.stopPropagation();               // rozwinięcie to NIE wybór kategorii
      wrap.hidden = !wrap.hidden;
      strzalka.textContent = wrap.hidden ? "▾" : "▴";
    };
    top.insertBefore(strzalka, top.firstChild);
  }
}

function showMame(genre, node){
  el("btnFixIcons").hidden = true;
  clearActive(); LOAD_TOK++;
  if (node) node.classList.add("active");
  // Wejście w INNY gatunek zaczyna wybór od nowa, ale samo filtrowanie nazwą
  // go zachowuje: przepływ „znajdź → zaznacz → wyczyść filtr → znajdź kolejną"
  // gubił dotąd wszystko przy każdym naciśnięciu klawisza.
  const sameView = (CURRENT_VIEW && CURRENT_VIEW.type === "mame"
                    && CURRENT_VIEW.filter === (genre || ""));
  MAME_GENRE = genre || "";
  const games = applyFilter(MAME_GENRE ? MAME_GAMES.filter(g => {
        const c = g.cat || "(bez kategorii)";
        return MAME_GENRE.includes(" / ")
          ? (c + " / " + (g.sub || "")) === MAME_GENRE
          : c === MAME_GENRE;
      }) : MAME_GAMES);
  el("gridTitle").textContent = "Arcade / " + (MAME_GENRE || "Wszystkie");
  el("gridSub").textContent = `${games.length} tytułów` + (FILTER ? " (filtr)" : "");
  el("filter").hidden = false;
  CURRENT_VIEW = {type: "mame", filter: MAME_GENRE};
  el("btnExport").hidden = games.length === 0;
  el("selbar").hidden = games.length === 0;
  // arcade: NIE zaznaczamy wszystkiego z automatu — eksport kopiuje pliki na dysk,
  // a tytułów bywa kilka tysięcy. Wybór jest świadomy.
  if (!sameView) SELECTED = new Set();
  updateSelCount();
  const grid = el("grid"); grid.innerHTML = "";
  // 4 tys. kafelków naraz zabiłoby renderowanie — pokazujemy porcjami
  renderMameChunk(games, 0);
}

const MAME_CHUNK = 300;
function renderMameChunk(games, from){
  const grid = el("grid");
  const slice = games.slice(from, from + MAME_CHUNK);
  for (const g of slice){
    const c = card(g.name, `MAME · ${g.set}`, "rom", g);
    grid.appendChild(c);
  }
  const old = grid.querySelector(".mame-more"); if (old) old.remove();
  if (from + MAME_CHUNK < games.length){
    const more = document.createElement("div");
    more.className = "mame-more welcome";
    more.innerHTML = `<button class="btn ghost">➕ Pokaż kolejne ${
      Math.min(MAME_CHUNK, games.length - from - MAME_CHUNK)} (z ${games.length})</button>`;
    more.querySelector("button").onclick = () => renderMameChunk(games, from + MAME_CHUNK);
    grid.appendChild(more);
  }
  loadCovers(slice);
}

// ── wybór wersji zestawu ────────────────────────────────────────────────────
let VER_GAME = null;
function openVersions(game){
  VER_GAME = game;
  el("verTitle").textContent = "Wersja: " + game.name;
  el("verMsg").textContent = "";
  const box = el("verList"); box.innerHTML = "";
  for (const v of (game.variants || [])){
    const row = document.createElement("div");
    row.className = "ver-row" + (v.name === (game.chosen || game.set) ? " sel" : "");
    row.dataset.set = v.name;
    row.innerHTML = `<span class="v-title" style="flex:1;color:var(--fg);font-size:13px">${v.title}</span>`
                  + `<span class="v-set" style="min-width:0;color:var(--fg2);font-size:11px">${v.name}</span>`
                  + `<span class="v-stat">—</span>`;
    row.onclick = () => {
      box.querySelectorAll(".ver-row").forEach(r => r.classList.remove("sel"));
      row.classList.add("sel");
      game.chosen = v.name;
    };
    box.appendChild(row);
  }
  el("verModal").hidden = false;
}

// eksport wybranej wersji: złożenie zestawu → weryfikacja → skrót
el("verExport").onclick = async () => {
  if (!VER_GAME) return;
  const sel = el("verList").querySelector(".ver-row.sel");
  const set = sel ? sel.dataset.set : (VER_GAME.chosen || VER_GAME.set);
  if (!set){ el("verMsg").textContent = "Zaznacz wersję na liście."; return; }
  el("verMsg").textContent = "Eksportuję " + set + "…";
  let r;
  try{ r = await api("mame_export", [set], true); }
  catch(e){ el("verMsg").textContent = "Błąd: " + e.message; return; }
  if (!r || !r.started){
    el("verMsg").textContent = "Nie uruchomiono: " + ((r && r.reason) || "?");
    return;
  }
  el("pfBar").hidden = false; el("pfFill").style.width = "0%";
  PF_LABEL = "Eksport arcade";
  if (PF_TIMER) clearInterval(PF_TIMER);
  PF_TIMER = setInterval(async () => {
    let st;
    try{ st = await api("job_status"); }catch(_){ return; }
    const pct = st.total ? Math.round(100 * st.done / st.total) : 0;
    el("pfFill").style.width = pct + "%";
    el("pfTxt").textContent = `${PF_LABEL}: ${st.done}/${st.total}` +
                              (st.phase ? ` — ${st.phase}` : "");
    if (st.running) return;
    clearInterval(PF_TIMER); PF_TIMER = null;
    const res = st.result || {};
    const bad = (res.failed || [])[0];
    el("verMsg").textContent = res.ok
      ? `✓ ${set} gotowy do gry — skrót utworzony`
      : `✗ ${bad ? bad.reason : "nie udało się"}`;
    el("pfTxt").textContent = res.ok ? `${PF_LABEL} — gotowe` : `${PF_LABEL} — błąd`;
    setTimeout(() => { el("pfBar").hidden = true; }, 2500);
    loadPlatforms();
  }, 600);
};
el("verClose").onclick = () => { el("verModal").hidden = true; VER_GAME = null; };
bindBackdropClose("verModal", () => { el("verModal").hidden = true; VER_GAME = null; });

// „Sprawdź kompletność" — MAME sam mówi, czy zestaw da się uruchomić
el("verCheck").onclick = async () => {
  if (!VER_GAME) return;
  const sets = (VER_GAME.variants || []).map(v => v.name);
  el("verMsg").textContent = "Sprawdzam…";
  let res;
  try{ res = await api("mame_verify", sets); }
  catch(e){ el("verMsg").textContent = "Błąd: " + e.message; return; }
  let ok = 0;
  for (const row of el("verList").querySelectorAll(".ver-row")){
    const st = res[row.dataset.set];
    const cell = row.querySelector(".v-stat");
    if (st === "ok"){ cell.textContent = "uruchomi się"; cell.className = "v-stat ok"; ok++; }
    else if (st === "bad"){ cell.textContent = "niekompletny"; cell.className = "v-stat bad"; }
    else { cell.textContent = "brak w bibliotece"; cell.className = "v-stat"; }
  }
  el("verMsg").textContent = `${ok} z ${sets.length} gotowych do uruchomienia`;
};

// ── menu kontekstowe (prawy klik) ────────────────────────────────────────────
let CTX_GAME = null;
function showCtx(x, y, game){
  CTX_GAME = game;
  const m = el("ctxMenu");
  // „Wskaż .exe" tylko dla gier Extra/GOG (ich skrót celuje w plik)
  const exeBtn = m.querySelector('[data-act="exe"]');
  if (exeBtn) exeBtn.hidden = !(game.source === "extra" || game.source === "gog");
  const verBtn = m.querySelector('[data-act="ver"]');
  if (verBtn) verBtn.hidden = !(game.variants && game.variants.length > 1);
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
  else if (act === "ver") openVersions(g);                // wersja zestawu MAME
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
// W widoku LINKS kafelek pokazuje IKONĘ WYCIĄGNIĘTĄ ZE SKRÓTU, a nie źródłową
// okładkę — samo przestylowanie (object-fit) nic tam nie zmienia, bo .ico jest już
// upieczony. Po każdej zmianie kadru trzeba przepiec ikonę i zapisać ją w pliku.
async function linksReapplyIcon(key, file){
  if (!(CURRENT_VIEW && CURRENT_VIEW.type === "link") || !key || !file) return false;
  setBusy("Przepiekam ikonę skrótu…");
  try{
    const r = await api("relink_icon", key, file);
    if (!r.ok){ console.warn("kadrowanie skrótu:", r.reason); return false; }
  }catch(e){ console.warn("kadrowanie skrótu:", e.message); return false; }
  finally{ setBusy(""); }
  showLinks(CURRENT_VIEW.filter, document.querySelector(".plat-item.active"));
  return true;
}

async function setCropMode(game, fit, offset){
  try{
    const r = await api("crop_apply", game.key, fit, offset, MODE);
    restyleCover(game.key, r.fit, r.offset);
    await linksReapplyIcon(game.key, game.file);
  }catch(e){ console.warn("kadr:", e.message); }
}
async function cropDefault(game){
  try{
    const r = await api("crop_reset", game.key, MODE);
    restyleCover(game.key, r.fit, r.offset);
    await linksReapplyIcon(game.key, game.file);
  }catch(e){ console.warn("kadr:", e.message); }
}

// ── Kadruj w OSOBNYM oknie: chwyć grafikę i przeciągnij (grab-drag) ─────────
let CROP_W = null;
function openCropWindow(game){
  el("cropTitle").textContent = "Kadrowanie: " + game.name;
  el("cropModal").hidden = false;
  const img = el("cropImg");
  CROP_W = {key: game.key, file: game.file || "", offset: 0.5, portrait: true};
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
    await linksReapplyIcon(CROP_W.key, CROP_W.file);
  }catch(e){}
  closeCropWindow();
};
el("cropFrameBtn").onclick = async () => {
  if (!CROP_W) return;
  try{ const r = await api("crop_apply", CROP_W.key, "pad", 0.5, MODE);
       restyleCover(CROP_W.key, r.fit, r.offset);
       await linksReapplyIcon(CROP_W.key, CROP_W.file); }catch(e){ console.warn("kadr:", e.message); }
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
let PK_PAGE = 0;        // strona wyników SGDB (limit 40 na stronę)
let PK_SEEN = new Set();// id już pokazanych — żeby „więcej" nie dublowało

// page>0 + append=true → DOPISUJE kolejną porcję zamiast przeładować listę
async function pkLoad(query, page = 0, append = false){
  if (!PK_GAME) return;
  PK_QUERY = query || "";
  PK_PAGE = page;
  if (!append){ PK_SEEN = new Set(); }
  const tok = ++PK_TOK;
  const box = el("pkGrid");
  if (!append) box.innerHTML = `<div class="pk-loading"><div class="spin"></div>Szukam grafik…</div>`;
  else el("pkMoreMsg").textContent = "Pobieram…";
  try{
    if (MODE === "steam"){
      const opt = await api("steam_art_options", PK_GAME.key, query || "", page);
      if (tok !== PK_TOK) return;
      renderSteamOptions(opt, append);
    }else{
      const r = await api("cover_candidates", PK_GAME.key, query || "", MODE, page);
      if (tok !== PK_TOK) return;
      renderCandidates(r, append);
    }
  }catch(e){
    if (tok === PK_TOK){
      if (append) el("pkMoreMsg").textContent = "Błąd: " + e.message;
      else box.innerHTML = `<div class="pk-empty">Błąd: ${e.message}</div>`;
    }
  }
}

// pojedynczy kafelek wariantu Steam (używany przy pierwszym renderze i przy „więcej")
function steamItem(asset, v, cur){
  const it = document.createElement("div");
  it.className = "pk-item";
  const dims = (v.w && v.h) ? `${v.w}×${v.h}` : "";
  it.title = `${dims} ${v.style||""}`.trim();
  if (cur && cur[asset] && cur[asset] === v.url) it.classList.add("chosen");
  it.appendChild(pkImg(v.thumb));
  if (dims){ const m = document.createElement("div"); m.className = "meta"; m.textContent = dims; it.appendChild(m); }
  it.onclick = () => chooseSteamAsset(asset, v.url, it);
  return it;
}

function pkMoreDone(msg){
  el("pkMore").disabled = false;
  el("pkMoreMsg").textContent = msg || "";
  el("pkMore").parentElement.hidden = false;
}

// Steam: trzy sekcje typów grafik (grid / hero-tło / logo), po jednym wyborze.
const STEAM_SECTIONS = [
  ["grid", "Okładka (grid)"],
  ["hero", "Tło (hero)"],
  ["logo", "Logo"],
];
function renderSteamOptions(opt, append = false){
  const box = el("pkGrid");
  box.classList.add("steam");
  if (!opt || opt.ok === false){
    if (append){ pkMoreDone("To już wszystko."); return; }
    box.innerHTML = `<div class="pk-empty">${(opt && opt.reason) || "Brak wyników."}</div>`;
    el("pkMore").parentElement.hidden = true;
    return;
  }
  const cur = opt.current || {};
  if (append){                       // dopisz kolejną porcję do istniejących pasków
    let added = 0;
    for (const [asset] of STEAM_SECTIONS){
      const row = box.querySelector(".pk-row-" + asset);
      if (!row) continue;
      for (const v of (opt[asset] || [])){
        const uid = asset + "|" + v.url;
        if (PK_SEEN.has(uid)) continue;
        PK_SEEN.add(uid);
        row.appendChild(steamItem(asset, v, cur));
        added++;
      }
    }
    pkMoreDone(added ? "" : "To już wszystko.");
    if (added) pkProgReset(added);
    return;
  }
  box.innerHTML = "";
  pkMoreDone("");
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
        PK_SEEN.add(asset + "|" + v.url);
        row.appendChild(steamItem(asset, v, cur));
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

function renderCandidates(r, append = false){
  const box = el("pkGrid");
  box.classList.remove("steam");
  let cs = (r && r.candidates) || [];
  cs = cs.filter(c => !PK_SEEN.has(String(c.id)));   // bez powtórek między stronami
  cs.forEach(c => PK_SEEN.add(String(c.id)));
  if (!cs.length){
    if (append){ pkMoreDone("To już wszystko."); return; }
    pkProgReset(0);
    box.innerHTML = `<div class="pk-empty">${(r && r.reason) || "Brak wyników."}</div>`;
    el("pkMore").parentElement.hidden = true;
    return;
  }
  if (!append) box.innerHTML = "";
  pkProgReset(cs.length);
  pkMoreDone("");
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
      // W widoku LINKS sam podgląd nie wystarcza — plik .lnk na dysku dalej
      // wskazywałby starą ikonę.
      if (CURRENT_VIEW && CURRENT_VIEW.type === "link" && PK_GAME.file){
        box.innerHTML = `<div class="pk-loading"><div class="spin"></div>Podmieniam ikonę skrótu…</div>`;
        try{
          const u = await api("relink_icon", key, PK_GAME.file);
          if (!u.ok){
            box.innerHTML = `<div class="pk-empty">Ikona pobrana, ale skrót nie zmieniony: ${u.reason}</div>`;
            return;
          }
        }catch(e){
          box.innerHTML = `<div class="pk-empty">Błąd podmiany ikony: ${e.message}</div>`;
          return;
        }
        closePicker();
        showLinks(CURRENT_VIEW.filter, document.querySelector(".plat-item.active"));
        return;
      }
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
el("pkReload").onclick = () => { hideSuggest(); pkLoad(PK_QUERY, 0, false); };

// „Pokaż więcej" — kolejna strona z SGDB (41–80, 81–120…), DOPISANA do listy
el("pkMore").onclick = () => {
  el("pkMore").disabled = true;
  pkLoad(PK_QUERY, PK_PAGE + 1, true);
};

// suwak wielkości miniatur w pickerze (pamiętany między otwarciami)
function pkApplySize(px){
  el("pkGrid").style.setProperty("--pk-size", px + "px");
  try{ localStorage.setItem("pk_thumb_size", String(px)); }catch(_){}
}
el("pkSize").oninput = (e) => pkApplySize(parseInt(e.target.value, 10));
try{
  const sz = parseInt(localStorage.getItem("pk_thumb_size") || "150", 10);
  if (sz){ el("pkSize").value = sz; pkApplySize(sz); }
}catch(_){}
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
  el("s_maxsrc").value = es.max_per_source || 12;
  const al = cfg.arcade_library || {};
  el("s_mameon").checked = !!al.enabled;
  el("s_mameexe").value = al.mame_exe || "";
  el("s_mamelib").value = al.lib_dir || "";
  el("s_mameroms").value = al.roms_dir || "";
  el("s_mamesup").value = al.support_dir || "";
  mameSettingsStatus();
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
  ph("s_ssdevid", sset.ss_devid); ph("s_ssdevpass", sset.ss_devpassword);
  ph("s_ssuser", sset.ss_user); ph("s_sspass", sset.ss_password);
  el("s_ss").checked = !!sset.screenscraper;
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
// ── testy API: jeden helper dla wszystkich przycisków ────────────────────────
const API_TESTS = [
  ["sgdb",     "test_sgdb",     "s_sgdbres",     "SteamGridDB"],
  ["steam",    "test_steam_api","s_steamres",    "Steam Web API"],
  ["igdb",     "test_igdb",     "s_igdbres",     "IGDB"],
  ["tgdb",     "test_tgdb",     "s_tgdbres",     "TheGamesDB"],
  ["screenscraper", "test_screenscraper", "s_ssres", "ScreenScraper.fr"],
  ["libretro", "test_libretro", "s_libretrores", "Libretro"],
  ["logos",    "test_logos_repo","s_logosres",   "Logotypy platform"],
];
function testMsg(r){
  if (!r) return "✗ błąd";
  return r.ok ? ("✓ " + (r.info || "OK")) : ("✗ " + (r.reason || "błąd"));
}
async function runApiTest(method, outId){
  el(outId).textContent = "…";
  try{ el(outId).textContent = testMsg(await api(method)); }
  catch(e){ el(outId).textContent = "✗ " + e.message; }
}
for (const [, method, outId] of API_TESTS){
  const btnId = "s_test" + (method === "test_logos_repo" ? "logos"
              : method.replace("test_", "").replace("_api", ""));
  const b = el(btnId);
  if (b) b.onclick = () => runApiTest(method, outId);
}

// zbiorczy test — wszystkie naraz, wynik w tabelce
el("s_testall").onclick = async () => {
  const box = el("s_testTable");
  el("s_allres").textContent = "sprawdzam…";
  box.hidden = false;
  box.innerHTML = `<div class="muted" style="padding:4px">Testuję…</div>`;
  let r;
  try{ r = await api("test_apis"); }
  catch(e){ el("s_allres").textContent = "✗ " + e.message; box.hidden = true; return; }
  box.innerHTML = "";
  let bad = 0;
  for (const [key, , outId, label] of API_TESTS){
    const v = r[key] || {};
    if (!v.ok) bad++;
    const row = document.createElement("div");
    row.className = "test-row " + (v.ok ? "ok" : "err");
    row.innerHTML = `<span class="t-name">${v.ok ? "✓" : "✗"} ${label}</span>`
                  + `<span class="t-msg">${v.info || v.reason || ""}</span>`;
    box.appendChild(row);
    el(outId).textContent = testMsg(v);          // odśwież też pojedyncze etykiety
  }
  el("s_allres").textContent = bad ? `${bad} z ${API_TESTS.length} nie działa`
                                   : "wszystko działa ✓";
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
      tgdb: sc("s_tgdb"), tgdb_key: sv("s_tgdbkey"),
      screenscraper: sc("s_ss"), ss_devid: sv("s_ssdevid"),
      ss_devpassword: sv("s_ssdevpass"), ss_user: sv("s_ssuser"),
      ss_password: sv("s_sspass"),
      max_per_source: Math.max(4, Math.min(80, parseInt(el("s_maxsrc").value,10) || 12))},
    arcade_library: {enabled: sc("s_mameon"), mame_exe: sv("s_mameexe"),
      lib_dir: sv("s_mamelib"), roms_dir: sv("s_mameroms"),
      support_dir: sv("s_mamesup"), skip_genres: collectSkipGenres()},
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
// zestaw wariantów to ~2000 plików — leci w TLE, z paskiem postępu u góry
// (wcześniej szło synchronicznie przez most: okno zamierało i nic nie było widać)
el("lgDownload").onclick = async () => {
  let r;
  try{ r = await api("download_logo_variants", LG.style); }
  catch(e){ el("lgDlMsg").textContent = "Błąd: " + e.message; return; }
  if (!r || !r.started){
    el("lgDlMsg").textContent = "Nie uruchomiono: " + ((r && r.reason) || "?");
    return;
  }
  el("lgDownload").disabled = true;
  el("lgDlMsg").textContent = "Pobieram…";
  el("pfBar").hidden = false;
  el("pfFill").style.width = "0%";
  PF_LABEL = "Pobieram logotypy";
  if (PF_TIMER) clearInterval(PF_TIMER);
  PF_TIMER = setInterval(async () => {
    let st;
    try{ st = await api("job_status"); }catch(_){ return; }
    const pct = st.total ? Math.round(100 * st.done / st.total) : 0;
    el("pfFill").style.width = pct + "%";
    el("pfTxt").textContent = `${PF_LABEL} [${st.phase}]: ${st.done}/${st.total}`;
    el("lgDlMsg").textContent = st.total ? `${st.done}/${st.total}…` : "Pobieram…";
    if (st.running) return;
    clearInterval(PF_TIMER); PF_TIMER = null;
    const res = st.result || {};
    el("pfTxt").textContent = `${PF_LABEL} — gotowe: ${res.downloaded ?? 0}/${res.total ?? 0}`;
    setTimeout(() => { el("pfBar").hidden = true; }, 2500);
    el("lgDlMsg").textContent = res.total ? `Pobrano ${res.downloaded}/${res.total} ✓`
                                         : "Nic nie pobrano";
    el("lgDownload").disabled = false;
    await refreshVariants();
    try{
      const info = await api("spine_logo_info");
      const cached = new Set(info.cached_styles || []);
      for (const o of el("lgStyle").options){
        const base = o.textContent.replace(/ ✓| \(brak cache\)$/,"");
        o.textContent = base + (cached.has(o.value) ? " ✓" : " (brak cache)");
      }
    }catch(_){}
  }, 700);
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


// ── ustawienia biblioteki arcade ────────────────────────────────────────────
async function mameSettingsStatus(){
  let st;
  try{ st = await api("mame_status"); }catch(e){ return; }
  const parts = [];
  if (st.mame_version) parts.push("MAME " + st.mame_version);
  else parts.push("nie wskazano mame.exe");
  if (st.cached_version) parts.push(`indeks: ${st.cached_version}`);
  else parts.push("brak indeksu — kliknij „Odśwież metadane”");
  if (st.needs_refresh) parts.push("⚠ inna wersja niż w indeksie");
  if (st.titles) parts.push(`${st.titles} tytułów`);
  el("s_mamestat").textContent = parts.join(" • ");
  renderGenreFilters();
}

// gatunki jako checkboxy — „atomizacja" filtrowania
async function renderGenreFilters(){
  const box = el("s_mameGenres");
  let gs = [];
  try{ gs = await api("mame_genres"); }catch(e){}
  if (!gs.length){ box.hidden = true; return; }
  let skip = [];
  try{ const c = await api("get_config"); skip = (c.arcade_library || {}).skip_genres || null; }catch(e){}
  box.hidden = false;
  box.innerHTML = "";
  for (const g of gs){
    const on = skip ? !skip.includes(g.genre) : g.default_on;
    const lab = document.createElement("label");
    lab.className = "genre-chip";
    lab.innerHTML = `<input type="checkbox" data-genre="${g.genre}"${on ? " checked" : ""}>`
                  + `<span>${g.genre}</span><span class="n">${g.titles}</span>`;
    box.appendChild(lab);
  }
}
function collectSkipGenres(){
  const box = el("s_mameGenres");
  if (box.hidden || !box.children.length) return undefined;   // nie nadpisuj
  return [...box.querySelectorAll("input[data-genre]")]
    .filter(i => !i.checked).map(i => i.dataset.genre);
}

// Sprzątanie duplikatów w bibliotece Steam. ZAWSZE najpierw podgląd: pokazujemy
// dokładnie, co zniknie, i dopiero po potwierdzeniu ruszamy plik. Metoda robi
// kopię zapasową `shortcuts.vdf` i podmienia go atomowo.
el("s_steamclean").onclick = async () => {
  const out = el("s_cleanres"), box = el("s_cleanbox");
  out.textContent = "Sprawdzam…"; box.hidden = true;
  let r;
  try{ r = await api("steam_cleanup", true); }
  catch(e){ out.textContent = "✗ " + e.message; return; }
  if (!r.ok){ out.textContent = "✗ " + (r.reason || "błąd"); return; }
  if (!r.found){
    out.textContent = `✓ nic do sprzątania (${r.total} wpisów w bibliotece)`;
    return;
  }
  out.textContent = `znaleziono ${r.found} duplikatów z ${r.total} wpisów`;
  box.textContent = (r.names || []).join("\n")
                  + ((r.found > (r.names || []).length)
                     ? `\n…i ${r.found - r.names.length} więcej` : "");
  box.hidden = false;
  if (r.steam_running){
    out.textContent += " — ZAMKNIJ STEAM, żeby usunąć";
    return;
  }
  if (!confirm(`Usunąć ${r.found} duplikatów z biblioteki Steam?\n\nZostanie kopia zapasowa shortcuts.vdf.`)) return;
  out.textContent = "Usuwam…";
  try{
    const d = await api("steam_cleanup", false);
    out.textContent = d.ok
      ? `✓ usunięto ${d.removed}, zostało ${d.left} wpisów`
      : "✗ " + (d.reason || "nie powiodło się");
  }catch(e){ out.textContent = "✗ " + e.message; }
};

el("s_showlog").onclick = async () => {
  const box = el("s_logbox");
  if (!box.hidden){ box.hidden = true; return; }
  try{
    const r = await api("get_log", 300);
    box.textContent = r.text || "(dziennik pusty)";
    el("s_logres").textContent = r.path || "";
    box.hidden = false;
    box.scrollTop = box.scrollHeight;
  }catch(e){ el("s_logres").textContent = "✗ " + e.message; }
};
el("s_openlog").onclick = async () => {
  try{ const r = await api("open_log_dir");
       if (!r.ok) el("s_logres").textContent = "✗ " + (r.reason || "błąd"); }
  catch(e){ el("s_logres").textContent = "✗ " + e.message; }
};

el("s_mamecovers").onclick = async () => {
  el("s_mamestat").textContent = "Czyszczę cache okładek arcade…";
  try{
    const r = await api("mame_recover_covers");
    el("s_mamestat").textContent =
      `✓ usunięto ${r.removed} automatycznych okładek — pobiorą się na nowo`
      + (r.kept_manual ? ` (zachowano ${r.kept_manual} wybranych ręcznie)` : "");
    scanMameUI();
  }catch(e){ el("s_mamestat").textContent = "✗ " + e.message; }
};

el("s_mamerefresh").onclick = async () => {
  el("s_mamestat").textContent = "Czytam metadane z MAME…";
  try{
    const r = await api("mame_refresh");
    if (!r.ok){ el("s_mamestat").textContent = "✗ " + (r.reason || "błąd"); return; }
    el("s_mamestat").textContent =
      `✓ ${r.sets} setów (${r.arcade} arcade) • catver ${r.catver||0} • mature ${r.mature||0}`;
    renderGenreFilters();
    scanMameUI();
  }catch(e){ el("s_mamestat").textContent = "✗ " + e.message; }
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
  // ARCADE idzie INNĄ drogą niż reszta: sam skrót by nie wystarczył, bo gry nie ma
  // jeszcze w katalogu do grania. Najpierw złożenie zestawu non-merged z biblioteki,
  // potem -verifyroms, a skrót powstaje TYLKO dla wersji, która przeszła kontrolę.
  if (CURRENT_VIEW.type === "mame"){ await exportMameSelection(); return; }
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

// Zbiorczy eksport zaznaczonych automatów do roms/mame + skróty.
async function exportMameSelection(){
  const chosen = MAME_GAMES.filter(g => SELECTED.has(g.key))
                           .map(g => g.chosen || g.set)
                           .filter(Boolean);
  if (!chosen.length){ alert("Nie zaznaczono żadnej gry arcade."); return; }
  if (!confirm(`Wyeksportować ${chosen.length} gier do katalogu do grania i utworzyć skróty?\n`
             + `Dla każdej powstanie samodzielny zestaw (non-merged) + BIOS/urządzenia,\n`
             + `a skrót tylko wtedy, gdy MAME potwierdzi, że wersja się uruchomi.`)) return;
  const b = el("btnExport"); const old = b.textContent;
  b.disabled = true; b.textContent = "⏳ Eksportuję…";
  let r;
  try{ r = await api("mame_export", chosen, true); }
  catch(e){ alert("Błąd eksportu: " + e.message); b.disabled = false; b.textContent = old; return; }
  if (!r || !r.started){
    alert("Nie uruchomiono: " + ((r && r.reason) || "?"));
    b.disabled = false; b.textContent = old; return;
  }
  el("pfBar").hidden = false; el("pfFill").style.width = "0%";
  PF_LABEL = "Eksport arcade";
  if (PF_TIMER) clearInterval(PF_TIMER);
  PF_TIMER = setInterval(async () => {
    let st;
    try{ st = await api("job_status"); }catch(_){ return; }
    const pct = st.total ? Math.round(100 * st.done / st.total) : 0;
    el("pfFill").style.width = pct + "%";
    el("pfTxt").textContent = `${PF_LABEL}: ${st.done}/${st.total}`
                            + (st.current ? ` — ${st.current}` : "");
    if (st.running) return;
    clearInterval(PF_TIMER); PF_TIMER = null;
    el("pfBar").hidden = true;
    b.disabled = false; b.textContent = old;
    const res = st.result || {};
    const okN = res.ok || 0, bad = res.failed || [];   // backend zwraca LICZBĘ, nie listę
    let msg = `Wyeksportowano ${okN}/${chosen.length} gier — skróty w LINKS\\MAME.`;
    if (bad.length){
      msg += `\n\nNie udało się (${bad.length}):\n`
           + bad.slice(0, 10).map(x => `• ${x.set || x}: ${x.reason || "?"}`).join("\n");
      if (bad.length > 10) msg += `\n…i ${bad.length - 10} więcej`;
    }
    alert(msg);
    loadPlatforms();
  }, 400);
}


// Naprawa ikon istniejących skrótów: przepiecze je z bieżącej okładki i kadru.
// Potrzebne, bo skrót zrobiony ZANIM gra dostała okładkę zostaje z gołą ikoną
// i sam się nie odświeży.
el("btnFixIcons").onclick = async () => {
  // Zaznaczenie decyduje. Dopiero gdy nic nie zaznaczono, proponujemy całą
  // platformę — wcześniej przycisk brał wszystko niezależnie od wyboru.
  const wybrane = LINK_GAMES.filter(g => SELECTED.has(g.key));
  const zrodlo = wybrane.length ? wybrane : LINK_GAMES;
  const items = zrodlo.filter(g => g.key && g.file).map(g => ({key: g.key, file: g.file}));
  const bezKlucza = zrodlo.length - items.length;
  if (!items.length){
    alert(wybrane.length
      ? "Zaznaczonych skrótów nie udało się powiązać z grami — uruchom skan."
      : "Nie rozpoznano żadnej gry — uruchom najpierw skan.");
    return;
  }
  let msg = wybrane.length
    ? `Przepiec ikony dla ${items.length} ZAZNACZONYCH skrótów?`
    : `Nie zaznaczono nic — przepiec ikony dla WSZYSTKICH ${items.length} skrótów?`;
  if (bezKlucza) msg += `\n(${bezKlucza} pominięte — nie rozpoznano gry)`;
  if (!confirm(msg)) return;
  const b = el("btnFixIcons"); const old = b.textContent;
  b.disabled = true; b.textContent = "⏳ Przepiekam…";
  let ok = 0; const bad = [];
  const STEP = 8;                       // paczkami — most jest synchroniczny
  for (let i = 0; i < items.length; i += STEP){
    setBusy(`Przepiekam ikony… ${Math.min(i + STEP, items.length)}/${items.length}`);
    try{
      const r = await api("relink_icons", items.slice(i, i + STEP));
      ok += r.done || 0;
      (r.failed || []).forEach(x => bad.push(x));
    }catch(e){ console.warn("naprawa ikon:", e.message); }
    await new Promise(r => setTimeout(r, 0));
  }
  setBusy("");
  b.disabled = false; b.textContent = old;
  let out = `Przepieczono ${ok} z ${items.length} ikon.`;
  if (bad.length){
    out += `\n\nBez zmian (${bad.length}):\n`
         + bad.slice(0, 10).map(x => `• ${x.file}: ${x.reason}`).join("\n");
    if (bad.length > 10) out += `\n…i ${bad.length - 10} więcej`;
  }
  alert(out);
  showLinks(CURRENT_VIEW.filter, document.querySelector(".plat-item.active"));
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
let FILTER_TIMER = null;
el("filter").oninput = (e) => {
  FILTER = e.target.value.trim();
  // Bez opóźnienia każdy wciśnięty klawisz przerysowywał porcję 300 kafelków
  // i uruchamiał pobieranie okładek — przy bibliotece arcade program stawał.
  if (FILTER_TIMER) clearTimeout(FILTER_TIMER);
  FILTER_TIMER = setTimeout(() => { FILTER_TIMER = null; refreshView(); }, 280);
};

document.querySelectorAll(".src").forEach(b => b.onclick = () => {
  clearActive(); b.classList.add("active");
  CUR_SRC = b.dataset.src || "";
  el("filter").hidden = PC_GAMES.length === 0;
  renderPC();
});

// ── pasek stanu + konsola ───────────────────────────────────────────────────
// WebView2 nie pokazuje konsoli, a długie operacje szły w ciszy: nie było jak
// zobaczyć, czy program pracuje, czy stanął.
function setBusy(txt){
  const bar = el("busyBar");
  if (!bar) return;
  if (txt){ el("busyTxt").textContent = txt; bar.hidden = false; }
  else bar.hidden = true;
}
let CON_TIMER = null;
// Bufor wierszy z samej przeglądarki. Dzięki niemu konsola pokazuje COKOLWIEK
// nawet gdy most nie działa — a to właśnie wtedy jest najbardziej potrzebna.
const CON_LOCAL = [];
function conLocal(tag, msg){
  const ts = new Date().toTimeString().slice(0, 8);
  CON_LOCAL.push(`${ts} [${tag}] ${msg}`);
  if (CON_LOCAL.length > 400) CON_LOCAL.shift();
  const p = el("conPanel");
  if (p && !p.hidden) conRender(null);
}
function conRender(pythonText){
  const box = el("conBox");
  if (!box) return;
  const atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 24;
  const parts = [];
  if (CON_LOCAL.length) parts.push("— przeglądarka —\n" + CON_LOCAL.join("\n"));
  if (pythonText != null) parts.push("— program —\n" + (pythonText || "(brak wpisów)"));
  box.textContent = parts.join("\n\n") || "(pusto)";
  if (atEnd) box.scrollTop = box.scrollHeight;      // nie wyrywaj przy czytaniu
}
async function conRefresh(){
  let r = null;
  try{
    r = await api("get_log", 400);
    el("conPath").textContent = r.path || "";
  }catch(e){
    // Wcześniej ten wyjątek był połykany i konsola świeciła pustką — czyli
    // narzędzie do diagnozy samo nie mówiło, że nie działa.
    el("conPath").textContent = "";
    conRender("✗ nie mogę odczytać dziennika: " + e.message);
    return;
  }
  conRender(r.text || "");
}
function conShow(on){
  const p = el("conPanel");
  p.hidden = !on;
  if (CON_TIMER){ clearInterval(CON_TIMER); CON_TIMER = null; }
  if (on){
    conRefresh();
    CON_TIMER = setInterval(() => { if (el("conAuto").checked) conRefresh(); }, 1200);
  }
}
el("busyLog").onclick = () => conShow(el("conPanel").hidden);
el("btnConsole").onclick = () => conShow(el("conPanel").hidden);
el("conClose").onclick = () => conShow(false);
el("conOpen").onclick = () => { try{ api("open_log_dir"); }catch(_){} };
el("conClear").onclick = async () => {
  try{ await api("clear_log"); conRefresh(); }catch(e){}
};
// skrót klawiszowy — konsola bywa potrzebna, gdy paska stanu akurat nie ma
document.addEventListener("keydown", (e) => {
  if (e.key === "F12" || (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === "j")){
    e.preventDefault();
    conShow(el("conPanel").hidden);
  }
});

// ── konsola: WebView2 nie pokazuje jej użytkownikowi, więc błędy z warstwy
// przeglądarki lądowały w próżni. Przekazujemy je do dziennika obok programu.
(function(){
  // Strażnik dotyczy TYLKO wysyłki przez most (błąd mostu zgłoszony przez konsolę
  // wywołałby kolejną wysyłkę i pętlę). Zapis lokalny jest bezwarunkowy — inaczej
  // seria błędów gubiła wszystkie poza pierwszym, a właśnie wtedy są najpotrzebniejsze.
  let sending = false;
  const send = (level, args) => {
    let txt;
    try{
      txt = args.map(a => {
        if (a instanceof Error) return (a.stack || a.message || String(a));
        if (typeof a === "object"){ try{ return JSON.stringify(a); }catch(_){ return String(a); } }
        return String(a);
      }).join(" ");
    }catch(_){ txt = "(nie udało się sformatować wpisu)"; }
    conLocal(level, txt);                     // widoczne od razu, także bez mostu
    if (sending || !hasBridge()) return;
    sending = true;
    try{
      Promise.resolve(api("log_js", level, txt))
        .catch(() => {}).finally(() => { sending = false; });
    }catch(_){ sending = false; }
  };
  for (const lvl of ["error", "warn"]){
    const orig = console[lvl].bind(console);
    console[lvl] = (...args) => { orig(...args); send(lvl, args); };
  }
  window.addEventListener("error", (e) => {
    send("error", [`${e.message} @ ${e.filename}:${e.lineno}:${e.colno}`,
                   e.error ? (e.error.stack || "") : ""]);
  });
  window.addEventListener("unhandledrejection", (e) => {
    send("error", ["unhandledrejection:", e.reason || ""]);
  });
})();

// start
if (hasBridge()) init();
else window.addEventListener("pywebviewready", init);
setTimeout(() => { if (!hasBridge()) init(); }, 800);
