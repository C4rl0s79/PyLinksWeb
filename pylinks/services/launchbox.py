"""services.launchbox — grafiki z bazy LaunchBox Games DB (bez konta).

LaunchBox udostępnia CAŁĄ bazę jednym plikiem `Metadata.zip` (ok. 108 MB):
189 tys. gier, 1,34 mln grafik i 71 tys. nazw alternatywnych. Grafiki leżą pod
`images.launchbox-app.com/<plik>`. Dla gier z emulatorów to dziś najbogatsze
źródło: okładki, logo (Clear Logo), GRZBIETY (Box - Spine), kartridże, płyty,
zrzuty ekranu — a dla automatów `Mame.xml` tłumaczy nazwę zestawu („avspu")
na tytuł, więc arcade też dostaje grafiki.

Pobraną bazę trzymamy jako SQLite (`Cache/launchbox.db`): XML ma 512 MB i nie
ma sensu go rozbierać przy każdym wyszukaniu, a tak kandydaci lecą z dysku bez
jednego zapytania do sieci (pobieramy dopiero wybraną grafikę).
"""

from __future__ import annotations

import re
import sqlite3
import time
import urllib.request
import zipfile
from pathlib import Path

METADATA_URL = "https://gamesdb.launchbox-app.com/Metadata.zip"
IMAGE_BASE = "https://images.launchbox-app.com/"
DB_NAME = "launchbox.db"
UA = "PyLinksWeb/1.0 (+https://github.com/C4rl0s79)"

# nasz system → nazwa platformy w LaunchBoksie (Platforms.xml)
PLATFORMS = {
    "PC": "Windows", "DOS": "MS-DOS",
    "PS1": "Sony Playstation", "PSX": "Sony Playstation",
    "PS2": "Sony Playstation 2", "PS3": "Sony Playstation 3",
    "PS4": "Sony Playstation 4", "PSP": "Sony PSP", "VITA": "Sony Playstation Vita",
    "N64": "Nintendo 64", "N64DD": "Nintendo 64DD", "NES": "Nintendo Entertainment System",
    "FDS": "Nintendo Famicom Disk System", "SNES": "Super Nintendo Entertainment System",
    "SNESMSU1": "Super Nintendo Entertainment System",
    "SATELLAVIEW": "Nintendo Satellaview",
    "GB": "Nintendo Game Boy", "GBC": "Nintendo Game Boy Color",
    "GBA": "Nintendo Game Boy Advance", "NDS": "Nintendo DS", "N3DS": "Nintendo 3DS",
    "GCN": "Nintendo GameCube", "WII": "Nintendo Wii", "WIIU": "Nintendo Wii U",
    "SWITCH": "Nintendo Switch", "VB": "Nintendo Virtual Boy",
    "MD": "Sega Genesis", "GENESIS": "Sega Genesis", "SEGACD": "Sega CD",
    "SEGA32X": "Sega 32X", "SMS": "Sega Master System", "GG": "Sega Game Gear",
    "SATURN": "Sega Saturn", "DC": "Sega Dreamcast", "SG1000": "Sega SG-1000",
    "NAOMI": "Sega Naomi", "NAOMI2": "Sega Naomi 2", "STV": "Sega ST-V",
    "MODEL2": "Sega Model 2", "MODEL3": "Sega Model 3",
    "MAME": "Arcade", "ARCADE": "Arcade", "FBNEO": "Arcade",
    "NEOGEO": "SNK Neo Geo AES", "NEOGEOCD": "SNK Neo Geo CD",
    "NGP": "SNK Neo Geo Pocket", "NGPC": "SNK Neo Geo Pocket Color",
    "MSX": "Microsoft MSX", "MSX2": "Microsoft MSX2", "MSX2P": "Microsoft MSX2+",
    "XBOX": "Microsoft Xbox", "X360": "Microsoft Xbox 360", "XBOXONE": "Microsoft Xbox One",
    "3DO": "3DO Interactive Multiplayer", "JAGUAR": "Atari Jaguar",
    "JAGUARCD": "Atari Jaguar CD", "LYNX": "Atari Lynx",
    "ATARI2600": "Atari 2600", "ATARI5200": "Atari 5200", "ATARI7800": "Atari 7800",
    "ATARIST": "Atari ST", "PCENGINE": "NEC TurboGrafx-16",
    "TG16": "NEC TurboGrafx-16", "PCECD": "NEC TurboGrafx-CD", "PCFX": "NEC PC-FX",
    "PC88": "NEC PC-8801", "PC98": "NEC PC-9801",
    "C64": "Commodore 64", "AMIGA": "Commodore Amiga", "ZX": "Sinclair ZX Spectrum",
    "CPC": "Amstrad CPC", "COLECO": "ColecoVision", "INTELLIVISION": "Mattel Intellivision",
    "WONDERSWAN": "WonderSwan", "WONDERSWANCOLOR": "WonderSwan Color",
}

