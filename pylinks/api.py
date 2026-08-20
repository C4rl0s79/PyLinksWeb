"""pylinks.api — most Python↔JS wystawiany do frontendu (pywebview js_api).

Cienka warstwa: metody wołane z JS jako `window.pywebview.api.<metoda>(...)`.
Cała logika docelowo trafia do pylinks/services/* — Api tylko orkiestruje i
zwraca wartości JSON-serializowalne. Faza 0: dowód działania stosu + realne
dane z silnika (presety ROM, config, paleta, skan istniejących skrótów).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from pylinks import __version__
from pylinks.app_paths import SCRIPT_DIR, CACHE_DIR, LINKS_DIR, CONFIG_PATH
from pylinks.constants import C
from pylinks.config.defaults import _default_config
from pylinks.config.migrate import migrate_config
from pylinks.roms.presets import ROM_SYSTEM_PRESETS
from pylinks.services import scan_pc, art


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
    return cfg


# sekrety szyfrowane DPAPI (ścieżki w configu)
SECRET_PATHS = [
    ("api_keys", "sgdb_key"),
    ("api_keys", "steam_api_key"),
    ("extra_sources", "igdb_client_secret"),
    ("extra_sources", "tgdb_key"),
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


_ICON_URI_CACHE: dict = {}


def _shortcut_icon_uri(f) -> "str | None":
    """Ikona (.ico) wskazana przez skrót .lnk/.url → PNG data-URI (z cache)."""
    key = str(f)
    if key in _ICON_URI_CACHE:
        return _ICON_URI_CACHE[key]
    ico = ""
    try:
        if f.suffix.lower() == ".url":
            for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.lower().startswith("iconfile="):
                    ico = line.split("=", 1)[1].strip()
                    break
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
        # cache upieczonych miniatur w RAM: (key,mode,fit,offset) → data-URI
        self._baked: dict = {}
        # ostatnie ręczne zapytanie/ID per gra (do zapamiętania dopasowania SGDB)
        self._pending: dict = {}

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

    def set_thumb_size(self, px) -> dict:
        try:
            px = max(96, min(320, int(px)))
        except Exception:
            px = 160
        self._save_config_value("thumb_size", px)
        return {"ok": True, "thumb_size": px}

    def get_theme(self) -> dict:
        return dict(C)

    # --- presety ROM (z silnika) ---
    def list_rom_presets(self) -> list:
        out = []
        for p in ROM_SYSTEM_PRESETS:
            out.append({
                "name":    p.get("name", ""),
                "display": p.get("display", p.get("name", "")),
                "exts":    p.get("primary_ext", ""),
                "note":    p.get("note", ""),
            })
        return out

    # --- biblioteka: skan istniejących skrótów LINKS/<platforma>/ ---
    def list_platforms(self) -> list:
        """Platformy = podfoldery LINKS/ z liczbą skrótów (bez Kolekcje)."""
        out = []
        if LINKS_DIR.is_dir():
            for d in sorted(LINKS_DIR.iterdir()):
                if not d.is_dir() or d.name.lower() in ("kolekcje", "_collections"):
                    continue
                n = sum(1 for f in d.iterdir()
                        if f.suffix.lower() in (".lnk", ".url"))
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
                if f.suffix.lower() in (".lnk", ".url"):
                    out.append({"name": f.stem, "platform": d.name,
                                "icon": _shortcut_icon_uri(f)})
        return out

    # --- skan bibliotek PC (Steam/GOG/Epic/Extra) ---
    def scan_pc(self) -> dict:
        """Uruchamia pełny skan PC i zwraca odchudzone wpisy + liczniki.

        UWAGA: synchronicznie (Steam Web API może chwilę potrwać). Frontend
        woła to asynchronicznie (Promise) i pokazuje spinner.
        """
        cfg = _load_config()
        res = scan_pc.scan_pc_library(cfg)
        self._pc_games = res["games"]
        return {
            "ok": True,
            "counts": res["counts"],
            "total": len(res["games"]),
            "steam_exe": res.get("steam_exe", ""),
            "games": [_slim_game(g) for g in res["games"]],
        }

    def get_pc_games(self, source: str = "") -> list:
        """Zwraca ostatnio zeskanowane gry PC (opcjonalnie filtr źródła)."""
        games = self._pc_games
        if source:
            games = [g for g in games if g.get("source") == source]
        return [_slim_game(g) for g in games]

    @staticmethod
    def _title_override(key: str, cfg: dict) -> str:
        """Zapamiętane dopasowanie SGDB (query/ID) dla gry — wspólne dla obu trybów."""
        store = cfg.get("sgdb_override", {}) or {}
        return str(store.get(key, "") or "") if isinstance(store, dict) else ""

    def _save_override(self, key: str) -> None:
        """Utrwala ostatnie ręczne zapytanie/ID dla gry (config.sgdb_override)."""
        q = (self._pending.get(key) or "").strip()
        if not q:
            return
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) \
                if CONFIG_PATH.exists() else _default_config()
            store = raw.get("sgdb_override")
            if not isinstance(store, dict):
                store = {}
            store[key] = q
            raw["sgdb_override"] = store
            CONFIG_PATH.write_text(json.dumps(raw, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        except Exception:
            pass

    def _find_game(self, key: str) -> dict | None:
        for g in self._pc_games:
            if _game_key(g) == key:
                return g
        for g in self._rom_games:
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
                "spine": {"enabled": spine_on, "side": spine_side}}

    def set_spine(self, enabled: bool, side: str = "left") -> dict:
        self._save_config_value("icon_platform_spine", bool(enabled))
        self._save_config_value("icon_spine_side", "right" if side == "right" else "left")
        return {"ok": True, "enabled": bool(enabled),
                "side": "right" if side == "right" else "left"}

    def get_rom_games(self, system: str = "") -> list:
        games = self._rom_games
        if system:
            games = [g for g in games if g.get("system") == system]
        return [{"key": _game_key(g), "name": g["name"],
                 "system": g.get("system", ""), "path": g.get("path", "")}
                for g in games]

    # --- eksport skrótów (.lnk/.url + ikony .ico do LINKS/<platforma>/) ---
    def export_pc(self, source: str = "") -> dict:
        games = self._pc_games
        if source:
            games = [g for g in games if g.get("source") == source]
        return self._export(games)

    def export_rom(self, system: str = "") -> dict:
        games = self._rom_games
        if system:
            games = [g for g in games if g.get("system") == system]
        return self._export(games)

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

    def missing_keys(self, kind: str = "pc", filt: str = "") -> list:
        """Klucze gier BEZ istniejącego skrótu w LINKS/<platforma>/."""
        from pylinks.core.naming import safe_name
        if kind == "rom":
            games = [g for g in self._rom_games if not filt or g.get("system") == filt]
        else:
            games = [g for g in self._pc_games if not filt or g.get("source") == filt]
        out = []
        for g in games:
            plat = g.get("system", "") if g.get("source") == "rom" else "PC"
            d = LINKS_DIR / plat
            safe = safe_name(g.get("name", ""))
            if not ((d / (safe + ".lnk")).exists() or (d / (safe + ".url")).exists()):
                out.append(_game_key(g))
        return out

    # --- grafiki: dwa tryby "icon" (kwadrat) / "steam" (portret) ---
    def _raw_uri(self, key: str, mode: str):
        """Surowy obraz jako data-URI z cache RAM (albo z dysku). Kształt robi
        CSS (object-fit/position), więc NIE pieczemy PIL do wyświetlania."""
        ck = (key, mode)
        if ck in self._baked:
            return self._baked[ck]
        uri = art.raw_cover(CACHE_DIR, key, mode)
        if uri:
            self._baked[ck] = uri
        return uri

    def _invalidate(self, key: str) -> None:
        for ck in [k for k in self._baked if k[0] == key]:
            self._baked.pop(ck, None)

    def cover_cached(self, key: str, mode: str = "icon") -> dict:
        """Surowy obraz z cache + per-grę kadr (fit/offset dla CSS)."""
        cfg = _load_config()
        uri = self._raw_uri(key, mode)
        fit, off = self._crop_for(key, cfg)
        return {"key": key, "found": bool(uri), "data_uri": uri,
                "fit": fit, "offset": off, "cached": True}

    def covers_batch(self, keys: list, mode: str = "icon") -> dict:
        """ZBIORCZO w JEDNYM wywołaniu mostu: {key: {uri, fit, offset}}."""
        cfg = _load_config()
        out = {}
        for k in keys:
            uri = self._raw_uri(k, mode)
            if not uri:
                out[k] = None
                continue
            fit, off = self._crop_for(k, cfg)
            out[k] = {"uri": uri, "fit": fit, "offset": off}
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
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) \
                if CONFIG_PATH.exists() else _default_config()
            store = raw.get("icon_crop_by_key")
            if not isinstance(store, dict):
                store = {}
            if entry is None:
                store.pop(key, None)
            else:
                store[key] = entry
            raw["icon_crop_by_key"] = store
            CONFIG_PATH.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

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
        res = art.fetch_cover(cfg, CACHE_DIR, key, g, mode, prefer_portrait,
                              query=override)
        if res.get("found"):
            self._invalidate(key)               # świeżo pobrany raw
            fit, off = self._crop_for(key, cfg)
            res["fit"], res["offset"] = fit, off
        return res

    def cover_candidates(self, key: str, query: str = "", mode: str = "icon") -> dict:
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
        cands = art.candidates_for_game(cfg, g, query=query, mode=mode)
        if mode != "steam" and not query and g.get("source") != "rom":
            try:
                from pylinks.services.extra_art import ExtraArtSources
                extra = ExtraArtSources(cfg).candidates(
                    g.get("name", ""), str(g.get("appid") or ""), "PC")
                seen = {c["id"] for c in cands}
                cands += [c for c in extra if c.get("id") not in seen]
            except Exception:
                pass
        return {"key": key, "candidates": cands,
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

    def reset_cover(self, key: str, mode: str = "icon") -> dict:
        """Przywraca automatyczną grafikę trybu: usuwa wybór + pobiera na nowo."""
        art.clear_cover(CACHE_DIR, key, mode)
        self._save_selection(key, None, mode)
        self._invalidate(key)
        g = self._find_game(key)
        if not g:
            return {"key": key, "found": False, "data_uri": None}
        cfg = _load_config()
        res = art.fetch_cover(cfg, CACHE_DIR, key, g, mode)
        if res.get("found"):
            self._invalidate(key)
            fit, off = self._crop_for(key, cfg)
            res["fit"], res["offset"] = fit, off
        return res

    # --- tryb Steam: kilka typów grafik (grid + hero/tło + logo) ---
    def steam_art_options(self, key: str, query: str = "") -> dict:
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
        res = art.steam_art_options(cfg, g, query=query)
        # bieżące wybory jako URL-e (do podświetlenia wariantu w UI)
        store = cfg.get("steam_art_by_key", {}) or {}
        entry = store.get(key, {}) if isinstance(store, dict) else {}
        res["current"] = {a: (entry.get(a) if isinstance(entry, dict) else None)
                          for a in ("grid", "hero", "logo")}
        res["key"] = key
        return res

    def choose_steam_asset(self, key: str, asset: str, url: str) -> dict:
        """Przypina wariant danego typu (grid/hero/logo) i zapisuje w config."""
        res = art.save_steam_asset(CACHE_DIR, key, asset, url)
        if res.get("found"):
            self._save_steam_asset_sel(key, asset, url)
            self._save_override(key)          # zapamiętaj dopasowanie (oba tryby)
        return {"key": key, "asset": asset, "found": res.get("found", False),
                "data_uri": res.get("data_uri")}

    def reset_steam_asset(self, key: str, asset: str) -> dict:
        """Usuwa wybrany wariant danego typu (reset)."""
        art.clear_steam_asset(CACHE_DIR, key, asset)
        self._save_steam_asset_sel(key, asset, None)
        return {"key": key, "asset": asset, "ok": True}

    def _save_steam_asset_sel(self, key: str, asset: str, url) -> None:
        """Zapis wyboru typu Steam w config.steam_art_by_key[key][asset]."""
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) \
                if CONFIG_PATH.exists() else _default_config()
            store = raw.get("steam_art_by_key")
            if not isinstance(store, dict):
                store = {}
            entry = store.get(key)
            if not isinstance(entry, dict):
                entry = {}
            if url is None:
                entry.pop(asset, None)
            else:
                entry[asset] = url
            if entry:
                store[key] = entry
            else:
                store.pop(key, None)
            raw["steam_art_by_key"] = store
            CONFIG_PATH.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    # --- trwały zapis wyboru (icon→selected_icon_keys, steam→steam_art_by_key) ---
    def _save_selection(self, key: str, url, mode: str = "icon") -> None:
        cfg_key = "steam_art_by_key" if str(mode).lower() == "steam" else "selected_icon_keys"
        try:
            raw = {}
            if CONFIG_PATH.exists():
                raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            else:
                raw = _default_config()
            sel = raw.get(cfg_key)
            if not isinstance(sel, dict):
                sel = {}
            if url is None:
                sel.pop(key, None)       # reset — usuń wybór
            else:
                sel[key] = url
            raw[cfg_key] = sel
            CONFIG_PATH.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

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
            CONFIG_PATH.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

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
            CONFIG_PATH.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
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

    def test_sgdb(self) -> dict:
        """Sprawdza klucz SGDB (tanie zapytanie)."""
        from pylinks.services.art import SgdbClient
        cli = SgdbClient((_load_config().get("api_keys", {}) or {}).get("sgdb_key", ""))
        if not cli.ok:
            return {"ok": False, "reason": "brak klucza"}
        return {"ok": bool(cli.search("celeste", 3))}
