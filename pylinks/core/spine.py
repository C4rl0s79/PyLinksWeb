"""core.spine — grzbiety platform: kanoniczny klucz + wyszukanie logo.

Grzbiet („spine") to pionowy pasek z logo platformy nakładany na ikonę ROM,
żeby odróżnić gry o tym samym tytule na różnych platformach. Do WYŚWIETLANIA
robimy to w CSS (logo jako data-URI); PIL (add_platform_spine) zostaje do
finalnego .ico przy eksporcie.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

PLATFORM_SPINE_ALIAS: dict = {
    "SNESMSU1": "SNES", "SNES-MSU1": "SNES", "MSU1": "SNES", "MSU-1": "SNES",
    "SFC": "SNES", "SUPERFAMICOM": "SNES",
    "FAMICOM": "NES", "FDS": "NES",
    "N64DD": "N64",
    "SEGACD": "MD", "MEGACD": "MD", "32X": "MD", "GENESIS": "MD", "MEGADRIVE": "MD",
    "TG16": "PCENGINE", "TURBOGRAFX16": "PCENGINE", "PCE": "PCENGINE",
    "PSX": "PS1", "PLAYSTATION": "PS1",
    "GAMECUBE": "GCN", "NGC": "GCN",
    "SWITCH": "NSW",
    "XBOX360": "X360",
    "XBOXONE": "XONE", "XBONE": "XONE",
    "VITA": "PSVITA",
    "DS": "NDS",
    "NAOMI2": "DC", "NAOMI": "DC",
}


def spine_canon_key(key: str) -> str:
    """Kanoniczny klucz platformy (rozwija aliasy, normalizuje separatory)."""
    k = re.sub(r"[\s._\-]+", "", str(key or "")).upper()
    return PLATFORM_SPINE_ALIAS.get(k, k)


def platform_logo_uri(logo_dir: str, system: str) -> str | None:
    """Logo platformy jako data-URI (własny klucz → alias bazowy)."""
    if not logo_dir or not system:
        return None
    orig = re.sub(r"[\s._\-]+", "", str(system)).upper()
    canon = spine_canon_key(system)
    base = Path(logo_dir)
    for key in (orig, canon):
        for ext in (".png", ".webp"):
            p = base / f"{key}{ext}"
            if p.is_file():
                try:
                    data = p.read_bytes()
                    mime = "image/webp" if ext == ".webp" else "image/png"
                    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
                except Exception:
                    return None
    return None
