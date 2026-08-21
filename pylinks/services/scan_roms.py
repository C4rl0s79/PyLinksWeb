"""services.scan_roms — skan ROM-ów per system (port z wersji tkinter).

Odtwarza kanoniczną logikę: .m3u/.cue/.gdi „pokrywają" swoje dyski, multi-disc
bez .m3u → tylko Dysk 1, tryb podkatalogów (folder = gra), wybór pliku głównego
wg rankingu rozszerzeń. Zwraca gry ROM: {name, system, path}.
"""

from __future__ import annotations

import re
from pathlib import Path

# domyślny zestaw rozszerzeń skanowanych zawsze (+ per-system extra)
_BASE_EXTS = {"m3u", "chd", "cue", "iso", "bin", "img", "ccd", "sub", "mdf",
              "nrg", "zip", "7z", "lnk"}

_MAIN_FILE_EXT_RANK = {
    "lnk": 0, "m3u": 0, "cue": 1, "gdi": 2, "ccd": 3, "mds": 4, "mdf": 40,
    "cdi": 10, "chd": 11, "iso": 12, "rvz": 13, "cso": 14, "pbp": 15, "nrg": 30,
    "bin": 90, "img": 91, "raw": 92, "sub": 93,
}


_BS = chr(92)                       # ukosnik odwrotny (bez literalu w kodzie)
_LNK_EXTRA_EXTS = {"iso", "xex", "rvz", "cso", "pbp", "wud", "wux", "gdi", "cdi"}


def strip_region(name: str) -> str:
    """Usuwa końcowe tagi (USA)/(Disc 1)/[!] itd. — wielokrotnie."""
    s = name.strip()
    changed = True
    while changed:
        s2 = re.sub(r"\s*\([^)]*\)\s*$", "", s).strip()
        s2 = re.sub(r"\s*\[[^\]]*\]\s*$", "", s2).strip()
        changed = (s2 != s)
        s = s2
    return s


def _norm_stem(stem: str) -> str:
    return strip_region(stem).lower()


def _main_file_sort_key(p: Path, priority: list[str] | None = None):
    ext = p.suffix.lower().lstrip(".")
    if priority and ext in priority:
        rank = priority.index(ext)
    else:
        rank = _MAIN_FILE_EXT_RANK.get(ext, 50)
        if priority:
            rank += len(priority)
    return (rank, p.name.lower())


def _rom_scan(folder: str, extra_exts: set[str] | None = None) -> list[Path]:
    exts = set(_BASE_EXTS)
    if extra_exts:
        exts |= extra_exts
    base = Path(folder)
    if not base.is_dir():
        return []
    files = [p for p in base.rglob("*")
             if p.is_file() and p.suffix.lower().lstrip(".") in exts]
    files.sort(key=lambda p: (0 if p.suffix.lower() == ".m3u" else 1, p.name.lower()))
    return files


def _m3u_refs(m3u: Path) -> set[Path]:
    refs: set[Path] = set()
    try:
        for line in m3u.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            ref = Path(line)
            if not ref.is_absolute():
                ref = m3u.parent / ref
            try:
                refs.add(ref.resolve())
            except Exception:
                refs.add(ref)
    except Exception:
        pass
    return refs


def _lnk_refs(lnk: Path) -> set:
    """Pliki ROM, na ktore wskazuje skrot .lnk (target + argumenty).

    Foldery ROM-ow bywaja pelne gotowych skrotow (X360/Xenia, PS3/RPCS3) lezacych
    OBOK surowych .iso. Bez tego ta sama gra wchodzila dwa razy: raz jako
    dzialajacy .lnk, raz jako surowy plik bez emulatora. Nazwy potrafia sie roznic
    ("Halo.lnk" -> "Halo (USA) [XGD3].iso"), wiec czytamy ZAWARTOSC skrotu.

    .lnk trzyma sciezki jako UTF-16LE - wyciagamy je z surowych bajtow, bez COM
    (skan bywa wolany z watku roboczego, a inicjalizacja COM jest tam zbedna).
    """
    refs: set = set()
    try:
        raw = lnk.read_bytes()
    except Exception:
        return refs
    # bloki tekstu w .lnk nie zawsze zaczynaja sie na parzystym bajcie, wiec
    # dekodujemy OBA wyrownania - inaczej czesc skrotow "nie ma" sciezki
    text = (raw.decode("utf-16-le", errors="ignore")
            + chr(0) + raw[1:].decode("utf-16-le", errors="ignore"))
    exts = "|".join(sorted(_BASE_EXTS | _LNK_EXTRA_EXTS))
    pat = ("[A-Za-z]:" + _BS + _BS + "[^" + chr(0) + chr(34) + "<>|*?]+?"
           + _BS + ".(?:" + exts + ")")
    for m in re.finditer(pat, text, re.IGNORECASE):
        cand = Path(m.group(0))
        try:
            refs.add(cand.resolve())
        except Exception:
            refs.add(cand)
    return refs


