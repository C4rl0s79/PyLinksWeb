"""services.export — tworzenie skrótów z ikonami.

Port z wersji tkinter: pieczenie ikony (kadr + grzbiet), ShortcutCreator per
źródło (Steam/GOG/Epic/Extra/ROM), orkiestracja do LINKS/<platforma>/.

Format skrótu zależy od systemu:
  Windows — .lnk przez pywin32/COM, .url dla adresów (steam:// itp.).
  Linux   — .desktop (freedesktop) w obu przypadkach; adres uruchamiany
            przez xdg-open, bo .url to format powłoki Windows.
"""

from __future__ import annotations

import os
import re

import json
from pathlib import Path

from pylinks.core.naming import safe_name
from pylinks.core import discs as _discs
from pylinks.core.rom_args import _rom_build_args
from pylinks.core import spine as _spine
from pylinks.services import art as _art

IS_WIN = os.name == "nt"
# Rozszerzenie skrótu do gry uruchamianej programem (nie adresem).
SHORTCUT_EXT = ".lnk" if IS_WIN else ".desktop"
# Rozszerzenie skrótu do adresu (steam://, com.epicgames.launcher://…).
URL_EXT = ".url" if IS_WIN else ".desktop"

try:
    import win32com.client  # type: ignore
    WIN32COM = True
except Exception:
    WIN32COM = False


# ── pieczenie .ico ─────────────────────────────────────────────────────────
def _raw_bytes(cache_dir: Path, key: str, mode: str = "icon") -> bytes | None:
    """Pełny obraz okładki do upieczenia .ico — NIE miniatura."""
    from pylinks.services.art import _raw_path
    f = _raw_path(cache_dir, key, mode)
    if not f:
        return None
    try:
        return f.read_bytes()
    except OSError:
        return None


def bake_args(cfg: dict, game: dict) -> dict:
    """Parametry grzbietu dla `bake_ico()` — JEDNO miejsce, w którym się o tym decyduje.

    Grzbiet dostają wyłącznie ROM-y i tylko przy włączonej opcji. Wcześniej regułę
    tę znał wyłącznie `export_games()`, więc przepieczenie ikony gdzie indziej
    (naprawa skrótu, eksport arcade) po cichu ZDEJMOWAŁO grzbiet z gotowego pliku.
    """
    on = bool(cfg.get("icon_platform_spine", False))
    return {
        "spine_system": (game or {}).get("system", "")
                        if (on and (game or {}).get("source") == "rom") else "",
        "spine_side": cfg.get("icon_spine_side", "left"),
        "logo_dir": cfg.get("platform_logo_dir", ""),
        "logo_scale": float(cfg.get("spine_logo_scale", 0.85) or 0.85),
        "spine_frac": float(cfg.get("icon_spine_frac", 0.22) or 0.22),
    }


def bake_ico(cache_dir: Path, key: str, fit: str, offset: float,
             spine_system: str = "", spine_side: str = "left",
             logo_dir: str = "", logo_scale: float = 0.85,
             spine_frac: float = 0.22) -> bytes | None:
    """Piecze .ico: surowa okładka → kadr(fit/offset) → grzbiet(opc.) → .ico."""
    raw = _raw_bytes(cache_dir, key, "icon")
    if not raw:
        return None
    if spine_system:
        # grzbiet: surowa grafika (plakat) → cover-fill obok grzbietu (jak podgląd)
        from io import BytesIO
        try:
            from PIL import Image
            base = Image.open(BytesIO(raw)).convert("RGBA")
        except Exception:
            return None
        img = _spine.add_platform_spine(base, spine_system, spine_side, frac=spine_frac,
                                        logo_dir=logo_dir, logo_scale=logo_scale)
    else:
        img = _art.display_thumb_img(raw, "icon", fit, offset, size=256)
    if img is None:
        return None
    try:
        return _art.make_icon_bytes(img)      # .ico na Windows, .png na Linuksie
    except Exception:
        return None


# ── skróty ─────────────────────────────────────────────────────────────────
def _desktop_quote(token: str) -> str:
    """Jeden argument linii Exec= wg specyfikacji Desktop Entry.

    Token idzie w cudzysłowach, znaki specjalne powłoki dostają backslash,
    a literalny procent musi być podwojony (pojedynczy `%` to pole typu %f).
    """
    out = token.replace("\\", "\\\\")
    for ch in ('"', "`", "$"):
        out = out.replace(ch, "\\" + ch)
    return '"' + out.replace("%", "%%") + '"'


