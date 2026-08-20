"""services.scan_pc — skan bibliotek gier PC: Steam / GOG / Epic / Extra.

Port czystej logiki z wersji tkinter (SteamScanner + skanery modułowe +
dedup między źródłami). Zwraca listę słowników-gier gotowych do siatki.

Zależności: tylko stdlib (re/os/json/urllib/winreg) — bez GUI.

Gra (dict):
    name, source ("steam"|"gog"|"epic"|"extra"), game_dir, launch_exe,
    appid (steam), gog_id, epic_app_name, enabled, + pola pomocnicze.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path

from pylinks.core.naming import norm_title

try:
    import winreg  # type: ignore
    WINREG_OK = True
except Exception:  # nie-Windows / brak modułu
    winreg = None  # type: ignore
    WINREG_OK = False


# ── rejestr: helper ────────────────────────────────────────────────────────
def _reg_val(handle, name: str):
    if not WINREG_OK:
        return None
    try:
        v, _ = winreg.QueryValueEx(handle, name)
        return v
    except Exception:
        return None


# ── Steam: detekcja + biblioteki ───────────────────────────────────────────
def detect_steam_exe() -> str | None:
    """Znajduje Steam.exe w rejestrze (HKCU/HKLM)."""
    if not WINREG_OK:
        return None
    paths = [
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamExe"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    ]
    for root, key, val in paths:
        try:
            with winreg.OpenKey(root, key) as h:
                data, _ = winreg.QueryValueEx(h, val)
                p = Path(data) if val == "SteamExe" else Path(data) / "Steam.exe"
                if p.exists():
                    return str(p)
        except Exception:
            continue
    return None


def parse_libraryfolders_vdf(steam_exe: str) -> list[str]:
    """Parsuje libraryfolders.vdf → lista katalogów steamapps."""
    try:
        root = Path(steam_exe).parent
        vdf = root / "steamapps" / "libraryfolders.vdf"
        if not vdf.exists():
            vdf = root / "config" / "libraryfolders.vdf"
        if not vdf.exists():
            return []
        txt = vdf.read_text(encoding="utf-8", errors="replace")
        libs: list[str] = []
        for m in re.finditer(r'"path"\s+"((?:[^"\\]|\\.)*)"', txt):
            raw = m.group(1).replace("\\\\", "\\")
            lib = Path(raw) / "steamapps"
            if lib.is_dir():
                libs.append(str(lib))
        seen: set[str] = set()
        out: list[str] = []
        for p in libs:
            if p.lower() not in seen:
                seen.add(p.lower())
                out.append(p)
        return out
    except Exception:
        return []


class SteamScanner:
    def __init__(self, steam_exe: str, extra_lib_dirs: list[str] | None = None,
                 use_libraryfolders_vdf: bool = True,
                 api_key: str | None = None, steam_id64: str | None = None,
                 use_web_api: bool = True):
        self.steam_exe = steam_exe
        self.extra_lib_dirs = list(extra_lib_dirs or [])
        self.use_libraryfolders_vdf = use_libraryfolders_vdf
        self.api_key = api_key
        self.steam_id64 = steam_id64
        self.use_web_api = use_web_api

    def steamapps_dirs(self) -> list[Path]:
        dirs: list[Path] = []
        seen: set[str] = set()

        def _add(p: Path) -> None:
            try:
                if p.is_dir():
                    k = str(p).lower()
                    if k not in seen:
                        seen.add(k)
                        dirs.append(p)
            except Exception:
                pass

        if self.steam_exe:
            _add(Path(self.steam_exe).parent / "steamapps")
            if self.use_libraryfolders_vdf:
                for lib in parse_libraryfolders_vdf(self.steam_exe):
                    _add(Path(lib))
        for part in self.extra_lib_dirs:
            _add(Path(part))
        return dirs

    def fetch_owned_games(self) -> list[dict]:
        if not (self.use_web_api and self.api_key and self.steam_id64):
            return []
        try:
            url = (f"https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
                   f"?key={self.api_key}&steamid={self.steam_id64}"
                   f"&include_appinfo=true&format=json")
            with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}),
                timeout=30,
            ) as r:
                return json.loads(r.read()).get("response", {}).get("games", [])
        except Exception:
            return []

    def scan_installed(self) -> list[dict]:
        """Skanuje appmanifest_*.acf we wszystkich katalogach biblioteki."""
        games: list[dict] = []
        seen_appids: set[str] = set()
        for mdir in self.steamapps_dirs():
            try:
                for mf in sorted(mdir.glob("appmanifest_*.acf")):
                    try:
                        c = mf.read_text(encoding="utf-8", errors="replace")
                        ma = re.search(r'"appid"\s+"(\d+)"', c)
                        mn = re.search(r'"name"\s+"(.+?)"', c)
                        if not ma or not mn:
                            continue
                        appid = ma.group(1)
                        if appid in seen_appids:
                            continue
                        seen_appids.add(appid)
                        games.append(_mk_game(
                            name=mn.group(1), source="steam", appid=appid,
                            content=c))
                    except Exception:
                        continue
            except Exception:
                continue
        games.sort(key=lambda g: g["name"].lower())
        return games

    def resolve_game_dir(self, game: dict) -> Path | None:
        m = re.search(r'"installdir"\s+"(.+?)"', game.get("content", ""))
        if not m:
            return None
        sub = m.group(1)
        for lib in self.steamapps_dirs():
            cand = Path(lib) / "common" / sub
            if cand.is_dir():
                return cand
        return None


# ── Epic / GOG / Extra ─────────────────────────────────────────────────────
def _epic_manifests_dir() -> Path | None:
    pd = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
    d = Path(pd) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
    return d if d.is_dir() else None


def scan_epic_games() -> list[dict]:
    games: list[dict] = []
    d = _epic_manifests_dir()
    if not d:
        return games
    try:
        for f in sorted(d.glob("*.item")):
            try:
                data = json.loads(f.read_text(encoding="utf-8", errors="replace"))
                name = data.get("DisplayName") or data.get("MainGameAppName") or ""
                app_name = data.get("AppName") or data.get("MainGameAppName") or ""
                install = data.get("InstallLocation") or ""
                launch = data.get("LaunchExecutable") or ""
                if not name or not app_name:
                    continue
                exe_path = str(Path(install) / launch) if install and launch else ""
                games.append(_mk_game(
                    name=name, source="epic", game_dir=install or None,
                    launch_exe=exe_path or None, epic_app_name=app_name))
            except Exception:
                continue
    except Exception:
        pass
    return games


def scan_gog_games() -> list[dict]:
    """Gry GOG z rejestru (HKLM/HKCU ...\\GOG.com\\Games)."""
    if not WINREG_OK:
        return []
    games: list[dict] = []
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\GOG.com\Games"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\GOG.com\Games"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\GOG.com\Games"),
    ]
    seen_ids: set[str] = set()
    for root, key in roots:
        try:
            with winreg.OpenKey(root, key) as h:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(h, i)
                    except OSError:
                        break
                    i += 1
                    try:
                        with winreg.OpenKey(h, sub) as gh:
                            gid = _reg_val(gh, "gameID") or sub
                            if gid in seen_ids:
                                continue
                            seen_ids.add(gid)
                            name = _reg_val(gh, "gameName") or sub
                            path = _reg_val(gh, "path") or _reg_val(gh, "workingDir") or ""
                            exe = _reg_val(gh, "exe") or _reg_val(gh, "launchCommand") or ""
                            exe_full = (str(Path(path) / exe)
                                        if path and exe and not os.path.isabs(exe)
                                        else exe)
                            games.append(_mk_game(
                                name=name, source="gog", game_dir=path or None,
                                launch_exe=exe_full or None, gog_id=gid))
                    except Exception:
                        continue
        except Exception:
            continue
    return games


def scan_extra_dir(extra_path: str) -> list[dict]:
    games: list[dict] = []
    p = Path(extra_path)
    if not p.is_dir():
        return games
    for subdir in sorted(p.iterdir()):
        if subdir.is_dir():
            games.append(_mk_game(
                name=subdir.name, source="extra", game_dir=str(subdir)))
    return games


# ── fabryka wpisu gry ──────────────────────────────────────────────────────
def _mk_game(*, name: str, source: str, game_dir: str | None = None,
             launch_exe: str | None = None, appid=None, gog_id=None,
             epic_app_name=None, content: str = "") -> dict:
    return {
        "appid": appid, "name": name, "content": content,
        "game_dir": game_dir, "source": source,
        "launch_exe": launch_exe,
        "gog_id": gog_id, "epic_app_name": epic_app_name,
        "sgdb_results": [], "sgdb_id": None, "ambiguous": False,
        "candidates": [], "selected_idx": None,
        "icons_loaded": False, "enabled": True,
    }


# ── dedup między źródłami (steam > gog > epic > extra) ──────────────────────
def dedupe_pc_games(games: list[dict]) -> list[dict]:
    """Ten sam znormalizowany tytuł LUB katalog gry → zostaje źródło o
    wyższym priorytecie. Dedup działa TYLKO między różnymi źródłami."""
    prio = {"steam": 0, "gog": 1, "epic": 2, "extra": 3}
    ordered = sorted(games, key=lambda g: prio.get(g.get("source", "extra"), 9))
    seen_titles: dict[str, dict] = {}
    seen_dirs: dict[str, dict] = {}
    out: list[dict] = []
    for g in ordered:
        if g.get("source") == "rom":
            out.append(g)
            continue
        t = norm_title(g.get("name", ""))
        d = ""
        try:
            gd = g.get("game_dir") or ""
            d = str(Path(gd).resolve()).lower() if gd else ""
        except Exception:
            pass
        dup_of = (seen_titles.get(t) if t else None) or \
                 (seen_dirs.get(d) if d else None)
        if dup_of is not None and dup_of.get("source") == g.get("source"):
            dup_of = None
        if (dup_of is not None and g.get("appid") and dup_of.get("appid")
                and str(g["appid"]) != str(dup_of["appid"])):
            dup_of = None
        if dup_of is not None:
            continue
        if t:
            seen_titles[t] = g
        if d:
            seen_dirs[d] = g
        out.append(g)
    # przywróć pierwotną kolejność źródeł (sort stabilny po starym indeksie)
    idx = {id(g): i for i, g in enumerate(games)}
    out.sort(key=lambda g: idx.get(id(g), 1 << 30))
    return out


# ── orkiestracja: pełny skan PC z configu ──────────────────────────────────
def scan_pc_library(cfg: dict, *, resolve_dirs: bool = True) -> dict:
    """Pełny skan bibliotek PC na podstawie configu.

    Zwraca {"games": [...], "counts": {"steam":n,...}, "steam_exe": str}.
    """
    api_keys = cfg.get("api_keys", {})
    steam_exe = (cfg.get("steam_exe") or "").strip() or (detect_steam_exe() or "")

    scanner = SteamScanner(
        steam_exe=steam_exe,
        extra_lib_dirs=cfg.get("steam_lib_dirs", []),
        use_libraryfolders_vdf=cfg.get("use_libraryfolders_vdf", True),
        api_key=api_keys.get("steam_api_key") or None,
        steam_id64=api_keys.get("steam_id64") or None,
        use_web_api=cfg.get("use_steam_web_api", True),
    )

    steam_games = scanner.scan_installed() if steam_exe else []
    if resolve_dirs:
        for g in steam_games:
            gd = scanner.resolve_game_dir(g)
            if gd:
                g["game_dir"] = str(gd)
    # posiadane, ale NIE zainstalowane (Steam Web API) — dołączamy dla grafik/eksportu
    if cfg.get("steam_include_owned") and scanner.use_web_api:
        have = {g.get("appid") for g in steam_games}
        for o in scanner.fetch_owned_games():
            aid = str(o.get("appid", ""))
            if aid and aid not in have:
                steam_games.append(_mk_game(name=o.get("name", aid), source="steam",
                                            appid=aid))
                have.add(aid)
        steam_games.sort(key=lambda g: (g.get("name") or "").lower())

    extra_games: list[dict] = []
    if (cfg.get("extra_dir") or "").strip():
        extra_games += scan_extra_dir(cfg["extra_dir"].strip())
    for part in cfg.get("extra_dirs_list", []):
        if part:
            extra_games += scan_extra_dir(part)

    epic_games = scan_epic_games() if cfg.get("scan_epic", True) else []
    gog_games = scan_gog_games() if cfg.get("scan_gog", True) else []

    all_games = steam_games + extra_games + epic_games + gog_games
    all_games = dedupe_pc_games(all_games)

    counts = {"steam": 0, "gog": 0, "epic": 0, "extra": 0}
    for g in all_games:
        s = g.get("source", "")
        if s in counts:
            counts[s] += 1
    return {"games": all_games, "counts": counts, "steam_exe": steam_exe}
