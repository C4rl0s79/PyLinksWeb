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
    try:
        if CONFIG_PATH.exists():
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return migrate_config(raw)
    except Exception:
        pass
    return _default_config()


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
                    out.append({"name": f.stem, "platform": d.name})
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
        self._rom_games = []
        for s in res.get("systems", []):
            for g in s.get("games", []):
                g["source"] = "rom"
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
        res = art.fetch_cover(cfg, CACHE_DIR, key, g, mode, prefer_portrait)
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
        return _load_config()
