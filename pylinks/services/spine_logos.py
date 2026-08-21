"""services.spine_logos — warianty logotypów platform (grzbiet) + instalacja.

Port z wersji tkinter: zestaw „console-logos" (PRO100BYTE) w kilku
kolorystykach. Warianty trzymane lokalnie w <logo_dir>/_variants/<style>/.
Wybrany wariant kopiowany jako <logo_dir>/<KEY>.png (aktywne logo grzbietu).
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import urllib.request
from pathlib import Path

from pylinks.core.spine import spine_canon_key

_CLOGOS_API = "https://api.github.com/repos/PRO100BYTE/console-logos/contents/"
_CLOGOS_FULLSET = "Normal (1920x1080 max) (Full Set) v2.1"
_CLOGOS_SUBDIRS = ("Consoles", "Handhelds", "Arcade", "Computers")

SPINE_LOGO_STYLES = {
    "Białe (na ciemny grzbiet)": "Light - Just White",
    "Kolorowe": "Light - Color",
    "Czarne (na jasny grzbiet)": "Dark - Just Black",
}

PLATFORM_LOGO_TARGET = {
    "PS1": "Sony PlayStation", "PS2": "Sony PlayStation 2", "PS3": "Sony PlayStation 3",
    "PS4": "Sony PlayStation 4", "PSP": "Sony PSP", "PSVITA": "Sony PlayStation Vita",
    "N64": "Nintendo 64", "SNES": "Super Nintendo Entertainment System",
    "NES": "Nintendo Entertainment System", "GCN": "Nintendo GameCube",
    "WII": "Nintendo Wii", "WIIU": "Nintendo Wii U", "NSW": "Nintendo Switch",
    "GB": "Nintendo Game Boy", "GBC": "Nintendo Game Boy Color",
    "GBA": "Nintendo Game Boy Advance", "NDS": "Nintendo DS", "3DS": "Nintendo 3DS",
    "MD": "Sega Genesis", "SMS": "Sega Master System", "GG": "Sega Game Gear",
    "SATURN": "Sega Saturn", "DC": "Sega Dreamcast",
    "NAOMI": "Sega Naomi", "NAOMI2": "Sega Naomi 2",
    "3DO": "3DO Interactive Multiplayer", "PCENGINE": "NEC TurboGrafx-16",
    "NEOGEO": "SNK Neo Geo", "ATARI2600": "Atari 2600", "ATARI7800": "Atari 7800",
    "XBOX": "Microsoft Xbox", "X360": "Microsoft Xbox 360", "XONE": "Microsoft Xbox One",
    "MSX": "Microsoft MSX", "MSX2": "Microsoft MSX2",
    "SEGACD": "Sega CD", "32X": "Sega 32X",
    "MAME": "MAME", "ARCADE": "Arcade",
}


def _style_folder(style: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", style)


def _variants_dir(logo_dir, style: str) -> Path:
    return Path(logo_dir) / "_variants" / _style_folder(style)


def _tokens(s: str) -> list:
    """Tokeny nazwy: bez nawiasów, separatory (spacje/-/./,) → granice tokenów.
    Dzięki temu indeks wariantu jest osobnym tokenem: „Sega Naomi 2-01" →
    [sega, naomi, 2, 01] (platforma kończy się na „2"), a „Sega Naomi -20" →
    [sega, naomi, 20] (całe „20" to indeks, NIE „2"+„0")."""
    s = re.sub(r"\([^)]*\)", " ", str(s or "")).lower()   # usuń (Japan)/(Alt Orange)…
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.split()


def _matches(file_toks: list, tgt_toks: list) -> bool:
    """Plik należy do targetu, gdy zaczyna się DOKŁADNIE jego tokenami, a reszta
    (indeks wariantu) to same tokeny-liczby."""
    n = len(tgt_toks)
    if n == 0 or len(file_toks) < n or file_toks[:n] != tgt_toks:
        return False
    return all(t.isdigit() for t in file_toks[n:])


def target_for(platform: str) -> str:
    return (PLATFORM_LOGO_TARGET.get(str(platform).upper())
            or PLATFORM_LOGO_TARGET.get(spine_canon_key(platform)) or "")


def list_variants(logo_dir, platform: str, style: str, limit: int = 60) -> list:
    """Warianty logo danej platformy z LOKALNEGO cache. [{name, source}].

    Dopasowanie tokenowe: plik zaczyna się tokenami targetu, reszta = indeks.
    Warianty pasujące do DŁUŻSZEGO targetu (np. „Sega Naomi 2" / „Sony PlayStation 2")
    są wykluczane z krótszego („Sega Naomi" / „Sony PlayStation")."""
    target = target_for(platform)
    if not target:
        return []
    tgt = _tokens(target)
    if not tgt:
        return []
    # targety, które ROZSZERZAJĄ nasz (te same tokeny + dodatkowy) — do wykluczenia
    longer = []
    for t in PLATFORM_LOGO_TARGET.values():
        tk = _tokens(t)
        if len(tk) > len(tgt) and tk[:len(tgt)] == tgt:
            longer.append(tk)
    out = []
    cache = _variants_dir(logo_dir, style)
    if cache.is_dir():
        for p in sorted(cache.glob("*.png")):
            ft = _tokens(p.stem)
            if not _matches(ft, tgt):
                continue
            if any(_matches(ft, L) for L in longer):   # należy do bardziej szczegółowego
                continue
            out.append({"name": p.stem, "source": str(p)})
    return out[:limit]


def variant_datauri(source: str, box: int = 220) -> str | None:
    """Miniatura wariantu jako PNG data-URI (na szachownicy — logo bywa jasne)."""
    try:
        from PIL import Image
        from io import BytesIO
        im = Image.open(source).convert("RGBA")
        im.thumbnail((box, box), Image.LANCZOS)
        buf = BytesIO(); im.save(buf, "PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        try:
            data = Path(source).read_bytes()
            return "data:image/png;base64," + base64.b64encode(data).decode("ascii")
        except Exception:
            return None


def install_variant(logo_dir, platform: str, source: str) -> bool:
    """Kopiuje wybrany wariant jako aktywne logo <KEY>.png."""
    canon = spine_canon_key(platform)
    dst = Path(logo_dir) / f"{canon}.png"
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dst)
        return True
    except Exception:
        return False


def cached_styles(logo_dir) -> list:
    """Style, dla których są pobrane warianty (folder w _variants/)."""
    base = Path(logo_dir) / "_variants"
    if not base.is_dir():
        return []
    have = {p.name for p in base.iterdir() if p.is_dir()}
    return [s for s in SPINE_LOGO_STYLES.values() if _style_folder(s) in have]


def download_variants(logo_dir, style: str, progress=None) -> tuple:
    """Pobiera CAŁY zestaw wariantów danej kolorystyki do cache. (nowe, wszystkie)."""
    cache = _variants_dir(logo_dir, style)
    try:
        cache.mkdir(parents=True, exist_ok=True)
    except Exception:
        return (0, 0)
    entries = []
    for sub in _CLOGOS_SUBDIRS:
        path = urllib.request.quote(f"{_CLOGOS_FULLSET}/{style}/{sub}", safe="/")
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    _CLOGOS_API + path, headers={"User-Agent": "Mozilla/5.0"}),
                    timeout=25) as r:
                arr = json.loads(r.read())
        except Exception:
            continue
        for e in arr:
            nm = e.get("name", "") if isinstance(e, dict) else ""
            url = e.get("download_url") if isinstance(e, dict) else None
            if nm.lower().endswith(".png") and url:
                entries.append((nm, url))
    total, got = len(entries), 0
    for i, (nm, url) in enumerate(entries):
        out = cache / nm
        if out.exists():
            continue
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    url, headers={"User-Agent": "Mozilla/5.0"}), timeout=25) as r:
                b = r.read()
            tmp = out.with_name(out.name + ".tmp"); tmp.write_bytes(b)
            os.replace(tmp, out); got += 1
        except Exception:
            pass
        if progress:
            progress(i + 1, total)
    return (got, total)
