"""services.mame — biblioteka arcade (MAME): metadane, warianty, kompletność.

Skąd metadane: z samego `mame.exe`, nie z plików DAT.
    -listfull    → nazwa setu -> tytuł  („avsp" -> „Alien vs. Predator (Europe…)")
    -listclones  → klon -> rodzic       (grupowanie wariantów jednego tytułu)
    -listxml SET → ROM-y, `romof` (BIOS/rodzic), `device_ref`, `disk` (CHD)
    -verifyroms  → czy zestaw da się URUCHOMIĆ (z uwzględnieniem BIOS-u i urządzeń)

Dzięki temu nie trzeba szukać DAT-a pasującego do wersji emulatora — źródłem
prawdy jest ten MAME, którym gra użytkownik. Wyniki `-listfull`/`-listclones`
są duże (50 tys. wpisów), więc trafiają do SQLite i są odświeżane dopiero przy
zmianie wersji MAME.
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mame_meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS mame_sets (
    name     TEXT PRIMARY KEY,
    title    TEXT NOT NULL,
    parent   TEXT NOT NULL DEFAULT '',   -- '' = to jest tytuł (rodzic)
    romof    TEXT NOT NULL DEFAULT '',   -- BIOS/rodzic, z którego dziedziczy ROM-y
    category TEXT NOT NULL DEFAULT '',   -- arcade | mech | computer | bios | device
    controls TEXT NOT NULL DEFAULT '',   -- typy sterowania z <input> (mahjong, dial…)
    genre    TEXT NOT NULL DEFAULT '',   -- płaski gatunek z genre.ini (61 wartości)
    catver   TEXT NOT NULL DEFAULT '',   -- szczegółowy „Gatunek / Podgatunek" z catver.ini
    maker    TEXT NOT NULL DEFAULT '',   -- producent; SGDB rozróżnia nim gry arcade
    adult    INTEGER NOT NULL DEFAULT 0,
    has_chd  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_mame_parent ON mame_sets(parent);
"""

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # 0 poza Windows


def _db(cache_dir) -> sqlite3.Connection:
    p = Path(cache_dir)
    p.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p / "pylinks.db"), timeout=10)
    con.executescript(_SCHEMA)
    try:                              # schemat mógł pochodzić ze starszej wersji
        cols = {r[1] for r in con.execute("PRAGMA table_info(mame_sets)")}
        if not {"controls", "adult", "romof", "genre", "catver", "maker"} <= cols:
            con.execute("DROP TABLE mame_sets")
            con.executescript(_SCHEMA)
    except Exception:
        pass
    return con


