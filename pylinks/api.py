"""pylinks.api — most Python↔JS wystawiany do frontendu (pywebview js_api).

Cienka warstwa: metody wołane z JS jako `window.pywebview.api.<metoda>(...)`.
Cała logika docelowo trafia do pylinks/services/* — Api tylko orkiestruje i
zwraca wartości JSON-serializowalne. Faza 0: dowód działania stosu + realne
dane z silnika (presety ROM, config, paleta, skan istniejących skrótów).
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
from pathlib import Path

from pylinks import __version__
from pylinks.app_paths import SCRIPT_DIR, CACHE_DIR, LINKS_DIR, CONFIG_PATH
from pylinks.constants import C
from pylinks.config.defaults import _default_config
from pylinks.config.migrate import migrate_config
from pylinks.services import scan_pc, art
import pylinks.services.thumbserver as _thumbsrv
from pylinks.core import store as _store


def _write_config_atomic(raw: dict) -> None:
    """Zapis configu przez plik tymczasowy + os.replace.

    Zwykły write_text() obcina plik na starcie: awaria/zanik zasilania w trakcie
    zostawiała PUSTY config — czyli utratę kluczy API i systemów ROM.
    """
    import os
    tmp = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
    tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    if os.name != "nt":
        # Na Linuksie nie ma TPM-a ani DPAPI, więc klucze API leżą w configu
        # jawnym tekstem — odbierzmy do niego prawa przynajmniej reszcie
        # użytkowników. Prawa nadajemy PRZED replace, żeby nie było chwili,
        # w której gotowy plik ma domyślne 0644.
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
    os.replace(tmp, CONFIG_PATH)


def _load_config() -> dict:
    """Config do UŻYTKU wewnętrznego — sekrety odszyfrowane (DPAPI)."""
    cfg = None
    try:
        if CONFIG_PATH.exists():
            cfg = migrate_config(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except Exception:
        cfg = None
    if cfg is None:
        cfg = _default_config()
    _decrypt_secrets(cfg)
    _overlay_store(cfg)
    return cfg


def _overlay_store(cfg: dict) -> None:
    """Wkłada stan per-gra z bazy pod te same klucze, których używał config.

    Dzięki temu WSZYSTKIE odczyty (export, steam_export, kadr, nadpisania) zostają
    bez zmian — zmienia się tylko miejsce przechowywania. W samym config.json tych
    kluczy już nie ma: plik jest znowu małym plikiem ustawień.
    """
    try:
        ch = _store.all_choices(CACHE_DIR)
        icons, steam = {}, {}
        for key, modes in ch.items():
            ico = (modes.get("icon") or {}).get("icon")
            if ico:
                icons[key] = ico.get("url") or ico.get("source_id")
            sm = modes.get("steam") or {}
            if sm:
                steam[key] = {a: (v.get("url") or v.get("source_id"))
                              for a, v in sm.items() if v}
        cfg["selected_icon_keys"] = icons
        cfg["steam_art_by_key"] = steam
        cfg["icon_crop_by_key"] = _store.all_crops(CACHE_DIR)
        cfg["sgdb_override"] = _store.all_overrides(CACHE_DIR, "title")
        cfg["exe_override"] = _store.all_overrides(CACHE_DIR, "exe")
    except Exception:
        pass


# sekrety szyfrowane DPAPI (ścieżki w configu)
SECRET_PATHS = [
    ("api_keys", "sgdb_key"),
    ("api_keys", "steam_api_key"),
    ("api_keys", "steam_id64"),
    ("extra_sources", "igdb_client_id"),
    ("extra_sources", "igdb_client_secret"),
    ("extra_sources", "tgdb_key"),
    ("extra_sources", "ss_devid"),
    ("extra_sources", "ss_devpassword"),
    ("extra_sources", "ss_user"),
    ("extra_sources", "ss_password"),
]


def _get_path(d: dict, path):
    for k in path:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _set_path(d: dict, path, val) -> None:
    for k in path[:-1]:
        d = d.setdefault(k, {})
    d[path[-1]] = val


def _has_path(d: dict, path) -> bool:
    for k in path[:-1]:
        if not isinstance(d, dict) or k not in d:
            return False
        d = d[k]
    return isinstance(d, dict) and path[-1] in d


def _decrypt_secrets(cfg: dict) -> None:
    from pylinks.core import secure
    for p in SECRET_PATHS:
        v = _get_path(cfg, p)
        if v:
            _set_path(cfg, p, secure.unprotect(v))


def _deep_merge(dst: dict, patch: dict) -> None:
    """Rekurencyjny merge patch→dst. Listy i wartości nadpisywane w całości."""
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v


def _game_key(g: dict) -> str:
    """Stabilny klucz gry (do ikon/wyboru). Nie zależy od pozycji."""
    src = g.get("source", "pc")
    if src == "mame":
        # klucz po NAZWIE SETU: tytuł bywa długi i zmienia się między wersjami MAME
        return f"mame::{(g.get('set') or '').lower()}"
    if src == "rom":
        return f"rom::{(g.get('system') or '').lower()}::{(g.get('name') or '').strip().lower()}"
    if g.get("appid"):
        return f"{src}::app::{g['appid']}"
    if g.get("gog_id"):
        return f"{src}::gog::{g['gog_id']}"
    if g.get("epic_app_name"):
        return f"{src}::epic::{g['epic_app_name']}"
    return f"{src}::name::{(g.get('name') or '').strip().lower()}"


def _ico_to_data_uri(ico_path: str) -> "str | None":
    """Wczytuje .ico i zwraca największą klatkę jako PNG data-URI."""
    try:
        import base64
        from io import BytesIO
        from PIL import Image
        p = ico_path.split(",")[0].strip().strip('"')   # usuń ',0' i cudzysłowy
        im = Image.open(p)
        # wybierz największy rozmiar z .ico
        try:
            sizes = im.info.get("sizes")
            if sizes:
                im.size = max(sizes)
                im.load()
        except Exception:
            pass
        im = im.convert("RGBA")
        buf = BytesIO()
        im.save(buf, "PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None


def _ini_value(f, prefix: str) -> str:
    """Wartość klucza z pliku w stylu INI (.url, .desktop) — pierwsze trafienie."""
    try:
        for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.lower().startswith(prefix):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def _shortcut_icon_path(f) -> str:
    """Ścieżka ikony wskazanej przez skrót (.lnk / .url / .desktop)."""
    try:
        suf = f.suffix.lower()
        if suf == ".url":
            return _ini_value(f, "iconfile=")
        if suf == ".desktop":
            return _ini_value(f, "icon=")
        import pythoncom, win32com.client  # type: ignore
        pythoncom.CoInitialize()
        try:
            ico = win32com.client.Dispatch("WScript.Shell").CreateShortcut(str(f)).IconLocation or ""
        finally:
            pythoncom.CoUninitialize()
        return ico.split(",")[0].strip().strip('"')
    except Exception:
        return ""


# Rozszerzenia skrótów obu systemów naraz: LINKS/ bywa na dysku dzielonym
# z Windowsem, więc .lnk/.url i .desktop mogą tam leżeć obok siebie.
_LINK_EXTS = (".lnk", ".url", ".desktop")

_ICON_URI_CACHE: dict = {}


def _shortcut_icon_uri(f) -> "str | None":
    """Ikona (.ico) wskazana przez skrót .lnk/.url → PNG data-URI (z cache)."""
    key = str(f)
    if key in _ICON_URI_CACHE:
        return _ICON_URI_CACHE[key]
    ico = ""
    try:
        suf = f.suffix.lower()
        if suf == ".url":
            ico = _ini_value(f, "iconfile=")
        elif suf == ".desktop":
            ico = _ini_value(f, "icon=")
        else:  # .lnk
            try:
                import pythoncom, win32com.client  # type: ignore
                pythoncom.CoInitialize()
                try:
                    sh = win32com.client.Dispatch("WScript.Shell")
                    ico = sh.CreateShortcut(str(f)).IconLocation or ""
                finally:
                    pythoncom.CoUninitialize()
            except Exception:
                ico = ""
    except Exception:
        ico = ""
    uri = _ico_to_data_uri(ico) if ico else None
    _ICON_URI_CACHE[key] = uri
    return uri


def _slim_game(g: dict) -> dict:
    """Wpis gry dla frontendu — bez ciężkiego pola `content` (ACF)."""
    return {
        "key": _game_key(g),
        "name": g.get("name", ""),
        "source": g.get("source", ""),
        "game_dir": g.get("game_dir"),
        "launch_exe": g.get("launch_exe"),
        "appid": g.get("appid"),
        "enabled": g.get("enabled", True),
    }


class Api:
    """Metody dostępne z JS. Zwracają dict/list/str (JSON-serializowalne)."""

    def __init__(self) -> None:
        # ostatni wynik skanu PC (pełne wpisy z `content`) — do dalszych
        # operacji (ikony, eksport). Frontend dostaje odchudzoną wersję.
        self._pc_games: list[dict] = []
        self._rom_games: list[dict] = []
        self._mame_games: list[dict] = []      # biblioteka arcade (po grupowaniu)
        # nazwa pliku skrótu → klucz gry; budowany leniwie, kasowany po skanie
        self._stem_index: dict | None = None
        self._set_to_group: dict | None = None   # zestaw MAME → klucz grupy
        # cache upieczonych miniatur w RAM: (key,mode,fit,offset) → data-URI
        self._baked: dict = {}
        # ostatnie ręczne zapytanie/ID per gra (do zapamiętania dopasowania SGDB)
        self._pending: dict = {}
        # zadanie w tle (pobierz/stwórz wszystko) — stan współdzielony z wątkiem
        self._job: dict = {"running": False, "kind": "", "done": 0, "total": 0,
                           "new": 0, "cached": 0, "failed": 0, "current": "",
                           "phase": "", "cancel": False, "result": None}

    # --- diagnostyka mostu ---
    def ping(self, msg: str = "") -> dict:
        return {"ok": True, "echo": msg, "ts": time.time(),
                "python": "PyLinks engine"}

    # --- meta / motyw ---
    def get_info(self) -> dict:
        cfg = _load_config()
        return {
            "version": __version__,
            "theme": C,
            "paths": {
                "root":   str(SCRIPT_DIR),
                "cache":  str(CACHE_DIR),
                "links":  str(LINKS_DIR),
                "config": str(CONFIG_PATH),
            },
            "config_exists": CONFIG_PATH.exists(),
            "rom_enabled": bool(cfg.get("rom_support", {}).get("enabled", False)),
            "thumb_size": int(cfg.get("thumb_size", 160) or 160),
        }

    def set_exe_override(self, key: str, path: str) -> dict:
        """Ręczny plik uruchamiający dla gry (Extra/GOG) — używany przy eksporcie."""
        try:
            _store.set_override(CACHE_DIR, key, "exe", path or None)
        except Exception as e:
            return {"ok": False, "reason": str(e)}
        return {"ok": True}

    @staticmethod
    def _title_override(key: str, cfg: dict) -> str:
        """Zapamiętane dopasowanie SGDB (query/ID) dla gry — wspólne dla obu trybów."""
        store = cfg.get("sgdb_override", {}) or {}
        return str(store.get(key, "") or "") if isinstance(store, dict) else ""

    def set_thumb_size(self, px) -> dict:
        try:
            px = max(96, min(320, int(px)))
        except Exception:
            px = 160
        self._save_config_value("thumb_size", px)
        return {"ok": True, "thumb_size": px}


    # --- presety ROM (z silnika) ---

    # --- biblioteka: skan istniejących skrótów LINKS/<platforma>/ ---
    def list_platforms(self) -> list:
        """Platformy = podfoldery LINKS/ z liczbą skrótów (bez Kolekcje)."""
        out = []
        if LINKS_DIR.is_dir():
            for d in sorted(LINKS_DIR.iterdir()):
                if not d.is_dir() or d.name.lower() in ("kolekcje", "_collections"):
                    continue
                n = sum(1 for f in d.iterdir()
                        if f.suffix.lower() in _LINK_EXTS)
                out.append({"name": d.name, "count": n})
        return out

    def list_games(self, platform: str = "") -> list:
        """Gry (skróty) danej platformy — do siatki. Faza 0: nazwa + platforma."""
        out = []
        base = LINKS_DIR / platform if platform else None
        dirs = [base] if base else (
            [d for d in LINKS_DIR.iterdir() if d.is_dir()]
            if LINKS_DIR.is_dir() else [])
        for d in dirs:
            if not d or not d.is_dir():
                continue
            for f in sorted(d.iterdir()):
                if f.suffix.lower() in _LINK_EXTS:
                    stem = f.stem
                    # Skrót arcade NIESIE nazwę zestawu w swoich argumentach —
                    # to źródło pewne. Dopasowanie po nazwie pliku jest zgadywaniem
                    # i myliło tytuły zbieżne („Super Street Fighter II" trafiało
                    # w bootleg na Mega Drive zamiast w automat).
                    key = self._key_from_args(f) or self._key_for_stem(stem, d.name)
                    out.append({"name": stem, "platform": d.name,
                                "icon": _shortcut_icon_uri(f),
                                # klucz gry pozwala kliknąć skrót i ZMIENIĆ ikonę;
                                # bez niego kafelek w tym widoku był martwy
                                "key": key,
                                "source": "link", "file": str(f)})
        return out

    def _key_from_args(self, f) -> str:
        """Klucz gry odczytany z ARGUMENTÓW skrótu (dotyczy arcade: `… <set>`)."""
        if not self._mame_games:
            return ""
        if self._set_to_group is None:
            m = {}
            for g in self._mame_games:
                gk = _game_key(g)
                for v in g.get("variants", []) or ():
                    if v.get("name"):
                        m[v["name"].lower()] = gk
                if g.get("set"):
                    m[g["set"].lower()] = gk
            self._set_to_group = m
        try:
            st = self._lnk_set(f)
        except Exception:
            return ""
        return self._set_to_group.get((st or "").lower(), "")

    def _key_for_stem(self, stem: str, platform: str = "") -> str:
        """Klucz gry dla nazwy pliku skrótu (odwrotność nazewnictwa eksportu)."""
        from pylinks.core.naming import safe_name
        import re as _re

        def norm(x):
            return _re.sub(r"[^a-z0-9]+", "", (x or "").lower())

        if self._stem_index is None:
            idx = {}
            for g in (list(self._pc_games) + list(self._rom_games)
                      + list(self._mame_games)):
                key = _game_key(g)
                if not key:
                    continue
                names = [g.get("name", "")]
                if g.get("source") == "mame":
                    from pylinks.services import mame as _m
                    names.append(_m.primary_title(g.get("name", "")))
                    names.append(_m.clean_title(g.get("name", "")))
                for n in names:
                    for cand in (n, safe_name(n)):
                        k = norm(cand)
                        if k:
                            idx.setdefault(k, key)
            self._stem_index = idx
        return self._stem_index.get(norm(stem), "")

    def relink_icons(self, items: list) -> dict:
        """Przepiekanie ikon dla WIELU skrótów naraz: [{key, file}, …].

        Skróty zrobione zanim gra dostała okładkę (albo przed poprawką klucza
        grafiki) zostają z gołą ikoną i same się nie naprawią — trzeba je przejść
        raz jeszcze. COM inicjalizujemy RAZ na całą paczkę, nie na skrót.
        """
        from pylinks.core import log as _log
        try:
            import pythoncom
            pythoncom.CoInitialize()
            co = True
        except Exception:
            co = False
        done, failed = 0, []
        try:
            for it in items or ():
                key, f = (it or {}).get("key", ""), (it or {}).get("file", "")
                if not key or not f:
                    failed.append({"file": f, "reason": "nierozpoznana gra"})
                    continue
                r = self.relink_icon(key, f)
                if r.get("ok"):
                    done += 1
                else:
                    failed.append({"file": Path(f).name,
                                   "reason": r.get("reason", "")})
        finally:
            if co:
                try:
                    import gc
                    gc.collect()
                    pythoncom.CoUninitialize()
                except Exception:
                    pass
        _log.write("links", f"naprawa ikon: {done} ok, {len(failed)} nieudanych")
        return {"ok": True, "done": done, "failed": failed}

    def relink_icon(self, key: str, file: str = "") -> dict:
        """Podmienia ikonę w ISTNIEJĄCYM skrócie — po wyborze grafiki w LINKS.

        Bez tego wybór w tym widoku zmieniał tylko podgląd: plik .lnk na dysku
        dalej wskazywał starą ikonę.
        """
        from pylinks.services import export as _exp
        from pylinks.core import log as _log
        pth = Path(file) if file else None
        if not pth or not pth.is_file():
            return {"ok": False, "reason": "nie znaleziono pliku skrótu"}
        cfg = _load_config()
        try:
            # Gdy okładki jeszcze nie ma (np. skrót zrobiony przed jej pobraniem),
            # dociągamy ją tutaj, zamiast odmawiać.
            if not art.has_cover(CACHE_DIR, key, "icon"):
                g = self._find_game(key)
                if g:
                    art.fetch_cover(cfg, CACHE_DIR, key, g, "icon")
            fit, off = self._crop_for(key, cfg)
            # Bez parametrów grzbietu przepieczenie ZDEJMOWAŁO go z gotowej ikony
            # — zgłoszone na Dreamcaście po edycji istniejącego skrótu.
            data = _exp.bake_ico(CACHE_DIR, key, fit, off,
                                 **_exp.bake_args(cfg, self._find_game(key) or {}))
            if not data:
                return {"ok": False, "reason": "brak okładki dla tej gry "
                                               "— wybierz grafikę ręcznie"}
            ico = _exp.write_cache_ico(CACHE_DIR / "icons", key, data)
        except Exception as e:
            return {"ok": False, "reason": str(e)}
        if pth.suffix.lower() == ".url":
            try:                      # .url trzyma ikonę jako zwykły wpis tekstowy
                txt = pth.read_text(encoding="utf-8", errors="replace")
                lines = [l for l in txt.splitlines()
                         if not l.lower().startswith(("iconfile=", "iconindex="))]
                lines += [f"IconFile={ico}", "IconIndex=0"]
                pth.write_text(chr(10).join(lines) + chr(10), encoding="utf-8")
            except Exception as e:
                return {"ok": False, "reason": str(e)}
            # Podgląd ikony jest zapamiętywany PO ŚCIEŻCE skrótu i nigdy nie był
            # unieważniany — plik .lnk dostawał nową ikonę, a siatka pokazywała
            # w kółko starą. Stąd „pobiera się, nie wyświetla".
            _ICON_URI_CACHE.pop(str(pth), None)
            _log.write("links", f"ikona podmieniona: {pth.name}")
            return {"ok": True, "icon": ico}
        if pth.suffix.lower() == ".desktop":
            try:                      # .desktop też jest zwykłym tekstem
                lines = [l for l in pth.read_text(encoding="utf-8",
                                                  errors="replace").splitlines()
                         if not l.lower().startswith("icon=")]
                lines.append(f"Icon={ico}")
                pth.write_text(chr(10).join(lines) + chr(10), encoding="utf-8")
                os.chmod(pth, 0o755)
            except Exception as e:
                return {"ok": False, "reason": str(e)}
            _ICON_URI_CACHE.pop(str(pth), None)
            _log.write("links", f"ikona podmieniona: {pth.name}")
            return {"ok": True, "icon": ico}
        try:
            import pythoncom, win32com.client  # type: ignore
            pythoncom.CoInitialize()
        except Exception:
            return {"ok": False, "reason": "brak pywin32"}
        try:
            sh = win32com.client.Dispatch("WScript.Shell")
            sc = sh.CreateShortcut(str(pth))
            sc.IconLocation = f"{ico},0"
            sc.Save()
        except Exception as e:
            return {"ok": False, "reason": str(e)}
        finally:
            try:
                import gc
                gc.collect()
                pythoncom.CoUninitialize()
            except Exception:
                pass
        _ICON_URI_CACHE.pop(str(pth), None)   # patrz niżej: bez tego stara grafika
        _log.write("links", f"ikona podmieniona: {pth.name}")
        return {"ok": True, "icon": ico}

    # --- skan bibliotek PC (Steam/GOG/Epic/Extra) ---
    def scan_pc(self) -> dict:
        """Uruchamia pełny skan PC i zwraca odchudzone wpisy + liczniki.

        UWAGA: synchronicznie (Steam Web API może chwilę potrwać). Frontend
        woła to asynchronicznie (Promise) i pokazuje spinner.
        """
        cfg = _load_config()
        res = scan_pc.scan_pc_library(cfg)
        self._pc_games = self._dedupe_by_key(res["games"])
        self._stem_index = None
        # Zwracamy ZDEDUPOWANĄ listę (tę samą, na której działa backend/eksport)
        # — inaczej UI renderował duplikaty kart o WSPÓLNYM kluczu (jedna
        # tożsamość zaznaczenia), a operacje ruszały tylko pierwszy rekord.
        return {
            "ok": True,
            "counts": res["counts"],
            "total": len(self._pc_games),
            "steam_exe": res.get("steam_exe", ""),
            "games": [_slim_game(g) for g in self._pc_games],
        }


    def _save_override(self, key: str) -> None:
        """Utrwala ostatnie ręczne zapytanie/ID dla gry (nadpisanie tytułu)."""
        q = (self._pending.get(key) or "").strip()
        if q:
            _store.set_override(CACHE_DIR, key, "title", q)

    def _find_game(self, key: str) -> dict | None:
        for g in self._pc_games:
            if _game_key(g) == key:
                return g
        for g in self._rom_games:
            if _game_key(g) == key:
                return g
        for g in self._mame_games:
            if _game_key(g) == key:
                return g
        return None

    # --- skan ROM-ów (config.rom_support) ---
    def scan_roms(self) -> dict:
        """Skanuje systemy ROM i zwraca {enabled, systems:[...], total, spine}."""
        from pylinks.services import scan_roms as _sr
        from pylinks.core import spine as _spine
        cfg = _load_config()
        res = _sr.scan_roms(cfg)
        # mapa system → (emulator, launch_args) do budowy komendy przy eksporcie
        sysmap = {s.get("name", ""): s for s in cfg.get("rom_support", {}).get("systems", [])}
        self._rom_games = []
        self._stem_index = None
        for s in res.get("systems", []):
            sc = sysmap.get(s["name"], {})
            for g in s.get("games", []):
                g["source"] = "rom"
                g["emulator"] = sc.get("emulator", "")
                g["launch_args"] = sc.get("launch_args", "")
                self._rom_games.append(g)
        # grzbiety: globalny przełącznik + logo per system (data-URI)
        spine_on = bool(cfg.get("icon_platform_spine", False))
        spine_side = cfg.get("icon_spine_side", "left")
        logo_dir = cfg.get("platform_logo_dir", "")
        out_sys = []
        for s in res.get("systems", []):
            logo = _spine.platform_logo_uri(logo_dir, s["name"]) if spine_on else None
            out_sys.append({
                "name": s["name"], "count": s["count"], "logo": logo,
                "games": [{"key": _game_key(dict(g, source="rom")),
                           "name": g["name"], "system": g.get("system", ""),
                           "path": g.get("path", "")} for g in s["games"]],
            })
        return {"enabled": res.get("enabled", False),
                "total": res.get("total", 0), "systems": out_sys,
                "spine": {"enabled": spine_on, "side": spine_side,
                          "frac": float(cfg.get("icon_spine_frac", 0.22) or 0.22)}}


    # --- warianty logo grzbietu + skala (edytor „ile % zajmuje logo") ---
    def spine_logo_info(self) -> dict:
        """Stan edytora grzbietów: styl, skala, dostępne platformy, cache styli."""
        from pylinks.services import spine_logos as _sl
        from pylinks.core import spine as _spine
        cfg = _load_config()
        logo_dir = cfg.get("platform_logo_dir", "")
        style = cfg.get("spine_logo_style", "Light - Color")
        scale = float(cfg.get("spine_logo_scale", 0.85) or 0.85)
        frac = float(cfg.get("icon_spine_frac", 0.22) or 0.22)
        # platformy: te faktycznie w kolekcji ROM (jeśli po skanie) + z mapy
        have = []
        seen = set()
        for g in getattr(self, "_rom_games", []):
            sysname = g.get("system", "")
            # klucz = WŁASNA nazwa systemu. Kluczowanie po nazwie kanonicznej
            # zjadało platformy z aliasem (SNESMSU1→SNES, SEGACD→MD): znikały
            # z listy, więc nie dało się nadać im własnego logo.
            key = _spine.logo_key_for(sysname)
            if not key or key in seen:
                continue
            label = _sl.target_for(sysname)
            if not label:
                continue
            seen.add(key)
            have.append({"key": key,
                         "label": label if key == _spine.spine_canon_key(sysname)
                                  else f"{sysname} ({label})",
                         "system": sysname})
        if not have:
            for k, tgt in _sl.PLATFORM_LOGO_TARGET.items():
                have.append({"key": k, "label": tgt, "system": k})
        # aktywne logo per platforma (data-URI) — do podglądu
        for p in have:
            p["active"] = _spine.platform_logo_uri(logo_dir, p["key"])
        return {"style": style, "scale": scale, "frac": frac,
                "styles": _sl.SPINE_LOGO_STYLES,
                "cached_styles": _sl.cached_styles(logo_dir),
                "platforms": have, "logo_dir": logo_dir}

    def spine_logo_variants(self, platform: str, style: str = "") -> list:
        """Warianty logo danej platformy (miniatury data-URI) z lokalnego cache."""
        from pylinks.services import spine_logos as _sl
        cfg = _load_config()
        logo_dir = cfg.get("platform_logo_dir", "")
        style = style or cfg.get("spine_logo_style", "Light - Color")
        out = []
        for v in _sl.list_variants(logo_dir, platform, style):
            uri = _sl.variant_datauri(v["source"])
            if uri:
                out.append({"name": v["name"], "source": v["source"], "thumb": uri})
        return out

    def set_platform_logo(self, platform: str, source: str) -> dict:
        """Instaluje wybrany wariant jako aktywne logo platformy."""
        from pylinks.services import spine_logos as _sl
        from pylinks.core import spine as _spine
        cfg = _load_config()
        logo_dir = cfg.get("platform_logo_dir", "")
        ok = _sl.install_variant(logo_dir, platform, source)
        uri = _spine.platform_logo_uri(logo_dir, platform) if ok else None
        return {"ok": ok, "active": uri}

    def set_spine_logo_scale(self, scale: float) -> dict:
        s = max(0.30, min(1.0, float(scale)))
        self._save_config_value("spine_logo_scale", s)
        return {"ok": True, "scale": s}

    def set_spine_frac(self, frac: float) -> dict:
        """Szerokość grzbietu jako % szerokości ikony (0.12–0.40)."""
        f = max(0.12, min(0.40, float(frac)))
        self._save_config_value("icon_spine_frac", f)
        return {"ok": True, "frac": f}

    def set_spine_logo_style(self, style: str) -> dict:
        from pylinks.services import spine_logos as _sl
        if style in _sl.SPINE_LOGO_STYLES.values():
            self._save_config_value("spine_logo_style", style)
            return {"ok": True, "style": style}
        return {"ok": False}

    def download_logo_variants(self, style: str = "") -> dict:
        """Pobiera zestaw wariantów danej kolorystyki — W TLE, z paskiem postępu.

        Zestaw to ~2000 plików: wcześniej szło to synchronicznie przez most, więc
        okno zamierało i nic nie było widać. Teraz leci jako zwykłe zadanie
        (`job_status`), tak jak pobieranie grafik."""
        import threading
        from pylinks.services import spine_logos as _sl
        if self._job_busy():
            return {"started": False, "reason": "zadanie już trwa"}
        cfg = _load_config()
        logo_dir = cfg.get("platform_logo_dir", "")
        style = style or cfg.get("spine_logo_style", "Light - Color")
        if not logo_dir:
            return {"started": False, "reason": "brak folderu logotypów"}
        self._job_reset("logos", 0)
        self._job["phase"] = style

        def _worker():
            def _prog(done, total):
                self._job["done"], self._job["total"] = done, total
                if self._job.get("cancel"):
                    raise KeyboardInterrupt
            try:
                got, total = _sl.download_variants(logo_dir, style, progress=_prog)
            except KeyboardInterrupt:
                got, total = self._job["new"], self._job["total"]
            except Exception:
                got, total = 0, 0
            self._job["new"] = got
            self._job["running"] = False
            self._job["result"] = {"kind": "logos", "downloaded": got,
                                   "total": total, "style": style}

        threading.Thread(target=_worker, daemon=True).start()
        return {"started": True, "style": style}

    def spine_preview(self, platform: str, scale: float = None,
                      frac: float = None) -> dict:
        """Podgląd grzbietu na przykładowej ikonie (kwadrat) — data-URI PNG."""
        from pylinks.core import spine as _spine
        import base64
        from io import BytesIO
        try:
            from PIL import Image, ImageDraw
        except Exception:
            return {"ok": False}
        cfg = _load_config()
        logo_dir = cfg.get("platform_logo_dir", "")
        side = cfg.get("icon_spine_side", "left")
        sc = float(cfg.get("spine_logo_scale", 0.85) if scale is None else scale)
        fr = float(cfg.get("icon_spine_frac", 0.22) if frac is None else frac)
        fr = max(0.12, min(0.40, fr))
        # przykładowy plakat (gradient) jako podkład
        s = 256
        base = Image.new("RGBA", (int(s * 0.72), s), (0, 0, 0, 255))
        d = ImageDraw.Draw(base)
        for y in range(s):
            t = y / s
            d.line([(0, y), (base.width, y)],
                   fill=(int(40 + 60 * t), int(50 + 40 * t), int(90 + 50 * t), 255))
        img = _spine.add_platform_spine(base, platform, side, frac=fr,
                                        logo_dir=logo_dir, logo_scale=sc)
        if img is None:
            return {"ok": False}
        buf = BytesIO(); img.convert("RGBA").save(buf, "PNG")
        uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
        return {"ok": True, "preview": uri, "scale": sc, "frac": fr}


    # --- eksport skrótów (.lnk/.url + ikony .ico do LINKS/<platforma>/) ---


    def export_keys(self, keys: list) -> dict:
        """Eksportuje tylko wskazane gry (po kluczach)."""
        want = set(keys or [])
        games = [g for g in (self._pc_games + self._rom_games)
                 if _game_key(g) in want]
        return self._export(games)

    def export_steam(self, keys: list) -> dict:
        """Dodaje wskazane gry do biblioteki Steam (shortcuts.vdf + grafiki +
        kolekcje per platforma). Wymaga ZAMKNIĘTEGO Steama."""
        from pylinks.services import steam_export as _se
        want = set(keys or [])
        games = [g for g in (self._pc_games + self._rom_games)
                 if _game_key(g) in want]
        if not games:
            return {"ok": False, "reason": "brak gier"}
        return _se.export_to_steam(_load_config(), games, CACHE_DIR)

    def steam_cleanup(self, dry_run: bool = True) -> dict:
        """Usuwa z biblioteki Steam wpisy dublujące gry, które Steam już ma.

        Chodzi o skróty `steam.exe -applaunch <appid>` — dodane omyłkowo jako
        „Non-Steam", przez co każda gra Steam widniała dwa razy. Wymaga
        ZAMKNIĘTEGO Steama (inaczej nadpisze plik przy wyjściu).
        """
        import shutil, uuid, os
        from datetime import datetime
        from pylinks.services import steam_export as _se
        cfg = _load_config()
        dirs = _se.find_userdata_configs(cfg.get("steam_exe", ""),
                                         cfg.get("steam_userdata_dir", ""))
        if not dirs:
            return {"ok": False, "reason": "nie znaleziono userdata Steam"}
        vdf = dirs[0] / "shortcuts.vdf"
        entries = _se.read_shortcuts_vdf(vdf)
        bad = _se.find_steam_selflinks(entries)
        names = [_se._entry_name(e) for e in bad]
        if dry_run:
            return {"ok": True, "dry_run": True, "found": len(bad),
                    "total": len(entries), "names": names[:50],
                    "steam_running": _se.is_steam_running()}
        if _se.is_steam_running():
            return {"ok": False, "reason": "zamknij Steam przed sprzątaniem",
                    "found": len(bad)}
        keep = [e for e in entries if e not in bad]
        try:
            shutil.copy2(vdf, vdf.with_name(
                f"shortcuts.vdf.bak-{datetime.now():%Y%m%d-%H%M%S}"))
            tmp = vdf.with_name(f"shortcuts.tmp-{uuid.uuid4().hex}.vdf")
            tmp.write_bytes(_se.write_shortcuts_vdf(keep))
            os.replace(tmp, vdf)
        except Exception as ex:
            return {"ok": False, "reason": str(ex)}
        return {"ok": True, "removed": len(bad), "left": len(keep), "names": names[:50]}

    def _steam_config_dir(self, cfg: dict = None):
        from pylinks.services import steam_export as _se
        cfg = cfg or _load_config()
        dirs = _se.find_userdata_configs(cfg.get("steam_exe", ""),
                                         cfg.get("steam_userdata_dir", ""))
        return dirs[0] if dirs else None

    def steam_groups(self) -> dict:
        """Kolekcje Steam + stan ich pozycji.

        Dla każdej podajemy, ile pozycji faktycznie ma odpowiednik w `shortcuts.vdf`
        i ile z nich wskazuje na NIEISTNIEJĄCY plik. Kolekcje rosną same: usunięcie
        gry z dysku nie rusza ani skrótu Steam, ani przynależności do kolekcji,
        więc po latach zostają w nich pozycje-widma.
        """
        from pylinks.services import steam_export as _se
        cfg = _load_config()
        cd = self._steam_config_dir(cfg)
        if not cd:
            return {"ok": False, "reason": "nie znaleziono userdata Steam"}
        coll = _se.read_collections(cd)
        entries = _se.read_shortcuts_vdf(cd / "shortcuts.vdf")
        by_appid, dead = {}, set()
        for e in entries:
            a = int(e.get("appid", 0)) & 0xFFFFFFFF
            by_appid[a] = e
            exe = (e.get("Exe") or e.get("exe") or "").strip('"')
            if exe and not Path(exe).exists():
                dead.add(a)
        # etykiety, które nadajemy sami — po nich poznajemy „nasze" kolekcje
        nasze = {"Non-Steam", "Windows", "Gog", "Epic", "Steam"}
        nasze |= {g.get("system", "") for g in self._rom_games if g.get("system")}
        out = []
        for name, v in coll.items():
            ids = [int(a) & 0xFFFFFFFF for a in v.get("added") or []]
            znane = [a for a in ids if a in by_appid]
            # Identyfikator BEZ wpisu w shortcuts.vdf znaczy dwie ROZŁĄCZNE rzeczy:
            # * appid ≥ 0x80000000 → skrót „Non-Steam", którego już nie ma = śmieć
            #   po zmianie ścieżki albo po usunięciu gry;
            # * mniejszy → zwyczajna gra ze Steama, dodana do kolekcji ręcznie.
            #   Takich NIE WOLNO liczyć jako zepsute (kolekcje typu „Yakuza").
            brak = [a for a in ids if a not in by_appid]
            smieci = [a for a in brak if a >= 0x80000000]
            gry_steam = [a for a in brak if a < 0x80000000]
            out.append({"name": name, "total": len(ids), "known": len(znane),
                        "dead": len([a for a in znane if a in dead]),
                        "stale": len(smieci), "steam_games": len(gry_steam),
                        "ours": name in nasze})
        out.sort(key=lambda x: (-x["total"], x["name"].lower()))
        return {"ok": True, "groups": out, "entries": len(entries),
                "dead_entries": len(dead), "config_dir": str(cd),
                "steam_running": _se.is_steam_running()}

    def steam_remove_groups(self, names: list, drop_entries: bool = True,
                            dry_run: bool = True) -> dict:
        """Kasuje CAŁE kolekcje Steam, opcjonalnie z ich pozycjami „Non-Steam".

        `drop_entries=False` zostawia gry w bibliotece, znika sama kolekcja.
        Wymaga ZAMKNIĘTEGO Steama — inaczej nadpisze pliki przy wyjściu.
        """
        from pylinks.services import steam_export as _se
        from pylinks.core import log as _log
        want = [str(x) for x in (names or []) if x]
        if not want:
            return {"ok": False, "reason": "nie wskazano kolekcji"}
        cd = self._steam_config_dir()
        if not cd:
            return {"ok": False, "reason": "nie znaleziono userdata Steam"}
        coll = _se.read_collections(cd)
        vdf = cd / "shortcuts.vdf"
        entries = _se.read_shortcuts_vdf(vdf)
        have = {int(e.get("appid", 0)) & 0xFFFFFFFF for e in entries}
        # pozycje NALEŻĄCE WYŁĄCZNIE do kasowanych kolekcji — gra w dwóch
        # kolekcjach nie może zniknąć razem z jedną z nich
        inne = set()
        for n, v in coll.items():
            if n not in want:
                inne |= {int(a) & 0xFFFFFFFF for a in v.get("added") or []}
        do_usuniecia = set()
        for n in want:
            for a in (coll.get(n) or {}).get("added") or []:
                a = int(a) & 0xFFFFFFFF
                if a in have and a not in inne:
                    do_usuniecia.add(a)
        nazwy = [_se._entry_name(e) for e in entries
                 if (int(e.get("appid", 0)) & 0xFFFFFFFF) in do_usuniecia]
        if dry_run:
            return {"ok": True, "dry_run": True, "groups": want,
                    "entries": len(do_usuniecia) if drop_entries else 0,
                    "names": nazwy[:50], "kept_shared": len(inne & have),
                    "steam_running": _se.is_steam_running()}
        if _se.is_steam_running():
            return {"ok": False, "reason": "zamknij Steam przed sprzątaniem"}
        usuniete = _se.remove_shortcuts(vdf, do_usuniecia) if drop_entries else 0
        res = _se.delete_collections(cd, want)
        if not res.get("ok"):
            return {"ok": False, "reason": res.get("error") or "błąd kolekcji",
                    "removed_entries": usuniete}
        _log.write("steam", f"usunieto kolekcje {res['deleted']} "
                            f"+ {usuniete} pozycji")
        return {"ok": True, "deleted": res["deleted"], "removed_entries": usuniete}

    def steam_clean_collections(self, dry_run: bool = True) -> dict:
        """Usuwa z kolekcji identyfikatory skrótów, których już nie ma.

        Dotyczy WYŁĄCZNIE appid ≥ 0x80000000 (skróty „Non-Steam") bez wpisu
        w `shortcuts.vdf`. Zwykłe gry ze Steama zostają nietknięte — w kolekcjach
        typu „Yakuza" to normalna, poprawna zawartość.
        """
        from pylinks.services import steam_export as _se
        from pylinks.core import log as _log
        cd = self._steam_config_dir()
        if not cd:
            return {"ok": False, "reason": "nie znaleziono userdata Steam"}
        coll = _se.read_collections(cd)
        have = {int(e.get("appid", 0)) & 0xFFFFFFFF
                for e in _se.read_shortcuts_vdf(cd / "shortcuts.vdf")}
        plan, razem = [], 0
        for name, v in coll.items():
            ids = [int(a) & 0xFFFFFFFF for a in v.get("added") or []]
            smieci = [a for a in ids if a >= 0x80000000 and a not in have]
            if smieci:
                plan.append({"name": name, "remove": len(smieci),
                             "left": len(ids) - len(smieci)})
                razem += len(smieci)
        if dry_run:
            return {"ok": True, "dry_run": True, "total": razem, "groups": plan,
                    "steam_running": _se.is_steam_running()}
        if not razem:
            return {"ok": True, "total": 0, "groups": []}
        if _se.is_steam_running():
            return {"ok": False, "reason": "zamknij Steam przed sprzątaniem"}
        keep = {}
        for name, v in coll.items():
            ids = [int(a) & 0xFFFFFFFF for a in v.get("added") or []]
            keep[name] = [a for a in ids if not (a >= 0x80000000 and a not in have)]
        res = _se.rewrite_collection_members(cd, keep)
        if not res.get("ok"):
            return {"ok": False, "reason": res.get("error") or "błąd zapisu"}
        _log.write("steam", f"kolekcje: usunieto {razem} martwych identyfikatorow")
        return {"ok": True, "total": razem, "groups": plan}

    def steam_push_collections(self, names: list = None) -> dict:
        """Wysyła kolekcje z „Kolekcje" (LINKS) do Steam jako kolekcje Steam.

        Członków dopasowujemy do istniejących wpisów „Non-Steam" po nazwie —
        kolekcja w Steam ma sens tylko dla gier, które są już w bibliotece.
        """
        from pylinks.services import steam_export as _se
        from pylinks.core.naming import safe_name
        from pylinks.core import log as _log
        cd = self._steam_config_dir()
        if not cd:
            return {"ok": False, "reason": "nie znaleziono userdata Steam"}
        colls = _load_config().get("collections", {}) or {}
        want = [n for n in (names or list(colls)) if n in colls]
        if not want:
            return {"ok": False, "reason": "brak kolekcji do wysłania"}
        entries = _se.read_shortcuts_vdf(cd / "shortcuts.vdf")
        idx = {}
        for e in entries:
            nm = safe_name(_se._entry_name(e) or "").rstrip()
            if nm:
                idx.setdefault(nm.lower(), int(e.get("appid", 0)) & 0xFFFFFFFF)
        tag_appids, braki = {}, {}
        for n in want:
            ids, miss = [], []
            for m in colls.get(n) or []:
                nazwa = (m.get("name") if isinstance(m, dict) else str(m)) or ""
                a = idx.get(safe_name(nazwa).rstrip().lower())
                (ids.append(a) if a else miss.append(nazwa))
            if ids:
                tag_appids[n] = ids
            if miss:
                braki[n] = miss[:20]
        if not tag_appids:
            return {"ok": False, "reason": "żadnej gry z kolekcji nie ma "
                                           "w bibliotece Steam", "missing": braki}
        if _se.is_steam_running():
            return {"ok": False, "reason": "zamknij Steam przed zapisem",
                    "would_send": {k: len(v) for k, v in tag_appids.items()}}
        res = _se.write_collections(cd, tag_appids)
        _log.write("steam", f"kolekcje z LINKS -> Steam: "
                            f"utworzone {res.get('created')}, "
                            f"zaktualizowane {res.get('updated')}")
        return {"ok": bool(res.get("ok")) or not res.get("error"),
                "created": res.get("created", []), "updated": res.get("updated", []),
                "error": res.get("error"), "missing": braki,
                "sent": {k: len(v) for k, v in tag_appids.items()}}

    def steam_running(self) -> dict:
        from pylinks.services import steam_export as _se
        return {"running": _se.is_steam_running()}

    def _export(self, games: list) -> dict:
        from pylinks.services import export as _exp
        if not games:
            return {"ok": False, "reason": "brak gier do eksportu", "created": 0}
        res = _exp.export_games(_load_config(), games, CACHE_DIR, LINKS_DIR)
        _ICON_URI_CACHE.clear()          # świeże ikony w widoku LINKS po eksporcie
        res["links_dir"] = str(LINKS_DIR)
        return res

    def find_orphans(self) -> dict:
        """Skróty w LINKS/<platforma>/ bez odpowiadającej (zeskanowanej) gry.

        Sprawdza tylko platformy, dla których mamy dane skanu (PC / systemy ROM),
        żeby brak skanu nie oznaczył wszystkiego jako osierocone."""
        from pylinks.core.naming import safe_name
        expected = {}   # platforma → zbiór safe_name istniejących gier
        if self._pc_games:
            expected["PC"] = {safe_name(g.get("name", "")) for g in self._pc_games}
        for g in self._rom_games:
            expected.setdefault(g.get("system", ""), set()).add(safe_name(g.get("name", "")))
        orphans = []
        if not LINKS_DIR.is_dir():
            return {"orphans": []}
        for d in sorted(LINKS_DIR.iterdir()):
            if not d.is_dir() or d.name.lower() in ("kolekcje", "_collections"):
                continue
            names = expected.get(d.name)
            if names is None:
                continue                     # tej platformy nie skanowaliśmy — pomiń
            for f in sorted(d.iterdir()):
                # stem bywa z doklejoną spacją (sortowanie serii) — porównujemy bez niej
                if f.suffix.lower() in _LINK_EXTS and f.stem.rstrip() not in names:
                    orphans.append({"name": f.stem, "platform": d.name, "path": str(f)})
        # Wpisy w bibliotece Steam nie znikają razem ze skrótem — trzeba je wskazać
        # osobno, inaczej po usunięciu gry z dysku zostaje martwa pozycja w Steamie.
        return {"orphans": orphans, "steam": self._steam_orphans(orphans)}

    def _steam_orphans(self, orphans: list) -> list:
        """Wpisy „Non-Steam" odpowiadające osieroconym skrótom (po nazwie)."""
        from pylinks.services import steam_export as _se
        from pylinks.core.naming import safe_name
        if not orphans:
            return []
        try:
            cd = self._steam_config_dir()
            if not cd:
                return []
            entries = _se.read_shortcuts_vdf(cd / "shortcuts.vdf")
        except Exception:
            return []
        chcemy = {safe_name(o["name"]).rstrip().lower() for o in orphans}
        out = []
        for e in entries:
            nm = _se._entry_name(e) or ""
            if safe_name(nm).rstrip().lower() in chcemy:
                out.append({"name": nm,
                            "appid": int(e.get("appid", 0)) & 0xFFFFFFFF})
        return out

    def delete_steam_entries(self, appids: list) -> dict:
        """Usuwa wskazane wpisy z biblioteki Steam (i ich przynależność do kolekcji)."""
        from pylinks.services import steam_export as _se
        from pylinks.core import log as _log
        ids = [int(a) for a in (appids or []) if a]
        if not ids:
            return {"ok": True, "removed": 0}
        cd = self._steam_config_dir()
        if not cd:
            return {"ok": False, "reason": "nie znaleziono userdata Steam"}
        if _se.is_steam_running():
            return {"ok": False, "reason": "zamknij Steam, żeby usunąć wpisy"}
        n = _se.remove_shortcuts(cd / "shortcuts.vdf", ids)
        # wyczyść je też z kolekcji, żeby nie zostały tam jako martwe numery
        coll = _se.read_collections(cd)
        keep = {nm: [int(a) & 0xFFFFFFFF for a in (v.get("added") or [])
                     if (int(a) & 0xFFFFFFFF) not in set(ids)]
                for nm, v in coll.items()}
        _se.rewrite_collection_members(cd, keep)
        _log.write("steam", f"usunieto {n} wpisow (duchy) + wyczyszczono kolekcje")
        return {"ok": True, "removed": n}

    def delete_orphans(self, paths: list) -> dict:
        """Usuwa wskazane skróty + ich ikony .ico (z IconLocation)."""
        deleted, icons = 0, 0
        for p in paths or []:
            f = Path(p)
            try:
                if f.suffix.lower() not in _LINK_EXTS or not f.is_file():
                    continue
                if f.parent.parent.resolve() != LINKS_DIR.resolve():
                    continue                 # bezpieczeństwo: tylko w LINKS/*/
                ico = _shortcut_icon_path(f)
                f.unlink()
                deleted += 1
                if ico and Path(ico).is_file() and "cache" in str(ico).lower():
                    try:
                        Path(ico).unlink(); icons += 1
                    except Exception:
                        pass
            except Exception:
                pass
        _ICON_URI_CACHE.clear()
        return {"ok": True, "deleted": deleted, "icons": icons}

    @staticmethod
    def _dedupe_by_key(games: list) -> list:
        """Usuwa powtórzone gry o tym samym kluczu (ta sama gra w dwóch katalogach).

        Klucz jest z założenia STABILNY — liczony z nazwy, nie ze ścieżki — żeby
        wybór ikony przeżył przeniesienie gry. Ceną jest to, że ta sama gra
        w dwóch miejscach (np. w Games i w GOG_Games) daje dwa wpisy
        o identycznym kluczu: powstawały dwa kafelki, a okładka trafiała tylko
        w pierwszy — drugi zostawał pusty. Zostawiamy pierwszy i piszemy o tym
        w dzienniku, bo to zwykle ślad po nieusuniętej kopii gry.
        """
        from pylinks.core import log as _log
        seen, out = {}, []
        for g in games:
            k = _game_key(g)
            if k in seen:
                _log.write("skan", f"pominieto duplikat {g.get('name','')} "
                                   f"({g.get('game_dir','')}) — juz jest "
                                   f"z {seen[k]}")
                continue
            seen[k] = g.get("game_dir", "")
            out.append(g)
        return out

    def missing_keys(self, kind: str = "pc", filt: str = "") -> list:
        """Klucze gier BEZ istniejącego skrótu w LINKS/<platforma>/.

        Nazwę pliku liczymy DOKŁADNIE tak jak eksport (`sortable_stem`), bo ten
        dokleja spację na końcu, gdy istnieje dłuższe rodzeństwo — „Dungeon Siege"
        obok „Dungeon Siege 2" zapisuje się jako „Dungeon Siege .lnk". Porównanie
        po samym `safe_name` nigdy w to nie trafiało, więc gotowe skróty całych
        serii (Trails of Cold Steel, Dungeon Siege) wiecznie wychodziły jako braki.
        """
        from pylinks.core.naming import safe_name
        from pylinks.services.export import sortable_stem
        if kind == "rom":
            games = [g for g in self._rom_games if not filt or g.get("system") == filt]
        else:
            games = [g for g in self._pc_games if not filt or g.get("source") == filt]
        # rodzeństwo per platforma — te same zbiory, na których pracuje eksport
        sibs: dict = {}
        for g in games:
            plat = g.get("system", "") if g.get("source") == "rom" else "PC"
            sibs.setdefault(plat, set()).add(safe_name(g.get("name", "") or "gra"))
        for plat, names in sibs.items():          # dołóż to, co JUŻ leży na dysku
            try:
                names |= {f.stem.rstrip() for f in (LINKS_DIR / plat).iterdir()
                          if f.suffix.lower() in _LINK_EXTS}
            except OSError:
                pass
        out = []
        for g in games:
            plat = g.get("system", "") if g.get("source") == "rom" else "PC"
            d = LINKS_DIR / plat
            stem = sortable_stem(safe_name(g.get("name", "") or "gra"), sibs[plat])
            if not any((d / (stem + ext)).exists() for ext in _LINK_EXTS):
                out.append(_game_key(g))
        return out

    # --- grafiki: dwa tryby "icon" (kwadrat) / "steam" (portret) ---
    def _raw_uri(self, key: str, mode: str):
        """MINIATURA okładki jako data-URI (RAM → dysk). Kształt robi CSS.

        Do siatki świadomie NIE wysyłamy pełnej rozdzielczości: kafelek ma ~200 px,
        a okładki bywają 600×900, więc przez most szło ~500 KB na grafikę — przy
        bibliotece arcade setki megabajtów na jedno przewinięcie. Pełny obraz
        zostaje tam, gdzie jest naprawdę potrzebny: w oknie kadrowania i przy
        pieczeniu .ico.
        """
        ck = (key, mode)
        if ck in self._baked:
            return self._baked[ck]
        uri = art.thumb_uri(CACHE_DIR, key, mode)
        if uri:
            self._baked[ck] = uri
        return uri

    def _invalidate(self, key: str) -> None:
        for ck in [k for k in self._baked if k[0] == key]:
            self._baked.pop(ck, None)
        for m in ("icon", "steam"):
            art.clear_thumb(CACHE_DIR, key, m)   # inaczej zostałaby stara miniatura

    def cover_cached(self, key: str, mode: str = "icon") -> dict:
        """Surowy obraz z cache + per-grę kadr (fit/offset dla CSS)."""
        cfg = _load_config()
        uri = self._raw_uri(key, mode)
        fit, off = self._crop_for(key, cfg)
        return {"key": key, "found": bool(uri), "data_uri": uri,
                "fit": fit, "offset": off, "cached": True}

    @staticmethod
    def _thumb_url(key: str, mode: str, ver: int) -> str:
        """Adres miniatury dla siatki. `ver` (czas okładki) unieważnia cache
        przeglądarki dokładnie wtedy, gdy grafika się zmieniła."""
        base = _thumbsrv.base_url() or _thumbsrv.start(CACHE_DIR)
        if not base:
            return ""
        return (f"{base}/{art._mode(mode)}/"
                f"{urllib.parse.quote(key, safe='')}?v={ver}")

    def covers_batch(self, keys: list, mode: str = "icon") -> dict:
        """ZBIORCZO w JEDNYM wywołaniu mostu: {key: {uri, fit, offset} | False | None}.

        `False` = wiemy, że dla tej gry nie ma grafiki w żadnym źródle. Bez tego
        rozróżnienia interfejs przy KAŻDYM przerysowaniu dopytywał o te same
        beznadziejne tytuły — przy bibliotece arcade to setki zbędnych przejść
        przez most po wyczyszczeniu filtra.
        """
        cfg = _load_config()
        m = art._mode(mode)
        known_miss = _store.all_overrides(CACHE_DIR, f"cover_miss_{m}")
        # JEDNO przejście po katalogu okładek zamiast globa na każdy klucz.
        # Nie czytamy tu ŻADNEGO obrazu: przy podawaniu po HTTP wystarczy wiedzieć,
        # że okładka istnieje — bajty pobierze sama przeglądarka, równolegle.
        stems = art.cover_index(CACHE_DIR, m)
        out = {}
        for k in keys:
            if not k:
                continue
            ver = stems.get(art._safe_key(k))
            if ver is None:
                out[k] = False if k in known_miss else None
                continue
            fit, off = self._crop_for(k, cfg)
            url = self._thumb_url(k, m, ver)
            if url:
                out[k] = {"url": url, "fit": fit, "offset": off}
            else:                       # bez serwera zostaje stara droga
                uri = self._raw_uri(k, mode)
                out[k] = ({"uri": uri, "fit": fit, "offset": off} if uri else None)
        return out

    def covers_fetch_batch(self, keys: list, mode: str = "icon") -> dict:
        """Dociąga BRAKUJĄCE okładki RÓWNOLEGLE i zapamiętuje nietrafienia.

        Dotąd interfejs pobierał je po jednej, w pętli przez most — przy bibliotece
        arcade wychodziło ~0,5 s na tytuł, czyli 2,5 minuty na porcję 300 kafelków,
        i to od nowa przy każdym wejściu w widok, bo nieudane szukania nie zostawiały
        śladu. Stąd wrażenie, że „grafiki przestały się wyświetlać".

        Nietrafienia lądują w bazie (`cover_miss_<tryb>`), więc drugie wejście jest
        natychmiastowe. Kasuje je „Odśwież okładki arcade" i ręczny wybór grafiki.
        """
        from concurrent.futures import ThreadPoolExecutor
        cfg = _load_config()
        miss_kind = f"cover_miss_{art._mode(mode)}"
        known_miss = _store.all_overrides(CACHE_DIR, miss_kind)
        todo, out = [], {}
        for k in keys:
            if not k:
                continue
            uri = self._raw_uri(k, mode)
            if uri:
                fit, off = self._crop_for(k, cfg)
                out[k] = {"uri": uri, "fit": fit, "offset": off}
            elif k in known_miss:
                out[k] = None            # wiemy, że nie ma — nie ruszamy sieci
            else:
                todo.append(k)

        def _one(k):
            g = self._find_game(k)
            if not g:
                return k, None
            try:
                pp = (g.get("source") == "rom" and mode != "steam"
                      and bool(cfg.get("icon_platform_spine", False)))
                override = self._pending.get(k) or self._title_override(k, cfg)
                if not art.has_cover(CACHE_DIR, k, mode):
                    self._restore_by_source_id(cfg, k, g, art._mode(mode))
                r = art.fetch_cover(cfg, CACHE_DIR, k, g, mode, pp, query=override)
                if (r or {}).get("found"):
                    self._invalidate(k)
                    return k, True
                return k, False
            except Exception:
                return k, None

        found = missed = 0
        if todo:
            with ThreadPoolExecutor(max_workers=6) as ex:
                for k, ok in ex.map(_one, todo):
                    if ok:
                        found += 1
                        fit, off = self._crop_for(k, cfg)
                        p = art._raw_path(CACHE_DIR, k, art._mode(mode))
                        url = (self._thumb_url(k, mode, int(p.stat().st_mtime))
                               if p else "")
                        if url:
                            out[k] = {"url": url, "fit": fit, "offset": off}
                        else:
                            uri = self._raw_uri(k, mode)
                            out[k] = ({"uri": uri, "fit": fit, "offset": off}
                                      if uri else None)
                    else:
                        missed += 1
                        out[k] = None
                        _store.set_override(CACHE_DIR, k, miss_kind, int(time.time()))
        from pylinks.core import log as _log
        _log.write("okladki", f"tryb={mode} zadano={len(keys)} "
                              f"do_pobrania={len(todo)} pobrano={found} "
                              f"bez_trafienia={missed}")
        return out

    @staticmethod
    def _crop_for(key: str, cfg: dict):
        """(fit, offset) dla gry: per-grę override albo globalny fit."""
        store = cfg.get("icon_crop_by_key", {}) or {}
        e = store.get(key) if isinstance(store, dict) else None
        if isinstance(e, dict):
            return (e.get("fit", "pad"),
                    float(e.get("offset", 0.5)))
        return (cfg.get("icon_fit_mode", "pad"), 0.5)

    # --- kadrowanie per gra (prawy klik → Kadruj) ---
    def crop_get(self, key: str, mode: str = "icon") -> dict:
        """Surowy obraz + bieżące ustawienia kadru do okna kadrowania."""
        cfg = _load_config()
        fit, off = self._crop_for(key, cfg)
        return {"key": key, "raw": art.raw_cover(CACHE_DIR, key, mode),
                "fit": fit, "offset": off}

    def crop_apply(self, key: str, fit: str, offset, mode: str = "icon") -> dict:
        """Zapisuje per-grę kadr; kształt renderuje CSS (zwraca fit/offset)."""
        fit = "crop" if str(fit).lower() == "crop" else "pad"
        try:
            offset = max(0.0, min(1.0, float(offset)))
        except Exception:
            offset = 0.5
        self._save_crop(key, {"fit": fit, "offset": offset})
        return {"key": key, "ok": True, "fit": fit, "offset": offset}

    def crop_reset(self, key: str, mode: str = "icon") -> dict:
        """Usuwa per-grę kadr (wraca do globalnego dopasowania)."""
        self._save_crop(key, None)
        fit, off = self._crop_for(key, _load_config())
        return {"key": key, "ok": True, "fit": fit, "offset": off}

    def _save_crop(self, key: str, entry) -> None:
        if entry is None:
            _store.clear_crop(CACHE_DIR, key)
        else:
            _store.set_crop(CACHE_DIR, key, entry.get("fit", "pad"),
                            float(entry.get("offset", 0.5)))

    @staticmethod
    def _forget_miss(key: str, mode: str) -> None:
        """Zapomina „tu nic nie ma" — po ręcznym wyborze albo udanym pobraniu."""
        _store.set_override(CACHE_DIR, key, f"cover_miss_{art._mode(mode)}", None)

    def fetch_cover(self, key: str, mode: str = "icon") -> dict:
        """Pobiera grafikę z SGDB (lub czyta z cache) dla trybu. Kształt robi CSS."""
        g = self._find_game(key)
        if not g:
            return {"key": key, "found": False, "data_uri": None,
                    "reason": "nieznana gra (najpierw skan)"}
        cfg = _load_config()
        # ROM z grzbietem: preferuj portret (portret + grzbiet = kwadrat)
        prefer_portrait = (g.get("source") == "rom"
                           and mode != "steam"
                           and bool(cfg.get("icon_platform_spine", False)))
        override = self._pending.get(key) or self._title_override(key, cfg)
        # stub zapisany jako ID (stary format) → odzyskaj konkretną grafikę
        if not art.has_cover(CACHE_DIR, key, mode):
            self._restore_by_source_id(cfg, key, g, art._mode(mode))
        res = art.fetch_cover(cfg, CACHE_DIR, key, g, mode, prefer_portrait,
                              query=override)
        if res.get("found"):
            self._invalidate(key)               # świeżo pobrany raw
            self._forget_miss(key, mode)
            fit, off = self._crop_for(key, cfg)
            res["fit"], res["offset"] = fit, off
        return res

    def _all_candidates(self, cfg: dict, g: dict, query: str, mode: str,
                        page: int = 0) -> list:
        """SteamGridDB + źródła dodatkowe (IGDB / TGDB / SteamCDN / Libretro).

        `page` > 0 = kolejna porcja z SGDB („pokaż więcej"); źródła dodatkowe
        dokładamy tylko na pierwszej stronie — one nie mają stronicowania."""
        # Wklejony ADRES OBRAZU albo ścieżka lokalna = gotowa okładka. To jedyne
        # wyjście dla gier, których nie ma w żadnym katalogu — np. przed premierą
        # albo w wersji demo, gdzie SteamGridDB (wkład społeczności) nic nie ma.
        wlasna = self._own_image_candidate(query)
        if wlasna:
            return [wlasna]
        # Wklejony appid albo adres sklepu Steam → grafiki z CDN Steama.
        cdn = self._steam_cdn_candidates(cfg, query)
        if cdn:
            return cdn
        cands = art.candidates_for_game(cfg, g, query=query, mode=mode, page=page)
        # Źródła dodatkowe dokładamy TAKŻE przy ręcznie wpisanym tytule. Wcześniej
        # warunek brzmiał „tylko gdy nie ma zapytania", więc wpisanie nazwy —
        # czyli dokładnie ta chwila, gdy automat zawiódł i szukasz sam — zawężało
        # wyszukiwanie do samego SteamGridDB. Szukamy WPISANEJ frazy, nie nazwy
        # gry: po to się ją wpisuje.
        if mode != "steam" and not page:
            szukaj = (query or "").strip() or g.get("name", "")
            try:
                seen = {c["id"] for c in cands}
                if g.get("source") == "rom":
                    from pylinks.services.extra_art import libretro_candidates
                    if (cfg.get("extra_sources", {}) or {}).get("libretro", True):
                        cands += [c for c in libretro_candidates(g.get("system", ""),
                                                                 szukaj)
                                  if c.get("id") not in seen]
                if g.get("source") != "rom" or query:
                    from pylinks.services.extra_art import ExtraArtSources
                    # appid podpinamy tylko przy szukaniu po nazwie własnej gry —
                    # przy ręcznej frazie grafiki ze Steama dotyczyłyby innej gry
                    appid = "" if query else str(g.get("appid") or "")
                    extra = ExtraArtSources(cfg).candidates(szukaj, appid, "PC")
                    cands += [c for c in extra if c.get("id") not in seen]
            except Exception:
                pass
        return cands

    @staticmethod
    def _own_image_candidate(query: str):
        """Kandydat z bezpośredniego adresu obrazu albo z pliku na dysku."""
        import base64
        import mimetypes
        q = (query or "").strip().strip('"')
        if not q:
            return None
        low = q.lower()
        if low.startswith(("http://", "https://")) and low.split("?")[0].endswith(
                (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")):
            return {"id": f"url_{abs(hash(q)) & 0xFFFFFF}", "url": q, "thumb": q,
                    "w": 0, "h": 0, "style": "własny adres", "kind": "own",
                    "square": False}
        p = Path(q)
        if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp",
                                                ".gif", ".bmp", ".ico"):
            try:
                data = p.read_bytes()
            except OSError:
                return None
            mime = mimetypes.guess_type(p.name)[0] or "image/png"
            uri = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
            return {"id": f"file_{p.stem[:40]}", "url": uri, "thumb": uri,
                    "w": 0, "h": 0, "style": "plik z dysku", "kind": "own",
                    "square": False}
        return None

    @staticmethod
    def _steam_cdn_candidates(cfg: dict, query: str):
        """Grafiki z CDN Steama dla wklejonego appid albo adresu sklepu.

        Gra przed premierą albo demo bywa już na Steamie — a wtedy ma komplet
        grafik, których katalogi społecznościowe jeszcze nie mają.
        """
        import re as _re
        q = (query or "").strip()
        if not q:
            return None
        # Goła liczba to od zawsze IDENTYFIKATOR SteamGridDB — nie wolno jej tu
        # przejąć. Appid Steama wymaga więc wyraźnego przedrostka albo adresu sklepu.
        m = (_re.search(r"store[.]steampowered[.]com/app/([0-9]+)", q)
             or _re.fullmatch(r"steam:[ ]*([0-9]+)", q))
        if not m:
            return None
        try:
            from pylinks.services.extra_art import ExtraArtSources
            return ExtraArtSources(cfg).steam_cdn_candidates(m.group(1)) or None
        except Exception:
            return None

    def _restore_by_source_id(self, cfg: dict, key: str, g: dict,
                              mode: str = "icon") -> bool:
        """Odtwarza wybór zapisany jako IDENTYFIKATOR kandydata (nie URL).

        Stara wersja trzymała mapowanie id→URL w bazie SQLite, więc w configu
        zostawało samo id (np. `113432`, `igdb_cover_9258`). Odzyskujemy je,
        szukając kandydata gry o tym id. Znaleziony URL DOPISUJEMY do bazy, żeby
        następnym razem poszła szybka ścieżka, bez odpytywania API.
        """
        asset = "grid" if mode == "steam" else "icon"
        url, sid = _store.get_choice(CACHE_DIR, key, mode, asset)
        if url or not sid:
            return False                      # brak stuba albo już mamy URL
        try:
            q = self._title_override(key, cfg)
            for c in self._all_candidates(cfg, g, q, mode):
                if str(c.get("id")) != str(sid):
                    continue
                full = c.get("url") or c.get("thumb")
                if not full:
                    break
                res = art.save_chosen_cover(CACHE_DIR, key, full, mode)
                if res.get("found"):
                    _store.set_choice(CACHE_DIR, key, mode, asset,
                                      url=full, source_id=sid)
                    self._invalidate(key)
                    return True
                break
        except Exception:
            pass
        return False

    def cover_candidates(self, key: str, query: str = "", mode: str = "icon",
                         page: int = 0) -> dict:
        """Lista kandydatów do okna wyboru (miniatury jako data-URI) dla trybu.

        Poza SteamGridDB dołącza dodatkowe źródła (IGDB / TheGamesDB / Steam CDN),
        gdy skonfigurowane — jak w oryginale. Tylko tryb ikon (Steam ma multi-asset)."""
        g = self._find_game(key)
        if not g:
            return {"key": key, "candidates": [], "reason": "nieznana gra"}
        cfg = _load_config()
        if query.strip():
            self._pending[key] = query.strip()          # ręczne — zapamiętaj tymczasowo
        else:
            query = self._title_override(key, cfg)       # użyj zapisanego dopasowania
        cands = self._all_candidates(cfg, g, query, mode, page=int(page or 0))
        return {"key": key, "candidates": cands, "page": int(page or 0),
                "reason": "" if cands else "brak wyników / brak klucza SGDB"}

    def fetch_thumb(self, url: str) -> dict:
        """Fallback: pobiera miniaturę serwerowo → data-URI (gdy przeglądarka
        nie wczyta zdalnego URL-a)."""
        return {"url": url, "data_uri": art.fetch_thumb_uri(url)}

    def sgdb_suggest(self, query: str) -> list:
        """Podpowiedzi tytułów (autouzupełnianie SGDB) dla pola szukania."""
        return art.suggest_games(_load_config(), query)

    def cover_candidates_by_id(self, key: str, sgdb_id, mode: str = "icon") -> dict:
        """Kandydaci dla konkretnego SGDB game id (klik w podpowiedź)."""
        return self.cover_candidates(key, str(sgdb_id), mode)

    def choose_cover(self, key: str, url: str, mode: str = "icon") -> dict:
        """Ustawia wskazaną grafikę jako wybraną: zapis w cache + config."""
        res = art.save_chosen_cover(CACHE_DIR, key, url, mode)
        if res.get("found"):
            self._save_selection(key, url, mode)
            self._save_override(key)          # zapamiętaj dopasowanie (oba tryby)
            self._invalidate(key)
            fit, off = self._crop_for(key, _load_config())
            res["fit"], res["offset"] = fit, off
        return {"key": key, "found": res.get("found", False),
                "data_uri": res.get("data_uri"),
                "fit": res.get("fit"), "offset": res.get("offset")}


    # --- tryb Steam: kilka typów grafik (grid + hero/tło + logo) ---
    def steam_art_options(self, key: str, query: str = "", page: int = 0) -> dict:
        """Wszystkie warianty grafik Steam naraz (grid/hero/logo) do okna wyboru."""
        g = self._find_game(key)
        if not g:
            return {"ok": False, "reason": "nieznana gra",
                    "grid": [], "hero": [], "logo": []}
        cfg = _load_config()
        if query.strip():
            self._pending[key] = query.strip()
        else:
            query = self._title_override(key, cfg)       # wspólne z trybem ikon
        res = art.steam_art_options(cfg, g, query=query, page=int(page or 0))
        res["page"] = int(page or 0)
        # bieżące wybory jako URL-e (do podświetlenia wariantu w UI)
        store = cfg.get("steam_art_by_key", {}) or {}
        entry = store.get(key, {}) if isinstance(store, dict) else {}
        res["current"] = {a: (entry.get(a) if isinstance(entry, dict) else None)
                          for a in ("grid", "hero", "logo")}
        # aktualnie ZAPISANE grafiki (z cache) — to, co realnie pójdzie do Steama.
        # MINIATURY, nie oryginały: hero potrafi mieć 45 MB (animowany webp),
        # a most przesyła base64 → okno zamierało.
        res["cached"] = {a: art.cached_steam_asset_thumb(CACHE_DIR, key, a)
                         for a in ("grid", "hero", "logo")}
        res["key"] = key
        return res

    def choose_steam_asset(self, key: str, asset: str, url: str) -> dict:
        """Przypina wariant danego typu (grid/hero/logo) i zapisuje w config."""
        res = art.save_steam_asset(CACHE_DIR, key, asset, url)
        if not res.get("found"):
            return {"key": key, "asset": asset, "found": False, "data_uri": None}
        self._save_steam_asset_sel(key, asset, url)
        self._save_override(key)              # zapamiętaj dopasowanie (oba tryby)
        # do UI oddajemy MINIATURĘ zapisanego pliku (oryginał hero bywa 45 MB)
        thumb = art.cached_steam_asset_thumb(CACHE_DIR, key, asset, box=512)
        return {"key": key, "asset": asset, "found": True,
                "data_uri": thumb or res.get("data_uri")}


    # --- zadania w tle: pobierz wszystko / stwórz wszystko ---
    def _games_for_scope(self, scope: str) -> list:
        scope = (scope or "all").lower()
        if scope == "pc":
            return list(self._pc_games)
        if scope == "rom":
            return list(self._rom_games)
        return list(self._pc_games) + list(self._rom_games)

    def _job_busy(self) -> bool:
        return bool(self._job.get("running"))

    def job_status(self) -> dict:
        """Snapshot postępu zadania (do paska w UI)."""
        p = self._job
        return {"running": p.get("running", False), "kind": p.get("kind", ""),
                "phase": p.get("phase", ""), "done": p.get("done", 0),
                "total": p.get("total", 0), "new": p.get("new", 0),
                "cached": p.get("cached", 0), "failed": p.get("failed", 0),
                "current": p.get("current", ""), "result": p.get("result")}

    def job_stop(self) -> dict:
        """Zatrzymuje bieżące zadanie (flaga anulowania — zadziała między grami)."""
        self._job["cancel"] = True
        return {"ok": True}

    def _job_reset(self, kind: str, total: int) -> None:
        self._job.update({"running": True, "kind": kind, "phase": "",
                          "done": 0, "total": total, "new": 0, "cached": 0,
                          "failed": 0, "current": "", "cancel": False,
                          "result": None})

    def job_download(self, scope: str = "all", icon: bool = True,
                     steam: bool = True) -> dict:
        """Pobiera KOMPLET grafik do cache dla gier z zakresu (scope: all/pc/rom).
        icon = okładka/ikona (tryb ikon), steam = grid+hero+logo. Bez klucza SGDB
        nic nie robi. Zwraca {started, total} — postęp przez job_status()."""
        import threading
        if self._job_busy():
            return {"started": False, "reason": "zadanie już trwa"}
        cfg = _load_config()
        if not (cfg.get("api_keys", {}) or {}).get("sgdb_key"):
            return {"started": False, "reason": "brak klucza SGDB", "total": 0}
        games = self._games_for_scope(scope)
        if not games:
            return {"started": False, "reason": "brak gier (najpierw skan)", "total": 0}
        self._job_reset("download", len(games))

        def _worker(games_snapshot, cfg_snap):
            for g in games_snapshot:
                if self._job.get("cancel"):
                    break
                key = _game_key(g)
                self._job["current"] = g.get("name", "")
                q = self._title_override(key, cfg_snap)
                # tryb ikon: okładka kwadratowa (ROM+grzbiet → portret)
                if icon:
                    try:
                        # TANIA kontrola (glob) — wcześniej leciało pełne
                        # dekodowanie miniatury (PIL + base64) tylko po to,
                        # by sprawdzić istnienie, a wynik szedł do kosza.
                        if art.has_cover(CACHE_DIR, key, "icon"):
                            self._job["cached"] += 1
                        elif self._restore_by_source_id(cfg_snap, key, g, "icon"):
                            self._job["new"] += 1       # odtworzony wybór ze stuba
                        else:
                            pp = (g.get("source") == "rom"
                                  and bool(cfg_snap.get("icon_platform_spine", False)))
                            r = art.fetch_cover(cfg_snap, CACHE_DIR, key, g, "icon",
                                                pp, query=q)
                            self._job["new" if r.get("found") else "failed"] += 1
                    except Exception:
                        self._job["failed"] += 1
                # tryb Steam: grid + hero + logo
                if steam:
                    try:
                        r = art.prefetch_steam_art_for_game(cfg_snap, CACHE_DIR,
                                                            key, g, query=q)
                        for a in ("grid", "hero", "logo"):
                            st = r.get(a)
                            if st == "new":
                                self._job["new"] += 1
                            elif st == "cached":
                                self._job["cached"] += 1
                            elif st in ("fail", "none"):
                                self._job["failed"] += 1
                    except Exception:
                        self._job["failed"] += 1
                self._job["done"] += 1
            self._job["running"] = False
            self._job["current"] = ""
            self._job["result"] = {"kind": "download",
                                   "new": self._job["new"],
                                   "cached": self._job["cached"],
                                   "failed": self._job["failed"]}

        threading.Thread(target=_worker, args=(games, cfg), daemon=True).start()
        return {"started": True, "total": len(games)}

    def job_create(self, scope: str = "all", icon: bool = True,
                   steam: bool = False) -> dict:
        """Tworzy WSZYSTKO dla gier z zakresu: icon = skróty .lnk/.url (LINKS/),
        steam = wpisy w bibliotece Steam (shortcuts.vdf + grafiki + kolekcje).
        Steam wymaga ZAMKNIĘTEGO Steama. Postęp przez job_status()."""
        import threading
        if self._job_busy():
            return {"started": False, "reason": "zadanie już trwa"}
        cfg = _load_config()
        games = self._games_for_scope(scope)
        if not games:
            return {"started": False, "reason": "brak gier (najpierw skan)", "total": 0}
        total = (len(games) if icon else 0) + (len(games) if steam else 0)
        self._job_reset("create", max(1, total))

        def _worker(games_snapshot, cfg_snap):
            from pylinks.services import export as _exp
            # COM (win32com) wymaga inicjalizacji NA TYM wątku — inaczej tworzenie
            # skrótów .lnk rzuca „releasing IUnknown" i bywa zawodne.
            try:
                import pythoncom
                pythoncom.CoInitialize()
            except Exception:
                pythoncom = None
            summary = {"kind": "create"}
            base = 0
            if icon:
                self._job["phase"] = "Skróty .lnk"

                def _p(i, tot, name):
                    self._job["done"] = base + i
                    self._job["current"] = name
                try:
                    res = _exp.export_games(cfg_snap, games_snapshot, CACHE_DIR,
                                            LINKS_DIR, progress=_p)
                    summary["links"] = res
                except Exception as ex:
                    summary["links_error"] = str(ex)
                base += len(games_snapshot)
                self._job["done"] = base
            if steam and not self._job.get("cancel"):
                self._job["phase"] = "Biblioteka Steam"
                from pylinks.services import steam_export as _se

                def _ps(i, tot, name):
                    self._job["done"] = base + i
                    self._job["current"] = name
                try:
                    res = _se.export_to_steam(cfg_snap, games_snapshot, CACHE_DIR,
                                              progress=_ps)
                    summary["steam"] = res
                except Exception as ex:
                    summary["steam_error"] = str(ex)
                base += len(games_snapshot)
                self._job["done"] = base
            if pythoncom is not None:
                try:
                    import gc
                    gc.collect()               # zwolnij obiekty COM zanim zamkniemy apartament
                    pythoncom.CoUninitialize()
                except Exception:
                    pass
            self._job["running"] = False
            self._job["current"] = ""
            self._job["phase"] = ""
            self._job["result"] = summary

        threading.Thread(target=_worker, args=(games, cfg), daemon=True).start()
        return {"started": True, "total": total, "steam": bool(steam)}

    def _save_steam_asset_sel(self, key: str, asset: str, url) -> None:
        """Zapis wyboru typu Steam (grid/hero/logo) w bazie."""
        if url is None:
            _store.clear_choice(CACHE_DIR, key, "steam", asset)
        else:
            _store.set_choice(CACHE_DIR, key, "steam", asset, url=url)

    # --- trwały zapis wyboru (icon→selected_icon_keys, steam→steam_art_by_key) ---
    def _save_selection(self, key: str, url, mode: str = "icon") -> None:
        """Zapamiętuje wybór grafiki (stub gry) w bazie — nie w config.json."""
        m = "steam" if str(mode).lower() == "steam" else "icon"
        asset = "grid" if m == "steam" else "icon"
        if url is None:
            _store.clear_choice(CACHE_DIR, key, m, asset)
        else:
            _store.set_choice(CACHE_DIR, key, m, asset, url=url)
            self._forget_miss(key, m)   # ręczny wybór unieważnia „tu nic nie ma"

    # --- tryb dopasowania ikony (kwadrat): "pad" | "crop" ---
    def get_fit_mode(self) -> str:
        return _load_config().get("icon_fit_mode", "pad")

    def set_fit_mode(self, mode: str) -> dict:
        mode = "crop" if str(mode).lower() == "crop" else "pad"
        self._save_config_value("icon_fit_mode", mode)
        return {"ok": True, "mode": mode}

    def _save_config_value(self, key: str, value) -> None:
        """Read-modify-write pojedynczego klucza w config.json (reszta nietknięta)."""
        try:
            raw = {}
            if CONFIG_PATH.exists():
                raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            else:
                raw = _default_config()
            raw[key] = value
            _write_config_atomic(raw)
        except Exception:
            pass

    # --- kolekcje serii (LINKS/Kolekcje/) ---
    def collections_data(self) -> dict:
        """Kolekcje z configu + wszystkie skróty do wyboru."""
        from pylinks.services.collections import enumerate_link_games
        cfg = _load_config()
        return {"collections": cfg.get("collections", {}) or {},
                "games": enumerate_link_games(LINKS_DIR)}

    def save_collections(self, collections: dict) -> dict:
        """Zapisuje całą strukturę kolekcji (config.collections)."""
        self._save_config_value("collections", collections or {})
        return {"ok": True}

    def generate_collection(self, name: str) -> dict:
        """Buduje folder LINKS/Kolekcje/<name>/ z aktualnych członków."""
        from pylinks.services.collections import generate_collection
        coll = (_load_config().get("collections", {}) or {}).get(name, [])
        res = generate_collection(LINKS_DIR, name, coll)
        _ICON_URI_CACHE.clear()
        return res

    def generate_all_collections(self) -> dict:
        from pylinks.services.collections import generate_collection
        colls = _load_config().get("collections", {}) or {}
        total, missing = 0, []
        for name, members in colls.items():
            r = generate_collection(LINKS_DIR, name, members)
            total += r["made"]; missing += r["missing"]
        _ICON_URI_CACHE.clear()
        return {"ok": True, "made": total, "missing": missing, "count": len(colls)}

    # --- config ---
    def get_config(self) -> dict:
        """Config dla UI — sekrety ZAMASKOWANE (nie ujawniamy kluczy).
        `_secrets_set`: które sekrety są zapisane (do placeholderów)."""
        cfg = _load_config()
        sset = {}
        for p in SECRET_PATHS:
            sset[p[-1]] = bool(_get_path(cfg, p))
            _set_path(cfg, p, "")            # maska — puste pole w UI
        cfg["_secrets_set"] = sset
        return cfg

    def save_config(self, patch: dict) -> dict:
        """Zapis (deep-merge) do config.json. Sekrety szyfrowane DPAPI; puste
        pole sekretu = zachowaj istniejący (nie nadpisuj)."""
        from pylinks.core import secure
        patch = dict(patch or {})
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) \
                if CONFIG_PATH.exists() else _default_config()
        except Exception:
            raw = _default_config()
        # istniejące sekrety → plaintext (żeby scalanie/ponowne szyfrowanie było spójne)
        for p in SECRET_PATHS:
            v = _get_path(raw, p)
            if v:
                _set_path(raw, p, secure.unprotect(v))
        # puste pole sekretu w patchu = NIE zmieniaj (usuń z patcha)
        import copy
        patch = copy.deepcopy(patch)
        for p in SECRET_PATHS:
            if _has_path(patch, p) and not (_get_path(patch, p) or "").strip():
                _set_path(patch, p, _get_path(raw, p) or "")
        _deep_merge(raw, patch)
        # zaszyfruj sekrety przed zapisem
        for p in SECRET_PATHS:
            v = _get_path(raw, p)
            if v:
                _set_path(raw, p, secure.protect(v))
        try:
            _write_config_atomic(raw)
        except Exception as e:
            return {"ok": False, "reason": str(e)}
        self._baked.clear()
        return {"ok": True, "secured": secure.available()}

    def detect_steam(self) -> dict:
        """Auto-wykrycie Steam.exe z rejestru."""
        from pylinks.services.scan_pc import detect_steam_exe
        return {"steam_exe": detect_steam_exe() or ""}

    def pick_folder(self, start: str = "") -> dict:
        """Natywny dialog wyboru folderu."""
        try:
            import webview
            w = webview.windows[0] if webview.windows else None
            if not w:
                return {"path": ""}
            res = w.create_file_dialog(webview.FOLDER_DIALOG,
                                       directory=start or "")
            return {"path": (res[0] if res else "")}
        except Exception:
            return {"path": ""}

    def pick_file(self, start: str = "") -> dict:
        """Natywny dialog wyboru pliku (np. .exe emulatora / Steam.exe)."""
        try:
            import webview
            w = webview.windows[0] if webview.windows else None
            if not w:
                return {"path": ""}
            res = w.create_file_dialog(
                webview.OPEN_DIALOG, directory=start or "",
                file_types=("Programy (*.exe)", "Wszystkie pliki (*.*)"))
            return {"path": (res[0] if res else "")}
        except Exception:
            return {"path": ""}

    def rom_autodetect(self, base_rom: str, base_emu: str) -> dict:
        """Auto-wykrywanie systemów ROM (podfoldery → preset + emulator)."""
        from pylinks.services import emulators as _emu
        return {"systems": _emu.autodetect_systems(base_rom, base_emu)}

    def scan_emulators(self, plat: str, base_emu: str) -> list:
        """Opcje emulatora dla platformy (rdzenie RetroArch + standalone)."""
        from pylinks.services import emulators as _emu
        return _emu.scan_emulators_for(plat, base_emu)

    def security_status(self) -> dict:
        """Stan zabezpieczeń: DPAPI + TPM (obecny/włączony/gotowy)."""
        from pylinks.core import secure
        out = {"backend": secure.backend(), "dpapi": secure.available(),
               "tpm_present": None, "tpm_enabled": None, "tpm_ready": None}
        if os.name != "nt":
            # Linux nie ma odpowiednika TPM/DPAPI w tej roli — sekrety leżą
            # w configu z prawami 0600 (patrz _write_config_atomic).
            out["backend"] = "posix-file"
            return out
        try:
            import subprocess
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-Tpm | ConvertTo-Json -Compress"],
                capture_output=True, text=True, timeout=8, startupinfo=si)
            d = json.loads(r.stdout or "{}")
            out["tpm_present"] = bool(d.get("TpmPresent"))
            out["tpm_enabled"] = bool(d.get("TpmEnabled"))
            out["tpm_ready"] = bool(d.get("TpmReady"))
        except Exception:
            pass
        return out

    # --- biblioteka arcade (MAME) ---
    def mame_status(self) -> dict:
        """Stan biblioteki: wersja MAME, czy metadane w cache, ile tytułów."""
        from pylinks.services import mame as _m
        cfg = _load_config()
        a = cfg.get("arcade_library", {}) or {}
        exe, lib = a.get("mame_exe", ""), a.get("lib_dir", "")
        cached = _m.cached_version(CACHE_DIR)
        live = _m.mame_version(exe) if exe else ""
        titles = len(_m.library_titles(CACHE_DIR, lib)) if (lib and cached) else 0
        return {"enabled": bool(a.get("enabled")), "mame_exe": exe,
                "lib_dir": lib, "roms_dir": a.get("roms_dir", ""),
                "mame_version": live, "cached_version": cached,
                "needs_refresh": bool(live and live != cached),
                "titles": titles,
                "lib_present": bool(lib and Path(lib).is_dir())}

    def mame_refresh(self) -> dict:
        """Wczytuje metadane z mame.exe do bazy (szybkie: ~1 s dla 50 tys. setów)."""
        from pylinks.services import mame as _m
        a = (_load_config().get("arcade_library", {}) or {})
        exe = a.get("mame_exe", "")
        if not exe:
            return {"ok": False, "reason": "nie wskazano mame.exe"}
        return _m.refresh_metadata(CACHE_DIR, exe,
                                   support_dir=a.get("support_dir", ""))

    def mame_library(self, limit: int = 0, skip_controls: bool = True,
                     skip_adult: bool = True, skip_extra_ctrl: list = None) -> list:
        """Biblioteka pogrupowana po TYTULE (docelowo wybiera się jedną wersję).

        Domyślnie odsiewa hazard, mahjong/hanafuda, spinner i treści dla dorosłych.
        `skip_extra_ctrl` pozwala dodatkowo wyciąć kierownice/trackballe/pistolety.
        """
        from pylinks.services import mame as _m
        cfg = _load_config()
        a = (cfg.get("arcade_library", {}) or {})
        lib = a.get("lib_dir", "")
        skip_g = a.get("skip_genres")
        groups = _m.library_titles(CACHE_DIR, lib, limit=limit,
                                   skip_controls=skip_controls, skip_adult=skip_adult,
                                   skip_extra_ctrl=tuple(skip_extra_ctrl or ()),
                                   skip_genres=skip_g,
                                   skip_cats=a.get("skip_cats"))
        # trzymamy je jako „gry" — dzięki temu okładki, kadrowanie i wybór grafiki
        # działają dokładnie tak samo jak dla reszty biblioteki
        chosen_map = _store.all_overrides(CACHE_DIR, "mame_set")
        self._stem_index = None
        self._set_to_group = None
        self._mame_games = [{
            "name": g["title"], "source": "mame", "system": "MAME",
            "set": g["name"], "genre": g.get("genre", ""),
            "maker": g.get("maker", ""), "clean": _m.clean_title(g["title"]),
            "cat": g.get("cat", ""), "sub": g.get("sub", ""),
            "chosen": chosen_map.get(f"mame::{g['name'].lower()}") or g["name"],
            "path": str(Path(lib) / (g["name"] + ".zip")) if lib else "",
            "variants": g["variants"],
        } for g in groups]
        return [{"key": _game_key(x), "name": x["name"], "set": x["set"],
                 "system": "MAME", "genre": x["genre"], "chosen": x["chosen"],
                 "cat": x["cat"], "sub": x["sub"],
                 "variants": x["variants"]} for x in self._mame_games]

    def mame_recover_covers(self) -> dict:
        """Kasuje AUTOMATYCZNIE dobrane okładki arcade, by pobrały się na nowo.

        Dopasowanie SGDB dla automatów rozróżnia teraz producenta („Tetris (Sega)"),
        ale wcześniej trafione okładki siedzą w cache i same się nie odświeżą.
        Grafiki WYBRANE RĘCZNIE zostają — poznajemy je po wpisie w bazie wyborów.
        """
        # Odświeżamy przy okazji spis Libretro — to on decyduje o większości
        # trafień dla automatów, a jest ważny miesiąc.
        try:
            from pylinks.services import libretro_mame as _lm
            _lm.refresh_index(CACHE_DIR, force=True)
        except Exception:
            pass
        manual = set(_store.all_choices(CACHE_DIR))
        removed = 0
        # Pamięć nietrafień też musi zniknąć — inaczej odświeżenie skasowałoby
        # pliki, a program i tak nie spróbowałby pobrać ich ponownie.
        forgotten = 0
        for kind in ("cover_miss_icon", "cover_miss_steam"):
            for k in _store.all_overrides(CACHE_DIR, kind):
                if k.startswith("mame::"):
                    _store.set_override(CACHE_DIR, k, kind, None)
                    forgotten += 1
        for mode in ("icon", "steam"):
            d = CACHE_DIR / "covers" / mode
            if not d.is_dir():
                continue
            for f in d.iterdir():
                if not f.name.startswith("mame_") or not f.is_file():
                    continue
                key = "mame::" + f.stem.split("__")[0][len("mame_"):]
                if key in manual:
                    continue
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass
        return {"ok": True, "removed": removed, "kept_manual": len(manual),
                "forgotten": forgotten}

    def log_js(self, level: str, msg: str) -> dict:
        """Wpis z warstwy przeglądarki. Bez tego błędy JS ginęły bezpowrotnie:
        WebView2 nie ma widocznej konsoli, a okno diagnostyczne bywa wyłączone."""
        from pylinks.core import log as _log
        _log.write("js/" + (level or "log")[:8], str(msg)[:2000])
        return {"ok": True}

    def get_log(self, lines: int = 300) -> dict:
        """Ostatnie wiersze dziennika + jego ścieżka (do podejrzenia w oknie)."""
        from pylinks.core import log as _log
        return {"path": _log.path(), "text": _log.tail(lines)}

    def clear_log(self) -> dict:
        """Czyści dziennik — żeby po odtworzeniu problemu zostały same świeże wpisy."""
        from pylinks.core import log as _log
        try:
            Path(_log.path()).write_text("", encoding="utf-8")
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "reason": str(e)}

    def open_log_dir(self) -> dict:
        """Pokazuje dziennik w menedżerze plików (Explorer / xdg-open / open)."""
        import subprocess
        import sys
        from pylinks.core import log as _log
        pth = Path(_log.path())
        try:
            if os.name == "nt":
                if pth.is_file():
                    subprocess.Popen(["explorer", "/select,", str(pth)])
                else:
                    subprocess.Popen(["explorer", str(pth.parent)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(pth.parent)])
            else:                               # Linux itd. — xdg-open na katalogu
                subprocess.Popen(["xdg-open", str(pth.parent)])
            return {"ok": True, "path": str(pth)}
        except Exception as e:
            return {"ok": False, "reason": str(e)}

    def mame_categories(self) -> dict:
        """Drzewo kategorii z catver.ini dla gier OBECNYCH w bibliotece.

        catver.ini opisuje sety łańcuchem „Kategoria / Podkategoria" i sam
        oznacza treści dla dorosłych („* Mature *"). To dwupoziomowe drzewo
        zastępuje płaską listę gatunków — jest dokładniejsze i pełne (100 %).
        """
        from pylinks.services import mame as _m
        cfg = _load_config()
        a = (cfg.get("arcade_library", {}) or {})
        lib = a.get("lib_dir", "")
        tree = _m.category_tree(CACHE_DIR, lib) if lib else {}
        return {"tree": tree, "skip": list(a.get("skip_cats") or []),
                "total": sum(e["titles"] for e in tree.values())}

    def mame_set_categories(self, skip: list) -> dict:
        """Zapisuje wyłączone gałęzie („Shooter" albo „Shooter / Gallery")."""
        cfg = _load_config()
        a = dict(cfg.get("arcade_library", {}) or {})
        a["skip_cats"] = sorted({str(x) for x in (skip or []) if x})
        return self.save_config({"arcade_library": a})

    def mame_genres(self) -> list:
        """Gatunki obecne w bibliotece + liczba tytułów (do filtrów w interfejsie)."""
        from pylinks.services import mame as _m
        a = (_load_config().get("arcade_library", {}) or {})
        return _m.library_genres(CACHE_DIR, a.get("lib_dir", ""))

    @staticmethod
    def _lnk_set(path) -> str:
        """Nazwa zestawu, do której celuje istniejący skrót.

        Argumenty to `-rompath "…" <set>`, więc bierzemy OSTATNI człon — porównanie
        całego łańcucha zawsze byłoby różne i wykrywanie kolizji nazw przestałoby
        działać (a przy okazji: starsze skróty mają sam set, co też tu wchodzi).

        COM inicjalizujemy TUTAJ, a nie liczymy na stan wątku: przy pustym wyniku
        wykrywanie kolizji nazw milcząco przepuszczało nadpisanie cudzego skrótu.
        Na Linuksie skrót to .desktop — zwykły tekst, wystarczy przeczytać Exec=.
        """
        if str(path).lower().endswith(".desktop"):
            exec_line = _ini_value(Path(path), "exec=")
            if not exec_line:
                return ""
            try:
                import shlex
                argv = shlex.split(exec_line)
            except ValueError:
                return ""
            return argv[-1] if argv else ""
        try:
            import pythoncom, win32com.client  # type: ignore
        except Exception:
            return ""
        co = False
        try:
            pythoncom.CoInitialize()
            co = True
        except Exception:
            pass
        try:
            sh = win32com.client.Dispatch("WScript.Shell")
            raw = (sh.CreateShortcut(str(path)).Arguments or "").strip()
            return raw.rsplit(None, 1)[-1] if raw else ""
        except Exception:
            return ""
        finally:
            if co:
                try:
                    import gc
                    gc.collect()
                    pythoncom.CoUninitialize()
                except Exception:
                    pass

    def mame_set_version(self, key: str, setname: str) -> dict:
        """Zapamiętuje, KTÓRA wersja tytułu jest wybrana (trwale, w bazie)."""
        if not key or not setname:
            return {"ok": False}
        _store.set_override(CACHE_DIR, key, "mame_set", setname)
        for g in self._mame_games:
            if _game_key(g) == key:
                g["chosen"] = setname
                break
        return {"ok": True, "key": key, "set": setname}

    def mame_verify(self, sets: list) -> dict:
        """Czy wskazane wersje da się URUCHOMIĆ z biblioteki (BIOS/rodzic/urządzenia)."""
        from pylinks.services import mame as _m
        a = (_load_config().get("arcade_library", {}) or {})
        exe, lib = a.get("mame_exe", ""), a.get("lib_dir", "")
        if not exe or not lib:
            return {}
        return _m.verify(exe, lib, *[s for s in (sets or []) if s])

    def mame_export(self, sets: list, make_shortcuts: bool = True) -> dict:
        """Eksportuje WYBRANE wersje do katalogu do grania i robi skróty.

        Kolejność jest istotna: najpierw złożenie zestawu, potem `-verifyroms`,
        a skrót powstaje TYLKO dla wersji, która przeszła weryfikację — żeby nie
        zostawiać w LINKS pozycji, która i tak się nie uruchomi.
        Skrót przekazuje `-rompath` jawnie — katalog do grania jest ustawieniem
        użytkownika i nie musi być domyślnym `roms/` MAME.
        """
        import threading
        from pylinks.services import mame as _m
        if self._job_busy():
            return {"started": False, "reason": "zadanie już trwa"}
        cfg = _load_config()
        a = cfg.get("arcade_library", {}) or {}
        exe, lib, roms = a.get("mame_exe", ""), a.get("lib_dir", ""), a.get("roms_dir", "")
        if not (exe and lib and roms):
            return {"started": False,
                    "reason": "ustaw mame.exe, bibliotekę i katalog do grania"}
        wanted = [x for x in (sets or []) if x]
        if not wanted:
            return {"started": False, "reason": "nie wskazano wersji"}
        self._job_reset("mame", len(wanted))

        def _worker():
            from pylinks.services import export as _exp
            from pylinks.core.naming import safe_name
            from pylinks.core import log as _log
            _log.write("mame", f"eksport {len(wanted)} zestawow -> {roms}")
            try:
                import pythoncom
                pythoncom.CoInitialize()
            except Exception:
                pythoncom = None
            creator = _exp.ShortcutCreator("")
            out_dir = LINKS_DIR / "MAME"
            icons_dir = CACHE_DIR / "icons"
            done, ok, failed = 0, [], []
            for st in wanted:
                if self._job.get("cancel"):
                    break
                self._job["current"] = st
                try:
                    r = _m.export_set(CACHE_DIR, lib, roms, exe, st,
                                      progress=lambda m: self._job.update({"phase": m}))
                except Exception as ex:
                    r = {"ok": False, "reason": str(ex)}
                if r.get("ok"):
                    ok.append(st)
                    if make_shortcuts:
                        # W interfejsie pokazujemy pełny opis (z wersją), ale PLIK
                        # skrótu ma nosić samą nazwę gry — bez nawiasu z regionem.
                        g = next((x for x in self._mame_games
                                  if st in [v["name"] for v in x.get("variants", [])]
                                  or x.get("set") == st), None)
                        title = st
                        for v in (g or {}).get("variants", []):
                            if v["name"] == st:
                                title = v["title"]
                                break
                        else:
                            title = (g or {}).get("name") or st
                        full_title = title
                        title = _m.clean_title(title)
                        # Klucz grafiki należy do GRUPY tytułu, nie do wybranego
                        # wariantu: siatka pobiera okładkę pod `mame::<set grupy>`,
                        # więc szukanie jej pod `mame::<klon>` zawsze chybiało.
                        # Stąd ikonę miały tylko te skróty, gdzie wybrana wersja
                        # była zarazem setem nadrzędnym (baddudes, mk, tekken).
                        key = f"mame::{((g or {}).get('set') or st).lower()}"
                        ico_path = ""
                        try:
                            # Ikona powstaje z OKŁADKI w cache. Przy eksporcie
                            # arcade zwykle jej jeszcze nie ma — użytkownik nie
                            # klikał „Pobierz", a bez tego skrót wychodził z gołą
                            # ikoną MAME. Dociągamy ją tutaj, raz, na żądanie.
                            from pylinks.services import art as _art
                            if not _art.has_cover(CACHE_DIR, key, "icon"):
                                _art.fetch_cover(cfg, CACHE_DIR, key, g or {}, "icon")
                            fit, off = self._crop_for(key, cfg)
                            data = _exp.bake_ico(CACHE_DIR, key, fit, off,
                                                 **_exp.bake_args(cfg, g or {}))
                            if data:
                                ico_path = _exp.write_cache_ico(icons_dir, key, data)
                            else:
                                _log.write("mame", f"{st}: skrót bez ikony — "
                                                   f"brak okładki dla {title}")
                        except Exception as ex:
                            ico_path = ""
                            _log.write("mame", f"{st}: ikona nieudana: {ex}")
                        try:
                            out_dir.mkdir(parents=True, exist_ok=True)
                            nm = safe_name(title)
                            dst = out_dir / f"{nm}{_exp.SHORTCUT_EXT}"
                            # Ta sama oczyszczona nazwa bywa zajęta przez INNĄ grę:
                            # „Tetris" to 7 osobnych automatów, a „Trivial Pursuit"
                            # 9 edycji. Wtedy wracamy do PEŁNEGO tytułu — nawias
                            # niesie wtedy sens („Genus II Edition"), w odróżnieniu
                            # od nazwy zestawu, która nic nie mówi.
                            if dst.exists() and self._lnk_set(dst) not in ("", st):
                                dst = out_dir / f"{safe_name(full_title)}{_exp.SHORTCUT_EXT}"
                                if dst.exists() and self._lnk_set(dst) not in ("", st):
                                    dst = out_dir / f"{safe_name(full_title)} ({st}){_exp.SHORTCUT_EXT}"
                            # -rompath MUSI być w skrócie. Wcześniej szedł sam
                            # set, przy założeniu że katalog do grania jest
                            # domyślnym `roms` MAME — a on jest konfigurowalny
                            # i u użytkownika wskazuje gdzie indziej. Efekt:
                            # `-verifyroms` (który rompath dostaje) przechodził,
                            # skrót powstawał, a gra kończyła się „NOT FOUND".
                            args = '-rompath "%s" %s' % (roms, st)
                            creator.make_lnk(str(dst), exe, args,
                                             ico_path, str(Path(exe).parent))
                        except Exception:
                            pass
                else:
                    failed.append({"set": st, "reason": r.get("reason", "")})
                done += 1
                self._job["done"] = done
            if pythoncom is not None:
                try:
                    import gc
                    gc.collect()
                    pythoncom.CoUninitialize()
                except Exception:
                    pass
            self._job["running"] = False
            self._job["current"] = ""
            self._job["phase"] = ""
            self._job["result"] = {"kind": "mame", "ok": len(ok),
                                   "failed": failed, "sets": ok}

        threading.Thread(target=_worker, daemon=True).start()
        return {"started": True, "total": len(wanted)}

    def test_apis(self) -> dict:
        """Sprawdza WSZYSTKIE używane API naraz → {nazwa: {ok, info}}."""
        return {"sgdb": self.test_sgdb(), "steam": self.test_steam_api(),
                "igdb": self.test_igdb(), "tgdb": self.test_tgdb(),
                "screenscraper": self.test_screenscraper(),
                "libretro": self.test_libretro(), "logos": self.test_logos_repo()}

    def test_screenscraper(self) -> dict:
        """ScreenScraper.fr — wymaga danych DEWELOPERA, nie tylko konta usera."""
        from pylinks.services.screenscraper import ScreenScraper
        e = (_load_config().get("extra_sources", {}) or {})
        ss = ScreenScraper(e.get("ss_devid", ""), e.get("ss_devpassword", ""),
                           e.get("ss_user", ""), e.get("ss_password", ""))
        if not ss.ok:
            return {"ok": False, "reason": "brak devid/devpassword — samo konto "
                                           "użytkownika API odrzuca"}
        q = ss.user_quota()
        if not q.get("ok"):
            return {"ok": False, "reason": q.get("reason") or "odrzucone"}
        return {"ok": True, "info": f"{q.get('today', '?')}/{q.get('max_day', '?')} "
                                    f"zapytań dziś, {q.get('threads', '?')} wątków"}

    def test_steam_api(self) -> dict:
        """Steam Web API — klucz + SteamID64 (lista posiadanych gier)."""
        import urllib.request
        cfg = _load_config()
        ak = cfg.get("api_keys", {}) or {}
        key, sid = (ak.get("steam_api_key") or ""), (ak.get("steam_id64") or "")
        if not key:
            return {"ok": False, "reason": "brak klucza"}
        if not sid:
            return {"ok": False, "reason": "brak SteamID64"}
        url = (f"https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
               f"?key={key}&steamid={sid}&format=json")
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    url, headers={"User-Agent": "Mozilla/5.0"}), timeout=15) as r:
                data = json.loads(r.read()).get("response", {})
        except Exception as e:
            code = getattr(e, "code", None)
            if code == 403:
                return {"ok": False, "reason": "klucz odrzucony (403)"}
            return {"ok": False, "reason": f"{type(e).__name__}"}
        if "games" not in data and not data.get("game_count"):
            return {"ok": False, "reason": "brak danych — sprawdź SteamID64 "
                                           "albo prywatność profilu"}
        return {"ok": True, "info": f"{data.get('game_count', 0)} gier"}

    def test_igdb(self) -> dict:
        """IGDB — token Twitch (client id + secret) i próbne zapytanie."""
        import urllib.request
        cfg = _load_config()
        es = cfg.get("extra_sources", {}) or {}
        cid, sec = (es.get("igdb_client_id") or ""), (es.get("igdb_client_secret") or "")
        if not cid or not sec:
            return {"ok": False, "reason": "brak Client ID / Secret"}
        url = (f"https://id.twitch.tv/oauth2/token?client_id={cid}"
               f"&client_secret={sec}&grant_type=client_credentials")
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(url, method="POST"), timeout=15) as r:
                json.loads(r.read())
        except Exception as e:
            code = getattr(e, "code", None)
            try:
                msg = json.loads(e.read()).get("message", "")   # type: ignore
            except Exception:
                msg = ""
            if code == 403:
                return {"ok": False, "reason": msg or "nieprawidłowy Client Secret"}
            if code == 400:
                return {"ok": False, "reason": msg or "nieprawidłowy Client ID"}
            return {"ok": False, "reason": f"{type(e).__name__}"}
        from pylinks.services.extra_art import ExtraArtSources
        x = ExtraArtSources(cfg)
        n = len(x.igdb_candidates("Celeste", "PC", limit=4)) if es.get("igdb") else 0
        return {"ok": True, "info": (f"token OK, {n} grafik testowo"
                                     if es.get("igdb") else "token OK (źródło wyłączone)")}

    def test_tgdb(self) -> dict:
        """TheGamesDB — klucz API (próbne wyszukanie)."""
        import urllib.request
        es = (_load_config().get("extra_sources", {}) or {})
        key = es.get("tgdb_key") or ""
        if not key:
            return {"ok": False, "reason": "brak klucza"}
        url = (f"https://api.thegamesdb.net/v1/Games/ByGameName"
               f"?apikey={key}&name=celeste&fields=overview")
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    url, headers={"User-Agent": "Mozilla/5.0"}), timeout=15) as r:
                resp = json.loads(r.read())
        except Exception as e:
            code = getattr(e, "code", None)
            body = {}
            try:
                body = json.loads(e.read())                     # type: ignore
            except Exception:
                pass
            if code in (401, 403):
                return {"ok": False, "reason": "klucz odrzucony"}
            if code == 429:
                # TGDB ma limit MIESIĘCZNY — klucz jest dobry, tylko wyczerpany
                hrs = int((body.get("allowance_refresh_timer") or 0) / 3600)
                return {"ok": False,
                        "reason": f"wyczerpany limit miesięczny (odnowa za ~{hrs} h)"}
            return {"ok": False, "reason": f"{type(e).__name__}"}
        if resp.get("code") != 200:
            return {"ok": False, "reason": resp.get("status") or "odpowiedź != 200"}
        rem = (resp.get("remaining_monthly_allowance")
               if "remaining_monthly_allowance" in resp else None)
        info = f"{len(resp.get('data', {}).get('games', []))} wyników"
        if rem is not None:
            info += f", limit miesięczny: {rem}"
        return {"ok": True, "info": info}

    def test_libretro(self) -> dict:
        """Libretro thumbnails — repozytorium bez klucza (+ indeks MAME)."""
        from pylinks.services.extra_art import libretro_candidates
        from pylinks.services import libretro_mame as _lm
        try:
            n = len(libretro_candidates("PS1", "Tekken 3"))
        except Exception as e:
            return {"ok": False, "reason": f"{type(e).__name__}"}
        idx = _lm.refresh_index(CACHE_DIR)
        extra = (f", indeks arcade: {idx.get('titles', 0)} tytułów"
                 if idx.get("ok") else f", indeks arcade: {idx.get('reason', 'błąd')}")
        return {"ok": n > 0,
                "info": (f"{n} kandydatów" if n else "brak trafień") + extra}

    def test_logos_repo(self) -> dict:
        """Repozytorium logotypów platform (console-logos, bez klucza)."""
        import urllib.request
        from pylinks.services import spine_logos as _sl
        style = _load_config().get("spine_logo_style", "Light - Color")
        path = urllib.request.quote(f"{_sl._CLOGOS_FULLSET}/{style}/Consoles", safe="/")
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    _sl._CLOGOS_API + path,
                    headers={"User-Agent": "Mozilla/5.0"}), timeout=20) as r:
                arr = json.loads(r.read())
        except Exception as e:
            code = getattr(e, "code", None)
            if code == 403:
                return {"ok": False, "reason": "limit zapytań GitHub (spróbuj później)"}
            return {"ok": False, "reason": f"{type(e).__name__}"}
        n = len(arr) if isinstance(arr, list) else 0
        return {"ok": n > 0, "info": f"{n} plików w „Consoles”"}

    def test_sgdb(self) -> dict:
        """SteamGridDB — klucz API (tanie zapytanie)."""
        from pylinks.services.art import SgdbClient
        cli = SgdbClient((_load_config().get("api_keys", {}) or {}).get("sgdb_key", ""))
        if not cli.ok:
            return {"ok": False, "reason": "brak klucza"}
        res = cli.search("celeste", 3)
        if not res:
            return {"ok": False, "reason": "brak odpowiedzi — zły klucz lub brak sieci"}
        return {"ok": True, "info": f"{len(res)} wyników"}