# kolejność pokazywania typów: najpierw to, z czego robi się ikonę i grzbiet
TYPE_ORDER = [
    "Box - Front", "Box - Front - Reconstructed", "Fanart - Box - Front",
    "Box - 3D", "Clear Logo", "Box - Spine", "Cart - Front", "Disc",
    "Screenshot - Game Title", "Screenshot - Gameplay", "Fanart - Background",
    "Banner", "Advertisement Flyer - Front", "Arcade - Marquee",
    "Arcade - Cabinet", "Arcade - Control Panel", "Box - Back",
]
MAX_PER_GAME = 60

_NORM_RE = re.compile(r"[^a-z0-9]+")
_TAG_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
# zapis No-Intro: „Legend of Zelda, The - Ocarina of Time" = „The Legend of
# Zelda: Ocarina of Time". Bez tego cała seria Zeldy nie miała trafień.
_ART_RE = re.compile(r"^(.*?),\s+(the|a|an)(?![a-z])(.*)$")
_LEAD_ART_RE = re.compile(r"^(the|an|a)\s+")


def norm_title(name: str) -> str:
    """Klucz dopasowania: bez tagów w nawiasach, bez interpunkcji i przedimka.

    Nazwy ROM-ów („Metal Gear 2 - Solid Snake (Japan)") i LaunchBoksa
    („Metal Gear 2: Solid Snake") różnią się tylko zapisem, więc zdejmujemy
    wszystko, co nie jest literą ani cyfrą."""
    s = (name or "").lower()
    s = _TAG_RE.sub(" ", s)
    m = _ART_RE.match(s)
    if m:                                   # „zelda, the - x" → „the zelda x"
        s = f"{m.group(2)} {m.group(1)} {m.group(3)}"
    s = s.replace("&", " and ")
    # przedimek zdejmujemy jako OSOBNE SŁOWO, jeszcze przed usunięciem spacji —
    # inaczej „Theme Park" traciło początek i zostawało „mepark"
    s = _LEAD_ART_RE.sub("", s.strip())
    return _NORM_RE.sub("", s)


def db_path(cache_dir) -> Path:
    return Path(cache_dir) / DB_NAME


