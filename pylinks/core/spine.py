"""core.spine — grzbiety platform: kanoniczny klucz + wyszukanie logo.

Grzbiet („spine") to pionowy pasek z logo platformy nakładany na ikonę ROM,
żeby odróżnić gry o tym samym tytule na różnych platformach. Do WYŚWIETLANIA
robimy to w CSS (logo jako data-URI); PIL (add_platform_spine) zostaje do
finalnego .ico przy eksporcie.
"""

from __future__ import annotations

import base64
import re
from functools import lru_cache
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
    # NAOMI / NAOMI 2 mają WŁASNE logotypy (nie Dreamcast) — bez aliasu do DC.
}


def spine_canon_key(key: str) -> str:
    """Kanoniczny klucz platformy (rozwija aliasy, normalizuje separatory)."""
    k = re.sub(r"[\s._\-]+", "", str(key or "")).upper()
    return PLATFORM_SPINE_ALIAS.get(k, k)


@lru_cache(maxsize=256)
def logo_key_for(system: str) -> str:
    """Klucz pliku logo dla systemu — jego WŁASNA nazwa, nie kanoniczna.

    SNESMSU1 czy SEGACD mają alias do SNES/MD (żeby był fallback, gdy nie mają
    własnego logo), ale MOGĄ mieć własne: `platform_logo_uri` sprawdza najpierw
    dokładny klucz. Zapis pod kluczem kanonicznym nadpisywałby logo konsoli bazowej.
    """
    return re.sub(r"[\s._\-]+", "", str(system or "")).upper()


def _canon_from_presets(system: str) -> str:
    """Rozwiązuje nazwę systemu (np. „Sega - Saturn", „Dreamcast", „3DO Interactive
    Multiplayer") na krótki klucz presetu (SATURN/DC/3DO) — po dir_names/display/name.
    Dzięki temu grzbiet znajduje logo niezależnie od tego, jak nazwano system.
    Cache: wynik zależy tylko od nazwy, a skan presetów leciał przy każdej grze."""
    try:
        from pylinks.roms.presets import ROM_SYSTEM_PRESETS
    except Exception:
        return ""
    def _n(s):
        return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())
    want = _n(system)
    if not want:
        return ""
    for p in ROM_SYSTEM_PRESETS:
        cands = [p.get("name", ""), p.get("display", "")] + list(p.get("dir_names", []))
        if any(_n(c) == want for c in cands):
            return str(p.get("name", ""))
    return ""


@lru_cache(maxsize=256)
def _logo_key_candidates(system: str) -> tuple:
    """Uporządkowana lista kluczy plików logo do sprawdzenia dla danego systemu."""
    orig = re.sub(r"[\s._\-]+", "", str(system or "")).upper()
    keys = [orig, spine_canon_key(system)]
    preset = _canon_from_presets(system)
    if preset:
        keys.append(re.sub(r"[\s._\-]+", "", preset).upper())
        keys.append(spine_canon_key(preset))
    # unikalne, zachowując kolejność (tuple — hashowalne dla lru_cache)
    seen, out = set(), []
    for k in keys:
        if k and k not in seen:
            seen.add(k); out.append(k)
    return tuple(out)


def _find_logo_file(logo_dir: str, system: str) -> "Path | None":
    """Ścieżka pliku logo dla systemu (pierwszy pasujący kandydat)."""
    if not logo_dir or not system:
        return None
    base = Path(logo_dir)
    for key in _logo_key_candidates(system):
        for ext in (".png", ".webp"):
            p = base / f"{key}{ext}"
            if p.is_file():
                return p
    return None


def platform_logo_uri(logo_dir: str, system: str) -> str | None:
    """Logo platformy jako data-URI (własny klucz → alias bazowy)."""
    p = _find_logo_file(logo_dir, system)
    if p is None:
        return None
    try:
        data = p.read_bytes()
        mime = "image/webp" if p.suffix.lower() == ".webp" else "image/png"
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
    except Exception:
        return None


# ── PIL: pieczenie grzbietu do .ico (eksport) ──────────────────────────────
def _hex_rgba(hexc: str, alpha: int = 255):
    hexc = hexc.lstrip("#")
    r, g, b = int(hexc[0:2], 16), int(hexc[2:4], 16), int(hexc[4:6], 16)
    return (r, g, b, alpha)


