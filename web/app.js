"use strict";

// Most pywebview: window.pywebview.api pojawia się po zdarzeniu 'pywebviewready'.
// W zwykłej przeglądarce (podgląd) mostu nie ma — degradujemy się łagodnie.
const el = (id) => document.getElementById(id);
const hasBridge = () => !!(window.pywebview && window.pywebview.api);

let CURRENT_PLAT = null;

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
      `root: ${info.paths.root}   •   LINKS: ${info.paths.links}` +
      (info.config_exists ? "   •   config ✓" : "   •   config: brak (domyślny)");
    setBridge(true, "OK");
    await loadPlatforms();
  }catch(e){
    setBridge(false, "błąd: " + e.message);
  }
}

async function loadPlatforms(){
  const box = el("platforms");
  try{
    const plats = await api("list_platforms");
    el("platCount").textContent = plats.length ? `(${plats.length})` : "";
    if (!plats.length){
      box.innerHTML = `<div class="muted pad">Brak skrótów w LINKS/.<br>
        Ta wersja to szkielet — skanowanie dojdzie w Fazie 1.</div>`;
      return;
    }
    box.innerHTML = "";
    for (const p of plats){
      const b = document.createElement("button");
      b.className = "plat-item";
      b.dataset.plat = p.name;
      b.innerHTML = `<span>${p.name}</span><span class="count">${p.count}</span>`;
      b.onclick = () => selectPlatform(p.name, b);
      box.appendChild(b);
    }
  }catch(e){
    box.innerHTML = `<div class="muted pad">Błąd: ${e.message}</div>`;
  }
}

function markActive(node){
  document.querySelectorAll(".side-item,.plat-item")
    .forEach(n => n.classList.remove("active"));
  if (node) node.classList.add("active");
}

async function selectPlatform(name, node){
  CURRENT_PLAT = name || "";
  markActive(node);
  const grid = el("grid");
  el("gridTitle").textContent = name || "Wszystkie platformy";
  el("gridSub").textContent = "ładowanie…";
  grid.innerHTML = `<div class="welcome muted">ładowanie gier…</div>`;
  try{
    const games = await api("list_games", name || "");
    el("gridSub").textContent = `${games.length} skrótów`;
    if (!games.length){ grid.innerHTML = `<div class="welcome muted">Brak gier.</div>`; return; }
    grid.innerHTML = "";
    for (const g of games){
      const c = document.createElement("div");
      c.className = "card";
      c.innerHTML =
        `<div class="thumb">🎮</div>
         <div class="cap"><span class="t" title="${g.name}">${g.name}</span>
         <span class="p">${g.platform}</span></div>`;
      grid.appendChild(c);
    }
  }catch(e){
    grid.innerHTML = `<div class="welcome muted">Błąd: ${e.message}</div>`;
  }
}

// przyciski
el("btnPing").onclick = async () => {
  try{
    const r = await api("ping", "hello z JS");
    setBridge(true, "OK (ping: " + (r.echo||"") + ")");
  }catch(e){ setBridge(false, e.message); }
};
el("btnScan").onclick = () =>
  alert("Skanowanie dojdzie w Fazie 1 (na razie widać istniejące skróty z LINKS/).");
el("btnSettings").onclick = () =>
  alert("Ustawienia dojdą w Fazie 2.");
document.querySelectorAll(".side-item").forEach(b =>
  b.onclick = () => selectPlatform(b.dataset.plat, b));

// start: most może być już gotowy albo dopiero nadejść
if (hasBridge()) init();
else window.addEventListener("pywebviewready", init);
// fallback dla podglądu w przeglądarce
setTimeout(() => { if (!hasBridge()) init(); }, 800);
