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

try:
    from PIL import Image
    _PIL = True
except Exception:
    _PIL = False

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


# ── PIL: pieczenie grzbietu do .ico (eksport) ──────────────────────────────
def _hex_rgba(hexc: str, alpha: int = 255):
    hexc = hexc.lstrip("#")
    r, g, b = int(hexc[0:2], 16), int(hexc[2:4], 16), int(hexc[4:6], 16)
    return (r, g, b, alpha)


def _platform_logo_pil(logo_dir: str, system: str):
    if not _PIL or not logo_dir:
        return None
    orig = re.sub(r"[\s._\-]+", "", str(system)).upper()
    canon = spine_canon_key(system)
    base = Path(logo_dir)
    for key in (orig, canon):
        for ext in (".png", ".webp"):
            p = base / f"{key}{ext}"
            if p.is_file():
                try:
                    return Image.open(p).convert("RGBA")
                except Exception:
                    return None
    return None


def _ensure_logo_contrast(logo):
    """Ciemny jednobarwny wordmark → jasny (czytelny na ciemnym grzbiecie)."""
    try:
        small = logo.convert("RGBA")
        small.thumbnail((64, 64))
        lum = sat = cnt = 0
        for r, g, b, a in small.getdata():
            if a < 40:
                continue
            mx, mn = max(r, g, b), min(r, g, b)
            lum += 0.299 * r + 0.587 * g + 0.114 * b
            sat += 0 if mx == 0 else (mx - mn) / mx
            cnt += 1
        if cnt and (lum / cnt) < 125 and (sat / cnt) < 0.30:
            alpha = logo.convert("RGBA").split()[3]
            light = Image.new("RGBA", logo.size, (238, 238, 238, 0))
            light.putalpha(alpha)
            return light
        return logo
    except Exception:
        return logo


def _cover(im, tw, th):
    """Skaluje, by wypełnić (tw,th), i przycina do środka (jak object-fit:cover)."""
    scale = max(tw / im.width, th / im.height)
    nw, nh = max(1, round(im.width * scale)), max(1, round(im.height * scale))
    src = im.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - tw) // 2, (nh - th) // 2
    return src.crop((left, top, left + tw, top + th))


def add_platform_spine(img, system: str, side: str = "left",
                       frac: float = 0.22, logo_dir: str = ""):
    """Kwadratowa ikona = grzbiet (frac szerokości) + grafika WYPEŁNIAJĄCA resztę
    (cover). DOKŁADNIE jak podgląd CSS w GUI. Bez logo/PIL — zwraca bez zmian."""
    if not _PIL or not system:
        return img
    logo = _platform_logo_pil(logo_dir, system)
    if logo is None:
        return img
    try:
        base = img.convert("RGBA")
        if min(base.size) < 24:
            return base
        S = 512                                   # bok kwadratu (jakość; .ico zejdzie do 256)
        sw = max(14, int(round(S * frac)))        # szerokość grzbietu (22%)
        aw = S - sw                               # obszar grafiki
        artimg = _cover(base, aw, S)              # grafika WYPEŁNIA (cover)
        logo = _ensure_logo_contrast(logo)

        from PIL import ImageDraw
        spine = Image.new("RGBA", (sw, S), _hex_rgba("#141414"))
        d = ImageDraw.Draw(spine)
        if side == "left":
            d.rectangle([sw - 2, 0, sw - 1, S - 1], fill=_hex_rgba("#000000", 120))
            d.line([(1, 0), (1, S)], fill=_hex_rgba("#ffffff", 55))
        else:
            d.rectangle([0, 0, 1, S - 1], fill=_hex_rgba("#000000", 120))
            d.line([(sw - 2, 0), (sw - 2, S)], fill=_hex_rgba("#ffffff", 55))

        lg = logo.rotate(90 if side == "left" else -90, expand=True)
        scale = max(1, int(sw * 0.84)) / max(1, lg.width)
        if lg.height * scale > S * 0.94:
            scale = (S * 0.94) / max(1, lg.height)
        lg = lg.resize((max(1, int(lg.width * scale)), max(1, int(lg.height * scale))),
                       Image.LANCZOS)
        spine.alpha_composite(lg, ((sw - lg.width) // 2, (S - lg.height) // 2))

        out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        if side == "left":
            out.alpha_composite(artimg, (sw, 0)); out.alpha_composite(spine, (0, 0))
        else:
            out.alpha_composite(artimg, (0, 0)); out.alpha_composite(spine, (S - sw, 0))
        return out
    except Exception:
        return img