def _db(cache_dir) -> sqlite3.Connection:
    p = db_path(cache_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p))
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS games(id INTEGER PRIMARY KEY, name TEXT,
                                         platform TEXT);
        CREATE TABLE IF NOT EXISTS names(norm TEXT, gid INTEGER, platform TEXT);
        CREATE TABLE IF NOT EXISTS images(gid INTEGER, type TEXT, region TEXT,
                                          fn TEXT);
        CREATE TABLE IF NOT EXISTS mame(setname TEXT PRIMARY KEY, title TEXT,
                                        cloneof TEXT);
        CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
    """)
    return con


def _indexes(con: sqlite3.Connection) -> None:
    con.executescript("""
        CREATE INDEX IF NOT EXISTS ix_names ON names(platform, norm);
        CREATE INDEX IF NOT EXISTS ix_names_n ON names(norm);
        CREATE INDEX IF NOT EXISTS ix_images ON images(gid);
    """)


def status(cache_dir) -> dict:
    p = db_path(cache_dir)
    if not p.is_file():
        return {"ready": False}
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        meta = dict(con.execute("SELECT k, v FROM meta").fetchall())
        con.close()
    except sqlite3.Error:
        return {"ready": False}
    return {"ready": bool(meta.get("updated")), "updated": meta.get("updated", ""),
            "games": int(meta.get("games", 0) or 0),
            "images": int(meta.get("images", 0) or 0),
            "size_mb": round(p.stat().st_size / (1 << 20), 1)}


def download(dest: Path, progress=None, cancel=None) -> Path:
    """Pobiera Metadata.zip (ok. 108 MB) z postępem."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        dest.unlink(missing_ok=True)      # resztka po przerwanym rozbiorze
    except OSError:
        pass
    tmp = dest.with_suffix(".part")
    req = urllib.request.Request(METADATA_URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        with open(tmp, "wb") as f:
            while True:
                if cancel and cancel():
                    tmp.unlink(missing_ok=True)
                    raise InterruptedError("przerwano pobieranie")
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if progress:
                    progress(got, total, f"pobieram bazę LaunchBox "
                                         f"({got >> 20}/{total >> 20} MB)")
    tmp.replace(dest)
    return dest


def build_index(cache_dir, zip_path: Path, progress=None, cancel=None) -> dict:
    """Rozbiera Metadata.zip do SQLite. Zwraca liczniki.

    Paczkę i bazę zamykamy ZAWSZE. Otwarty uchwyt blokuje plik na Windows:
    sprzątanie po rozbiorze nie mogło skasować archiwum („WinError 32 — plik
    jest używany przez inny proces"), a przy kolejnej próbie błąd wracał już
    przy pobieraniu, bo nie dało się nadpisać zablokowanego pliku.
    """
    con = _db(cache_dir)
    z = zipfile.ZipFile(zip_path)
    try:
        return _parse_metadata(con, z, progress, cancel)
    finally:
        try:
            z.close()
        finally:
            con.close()


def _parse_metadata(con, z, progress=None, cancel=None) -> dict:
    from xml.etree.ElementTree import iterparse
    con.executescript("DELETE FROM games; DELETE FROM names; DELETE FROM images;"
                      " DELETE FROM mame;")
    n_games = n_img = n_alt = 0
    games: list = []
    names: list = []
    imgs: list = []

    def _flush(force=False):
        nonlocal games, names, imgs
        if force or len(games) >= 5000 or len(imgs) >= 20000:
            con.executemany("INSERT OR REPLACE INTO games VALUES(?,?,?)", games)
            con.executemany("INSERT INTO names VALUES(?,?,?)", names)
            con.executemany("INSERT INTO images VALUES(?,?,?,?)", imgs)
            games, names, imgs = [], [], []

    plat_of: dict = {}
    with z.open("Metadata.xml") as f:
        for _ev, el in iterparse(f, events=("end",)):
            if el.tag == "Game":
                gid = el.findtext("DatabaseID")
                name = el.findtext("Name") or ""
                plat = el.findtext("Platform") or ""
                if gid and name:
                    gid = int(gid)
                    plat_of[gid] = plat
                    games.append((gid, name, plat))
                    names.append((norm_title(name), gid, plat))
                    n_games += 1
                el.clear()
                if n_games % 20000 == 0:
                    _flush()
                    if progress:
                        progress(n_games, 0, f"gry: {n_games}")
                    if cancel and cancel():
                        raise InterruptedError("przerwano")
            elif el.tag == "GameImage":
                gid = el.findtext("DatabaseID")
                fn = el.findtext("FileName")
                if gid and fn:
                    imgs.append((int(gid), el.findtext("Type") or "",
                                 el.findtext("Region") or "", fn))
                    n_img += 1
                el.clear()
                if n_img % 100000 == 0:
                    _flush()
                    if progress:
                        progress(n_img, 0, f"grafiki: {n_img}")
            elif el.tag == "GameAlternateName":
                gid = el.findtext("DatabaseID")
                nm = el.findtext("AlternateName")
                if gid and nm:
                    gid = int(gid)
                    names.append((norm_title(nm), gid, plat_of.get(gid, "")))
                    n_alt += 1
                el.clear()
    _flush(force=True)

    # Mame.xml: zestaw → tytuł (+ rodzic klonu) — most dla automatów
    rows = []
    with z.open("Mame.xml") as f:
        for _ev, el in iterparse(f, events=("end",)):
            if el.tag == "MameFile":
                st = el.findtext("FileName")
                if st:
                    rows.append((st.lower(), el.findtext("Name") or "",
                                 (el.findtext("CloneOf") or "").lower()))
                el.clear()
                if len(rows) >= 5000:
                    con.executemany("INSERT OR REPLACE INTO mame VALUES(?,?,?)", rows)
                    rows = []
    con.executemany("INSERT OR REPLACE INTO mame VALUES(?,?,?)", rows)

    _indexes(con)
    con.executemany("INSERT OR REPLACE INTO meta VALUES(?,?)", [
        ("updated", time.strftime("%Y-%m-%d %H:%M")),
        ("games", str(n_games)), ("images", str(n_img)), ("alts", str(n_alt))])
    con.commit()
    con.execute("VACUUM")
    return {"games": n_games, "images": n_img, "alts": n_alt}


def update(cache_dir, progress=None, cancel=None) -> dict:
    """Pobiera bazę i buduje indeks. Plik ZIP kasujemy po rozbiorze."""
    zp = Path(cache_dir) / "launchbox_metadata.zip"
    try:
        download(zp, progress, cancel)
        if progress:
            progress(0, 0, "rozbieram bazę (512 MB XML)…")
        out = build_index(cache_dir, zp, progress, cancel)
    finally:
        for p in (zp, zp.with_suffix(".part")):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass            # zostanie na dysku; następne pobranie go nadpisze
    return dict(out, **status(cache_dir))


def lb_platform(system: str) -> str:
    """Nazwa platformy w LaunchBoksie dla naszego systemu („PS1" → „Sony Playstation")."""
    s = (system or "").strip()
    if not s:
        return ""
    hit = PLATFORMS.get(s.upper())
    if hit:
        return hit
    # system nazwany wprost jak u LaunchBoksa (np. „Sega Saturn")
    return s


def _mame_titles(con: sqlite3.Connection, setname: str) -> list:
    """Tytuł automatu z nazwy zestawu — własny i rodzica klonu."""
    out = []
    row = con.execute("SELECT title, cloneof FROM mame WHERE setname=?",
                      (setname.lower(),)).fetchone()
    if not row:
        return out
    if row[0]:
        out.append(row[0])
    if row[1]:
        par = con.execute("SELECT title FROM mame WHERE setname=?", (row[1],)).fetchone()
        if par and par[0]:
            out.append(par[0])
    return out


def candidates(cache_dir, system: str, title: str, mame_set: str = "",
               limit: int = MAX_PER_GAME) -> list:
    """Kandydaci grafik dla gry — WSZYSTKIE typy, uporządkowane wg TYPE_ORDER."""
    p = db_path(cache_dir)
    if not p.is_file() or not (title or mame_set):
        return []
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        plat = lb_platform(system)
        tytuly = [title] if title else []
        if mame_set:
            tytuly = _mame_titles(con, mame_set) + tytuly
        normy = [n for n in (norm_title(t) for t in tytuly) if n]
        if not normy:
            return []
        gids: list = []
        q = ",".join("?" * len(normy))
        if plat:
            gids = [r[0] for r in con.execute(
                f"SELECT DISTINCT gid FROM names WHERE platform=? AND norm IN ({q})",
                [plat] + normy)]
        if not gids:                      # bez platformy — lepiej coś niż nic
            gids = [r[0] for r in con.execute(
                f"SELECT DISTINCT gid FROM names WHERE norm IN ({q})", normy)]
        if not gids:
            return []
        gq = ",".join("?" * len(gids))
        rows = con.execute(
            f"SELECT type, region, fn FROM images WHERE gid IN ({gq})", gids).fetchall()
    finally:
        con.close()

    def _rank(r):
        t = r[0]
        return (TYPE_ORDER.index(t) if t in TYPE_ORDER else len(TYPE_ORDER), t)

    rows.sort(key=_rank)
    out = []
    for typ, region, fn in rows[:limit]:
        url = IMAGE_BASE + fn
        etykieta = typ or "LaunchBox"
        if region:
            etykieta += f" · {region}"
        out.append({"id": f"launchbox_{fn}", "url": url, "thumb": url,
                    "w": 0, "h": 0, "style": f"LB: {etykieta}",
                    "kind": "launchbox", "square": False})
    return out
