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
    """Stabilny klucz gry PC (do ikon/wyboru). Nie zależy od pozycji."""
    src = g.get("source", "pc")
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
        }

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
        return None

    # --- okładki (SteamGridDB + cache dyskowy) ---
    def cover_cached(self, key: str) -> dict:
        """Szybki odczyt okładki z cache dyskowego (bez sieci)."""
        uri = art.cached_cover(CACHE_DIR, key)
        return {"key": key, "found": bool(uri), "data_uri": uri, "cached": True}

    def fetch_cover(self, key: str) -> dict:
        """Pobiera okładkę z SGDB (lub czyta z cache). Może ruszyć sieć."""
        g = self._find_game(key)
        if not g:
            return {"key": key, "found": False, "data_uri": None,
                    "reason": "nieznana gra (najpierw skan)"}
        cfg = _load_config()
        return art.fetch_cover(cfg, CACHE_DIR, key, g)

    def cover_candidates(self, key: str, query: str = "") -> dict:
        """Lista okładek-kandydatów do okna wyboru (miniatury jako data-URI)."""
        g = self._find_game(key)
        if not g:
            return {"key": key, "candidates": [], "reason": "nieznana gra"}
        cfg = _load_config()
        cands = art.candidates_for_game(cfg, g, query=query)
        return {"key": key, "candidates": cands,
                "reason": "" if cands else "brak wyników / brak klucza SGDB"}

    def sgdb_suggest(self, query: str) -> list:
        """Podpowiedzi tytułów (autouzupełnianie SGDB) dla pola szukania."""
        return art.suggest_games(_load_config(), query)

    def cover_candidates_by_id(self, key: str, sgdb_id) -> dict:
        """Kandydaci dla konkretnego SGDB game id (klik w podpowiedź)."""
        return self.cover_candidates(key, str(sgdb_id))

    def choose_cover(self, key: str, url: str) -> dict:
        """Ustawia wskazaną okładkę jako wybraną: zapis w cache + config."""
        res = art.save_chosen_cover(CACHE_DIR, key, url)
        if res.get("found"):
            self._save_selection(key, url)
        return {"key": key, "found": res.get("found", False),
                "data_uri": res.get("data_uri")}

    def reset_cover(self, key: str) -> dict:
        """Przywraca automatyczną okładkę: usuwa wybór z cache + config,
        pobiera na nowo (kwadrat → ikona → plakat)."""
        art.clear_cover(CACHE_DIR, key)
        self._save_selection(key, None)
        g = self._find_game(key)
        if not g:
            return {"key": key, "found": False, "data_uri": None}
        return art.fetch_cover(_load_config(), CACHE_DIR, key, g)

    # --- trwały zapis wyboru okładki (config.selected_icon_keys) ---
    def _save_selection(self, key: str, url) -> None:
        try:
            raw = {}
            if CONFIG_PATH.exists():
                raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            else:
                raw = _default_config()
            sel = raw.get("selected_icon_keys")
            if not isinstance(sel, dict):
                sel = {}
            if url is None:
                sel.pop(key, None)       # reset — usuń wybór
            else:
                sel[key] = url
            raw["selected_icon_keys"] = sel
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