def _platform_logo_pil(logo_dir: str, system: str):
    if not _PIL or not logo_dir:
        return None
    p = _find_logo_file(logo_dir, system)
    if p is None:
        return None
    try:
        return Image.open(p).convert("RGBA")
    except Exception:
        return None


# Gotowe logo (wczytane + z poprawionym kontrastem) per plik. Przy eksporcie
# setek ROM-ów ten sam PNG był otwierany i analizowany pikselowo raz na grę.
# Klucz zawiera mtime, więc podmiana wariantu w edytorze od razu unieważnia wpis.
_LOGO_READY: dict = {}


def _spine_logo_ready(logo_dir: str, system: str):
    """Logo gotowe do nałożenia na grzbiet (z cache). Nie mutujemy zwracanego
    obrazu — rotate()/resize() u wołających tworzą nowe obiekty."""
    p = _find_logo_file(logo_dir, system)
    if p is None:
        return None
    try:
        ck = (str(p), p.stat().st_mtime_ns)
    except Exception:
        ck = (str(p), 0)
    if ck in _LOGO_READY:
        return _LOGO_READY[ck]
    img = _platform_logo_pil(logo_dir, system)
    if img is None:
        return None
    img = _ensure_logo_contrast(img)
    if len(_LOGO_READY) > 64:
        _LOGO_READY.clear()
    _LOGO_READY[ck] = img
    return img


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


def add_platform_spine(img, system: str, side: str = "left",
                       frac: float = 0.22, logo_dir: str = "",
                       logo_scale: float = 0.85):
    """Kwadratowa ikona = STAŁY grzbiet (frac szerokości) + grafika w CAŁOŚCI
    (contain, proporcjonalnie — bez ucinania góry/dołu, bez zniekształceń).
    Kwadratowa ikona też OK (zwężona, wyśrodkowana). Bez logo/PIL — zwraca img."""
    if not _PIL or not system:
        return img
    logo = _spine_logo_ready(logo_dir, system)   # z cache (wczytane + kontrast)
    if logo is None:
        return img
    try:
        base = img.convert("RGBA")
        W0, H0 = base.size
        if min(W0, H0) < 24:
            return base
        _LONG = 512
        r = _LONG / max(W0, H0)
        base = base.resize((max(1, round(W0 * r)), max(1, round(H0 * r))), Image.LANCZOS)
        W, H = base.size
        S = max(W, H)

        sw = max(14, int(round(S * frac)))    # STAŁY grzbiet (nie zależy od plakatu)

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
        ls = 0.85 if not logo_scale else max(0.30, min(1.0, float(logo_scale)))
        wfill = min(0.98, ls)            # % szerokości grzbietu zajętej przez logo
        hcap = min(0.98, ls * 1.10 + 0.03)  # dopuszczalna długość wzdłuż grzbietu
        scale = max(1, int(sw * wfill)) / max(1, lg.width)
        if lg.height * scale > S * hcap:
            scale = (S * hcap) / max(1, lg.height)
        lg = lg.resize((max(1, int(lg.width * scale)), max(1, int(lg.height * scale))),
                       Image.LANCZOS)
        spine.alpha_composite(lg, ((sw - lg.width) // 2, (S - lg.height) // 2))

        out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        aw = S - sw
        area_aspect = aw / S
        src_aspect = W / H
        if abs(src_aspect - area_aspect) / area_aspect <= 0.20:
            # bliskie proporcje (plakat) → lekko poszerz do WYPEŁNIENIA obszaru
            art = base.resize((aw, S), Image.LANCZOS)
            out.alpha_composite(art, (sw if side == "left" else 0, 0))
        else:
            # duża różnica (np. kwadrat) → contain, bez zniekształcenia/ucięcia
            sc = min(aw / W, S / H)
            nw, nh = max(1, round(W * sc)), max(1, round(H * sc))
            art = base.resize((nw, nh), Image.LANCZOS)
            ax = sw + (aw - nw) // 2 if side == "left" else (aw - nw) // 2
            out.alpha_composite(art, (ax, (S - nh) // 2))
        out.alpha_composite(spine, (0, 0) if side == "left" else (S - sw, 0))
        return out
    except Exception:
        return img