def _run(exe: str, *args, timeout: int = 600) -> str:
    try:
        r = subprocess.run([exe, *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=timeout, creationflags=_NO_WINDOW)
        return r.stdout or ""
    except Exception:
        return ""


def mame_version(exe: str) -> str:
    out = _run(exe, "-version", timeout=60).strip()
    return out.splitlines()[0].strip() if out else ""


# ── pliki wsparcia (catver.ini, mature.ini) ────────────────────────────────
# MAME nie zna gatunków ani oznaczenia „mature". Te pliki (projekt progetto-SNAPS)
# wydawane są w wersjach zgodnych z MAME, więc dokładnie pokrywają nasz indeks.
_SUPPORT_DIRS = ("", "UI_files")


def _find_support(support_dir, filename: str):
    base = Path(support_dir or "")
    for sub in _SUPPORT_DIRS:
        p = (base / sub / filename) if sub else (base / filename)
        if p.is_file():
            return p
    return None


def parse_catver(support_dir) -> dict:
    """catver.ini → {set: 'Gatunek / Podgatunek'}."""
    p = _find_support(support_dir, "catver.ini")
    if not p:
        return {}
    out, sec = {}, ""
    try:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                sec = line[1:-1]
            elif sec == "Category" and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except Exception:
        pass
    return out


def parse_listfile(support_dir, filename: str) -> set:
    """Plik typu mature.ini: sekcja [ROOT_FOLDER] i gołe nazwy setów."""
    p = _find_support(support_dir, filename)
    if not p:
        return set()
    out, on = set(), False
    try:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                on = line[1:-1].upper() == "ROOT_FOLDER"
            elif on and line and not line.startswith(";"):
                out.add(line)
    except Exception:
        pass
    return out


def parse_sections(support_dir, filename: str) -> dict:
    """Plik typu genre.ini: [Sekcja] + gołe nazwy setów → {set: sekcja}."""
    p = _find_support(support_dir, filename)
    if not p:
        return {}
    out, sec = {}, ""
    try:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                sec = line[1:-1]
            elif line and not line.startswith(";") and sec and                     sec.upper() not in ("ROOT_FOLDER", "FOLDER_SETTINGS"):
                out[line] = sec
    except Exception:
        pass
    return out


# Gatunki spoza „grania na automacie" — DOMYŚLNIE odsiewane. Lista jest tylko
# wartością startową: docelowo użytkownik zaznacza gatunki w interfejsie.
GENRE_SKIP_DEFAULT = (
    "Slot Machine", "Gambling", "Casino", "Medal Game", "Redemption Game",
    "Electromechanical", "System", "Computer", "Computer Graphic Workstation",
    "Game Console", "Game Console/Computer", "Handheld", "Tabletop", "Watch",
    "Calculator", "Telephone", "Radio", "Printer", "Digital Camera", "Tablet",
    "Medical Equipment", "Musical Instrument", "Musical Instrument Accessory",
    "Music Player", "Utilities", "Non Arcade", "Player", "TV Bundle",
    "Road Indicator", "Digital Simulator", "Misc.",
)


# MAME nie ma flagi „dla dorosłych" — jedyne, czym dysponujemy, to tytuł.
# Lista celowo wąska: lepiej czegoś nie odfiltrować, niż ukryć zwykłą grę.
_ADULT_RE = re.compile(
    r"\b(strip|nude|nudes|nudity|erotic|hentai|porno?|xxx|sexy|topless)\b", re.I)

# Sterowanie odsiewane DOMYŚLNIE: hazard, mahjong/hanafuda oraz spinner (`dial`).
# `paddle` (kierownice) i `trackball` celowo ZOSTAJĄ — inaczej wypadłyby klasyki
# grywalne myszą (Missile Command, Centipede, 720 Degrees, 18 Wheeler).
_CTRL_EXCLUDE = {"mahjong", "hanafuda", "gambling", "dial"}
_CTRL_OPTIONAL = {"paddle", "trackball", "lightgun", "pedal"}


def _controls(el) -> str:
    inp = el.find("input")
    if inp is None:
        return ""
    return ",".join(sorted({c.get("type") or "" for c in inp.findall("control")} - {""}))


def _classify(el) -> str:
    """Rodzaj maszyny. MAME emuluje nie tylko arcade — w pełnym secie 15 851 pozycji
    to automaty hazardowe (MPU4/Barcrest), a 2 675 to komputery i konsole."""
    if el.get("isdevice") == "yes":
        return "device"
    if el.get("isbios") == "yes":
        return "bios"
    if el.get("ismechanical") == "yes":
        return "mech"
    if el.get("runnable") == "no":
        return "device"
    if el.find("softwarelist") is not None:
        return "computer"
    return "arcade"


def refresh_metadata(cache_dir, exe: str, progress=None, support_dir="") -> dict:
    """Buduje indeks metadanych z `mame.exe -listxml` (jedno źródło prawdy).

    Pełny XML to ~312 MB, ale MAME generuje go w ~3 s, a parsowanie strumieniowe
    zajmuje ~6 s — więc taniej i pewniej niż szukanie DAT-a pasującego do wersji.
    Zapisujemy tytuł, rodzica, `romof`, kategorię i informację o CHD.
    """
    import xml.etree.ElementTree as ET
    import tempfile, os as _os
    ver = mame_version(exe)
    if not ver:
        return {"ok": False, "reason": "nie udało się uruchomić mame.exe"}
    if progress:
        progress("Generuję listę z MAME…")
    tmp = Path(tempfile.gettempdir()) / "pylinks_mame_listxml.xml"
    try:
        with open(tmp, "wb") as fh:
            r = subprocess.run([exe, "-listxml"], stdout=fh, stderr=subprocess.DEVNULL,
                               timeout=900, creationflags=_NO_WINDOW)
        if r.returncode not in (0, None):
            return {"ok": False, "reason": f"mame -listxml zwrócił {r.returncode}"}
    except Exception as ex:
        return {"ok": False, "reason": str(ex)}
    if progress:
        progress("Wczytuję metadane…")
    catver = parse_catver(support_dir) if support_dir else {}
    genres = parse_sections(support_dir, "genre.ini") if support_dir else {}
    mature = parse_listfile(support_dir, "mature.ini") if support_dir else set()
    rows, cats = [], {}
    try:
        for _ev, el in ET.iterparse(str(tmp), events=("end",)):
            if el.tag != "machine":
                continue
            cat = _classify(el)
            cats[cat] = cats.get(cat, 0) + 1
            nm = el.get("name") or ""
            desc = el.findtext("description") or nm
            # mature.ini jest autorytatywne; heurystyka po tytule tylko gdy go brak
            is_adult = (1 if nm in mature else 0) if mature else                        (1 if _ADULT_RE.search(desc) else 0)
            rows.append((nm, desc,
                         el.get("cloneof") or "",
                         el.get("romof") or "",
                         cat, _controls(el), genres.get(nm, ""), catver.get(nm, ""),
                         el.findtext("manufacturer") or "",
                         is_adult,
                         1 if el.find("disk") is not None else 0))
            el.clear()
    except Exception as ex:
        return {"ok": False, "reason": f"parsowanie XML: {ex}"}
    finally:
        try:
            _os.unlink(tmp)
        except Exception:
            pass
    with _db(cache_dir) as con:
        con.execute("DELETE FROM mame_sets")
        con.executemany("INSERT OR REPLACE INTO mame_sets"
                        "(name,title,parent,romof,category,controls,genre,catver,"
                        "maker,adult,has_chd) VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)
        con.execute("INSERT OR REPLACE INTO mame_meta(key,value) VALUES('version',?)", (ver,))
    return {"ok": True, "sets": len(rows), "version": ver,
            "catver": len(catver), "genres": len(genres), "mature": len(mature),
            "arcade": cats.get("arcade", 0), "mech": cats.get("mech", 0),
            "computer": cats.get("computer", 0), "device": cats.get("device", 0),
            "bios": cats.get("bios", 0)}


def cached_version(cache_dir) -> str:
    try:
        with _db(cache_dir) as con:
            r = con.execute("SELECT value FROM mame_meta WHERE key='version'").fetchone()
        return r[0] if r else ""
    except Exception:
        return ""


def scan_library(lib_dir) -> set:
    """Nazwy setów obecnych w bibliotece (po nazwach plików .zip/.7z)."""
    p = Path(lib_dir)
    if not p.is_dir():
        return set()
    out = set()
    for f in p.iterdir():
        if f.suffix.lower() in (".zip", ".7z"):
            out.add(f.stem.lower())
    return out


# ── kategorie z catver.ini ─────────────────────────────────────────────────
# catver.ini opisuje 50 368 setów łańcuchem „Kategoria / Podkategoria", czasem
# trzypoziomowym („Handheld / Plug n' Play TV Game / Music"), z przyrostkiem
# „* Mature *" tam, gdzie trzeba. To najlepiej uporządkowane źródło, jakie mamy:
# 58 kategorii głównych, 590 par kategoria/podkategoria, 100 % pokrycia — dlatego
# wybór w interfejsie opiera się właśnie na nim, a nie na płaskim genre.ini.
_MATURE_RE = re.compile(r"\s*\*\s*Mature\s*\*\s*", re.I)


def split_catver(value: str) -> tuple:
    """„Shooter / Flying Vertical * Mature *" → („Shooter", „Flying Vertical", True).

    Poziomy dalsze niż drugi sklejamy w podkategorię — inaczej drzewo rozjechałoby
    się na trzy piętra dla 518 setów z 50 tysięcy.
    """
    raw = str(value or "")
    mature = bool(_MATURE_RE.search(raw))
    core = _MATURE_RE.sub(" ", raw).strip()
    parts = [x.strip() for x in core.split("/") if x.strip()]
    if not parts:
        return ("(bez kategorii)", "", mature)
    return (parts[0], " / ".join(parts[1:]), mature)


def cat_key(cat: str, sub: str = "") -> str:
    """Klucz gałęzi do zapamiętania wyboru: „Shooter" albo „Shooter / Gallery"."""
    return f"{cat} / {sub}" if sub else cat


def category_tree(cache_dir, lib_dir) -> dict:
    """{kategoria: {"titles": n, "subs": {podkategoria: n}, "mature": n}}
    — liczone dla TYTUŁÓW faktycznie obecnych w bibliotece użytkownika.

    Liczymy tytuły (rodziców), nie sety: docelowo wybiera się jedną wersję.
    """
    present = scan_library(lib_dir)
    if not present:
        return {}
    try:
        with _db(cache_dir) as con:
            rows = con.execute(
                "SELECT name,parent,category,catver FROM mame_sets").fetchall()
    except Exception:
        return {}
    meta = {n: (p, c, cv) for n, p, c, cv in rows}
    seen, out = set(), {}
    for name in present:
        info = meta.get(name)
        if not info:
            continue
        parent, category, catver = info
        if category != "arcade":
            continue
        root = parent or name
        if root in seen:
            continue
        seen.add(root)
        rcv = (meta.get(root) or ("", "", ""))[2] or catver
        cat, sub, mature = split_catver(rcv)
        e = out.setdefault(cat, {"titles": 0, "subs": {}, "mature": 0})
        e["titles"] += 1
        if mature:
            e["mature"] += 1
        if sub:
            e["subs"][sub] = e["subs"].get(sub, 0) + 1
    return out


def library_titles(cache_dir, lib_dir, limit: int = 0,
                   categories=("arcade",), skip_controls: bool = True,
                   skip_adult: bool = True, skip_extra_ctrl=(),
                   skip_genres=None, skip_cats=None) -> list:
    """Biblioteka pogrupowana PO TYTULE — docelowo wybiera się jedną wersję.

    Zwraca [{name, title, variants:[{name,title}], have_parent}] tylko dla
    tytułów, których choć jedna wersja leży w bibliotece.

    Uwaga: pełny romset to 40+ tys. plików, więc NIE pytamy bazy przez
    `IN (...)` (limit zmiennych SQLite) — wczytujemy tabelę raz i przecinamy
    zbiory w pamięci.
    """
    skip = set(GENRE_SKIP_DEFAULT if skip_genres is None else skip_genres)
    # `skip_cats` zawiera gałęzie wyłączone przez użytkownika: „Shooter" wycina
    # całą kategorię, „Shooter / Gallery" tylko tę podkategorię.
    skip_c = set(skip_cats or ())
    present = scan_library(lib_dir)
    if not present:
        return []
    try:
        with _db(cache_dir) as con:
            rows = con.execute(
                "SELECT name,title,parent,category,controls,genre,adult,maker,"
                "catver FROM mame_sets").fetchall()
    except Exception:
        return []
    if not rows:
        return []
    meta = {n: (t, p, c, ct, g, ad, mk, cv)
            for n, t, p, c, ct, g, ad, mk, cv in rows}
    groups: dict = {}
    for name in present:
        info = meta.get(name)
        if not info:
            continue                      # plik nieznany temu MAME (inna wersja setu)
        title, parent, cat, ctrl, genre, adult, maker, catver = info
        if categories and cat not in categories:
            continue                      # domyślnie tylko arcade
        c_top, c_sub, c_mature = split_catver(catver)
        if skip_c and (c_top in skip_c or cat_key(c_top, c_sub) in skip_c):
            continue                      # gałąź wyłączona w oknie kategorii
        if not skip_c and skip and genre in skip:
            continue                      # zgodność ze starym filtrem gatunków
        if skip_adult and (adult or c_mature):
            continue                      # mature.ini ORAZ „* Mature *" z catver
        cset = set(ctrl.split(","))
        if skip_controls and (cset & _CTRL_EXCLUDE):
            continue                      # hazard, mahjong/hanafuda, spinner
        if skip_extra_ctrl and (cset & set(skip_extra_ctrl)):
            continue                      # opcjonalnie: kierownica, trackball…
        root = parent or name
        groups.setdefault(root, []).append({"name": name, "title": title})
    out = []
    for root, variants in groups.items():
        head = (meta.get(root) or ("",))[0] or variants[0]["title"]
        variants.sort(key=lambda v: (v["name"] != root, v["name"]))
        m = meta.get(root) or ("",) * 8
        r_top, r_sub, _ = split_catver(m[7])
        out.append({"name": root, "title": head, "variants": variants,
                    "genre": m[4], "maker": m[6],
                    "cat": r_top, "sub": r_sub,
                    "have_parent": root in present})
    out.sort(key=lambda g: g["title"].lower())
    return out[:limit] if limit else out


def library_genres(cache_dir, lib_dir) -> list:
    """Gatunki OBECNE w bibliotece + liczba tytułów — do zaznaczania w interfejsie.

    Liczymy tytuły (rodziców), nie sety, bo docelowo wybiera się jedną wersję.
    """
    present = scan_library(lib_dir)
    if not present:
        return []
    try:
        with _db(cache_dir) as con:
            rows = con.execute(
                "SELECT name,parent,category,genre FROM mame_sets").fetchall()
    except Exception:
        return []
    seen, counts = set(), {}
    meta = {n: (p, c, g) for n, p, c, g in rows}
    for name in present:
        info = meta.get(name)
        if not info:
            continue
        parent, cat, genre = info
        root = parent or name
        if root in seen:
            continue
        seen.add(root)
        g = genre or "(bez gatunku)"
        counts[g] = counts.get(g, 0) + 1
    out = [{"genre": g, "titles": n,
            "default_on": g not in GENRE_SKIP_DEFAULT and g != "(bez gatunku)"}
           for g, n in counts.items()]
    out.sort(key=lambda x: -x["titles"])
    return out


def verify_detail(exe: str, rompath: str, *sets, timeout: int = 600) -> dict:
    """`-verifyroms` ze SZCZEGÓŁAMI → {set: {"status":…, "problems":[…]}}.

    MAME wypisuje wiersz na każdy brakujący/uszkodzony plik („ei04-1.3c NOT FOUND",
    „… INCORRECT CHECKSUM"), a dopiero potem podsumowanie zestawu. Wcześniej te
    wiersze były wyrzucane i zostawał komunikat „zestaw niekompletny", z którego
    nie dało się dojść, czego właściwie brakuje — najczęstszy przypadek to brak
    pliku CHD, który leży poza zipem.
    """
    if not sets:
        return {}
    out = _run(exe, "-rompath", rompath, "-verifyroms", *sets, timeout=timeout)
    res: dict = {}
    pending: list[str] = []          # wiersze szczegółów przed podsumowaniem
    for line in out.splitlines():
        s = line.strip()
        if not s:
            continue
        m = re.match(r"^romset (\S+)(?: \[[^\]]+\])? is (\w+)", s)
        if m:
            res[m.group(1)] = {
                "status": "ok" if m.group(2) in ("good", "best") else "bad",
                "problems": pending}
            pending = []
            continue
        m2 = re.match(r"^romset (\S+) not found", s)
        if m2:
            res[m2.group(1)] = {"status": "missing", "problems": pending}
            pending = []
            continue
        if re.search(r"NOT FOUND|INCORRECT|WRONG|CHECKSUM|BAD ", s, re.I):
            pending.append(s)
    return res


def verify(exe: str, rompath: str, *sets, timeout: int = 600) -> dict:
    """`-verifyroms` dla podanych setów → {set: 'ok'|'bad'|'missing'}."""
    return {k: v["status"]
            for k, v in verify_detail(exe, rompath, *sets, timeout=timeout).items()}


# ── eksport wybranej wersji do katalogu do grania ──────────────────────────
def _machine_xml(exe: str, setname: str):
    """`-listxml SET` → (machine, {nazwa: machine}) z dołączonymi urządzeniami."""
    import xml.etree.ElementTree as ET
    out = _run(exe, "-listxml", setname, timeout=120)
    if not out.strip():
        return None, {}
    try:
        root = ET.fromstring(out)
    except Exception:
        return None, {}
    ms = {m.get("name"): m for m in root.findall("machine")}
    return ms.get(setname), ms


def plan_export(cache_dir, exe: str, setname: str) -> dict:
    """Co trzeba, żeby ta wersja ruszyła: ROM-y gry, BIOS, urządzenia, CHD.

    Rozróżnienie jest istotne:
    * `romof` wskazujący BIOS  → ROM-y BIOS-u zostają w OSOBNYM zipie (dzielone
      między gry; duplikowanie kosztowałoby ~5 MB na każdą grę Neo Geo),
    * `romof` wskazujący RODZICA (klon) → jego ROM-y trafiają DO zipa gry,
      bo katalog do grania ma być samowystarczalny (non-merged).
    """
    m, ms = _machine_xml(exe, setname)
    if m is None:
        return {"ok": False, "reason": f"MAME nie zna zestawu „{setname}”"}
    def _is_bios(name: str) -> bool:
        if not name:
            return False
        try:
            with _db(cache_dir) as con:
                r = con.execute("SELECT category FROM mame_sets WHERE name=?",
                                (name,)).fetchone()
            return bool(r and r[0] == "bios")
        except Exception:
            return False

    romof = m.get("romof") or ""
    bios = romof if _is_bios(romof) else ""
    parent = romof if (romof and not bios) else ""
    # KLON GRY NA BIOS-IE: łańcuch ma DWA ogniwa — `sengoku3a` → `sengoku3` → `neogeo`.
    # Patrzenie tylko o jeden poziom gubiło BIOS: ROM-y Neo Geo (sp-s2.sp1 itd.)
    # lądowały na liście „weź z zipa rodzica", a w zestawie split ich tam nie ma —
    # leżą w neogeo.zip. Efekt: zbudowany zip bez BIOS-u i „NOT FOUND" przy starcie.
    bios_roms: set = set()
    if parent:
        pm, _ = _machine_xml(exe, parent)
        pref = (pm.get("romof") or "") if pm is not None else ""
        if _is_bios(pref):
            bios = pref
            bm, _ = _machine_xml(exe, bios)
            if bm is not None:
                for br in bm.findall("rom"):
                    if br.get("name"):
                        bios_roms.add(br.get("name"))
    roms, from_parent = [], []
    for r in m.findall("rom"):
        nm, mg = r.get("name") or "", r.get("merge") or ""
        if not nm:
            continue
        if mg and bios and not parent:
            continue                       # ROM z BIOS-u — zostaje w bios.zip
        if mg and bios_roms and (mg in bios_roms or nm in bios_roms):
            continue                       # jw., ale gdy BIOS jest o dwa poziomy wyżej
        if mg and parent:
            from_parent.append((nm, mg))   # weź z zipa rodzica, zapisz pod nazwą klonu
        else:
            roms.append(nm)
    # Urządzenia z własnymi ROM-ami. UWAGA: zip nazywa się jak `romof` urządzenia,
    # nie jak samo urządzenie — np. `qsound_hle` ma romof="qsound", a w bibliotece
    # leży `qsound.zip`. Bierzemy obie nazwy i kopiujemy tę, która istnieje.
    devices = []
    for d in m.findall("device_ref"):
        dn = d.get("name") or ""
        dm = ms.get(dn)
        if dm is None or not dm.findall("rom"):
            continue
        devices.append(dn)
        dr = dm.get("romof") or ""
        if dr:
            devices.append(dr)
    disks = [d.get("name") for d in m.findall("disk") if d.get("name")]
    return {"ok": True, "set": setname, "bios": bios, "parent": parent,
            "own": roms, "from_parent": from_parent,
            "devices": sorted(set(devices)), "disks": disks}


def export_set(cache_dir, lib_dir, roms_dir, exe: str, setname: str,
               progress=None) -> dict:
    """Składa wybraną wersję w `roms_dir` i sprawdza `-verifyroms`."""
    import shutil, zipfile
    lib, dst = Path(lib_dir), Path(roms_dir)
    if not lib.is_dir():
        return {"ok": False, "reason": "biblioteka niedostępna (odpięty dysk?)"}
    dst.mkdir(parents=True, exist_ok=True)
    plan = plan_export(cache_dir, exe, setname)
    if not plan.get("ok"):
        return plan
    src_zip = lib / f"{setname}.zip"
    if not src_zip.is_file():
        return {"ok": False, "reason": f"brak {setname}.zip w bibliotece"}

    copied, built, skipped = [], False, []
    if plan["from_parent"]:
        # klon dziedziczący po rodzicu → budujemy zip samowystarczalny
        if progress:
            progress(f"Składam {setname}.zip…")
        par_zip = lib / f"{plan['parent']}.zip"
        if not par_zip.is_file():
            return {"ok": False, "reason": f"brak zipa rodzica {plan['parent']}.zip"}
        tmp = dst / f"{setname}.zip.tmp"
        try:
            with zipfile.ZipFile(src_zip) as zs, zipfile.ZipFile(par_zip) as zp, \
                 zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zo:
                have = set()
                for nm in plan["own"]:
                    try:
                        zo.writestr(nm, zs.read(nm)); have.add(nm)
                    except KeyError:
                        skipped.append(f"{nm} (nie ma w {setname}.zip)")
                for nm, mg in plan["from_parent"]:
                    if nm in have:
                        continue
                    try:                    # w zipie rodzica leży pod nazwą `merge`
                        zo.writestr(nm, zp.read(mg))
                    except KeyError:
                        try:
                            zo.writestr(nm, zp.read(nm))
                        except KeyError:
                            skipped.append(
                                f"{nm} (nie ma w {plan['parent']}.zip ani jako {mg})")
            import os as _os
            _os.replace(tmp, dst / f"{setname}.zip")
            built = True
        except Exception as ex:
            return {"ok": False, "reason": f"budowa zipa: {ex}"}
    else:
        if progress:
            progress(f"Kopiuję {setname}.zip…")
        shutil.copy2(src_zip, dst / src_zip.name)
    copied.append(f"{setname}.zip")

    for extra in ([plan["bios"]] if plan["bios"] else []) + plan["devices"]:
        p = lib / f"{extra}.zip"
        target = dst / p.name
        if not p.is_file():
            if not target.exists():
                skipped.append(f"{p.name} (BIOS/urządzenie — brak w bibliotece)")
            continue
        if not target.exists():                   # BIOS/urządzenia dzielone — raz
            if progress:
                progress(f"Kopiuję {p.name}…")
            try:
                shutil.copy2(p, target); copied.append(p.name)
            except Exception as ex:
                skipped.append(f"{p.name} ({ex})")

    for disk in plan["disks"]:                    # CHD leży w podkatalogu setu
        cands = (lib / setname / f"{disk}.chd", lib / f"{disk}.chd")
        if not any(c.is_file() for c in cands) and not (dst / setname / f"{disk}.chd").is_file():
            skipped.append(f"{disk}.chd (obraz dysku — brak w bibliotece)")
        for cand in cands:
            if cand.is_file():
                out = dst / setname / f"{disk}.chd"
                out.parent.mkdir(parents=True, exist_ok=True)
                if not out.exists():
                    if progress:
                        progress(f"Kopiuję {disk}.chd…")
                    try:
                        shutil.copy2(cand, out); copied.append(f"{setname}/{disk}.chd")
                    except Exception:
                        pass
                break

    if progress:
        progress("Sprawdzam kompletność…")
    det = verify_detail(exe, str(dst), setname).get(setname) or {}
    status = det.get("status", "bad")
    problems = det.get("problems") or []
    reason = ""
    if status != "ok":
        # Kolejność ma znaczenie: to, czego NIE UDAŁO SIĘ SKOPIOWAĆ, mówi więcej
        # niż lista „NOT FOUND" z MAME, bo wskazuje przyczynę, a nie skutek.
        if skipped:
            reason = "brakuje w bibliotece: " + "; ".join(skipped[:6])
            if len(skipped) > 6:
                reason += f" (+{len(skipped) - 6})"
        elif problems:
            reason = "MAME zgłasza: " + "; ".join(problems[:4])
            if len(problems) > 4:
                reason += f" (+{len(problems) - 4})"
        else:
            reason = f"zestaw niekompletny po eksporcie (status: {status})"
    try:
        from pylinks.core import log as _log
        _log.write("mame", f"{setname}: status={status} zbudowany={built} "
                           f"skopiowano={len(copied)} pominieto={len(skipped)}"
                           + (f" | {reason}" if reason else ""))
        for x in skipped:
            _log.write("mame", f"  {setname}: POMINIETO {x}")
        for x in problems:
            _log.write("mame", f"  {setname}: MAME {x}")
    except Exception:
        pass
    return {"ok": status == "ok", "set": setname, "status": status,
            "built": built, "copied": copied, "skipped": skipped,
            "problems": problems, "plan": plan, "reason": reason}


_TITLE_TAIL_RE = re.compile(r"\s*[\(\[][^()\[\]]*[\)\]]\s*$")


def clean_title(title: str) -> str:
    """Tytuł bez końcowych nawiasów z regionem/rewizją — do NAZWY PLIKU skrótu.

    „Alien vs. Predator (Europe 940520)" → „Alien vs. Predator"
    W interfejsie pokazujemy pełny opis (żeby dało się odróżnić wersje), ale plik
    skrótu ma nosić samą nazwę gry. Nie tniemy do pustego łańcucha ani nie ruszamy
    tytułów, które w całości są nawiasem.
    """
    out = str(title or "").strip()
    while True:
        stripped = _TITLE_TAIL_RE.sub("", out).strip()
        if not stripped or stripped == out:
            break
        out = stripped
    return out or str(title or "").strip()


def primary_title(title: str) -> str:
    """Tytuł do NAZWY PLIKU skrótu — bez nawiasów i bez drugiej nazwy po ukośniku.

    MAME zapisuje tytuły dwujęzyczne jednym łańcuchem ("Sengoku 3 / Sengoku Densho
    2001"), a ukośnik jest w nazwie pliku niedozwolony — powstawało z tego
    "Sengoku 3 _ Sengoku Densho 2001.lnk". Bierzemy człon pierwszy, bo MAME podaje
    tam nazwę międzynarodową. Do SZUKANIA OKŁADEK zostaje pełny tytuł: tam obie
    połowy są przydatne (patrz `_arcade_titles` w services/art.py).
    """
    t = clean_title(title)
    head = re.split(r"\s+/\s+", t, maxsplit=1)[0].strip()
    return head or t
