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


def _load_config() -> dict:
    try:
        if CONFIG_PATH.exists():
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return migrate_config(raw)
    except Exception:
        pass
    return _default_config()


class Api:
    """Metody dostępne z JS. Zwracają dict/list/str (JSON-serializowalne)."""

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

    # --- config ---
    def get_config(self) -> dict:
        return _load_config()
