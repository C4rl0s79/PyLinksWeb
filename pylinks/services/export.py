"""services.export — tworzenie skrótów (.lnk/.url) z ikonami .ico.

Port z wersji tkinter: pieczenie .ico (kadr + grzbiet), ShortcutCreator per
źródło (Steam/GOG/Epic/Extra/ROM), orkiestracja do LINKS/<platforma>/.
Windows-only dla .lnk (pywin32); .url działa wszędzie.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pylinks.core.naming import safe_name
from pylinks.core.rom_args import _rom_build_args
from pylinks.core import spine as _spine
from pylinks.services import art as _art

try:
    import win32com.client  # type: ignore
    WIN32COM = True
except Exception:
    WIN32COM = False


# ── pieczenie .ico ─────────────────────────────────────────────────────────
def _raw_bytes(cache_dir: Path, key: str, mode: str = "icon") -> bytes | None:
    d = Path(cache_dir) / "covers" / ("steam" if mode == "steam" else "icon")
    from pylinks.services.art import _safe_key
    for f in d.glob(_safe_key(key) + ".*"):
        try:
            return f.read_bytes()
        except Exception:
            return None
    return None


def bake_ico(cache_dir: Path, key: str, fit: str, offset: float,
             spine_system: str = "", spine_side: str = "left",
             logo_dir: str = "") -> bytes | None:
    """Piecze .ico: surowa okładka → kadr(fit/offset) → grzbiet(opc.) → .ico."""
    raw = _raw_bytes(cache_dir, key, "icon")
    if not raw:
        return None
    img = _art.display_thumb_img(raw, "icon", fit, offset, size=256)
    if img is None:
        return None
    if spine_system:
        img = _spine.add_platform_spine(img, spine_system, spine_side, logo_dir=logo_dir)
    try:
        return _art.make_ico_bytes(img)
    except Exception:
        return None


# ── skróty ─────────────────────────────────────────────────────────────────
class ShortcutCreator:
    def __init__(self, steam_exe: str = ""):
        self.steam_exe = steam_exe

    def make_lnk(self, dst: str, target: str, args: str, icon: str,
                 work_dir: str = "") -> bool:
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
        try:
            Path(dst).write_text(
                f"[InternetShortcut]\nURL={url}\nIconFile={icon}\nIconIndex=0\n",
                encoding="utf-8")
            return True
        except Exception:
            return False

    def copy_lnk_with_icon(self, src: str, dst: str, icon: str) -> bool:
        """Kopiuje gotowy .lnk (np. RPCS3/PS3) i podmienia ikonę."""
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
def _launch_for(game: dict, creator: ShortcutCreator) -> dict | None:
    """Zwraca {"kind": "lnk"|"url"|"copy", "target","args","url","work_dir","src"}."""
    src = game.get("source", "")
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
    if src == "gog":
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
        exe = game.get("launch_exe") or _find_exe(game.get("game_dir", ""))
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
        return {"kind": "lnk", "target": emu,
                "args": _rom_build_args(game.get("launch_args", ""), rom),
                "work_dir": str(Path(emu).parent)}
    return None


# ── platforma docelowa (folder LINKS) ──────────────────────────────────────
def _platform_dir(game: dict) -> str:
    """Gry PC → 'PC'; ROM → nazwa systemu (jak w oryginale)."""
    return game.get("system", "") if game.get("source") == "rom" else "PC"


# ── orkiestracja ───────────────────────────────────────────────────────────
def export_games(cfg: dict, games: list[dict], cache_dir: Path,
                 links_dir: Path, crop_of=None, progress=None) -> dict:
    """Tworzy skróty + ikony dla listy gier. Zwraca podsumowanie.

    crop_of(key) -> (fit, offset)  [opcjonalne; domyślnie z config].
    """
    steam_exe = (cfg.get("steam_exe") or "").strip()
    creator = ShortcutCreator(steam_exe)
    spine_on = bool(cfg.get("icon_platform_spine", False))
    spine_side = cfg.get("icon_spine_side", "left")
    logo_dir = cfg.get("platform_logo_dir", "")
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
    done, failed, no_icon = 0, [], 0
    total = len(games)
    for i, g in enumerate(games):
        key = _game_key(g)
        name = safe_name(g.get("name", "") or "gra")
        plat = _platform_dir(g)
        out_dir = Path(links_dir) / plat
        out_dir.mkdir(parents=True, exist_ok=True)

        launch = _launch_for(g, creator)
        if not launch:
            failed.append(g.get("name", "")); continue

        # ikona .ico (grzbiet tylko dla ROM-a)
        fit, off = _crop(key)
        spine_sys = g.get("system", "") if (spine_on and g.get("source") == "rom") else ""
        ico_path = ""
        ico = bake_ico(cache_dir, key, fit, off, spine_sys, spine_side, logo_dir)
        if ico:
            ip = icons_dir / f"{_safe_ico(key)}.ico"
            try:
                ip.write_bytes(ico); ico_path = str(ip)
            except Exception:
                ico_path = ""
        else:
            no_icon += 1

        ok = False
        if launch["kind"] == "url":
            ok = creator.make_url(str(out_dir / f"{name}.url"), launch["url"], ico_path)
        elif launch["kind"] == "copy":
            ok = creator.copy_lnk_with_icon(launch["src"],
                                            str(out_dir / f"{name}.lnk"), ico_path)
        else:  # lnk
            ok = creator.make_lnk(str(out_dir / f"{name}.lnk"), launch["target"],
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
            "failed": failed, "total": total,
            "win32com": WIN32COM}


def _safe_ico(key: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9._-]+", "_", key)[:180]
