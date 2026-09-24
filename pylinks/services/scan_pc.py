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

IS_WIN = os.name == "nt"

# Katalogi danych Steama na Linuksie. Nie ma tam rejestru, a binarka `steam`
# leży w /usr/bin — czyli NIE obok bibliotek gier, jak na Windows. Dlatego
# korzeń Steama wyszukujemy osobno, po znanych lokalizacjach (instalacja
# natywna, stary układ ~/.steam, Flatpak, Snap).
_LINUX_STEAM_ROOTS = (
    "~/.steam/steam",
    "~/.local/share/Steam",
    "~/.steam/root",
    "~/.var/app/com.valvesoftware.Steam/.local/share/Steam",
    "~/snap/steam/common/.local/share/Steam",
)


def steam_data_roots(steam_exe: str = "") -> list[Path]:
    """Korzenie instalacji Steama — katalogi, w których leżą steamapps/ i config/."""
    out: list[Path] = []
    seen: set[str] = set()

    def _add(p: Path) -> None:
        try:
            if p.is_dir():
                k = str(p).lower()
                if k not in seen:
                    seen.add(k)
                    out.append(p)
        except OSError:
            pass

    if steam_exe and IS_WIN:
        _add(Path(steam_exe).parent)
    if not IS_WIN:
        for raw in _LINUX_STEAM_ROOTS:
            _add(Path(raw).expanduser())
    return out


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
    """Znajduje binarkę Steama: rejestr na Windows, PATH na Linuksie.

    Na Linuksie brak wyniku NIE oznacza braku Steama — przy instalacji przez
    Flatpaka nie ma binarki w PATH, a gry i tak uruchamia się adresem
    `steam://rungameid/…` przez xdg-open (patrz services.export).
    """
    if not IS_WIN:
        import shutil
        return shutil.which("steam")
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
    """Parsuje libraryfolders.vdf → lista katalogów steamapps.

    Przeszukuje każdy znany korzeń Steama, bo na Linuksie może ich być kilka
    (natywny i Flatpak obok siebie), a `steam_exe` nic o nich nie mówi.
    """
    libs: list[str] = []
    for root in steam_data_roots(steam_exe):
        try:
            vdf = root / "steamapps" / "libraryfolders.vdf"
            if not vdf.exists():
                vdf = root / "config" / "libraryfolders.vdf"
            if not vdf.exists():
                continue
            txt = vdf.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r'"path"\s+"((?:[^"\\]|\\.)*)"', txt):
                raw = m.group(1).replace("\\\\", "\\")
                lib = Path(raw) / "steamapps"
                if lib.is_dir():
                    libs.append(str(lib))
        except Exception:
            continue
    seen: set[str] = set()
    out: list[str] = []
    for p in libs:
        if p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return out


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

        for root in steam_data_roots(self.steam_exe):
            _add(root / "steamapps")
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
    if not IS_WIN:
        return None          # brak launchera Epica na Linuksa — patrz _legendary_installed
    pd = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
    d = Path(pd) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
    return d if d.is_dir() else None


# Epic nie wydał launchera na Linuksa; gry z Epica instaluje tam Heroic albo
# legendary (CLI), które trzymają stan w tym samym formacie installed.json.
_LEGENDARY_INSTALLED = (
    "~/.config/legendary/installed.json",
    "~/.config/heroic/legendaryConfig/legendary/installed.json",
    "~/.var/app/com.heroicgameslauncher.hgl/config/heroic/legendaryConfig/legendary/installed.json",
)


def _scan_epic_linux() -> list[dict]:
    """Gry Epica zainstalowane przez Heroic/legendary."""
    games: list[dict] = []
    seen: set[str] = set()
    for raw in _LEGENDARY_INSTALLED:
        f = Path(raw).expanduser()
        if not f.is_file():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        for app_name, entry in data.items():
            if not isinstance(entry, dict) or app_name in seen:
                continue
            install = entry.get("install_path") or ""
            if not install or not _install_present(install):
                continue
            seen.add(app_name)
            exe = entry.get("executable") or ""
            games.append(_mk_game(
                name=entry.get("title") or app_name, source="epic",
                game_dir=install,
                launch_exe=str(Path(install) / exe.replace("\\", "/")) if exe else None,
                epic_app_name=app_name))
    return games


