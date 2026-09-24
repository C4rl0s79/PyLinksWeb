"""services.link_check — skróty wskazujące pliki ROM, których już nie ma.

Po konwersji kolekcji (bin/cue/iso → chd, 7z → zip) gra ma tę samą NAZWĘ, więc
nie wychodzi ani jako sierota, ani jako brak — ale skrót `.lnk` dalej niesie
w argumentach starą ścieżkę i gra się nie uruchamia. Użytkownik nie wie, co
zostało przerobione (kolekcja to ponad 100 tys. plików), więc sprawdzamy od
strony SKRÓTÓW: każda ścieżka pliku z argumentów musi istnieć. Dla brakującej
szukamy pliku o tej samej nazwie w tym samym katalogu z innym rozszerzeniem.

Istnienie sprawdzamy listą katalogu (jedno zapytanie na katalog), a nie per
plik — na udziale sieciowym to różnica między sekundami a minutami.
"""

from __future__ import annotations

import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# rozszerzenia, dla których szukamy zamiennika (plik ROM/obrazu płyty)
ROM_EXTS = {".chd", ".m3u", ".zip", ".7z", ".rvz", ".iso", ".cso", ".pbp", ".gdi",
            ".cue", ".cdi", ".bin", ".img", ".ccd", ".mds", ".nrg", ".wbfs", ".gcz",
            ".xex", ".wud", ".wux", ".wua", ".nsp", ".xci", ".3ds", ".cia"}

# kolejność preferencji zamiennika — docelowe formaty ujednoliconej kolekcji
# (płyty → CHD, kartridże → ZIP) najpierw
PREFER = [".chd", ".m3u", ".zip", ".7z", ".rvz", ".iso", ".cso", ".pbp", ".gdi",
          ".cue", ".cdi", ".bin", ".img"]

_BS = chr(92)
_PATH_RE = re.compile(
    '"([A-Za-z]:[' + _BS + _BS + '/][^"]+)"'                      # "X:\…"
    + '|(?<![' + _BS + 'w"])([A-Za-z]:[' + _BS + _BS + '/][^' + _BS + 's"]+)')

# emulatory samodzielne, które NIE czytają danego formatu (ostrzeżenie, nie blokada)
_NO_EXT = {
    "dolphin": {".chd", ".zip", ".7z"},
    "xemu": {".chd", ".zip", ".7z"},
    "xenia": {".chd", ".zip", ".7z"},
    "rpcs3": {".chd", ".zip", ".7z"},
    "cemu": {".chd", ".zip", ".7z"},
    "shadps4": {".chd", ".zip", ".7z"},
    "pcsx2": {".zip", ".7z"},
}


def arg_paths(args: str) -> list:
    """Ścieżki plików (surowo, tak jak zapisane) z argumentów skrótu."""
    out = []
    for m in _PATH_RE.finditer(args or ""):
        raw = m.group(1) or m.group(2)
        if raw and Path(raw).suffix:
            out.append(raw)
    return out


def _name_of(raw: str) -> str:
    return re.split("[" + _BS + _BS + "/]", raw)[-1]


def swap_name(raw: str, new_name: str) -> str:
    """Ta sama ścieżka z inną nazwą pliku — separatory i zapis zostają."""
    return raw[:len(raw) - len(_name_of(raw))] + new_name


class DirCache:
    """Zawartość katalogów (nazwy małymi literami → prawdziwa nazwa)."""

    def __init__(self) -> None:
        self._d: dict = {}
        self._lock = threading.Lock()

    def load(self, dirs, workers: int = 8) -> None:
        todo = sorted({os.path.normcase(str(d)) for d in dirs} - set(self._d))

        def _ls(d):
            try:
                return d, {n.lower(): n for n in os.listdir(d)}
            except OSError:
                return d, None
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            for d, names in ex.map(_ls, todo):
                with self._lock:
                    self._d[d] = names

    def names(self, directory) -> "dict | None":
        key = os.path.normcase(str(directory))
        if key not in self._d:
            self.load([directory], workers=1)
        return self._d.get(key)

    def exists(self, path) -> bool:
        p = Path(path)
        names = self.names(p.parent)
        return names is not None and p.name.lower() in names


def find_replacement(raw: str, dirs: DirCache) -> str:
    """Nazwa pliku-zamiennika (ta sama nazwa, inne rozszerzenie) albo ""."""
    p = Path(raw)
    names = dirs.names(p.parent)
    if not names:
        return ""
    stem, orig = p.stem.lower(), p.suffix.lower()
    for ext in PREFER:
        if ext != orig and (stem + ext) in names:
            return names[stem + ext]
    return ""


def _core_extensions(target: str, args: str) -> "set | None":
    """Rozszerzenia obsługiwane przez rdzeń RetroArch (z info/*.info) albo None."""
    if "retroarch" not in Path(target).stem.lower():
        return None
    m = re.search('-L' + _BS + 's+"([^"]+)"', args or "") \
        or re.search('-L' + _BS + 's+(' + _BS + 'S+)', args or "")
    if not m:
        return None
    core = Path(m.group(1))
    info = Path(target).parent / "info" / (core.stem + ".info")
    try:
        txt = info.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    mm = re.search(r'supported_extensions\s*=\s*"([^"]*)"', txt)
    return {"." + e.strip().lower() for e in mm.group(1).split("|")} if mm else None


