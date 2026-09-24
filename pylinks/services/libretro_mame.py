"""services.libretro_mame — okładki automatów ze zbioru Libretro Thumbnails.

Dlaczego to najlepsze źródło dla arcade: pliki są nazwane DOKŁADNIE opisem gry
z MAME („Alien vs. Predator (Europe 940520).png"), więc dopasowanie jest
tożsamościowe, a nie zgadywane po tytule jak w SteamGridDB. Automaty mają krótkie,
wieloznaczne nazwy i to właśnie tam SGDB myli je z grami konsolowymi.

Nie sondujemy adresów po kolei — nazwy w repozytorium pochodzą z INNEJ wersji MAME
niż lokalna („Euro 940520" vs „Europe 940520”), więc zgadywanie by chybiało.
Zamiast tego pobieramy spis całego drzewa (jedno zapytanie, ~35 tys. wpisów w ~4 s)
i trzymamy go w bazie; dopasowanie jest potem lokalne i natychmiastowe.

Indeks siedzi w `Cache/pylinks.db`, tam gdzie reszta trwałego stanu — plik JSON
wymagałby sparsowania CAŁOŚCI (~1,9 MB, ~9 ms) przy każdym dostępie, a okładek
szukamy z sześciu wątków naraz. Tu jest to odczyt po kluczu głównym.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = "libretro-thumbnails/MAME"
TREE_URL = f"https://api.github.com/repos/{REPO}/git/trees/master?recursive=1"
RAW = f"https://raw.githubusercontent.com/{REPO}/master"
# kolejność = jakość dla naszego zastosowania: okładka > ekran tytułowy > zrzut
FOLDERS = ("Named_Boxarts", "Named_Titles", "Named_Snaps")
_MAX_AGE = 30 * 24 * 3600          # spis zmienia się rzadko; odświeżamy raz na miesiąc

_SCHEMA = """
CREATE TABLE IF NOT EXISTS libretro_mame (
    norm   TEXT PRIMARY KEY,        -- tytuł bez interpunkcji, do porównań
    files  TEXT NOT NULL            -- {folder: nazwa pliku} w JSON-ie
);
CREATE TABLE IF NOT EXISTS libretro_meta (
    k TEXT PRIMARY KEY,
    v TEXT
);
"""


def _db(cache_dir) -> sqlite3.Connection:
    p = Path(cache_dir)
    p.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p / "pylinks.db"), timeout=10)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(_SCHEMA)
    return con


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _meta(con, k: str) -> str:
    r = con.execute("SELECT v FROM libretro_meta WHERE k=?", (k,)).fetchone()
    return r[0] if r else ""


def index_stats(cache_dir) -> dict:
    """Ile tytułów i kiedy pobrano — bez ruszania sieci."""
    try:
        with _db(cache_dir) as con:
            n = con.execute("SELECT COUNT(*) FROM libretro_mame").fetchone()[0]
            return {"titles": n, "ts": float(_meta(con, "ts") or 0)}
    except Exception:
        return {"titles": 0, "ts": 0.0}


def refresh_index(cache_dir, force: bool = False) -> dict:
    """Pobiera spis repozytorium do bazy. Świeży indeks = brak ruchu sieciowego."""
    st = index_stats(cache_dir)
    if not force and st["titles"] and time.time() - st["ts"] < _MAX_AGE:
        return {"ok": True, "cached": True, "titles": st["titles"]}
    try:
        req = urllib.request.Request(TREE_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
    except Exception as e:
        return {"ok": False, "reason": f"{type(e).__name__}"}
    if data.get("truncated"):
        # gdyby repozytorium urosło ponad limit API — lepiej wiedzieć, niż milczeć
        return {"ok": False, "reason": "spis obcięty przez GitHub API"}
    idx: dict = {}
    for ent in data.get("tree") or ():
        path = ent.get("path") or ""
        if not path.endswith(".png"):
            continue
        folder, _, fname = path.partition("/")
        if folder not in FOLDERS or not fname:
            continue
        key = _norm(fname[:-4])
        if key:
            idx.setdefault(key, {}).setdefault(folder, fname)
    try:
        with _db(cache_dir) as con:
            con.execute("DELETE FROM libretro_mame")
            con.executemany("INSERT INTO libretro_mame(norm,files) VALUES(?,?)",
                            [(k, json.dumps(v)) for k, v in idx.items()])
            con.execute("INSERT INTO libretro_meta(k,v) VALUES('ts',?) "
                        "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(time.time()),))
    except Exception as e:
        return {"ok": False, "reason": str(e)}
    return {"ok": True, "cached": False, "titles": len(idx)}


def urls_for(cache_dir, titles) -> list[str]:
    """Adresy grafik dla PIERWSZEGO tytułu, który jest w indeksie."""
    keys = []
    seen = set()
    for t in titles:
        k = _norm(t)
        if k and k not in seen:
            seen.add(k)
            keys.append(k)
    if not keys:
        return []
    try:
        with _db(cache_dir) as con:
            rows = dict(con.execute(
                "SELECT norm,files FROM libretro_mame WHERE norm IN "
                "(%s)" % ",".join("?" * len(keys)), keys).fetchall())
    except Exception:
        return []
    for k in keys:                       # kolejność kandydatów ma znaczenie
        raw = rows.get(k)
        if not raw:
            continue
        try:
            hit = json.loads(raw)
        except Exception:
            continue
        out = [f"{RAW}/{f}/{urllib.parse.quote(hit[f])}"
               for f in FOLDERS if hit.get(f)]
        if out:
            return out
    return []