def _install_present(*paths) -> bool:
    """Czy instalacja gry realnie leży na dysku.

    Launchery trzymają wpisy o grach w rejestrze / manifestach i NIE sprzątają
    ich po ręcznym skasowaniu katalogu — bez tej kontroli skan pokazywał gry
    „duchy" (a skrót do nich nie był zgłaszany jako osierocony, tylko odtwarzany).

    Konserwatywnie: gdy weryfikacja jest niemożliwa — brak ścieżki albo DYSK
    NIEDOSTĘPNY (odpięty zewnętrzny, niezamapowany sieciowy) — zwracamy True,
    żeby nie skasować z listy gier, których chwilowo nie widać.
    """
    checkable = False
    for raw in paths:
        s = str(raw or "").strip().strip('"')
        if not s:
            continue
        try:
            pp = Path(s)
            drive = pp.drive
            if drive and not os.path.exists(drive + os.sep):
                return True          # dysk niedostępny → nie oceniamy
            checkable = True
            if pp.is_dir() or pp.is_file():
                return True
        except Exception:
            return True              # nie da się sprawdzić → zostawiamy
    return not checkable             # nic nie było do sprawdzenia → zostawiamy


def scan_epic_games() -> list[dict]:
    if not IS_WIN:
        return _scan_epic_linux()
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
                if not _install_present(install, exe_path):
                    continue          # wpis został, gry nie ma → to nie jest gra
                games.append(_mk_game(
                    name=name, source="epic", game_dir=install or None,
                    launch_exe=exe_path or None, epic_app_name=app_name))
            except Exception:
                continue
    except Exception:
        pass
    return games


# Gdzie lądują gry GOG-a na Linuksie: domyślny katalog instalatora MojoSetup,
# katalog używany przez GOG Library Managera i biblioteka Heroica.
_LINUX_GOG_DIRS = ("~/GOG Games", "~/GOG/games", "~/Games/gog")
_LINUX_GOG_HEROIC = (
    "~/.config/heroic/gog_store/installed.json",
    "~/.var/app/com.heroicgameslauncher.hgl/config/heroic/gog_store/installed.json",
)


def _gog_dir_entry(d: Path) -> dict | None:
    """Katalog gry GOG-a na Linuksie → wpis gry (albo None, gdy to nie gra).

    Natywne wydanie ma w korzeniu `start.sh`; identyfikator i tytuł bierzemy
    z `goggame-<id>.info`, gdy instalator go zostawił.
    """
    start = d / "start.sh"
    if not start.is_file():
        return None
    gid, name = "", d.name
    info = next(iter(sorted(d.glob("goggame-*.info"))), None)
    if info is not None:
        try:
            data = json.loads(info.read_text(encoding="utf-8", errors="replace"))
            gid = str(data.get("gameId") or "")
            name = data.get("name") or name
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
        if not gid:
            m = re.search(r"goggame-(\d+)\.info$", info.name)
            gid = m.group(1) if m else ""
    return _mk_game(name=name, source="gog", game_dir=str(d),
                    launch_exe=str(start), gog_id=gid or d.name)


def _scan_gog_linux() -> list[dict]:
    games: list[dict] = []
    seen: set[str] = set()
    for raw in _LINUX_GOG_DIRS:
        root = Path(raw).expanduser()
        if not root.is_dir():
            continue
        try:
            subdirs = sorted(p for p in root.iterdir() if p.is_dir())
        except OSError:
            continue
        for d in subdirs:
            g = _gog_dir_entry(d)
            if g and str(g.get("gog_id")) not in seen:
                seen.add(str(g.get("gog_id")))
                games.append(g)
    for raw in _LINUX_GOG_HEROIC:
        f = Path(raw).expanduser()
        if not f.is_file():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        for entry in (data.get("installed") or []):
            if not isinstance(entry, dict):
                continue
            gid = str(entry.get("appName") or "")
            install = entry.get("install_path") or ""
            if not gid or gid in seen or not _install_present(install):
                continue
            seen.add(gid)
            start = Path(install) / "start.sh"
            games.append(_mk_game(
                name=Path(install).name, source="gog", game_dir=install or None,
                launch_exe=str(start) if start.is_file() else None, gog_id=gid))
    return games


def scan_gog_games() -> list[dict]:
    """Gry GOG: rejestr na Windows, katalogi instalacji + Heroic na Linuksie."""
    if not IS_WIN:
        return _scan_gog_linux()
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
                            if not _install_present(path, exe_full):
                                continue   # wpis w rejestrze został, gry nie ma
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

    # Na Linuksie Steam bywa bez binarki w PATH (Flatpak/Snap) — o skanie
    # decyduje obecność katalogu danych, nie ścieżka do launchera.
    have_steam = bool(steam_exe) or bool(steam_data_roots(steam_exe))
    steam_games = scanner.scan_installed() if have_steam else []
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