def emulator_warning(target: str, args: str, new_name: str) -> str:
    ext = Path(new_name).suffix.lower()
    exts = _core_extensions(target, args)
    if exts is not None:
        return "" if ext in exts else f"rdzeń nie obsługuje {ext}"
    stem = Path(target).stem.lower()
    for emu, bad in _NO_EXT.items():
        if emu in stem and ext in bad:
            return f"{Path(target).stem} nie obsługuje {ext}"
    return ""


def _m3u_lines(path: Path) -> list:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def _read_lnk(sh, f: Path) -> tuple:
    sc = sh.CreateShortcut(str(f))
    return sc.TargetPath or "", sc.Arguments or "", sc.WorkingDirectory or ""


def check_links(links_dir: Path, progress=None, cancel=None) -> dict:
    """Sprawdza skróty .lnk w LINKS (także Kolekcje). Zwraca raport problemów."""
    files = sorted(p for p in Path(links_dir).rglob("*.lnk") if p.is_file())
    import pythoncom  # type: ignore
    import win32com.client  # type: ignore
    pythoncom.CoInitialize()
    try:
        sh = win32com.client.Dispatch("WScript.Shell")
        recs = []
        for i, f in enumerate(files, 1):
            if cancel and cancel():
                break
            try:
                target, args, wd = _read_lnk(sh, f)
            except Exception:
                continue
            recs.append((f, target, args, arg_paths(args)))
            if progress and (i % 25 == 0 or i == len(files)):
                progress(i, len(files), f"czytam skróty: {f.name}")
        del sh                       # zwolnij COM PRZED CoUninitialize
    finally:
        pythoncom.CoUninitialize()

    dirs = DirCache()
    if progress:
        progress(0, 0, "sprawdzam katalogi ROM-ów…")
    dirs.load({Path(r).parent for _, _, _, paths in recs for r in paths})

    # .m3u, które istnieją — ich wpisy też muszą istnieć
    m3us = {}
    for _, _, _, paths in recs:
        for raw in paths:
            if raw.lower().endswith(".m3u") and dirs.exists(raw):
                m3us.setdefault(os.path.normcase(raw), raw)
    m3u_refs = {}
    for key, raw in m3us.items():
        base = Path(raw).parent
        refs = []
        for line in _m3u_lines(Path(raw)):
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            full = Path(s) if Path(s).is_absolute() else base / s
            refs.append((line, s, full))
        m3u_refs[key] = refs
    dirs.load({full.parent for refs in m3u_refs.values() for _, _, full in refs})

    items = []
    ok = 0
    for f, target, args, paths in recs:
        issues = []
        for raw in paths:
            if dirs.exists(raw):
                if raw.lower().endswith(".m3u"):
                    bad = []
                    for line, s, full in m3u_refs.get(os.path.normcase(raw), []):
                        if dirs.exists(full):
                            continue
                        rep = find_replacement(str(full), dirs)
                        bad.append({"line": line,
                                    "new_line": (line.replace(_name_of(s), rep, 1)
                                                 if rep else ""),
                                    "old": _name_of(s), "new": rep})
                    if bad:
                        issues.append({"kind": "m3u", "raw": raw, "entries": bad,
                                       "fixable": all(b["new"] for b in bad)})
                continue
            ext = Path(raw).suffix.lower()
            if ext not in ROM_EXTS:
                issues.append({"kind": "other", "raw": raw, "new_raw": "",
                               "fixable": False, "warn": "brak pliku (nie ROM)"})
                continue
            rep = find_replacement(raw, dirs)
            if not rep:
                parent_ok = dirs.names(Path(raw).parent) is not None
                issues.append({"kind": "rom", "raw": raw, "new_raw": "", "fixable": False,
                               "warn": ("brak pliku o tej nazwie" if parent_ok
                                        else "katalog nie istnieje")})
                continue
            issues.append({"kind": "rom", "raw": raw, "new_raw": swap_name(raw, rep),
                           "fixable": True, "warn": emulator_warning(target, args, rep)})
        if issues:
            items.append({"file": str(f), "name": f.stem.rstrip(),
                          "platform": f.parent.name,
                          "rel": str(f.relative_to(links_dir)),
                          "target": target, "issues": issues})
        else:
            ok += 1
    return {"checked": len(recs), "ok": ok, "items": items}


def fix_link(sh, item: dict) -> tuple:
    """Podmienia ścieżki w jednym skrócie. Zwraca (zmieniono_skrót, pary_do_steam)."""
    pairs = [(i["raw"], i["new_raw"]) for i in item.get("issues", [])
             if i.get("kind") == "rom" and i.get("fixable") and i.get("new_raw")]
    if not pairs:
        return False, []
    sc = sh.CreateShortcut(item["file"])
    args = sc.Arguments or ""
    new_args = args
    for old, new in pairs:
        new_args = new_args.replace(old, new)
    if new_args == args:
        return False, []
    sc.Arguments = new_args
    sc.Save()
    return True, pairs


def fix_m3u(issue: dict) -> bool:
    """Poprawia wpisy .m3u na pliki-zamienniki (tylko gdy wszystkie mają zamiennik)."""
    if issue.get("kind") != "m3u" or not issue.get("fixable"):
        return False
    p = Path(issue["raw"])
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    nl = "\r\n" if "\r\n" in text else "\n"
    zmiany = {e["line"]: e["new_line"] for e in issue["entries"] if e.get("new_line")}
    lines = [zmiany.get(line, line) for line in text.splitlines()]
    new = nl.join(lines) + (nl if text.endswith(("\n", "\r")) else "")
    if new == text:
        return False
    tmp = p.with_name(p.name + ".pylinks_tmp")
    tmp.write_text(new, encoding="utf-8", newline="")
    os.replace(tmp, p)
    return True