def _cue_gdi_refs(path: Path) -> set[Path]:
    refs: set[Path] = set()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return refs
    base = path.parent
    if path.suffix.lower() == ".cue":
        for m in re.finditer(r'FILE\s+"([^"]+)"', text, flags=re.I):
            refs.add((base / m.group(1)).resolve())
        for m in re.finditer(r'FILE\s+([^"\s]+)\s+\w+', text, flags=re.I):
            refs.add((base / m.group(1)).resolve())
    elif path.suffix.lower() == ".gdi":
        for line in text.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 5:
                m = re.search(r'"([^"]+)"', line)
                refs.add((base / (m.group(1) if m else parts[4])).resolve())
    return refs


def _rom_m3u_bundle(roms: list[Path], primary_ext: list[str] | None = None) -> list[Path]:
    """M3U/CUE/GDI pokrywają swoje dyski; multi-disc bez M3U → tylko Dysk 1."""
    m3u_files = [p for p in roms if p.suffix.lower() == ".m3u"]
    game_files = [p for p in roms if p.suffix.lower() != ".m3u"]

    covered: set[Path] = set()
    for m3u in m3u_files:
        covered |= _m3u_refs(m3u)
    for sheet in game_files:
        if sheet.suffix.lower() in (".cue", ".gdi"):
            covered |= _cue_gdi_refs(sheet)
        elif sheet.suffix.lower() == ".lnk":
            covered |= _lnk_refs(sheet)   # skrót pokrywa ROM, który uruchamia

    def _res(p):
        try:
            return p.resolve()
        except Exception:
            return p

    uncovered = [p for p in game_files if _res(p) not in covered]

    by_stem: dict[str, list[Path]] = {}
    for p in uncovered:
        by_stem.setdefault(_norm_stem(p.stem), []).append(p)

    standalone: list[Path] = []
    for items in by_stem.values():
        has_sheet = any(p.suffix.lower() in (".cue", ".gdi", ".m3u") for p in items)
        if has_sheet:
            items = [p for p in items
                     if p.suffix.lower() not in (".bin", ".img", ".raw", ".sub")] or items
        items.sort(key=lambda p: _main_file_sort_key(p, primary_ext))
        standalone.append(items[0])
    return m3u_files + standalone


def _pick_main_file(files: list[Path], priority: list[str]) -> Path:
    has_sheet = any(f.suffix.lower() in (".cue", ".gdi", ".m3u") for f in files)
    cand = ([f for f in files
             if f.suffix.lower() not in (".bin", ".img", ".raw", ".sub")]
            if has_sheet else files) or files
    for ext in priority:
        for f in cand:
            if f.suffix.lower().lstrip(".") == ext:
                return f
    return min(cand, key=lambda p: _main_file_sort_key(p))


def scan_system(syscfg: dict) -> list[dict]:
    """Zwraca gry ROM systemu: [{name, system, path}]."""
    plat = syscfg.get("name", "")
    rom_dir = (syscfg.get("rom_dir") or "").strip()
    if not rom_dir or not Path(rom_dir).is_dir():
        return []
    primary_ext = [e.strip().lower().lstrip(".")
                   for e in (syscfg.get("primary_ext", "") or "").split(",") if e.strip()]
    all_exts_str = syscfg.get("all_exts", syscfg.get("primary_ext", ""))
    extra = {e.strip().lower().lstrip(".") for e in all_exts_str.split(",") if e.strip()}

    out: list[dict] = []
    if syscfg.get("roms_in_subdirs"):
        base = Path(rom_dir)
        try:
            entries = sorted(base.iterdir())
        except Exception:
            return []
        pri = list(primary_ext)
        disc_exts = frozenset(extra | _BASE_EXTS)
        for d in entries:
            if d.is_file() and d.suffix.lower() == ".lnk":
                out.append({"name": strip_region(d.stem), "system": plat, "path": str(d)})
                continue
            if not d.is_dir():
                continue
            try:
                files = [p for p in sorted(d.iterdir()) if p.is_file()]
            except Exception:
                continue
            ext_files = [p for p in files
                         if p.suffix.lower().lstrip(".") in disc_exts]
            if not ext_files:
                continue
            main = _pick_main_file(ext_files, pri)
            out.append({"name": strip_region(d.name), "system": plat, "path": str(main)})
    else:
        mains = _rom_m3u_bundle(_rom_scan(rom_dir, extra), primary_ext)
        for m in mains:
            out.append({"name": strip_region(m.stem), "system": plat, "path": str(m)})

    out.sort(key=lambda g: g["name"].lower())
    return out


def scan_roms(cfg: dict) -> dict:
    """Skan wszystkich systemów ROM z config.rom_support.

    Zwraca {"enabled", "systems": [{name, count, games}], "total"}.
    """
    rs = cfg.get("rom_support", {}) or {}
    if not rs.get("enabled"):
        return {"enabled": False, "systems": [], "total": 0}
    systems = []
    total = 0
    for syscfg in rs.get("systems", []):
        games = scan_system(syscfg)
        systems.append({"name": syscfg.get("name", ""), "count": len(games),
                        "games": games})
        total += len(games)
    return {"enabled": True, "systems": systems, "total": total}