def _write_desktop(dst: str, name: str, exec_line: str, icon: str = "",
                   work_dir: str = "") -> bool:
    """Zapisuje plik .desktop atomowo i z bitem wykonywalnym.

    Bez `chmod +x` GNOME/KDE pokazują skrót jako plik tekstowy zamiast go
    uruchamiać. Zapis idzie przez plik tymczasowy obok celu, żeby przerwany
    eksport nie zostawił połówki skrótu.
    """
    def _val(text: str) -> str:
        return text.replace("\\", "\\\\").replace("\n", " ").replace("\r", " ").strip()

    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Version=1.0",
        f"Name={_val(name)}",
        f"Exec={exec_line}",
        "Terminal=false",
        "StartupNotify=true",
        "Categories=Game;",
    ]
    if work_dir and Path(work_dir).is_dir():
        lines.insert(5, f"Path={_val(work_dir)}")
    if icon:
        lines.append(f"Icon={_val(icon)}")
    p = Path(dst)
    tmp = p.with_name(f".{p.name}.tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o755)
        os.replace(tmp, p)
        return True
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


class ShortcutCreator:
    def __init__(self, steam_exe: str = ""):
        self.steam_exe = steam_exe

    def make_desktop(self, dst: str, target: str, args: str, icon: str,
                     work_dir: str = "") -> bool:
        """Odpowiednik make_lnk na Linuksie: skrót .desktop do programu."""
        import shlex
        try:
            argv = shlex.split(args) if args else []
        except ValueError:
            return False        # niedomknięty cudzysłów → lepiej nie zgadywać
        exec_line = " ".join([_desktop_quote(target)]
                             + [_desktop_quote(a) for a in argv])
        return _write_desktop(dst, Path(dst).stem, exec_line, icon, work_dir)

    def make_lnk(self, dst: str, target: str, args: str, icon: str,
                 work_dir: str = "") -> bool:
        if not IS_WIN:
            return self.make_desktop(dst, target, args, icon, work_dir)
        if not WIN32COM:
            return False
        _co = False
        try:
            import pythoncom  # type: ignore
            pythoncom.CoInitialize()
            _co = True
        except Exception:
            pass
        try:
            sh = win32com.client.Dispatch("WScript.Shell")
            sc = sh.CreateShortcut(dst)
            sc.TargetPath = target
            sc.Arguments = args
            if icon:
                sc.IconLocation = icon
            if work_dir:
                sc.WorkingDirectory = work_dir
            sc.Save()
            return True
        except Exception:
            return False
        finally:
            if _co:
                try:
                    import pythoncom  # type: ignore
                    pythoncom.CoUninitialize()
                except Exception:
                    pass

    def make_url(self, dst: str, url: str, icon: str) -> bool:
        if not IS_WIN:
            # .url to format powłoki Windows — na Linuksie adres (steam://,
            # com.epicgames.launcher://) podajemy xdg-openowi, który znajdzie
            # zarejestrowany handler protokołu.
            exec_line = f'"xdg-open" {_desktop_quote(url)}'
            return _write_desktop(dst, Path(dst).stem, exec_line, icon)
        try:
            Path(dst).write_text(
                f"[InternetShortcut]\nURL={url}\nIconFile={icon}\nIconIndex=0\n",
                encoding="utf-8")
            return True
        except Exception:
            return False

    def copy_lnk_with_icon(self, src: str, dst: str, icon: str) -> bool:
        """Kopiuje gotowy .lnk (np. RPCS3/PS3) i podmienia ikonę."""
        if not IS_WIN:
            # .lnk to binarny format powłoki Windows — bez COM-a nie da się go
            # ani przeczytać, ani przestemplować, a skopiowany byłby martwy.
            return False
        import shutil
        try:
            shutil.copy2(src, dst)
        except Exception:
            return False
        if WIN32COM and icon:
            _co = False
            try:
                import pythoncom  # type: ignore
                pythoncom.CoInitialize()
                _co = True
                sh = win32com.client.Dispatch("WScript.Shell")
                sc = sh.CreateShortcut(dst)
                sc.IconLocation = icon
                sc.Save()
            except Exception:
                pass
            finally:
                if _co:
                    try:
                        import pythoncom  # type: ignore
                        pythoncom.CoUninitialize()
                    except Exception:
                        pass
        return True


# ── EXE / GOG helpers (port) ───────────────────────────────────────────────
def _find_exe(game_dir: str, skip_re=None) -> str:
    """Największy .exe w folderze gry (fallback dla Extra bez launch_exe)."""
    import re
    gd = Path(game_dir or "")
    if not gd.is_dir():
        return ""
    skip = skip_re or re.compile(
        r"(unins|setup|redist|vcredist|dxsetup|dotnet|crash|report|config|"
        r"launcher|update|patch|touchup|activation)", re.I)
    best, best_sz = "", -1
    try:
        for exe in gd.rglob("*.exe"):
            rel = exe.relative_to(gd)
            if len(rel.parts) > 5 or skip.search(exe.name):
                continue
            sz = exe.stat().st_size if exe.exists() else 0
            if sz > best_sz:
                best, best_sz = str(exe), sz
    except Exception:
        pass
    return best


def _gog_playtask(game_dir: str):
    try:
        gd = Path(game_dir)
        infos = sorted(gd.glob("goggame-*.info"))
        if not infos:
            return None
        data = json.loads(infos[0].read_text(encoding="utf-8-sig", errors="replace"))
        tasks = data.get("playTasks") or []

        def _isf(t):
            return isinstance(t, dict) and t.get("type") == "FileTask" and t.get("path")
        prim = (next((t for t in tasks if _isf(t) and t.get("isPrimary")), None)
                or next((t for t in tasks if _isf(t) and t.get("category") == "game"), None)
                or next((t for t in tasks if _isf(t)), None))
        if not prim:
            return None
        exe = gd / str(prim["path"]).replace("\\", "/")
        if not exe.exists():
            return None
        wd = str(prim.get("workingDir", "") or "").replace("\\", "/")
        return str(exe), prim.get("arguments", "") or "", str(gd / wd) if wd else str(exe.parent)
    except Exception:
        return None


# ── budowa komendy uruchomienia per gra ────────────────────────────────────
def _launch_for(game: dict, creator: ShortcutCreator, cfg: dict = None) -> dict | None:
    """Zwraca {"kind": "lnk"|"url"|"copy", "target","args","url","work_dir","src"}.

    Gry PC mogą iść przez Special K (ustawienie `pc_launch_mode`): wtedy skrót
    uruchamia SKIF-a z plikiem gry zamiast gry wprost. Gdy się nie da (brak
    SKIF-a albo nie wiadomo, który .exe uruchomić), robimy zwykły skrót —
    lepszy działający link niż żaden."""
    src = game.get("source", "")
    if cfg and src in ("steam", "gog", "epic", "extra"):
        from pylinks.services import specialk as _sk
        if _sk.mode(cfg) == _sk.MODE_SPECIALK:
            sk = _sk.launch_for(cfg, game)
            if sk:
                return sk
    if src == "steam":
        appid = game.get("appid")
        if not appid:
            return None
        if creator.steam_exe:
            return {"kind": "lnk", "target": creator.steam_exe,
                    "args": f"-applaunch {appid}", "work_dir": ""}
        return {"kind": "url", "url": f"steam://rungameid/{appid}"}
    if src == "epic":
        an = game.get("epic_app_name")
        if not an:
            return None
        return {"kind": "url",
                "url": f"com.epicgames.launcher://apps/{an}?action=launch&silent=true"}
    override = game.get("exe_override") or ""
    if src == "gog":
        if override:
            return {"kind": "lnk", "target": override, "args": "",
                    "work_dir": str(Path(override).parent)}
        pt = _gog_playtask(game.get("game_dir", "") or "")
        if pt:
            exe, args, wd = pt
            return {"kind": "lnk", "target": exe, "args": args, "work_dir": wd}
        exe = game.get("launch_exe") or ""
        if exe:
            return {"kind": "lnk", "target": exe, "args": "",
                    "work_dir": str(Path(exe).parent)}
        return None
    if src == "extra":
        exe = override or game.get("launch_exe") or _find_exe(game.get("game_dir", ""))
        if not exe:
            return None
        return {"kind": "lnk", "target": exe, "args": "",
                "work_dir": str(Path(exe).parent)}
    if src == "rom":
        rom = game.get("path", "")
        if rom.lower().endswith(".lnk"):
            return {"kind": "copy", "src": rom}
        emu = game.get("emulator", "")
        if not emu:
            return None
        # .m3u tylko dla emulatorów, które je czytają; reszta dostaje DYSK
        # (PCSX2/Dolphin/RPCS3 padają na playliście). Działa dwukierunkowo.
        rom = _discs.disc_path_for_emulator(rom, emu)
        return {"kind": "lnk", "target": emu,
                "args": _rom_build_args(game.get("launch_args", ""), rom),
                "work_dir": str(Path(emu).parent)}
    return None


# ── platforma docelowa (folder LINKS) ──────────────────────────────────────
def _platform_dir(game: dict) -> str:
    """Gry PC → 'PC'; ROM → nazwa systemu (jak w oryginale)."""
    return game.get("system", "") if game.get("source") == "rom" else "PC"


# ── orkiestracja ───────────────────────────────────────────────────────────
def ensure_covers(games: list[dict], cache_dir: Path, fetch_cover,
                  workers: int = 6) -> int:
    """Dociąga okładki, których brak w cache — RÓWNOLEGLE, przed pieczeniem ikon.

    Ikona powstaje z okładki na dysku. Bez tego kroku gra, której okładki nie
    ma (skasowany cache, gra dodana po pobraniu), dostawała skrót bez ikony,
    choć grafika była wybrana. fetch_cover(key, game) -> bool. Zwraca liczbę
    pobranych.
    """
    if not fetch_cover:
        return 0
    from concurrent.futures import ThreadPoolExecutor
    from pylinks.api import _game_key
    todo = []
    for g in games:
        k = _game_key(g)
        if not _art.has_cover(cache_dir, k, "icon"):
            todo.append((k, g))
    if not todo:
        return 0

    def _one(kg):
        try:
            return bool(fetch_cover(*kg))
        except Exception:
            return False
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return sum(ex.map(_one, todo))


def export_games(cfg: dict, games: list[dict], cache_dir: Path,
                 links_dir: Path, crop_of=None, progress=None,
                 fetch_cover=None) -> dict:
    """Tworzy skróty + ikony dla listy gier. Zwraca podsumowanie.

    crop_of(key) -> (fit, offset)  [opcjonalne; domyślnie z config].
    fetch_cover(key, game) -> bool [opcjonalne] — dociąga brakującą okładkę
    przed pieczeniem ikony (patrz `ensure_covers`).
    """
    fetched = ensure_covers(games, cache_dir, fetch_cover)
    steam_exe = (cfg.get("steam_exe") or "").strip()
    creator = ShortcutCreator(steam_exe)
    icons_dir = Path(cache_dir) / "icons"
    icons_dir.mkdir(parents=True, exist_ok=True)

    def _crop(key):
        if crop_of:
            return crop_of(key)
        store = cfg.get("icon_crop_by_key", {}) or {}
        e = store.get(key) if isinstance(store, dict) else None
        if isinstance(e, dict):
            return e.get("fit", "pad"), float(e.get("offset", 0.5))
        return cfg.get("icon_fit_mode", "pad"), 0.5

    from pylinks.api import _game_key       # klucz spójny z resztą (lazy, bez cyklu)
    exe_over = cfg.get("exe_override", {}) or {}
    done, failed, no_icon = 0, [], 0
    _sibs: dict = {}          # platforma → nazwy rodzeństwa (do sortowania serii)
    total = len(games)
    for i, g in enumerate(games):
        key = _game_key(g)
        if exe_over.get(key):
            g["exe_override"] = exe_over[key]
        name = safe_name(g.get("name", "") or "gra")
        plat = _platform_dir(g)
        out_dir = Path(links_dir) / plat
        out_dir.mkdir(parents=True, exist_ok=True)
        if plat not in _sibs:
            existing = set()
            try:
                existing = {f.stem.rstrip() for f in out_dir.iterdir()
                            if f.suffix.lower() in (".lnk", ".url", ".desktop")}
            except Exception:
                pass
            _sibs[plat] = existing | {safe_name(x.get("name", "") or "gra")
                                      for x in games if _platform_dir(x) == plat}
        name = sortable_stem(name, _sibs[plat])

        # cfg → tryb uruchamiania gier PC (zwykły / Special K). Eksport do
        # BIBLIOTEKI STEAM świadomie go nie bierze: tam wpis ma uruchamiać grę
        # tak jak Steam, a nie przez cudzy launcher.
        launch = _launch_for(g, creator, cfg)
        if not launch:
            failed.append(g.get("name", "")); continue

        # ikona .ico (grzbiet tylko dla ROM-a)
        fit, off = _crop(key)
        ico_path = ""
        ico = bake_ico(cache_dir, key, fit, off, **bake_args(cfg, g))
        if ico:
            try:
                ico_path = write_cache_ico(icons_dir, key, ico)
            except Exception:
                ico_path = ""
        else:
            no_icon += 1

        ok = False
        if launch["kind"] == "url":
            ok = creator.make_url(str(out_dir / f"{name}{URL_EXT}"),
                                  launch["url"], ico_path)
        elif launch["kind"] == "copy":
            ok = creator.copy_lnk_with_icon(
                launch["src"], str(out_dir / f"{name}{SHORTCUT_EXT}"), ico_path)
        else:  # skrót do programu
            ok = creator.make_lnk(str(out_dir / f"{name}{SHORTCUT_EXT}"),
                                  launch["target"],
                                  launch.get("args", ""), ico_path,
                                  launch.get("work_dir", ""))
        if ok:
            done += 1
        else:
            failed.append(g.get("name", ""))
        if progress:
            try:
                progress(i + 1, total, g.get("name", ""))
            except Exception:
                pass

    return {"ok": True, "created": done, "no_icon": no_icon,
            "covers_fetched": fetched,
            "failed": failed, "total": total,
            "win32com": WIN32COM}


def sortable_stem(name: str, siblings) -> str:
    """Nazwa pliku skrótu tak, żeby Explorer sortował serię po ludzku.

    Problem: „Tekken.lnk" ląduje ZA „Tekken 2.lnk", bo porównanie rozjeżdża się
    na 7. znaku — tam jedna nazwa ma KROPKĘ rozszerzenia, a druga jeszcze spację
    (spacja 0x20 < kropka 0x2E). Wspólne `.lnk` nie pomaga, bo kropka zaczyna się
    w innym miejscu. Znaki zero-width też nie — mają zerową wagę w porównaniu.

    Rozwiązanie: gdy istnieje DŁUŻSZE rodzeństwo („Tekken 2", ale też
    „Final Fantasy VII" obok „Final Fantasy"), doklejamy do krótszego SPACJĘ. Spacja ma wagę, więc `Tekken .lnk` sortuje się pierwszy,
    a że Explorer nigdy nie pokazuje rozszerzenia `.lnk` (NeverShowExt), podpis
    wygląda identycznie jak dotąd.
    """
    base = str(name or "")
    if not base or base.endswith(" "):
        return base
    pref = base + " "
    for sib in siblings:
        if sib != base and sib.startswith(pref):
            return pref
    return base


def _safe_ico(key: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9._-]+", "_", key)[:180]


def write_cache_ico(icons_dir: Path, key: str, data: bytes) -> str:
    """Zapisuje .ico pod nazwą <klucz>_<md5(treści)>.ico i zwraca ścieżkę.

    WAŻNE (cache ikon Windows): Explorer buforuje ikonę per ŚCIEŻKA. Przy stałej
    nazwie <klucz>.ico podmiana grafiki nie zmieniała ścieżki, więc shell dalej
    serwował STARĄ ikonę z bazy iconcache — restart explorera tego nie naprawia.
    Hash treści w nazwie sprawia, że inna grafika = inna ścieżka = świeża ikona
    od razu. Ta sama treść → ta sama nazwa (bez śmiecenia); starsze warianty
    tego samego klucza są sprzątane.
    """
    import hashlib
    ext = _art.ICON_EXT
    icons_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe_ico(key)
    target = icons_dir / f"{stem}_{hashlib.md5(data).hexdigest()[:10]}{ext}"
    if not target.exists():
        target.write_bytes(data)
    for old in icons_dir.glob(f"{stem}_*{ext}"):  # sprzątnij poprzednie warianty
        if old != target:
            try:
                old.unlink()
            except Exception:
                pass
    legacy = icons_dir / f"{stem}{ext}"           # nazwa sprzed poprawki
    if legacy.exists():
        try:
            legacy.unlink()
        except Exception:
            pass
    return str(target)
