"""services.art — okładki gier ze SteamGridDB + cache na dysku.

Port czystej logiki HTTP/SGDB z wersji tkinter. Zwraca okładkę jako data-URI
(base64) — bezpieczne dla webview (żadnego cross-origin file://).

Przepływ okładki:
    search(nazwa) → grids_for_id(sgdb_id) → pierwszy grid → pobierz miniaturę
    → zapisz w CACHE_DIR/covers/<key>.<ext> → data-URI.
Cache dyskowy: kolejne wywołania nie ruszają sieci.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.request
from io import BytesIO
from pathlib import Path

try:
    from PIL import Image
    PIL_OK = True
except Exception:
    PIL_OK = False

# DWA TRYBY grafiki (jak w starej wersji):
#   "icon"  → skróty .lnk z własną ikoną = KWADRAT
#   "steam" → grafika wprost do biblioteki Steam = PORTRET (grid 600x900)
_SQUARE_DIMS = "512x512,1024x1024"                 # kwadraty (tryb ikon)
_STEAM_DIMS = "600x900,342x482,660x930"            # portrety (tryb Steam), bez landscape
_PORTRAIT_DIMS = "600x900,342x482,660x930,512x512,1024x1024"  # portret+kwadrat (fallback)


# ── HTTP (port fetch / fetch_api) ──────────────────────────────────────────
def fetch(url: str, hdrs: dict | None = None, timeout: int = 12) -> bytes | None:
    try:
        h = {"User-Agent": "Mozilla/5.0"}
        if hdrs:
            h.update(hdrs)
        with urllib.request.urlopen(
                urllib.request.Request(url, headers=h), timeout=timeout) as r:
            if getattr(r, "status", 200) >= 400:
                return None
            d = r.read()
        return d or None
    except Exception:
        return None


def fetch_api(url: str, hdrs: dict | None = None, timeout: int = 12) -> bytes | None:
    """Jak fetch(), bez odrzucania krótkich odpowiedzi (JSON API)."""
    try:
        h = {"User-Agent": "Mozilla/5.0"}
        if hdrs:
            h.update(hdrs)
        with urllib.request.urlopen(
                urllib.request.Request(url, headers=h), timeout=timeout) as r:
            d = r.read()
        return d or None
    except Exception:
        return None


# ── klient SteamGridDB ─────────────────────────────────────────────────────
class SgdbClient:
    BASE = "https://www.steamgriddb.com/api/v2"

    def __init__(self, key: str):
        self.key = (key or "").strip()

    @property
    def ok(self) -> bool:
        return bool(self.key)

    def _hdr(self) -> dict:
        return {"Authorization": f"Bearer {self.key}"}

    def search(self, name: str, max_results: int = 8) -> list[dict]:
        if not self.ok or not name:
            return []
        enc = urllib.request.quote(name)
        d = fetch_api(f"{self.BASE}/search/autocomplete/{enc}", hdrs=self._hdr())
        if not d:
            return []
        try:
            return json.loads(d).get("data", [])[:max_results]
        except Exception:
            return []

    def search_with_fallback(self, query: str) -> list[dict]:
        """Skraca zapytanie, gdy autocomplete zawodzi (stare/rzadkie tytuły)."""
        res = self.search(query)
        if res:
            return res
        seen = {query}
        attempts: list[str] = []
        if " - " in query:
            attempts.append(query.split(" - ")[0].strip())
        for sep in (": ", ":"):
            if sep in query:
                attempts.append(query.split(sep)[0].strip())
                break
        words = query.split()
        if len(words) >= 3:
            attempts.append(" ".join(words[:2]).rstrip(":;,- "))
        if len(words) >= 2 and len(words[0]) >= 4:
            attempts.append(words[0].rstrip(":;,- "))
        for a in attempts:
            if not a or a in seen:
                continue
            seen.add(a)
            res = self.search(a)
            if res:
                return res
        return []

    def _grids(self, path: str, per_page: int, dims: str = _PORTRAIT_DIMS) -> list[dict]:
        url = f"{self.BASE}/{path}?"
        if dims:
            url += f"dimensions={dims}&"
        url += f"limit={per_page}&page=0"
        d = fetch_api(url, hdrs=self._hdr())
        if not d:
            return []
        try:
            obj = json.loads(d)
            return obj.get("data", []) if obj.get("success") else []
        except Exception:
            return []

    def grids_for_id(self, sgdb_id, per_page: int = 8, dims: str = _PORTRAIT_DIMS) -> list[dict]:
        return self._grids(f"grids/game/{sgdb_id}", per_page, dims)

    def grids_for_appid(self, appid, per_page: int = 8, dims: str = _PORTRAIT_DIMS) -> list[dict]:
        return self._grids(f"grids/steam/{appid}", per_page, dims)

    def _icons(self, path: str) -> list[dict]:
        """Endpoint ikon SGDB (kwadratowe ikony gry, często z przezroczystością)."""
        d = fetch_api(f"{self.BASE}/{path}", hdrs=self._hdr())
        if not d:
            return []
        try:
            obj = json.loads(d)
            return obj.get("data", []) if obj.get("success", True) else []
        except Exception:
            return []

    def icons_for_id(self, sgdb_id) -> list[dict]:
        return self._icons(f"icons/game/{sgdb_id}")

    def icons_for_appid(self, appid) -> list[dict]:
        return self._icons(f"icons/steam/{appid}")

    # heroes (tło/banner) i logos (przezroczyste logo) — do trybu Steam
    def heroes_for_id(self, sgdb_id) -> list[dict]:
        return self._icons(f"heroes/game/{sgdb_id}?types=static,animated")

    def heroes_for_appid(self, appid) -> list[dict]:
        return self._icons(f"heroes/steam/{appid}?types=static,animated")

    def logos_for_id(self, sgdb_id) -> list[dict]:
        return self._icons(f"logos/game/{sgdb_id}")

    def logos_for_appid(self, appid) -> list[dict]:
        return self._icons(f"logos/steam/{appid}")


# ── data-URI helpers ───────────────────────────────────────────────────────
def _mime(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/png"


def _ext(mime: str) -> str:
    return {"image/png": ".png", "image/jpeg": ".jpg",
            "image/webp": ".webp", "image/gif": ".gif"}.get(mime, ".img")


def _to_data_uri(data: bytes) -> str:
    m = _mime(data)
    return f"data:{m};base64,{base64.b64encode(data).decode('ascii')}"


# ── pieczenie miniatury do wyświetlenia (jak w oryginale: gotowy kształt) ──
# Zamiast liczyć na CSS (aspect-ratio bywa ignorowane w WebView2), zwracamy
# obrazek już w docelowym kształcie: ikony = KWADRAT (pad/crop), Steam = PORTRET.
def _fit_pad(im, tw, th):
    """Skaluje z zachowaniem proporcji i wkleja wyśrodkowany na przezroczyste tło."""
    src = im.copy()
    src.thumbnail((tw, th), Image.LANCZOS)
    canvas = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    canvas.paste(src, ((tw - src.width) // 2, (th - src.height) // 2), src)
    return canvas


def _fit_cover(im, tw, th):
    """Skaluje tak, by wypełnić (tw,th), i przycina do środka."""
    scale = max(tw / im.width, th / im.height)
    nw, nh = max(1, round(im.width * scale)), max(1, round(im.height * scale))
    src = im.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - tw) // 2, (nh - th) // 2
    return src.crop((left, top, left + tw, top + th))


def _crop_square_offset(im, offset: float):
    """Przycina dłuższy bok do kwadratu; pozycja kadru = offset 0..1
    (portret: 0=góra…1=dół; panorama: 0=lewo…1=prawo). Jak w oryginale."""
    w, h = im.size
    if w == h:
        return im
    side = min(w, h)
    off = max(0.0, min(1.0, offset))
    if h > w:
        left, top = (w - side) // 2, int(round((h - side) * off))
    else:
        left, top = int(round((w - side) * off)), (h - side) // 2
    return im.crop((left, top, left + side, top + side))


def display_thumb_img(data: bytes, mode: str = "icon",
                      fit: str = "pad", offset: float = 0.5, size: int = 256):
    """Zwraca obraz PIL (RGBA) w docelowym kształcie albo None."""
    if not PIL_OK:
        return None
    try:
        im = Image.open(BytesIO(data)).convert("RGBA")
    except Exception:
        return None
    if _mode(mode) == "steam":
        return _fit_cover(im, size, round(size * 1.5))    # portret 2:3
    if fit == "crop":
        return _crop_square_offset(im, offset).resize((size, size), Image.LANCZOS)
    return _fit_pad(im, size, size)


def make_display_thumb(data: bytes, mode: str = "icon",
                       fit: str = "pad", offset: float = 0.5,
                       size: int = 256) -> bytes:
    """PNG w docelowym kształcie (opakowanie display_thumb_img)."""
    out = display_thumb_img(data, mode, fit, offset, size)
    if out is None:
        return data
    buf = BytesIO()
    out.save(buf, "PNG")
    return buf.getvalue()


def make_ico_bytes(square_img,
                   sizes=(256, 128, 64, 48, 32, 16)) -> bytes:
    """Kwadratowy obraz PIL → wielorozmiarowy .ico (maks 256, jak w oryginale)."""
    img = square_img.convert("RGBA")
    if img.size != (256, 256):
        img = img.resize((256, 256), Image.LANCZOS)
    frames = [img.resize((s, s), Image.LANCZOS) for s in sizes]
    buf = BytesIO()
    frames[0].save(buf, format="ICO", sizes=[(s, s) for s in sizes],
                   append_images=frames[1:])
    return buf.getvalue()


# ── okładka dla gry (z cache dyskowym, per tryb) ───────────────────────────
def _mode(mode: str) -> str:
    return "steam" if str(mode).lower() == "steam" else "icon"


def _covers_dir(cache_dir: Path, mode: str = "icon") -> Path:
    # osobny cache per tryb: gra może mieć i kwadratową ikonę, i portret Steam
    d = Path(cache_dir) / "covers" / _mode(mode)
    d.mkdir(parents=True, exist_ok=True)
    return d


def cached_cover(cache_dir: Path, key: str, mode: str = "icon",
                 fit: str = "pad", offset: float = 0.5) -> str | None:
    """Zwraca data-URI PRZETWORZONEJ miniatury (kwadrat/portret) z cache."""
    d = _covers_dir(cache_dir, mode)
    safe = _safe_key(key)
    for f in d.glob(safe + ".*"):
        try:
            return _to_data_uri(make_display_thumb(f.read_bytes(), mode, fit, offset))
        except Exception:
            return None
    return None


def raw_cover(cache_dir: Path, key: str, mode: str = "icon") -> str | None:
    """Zwraca data-URI SUROWEGO obrazu z cache (do okienka kadrowania)."""
    d = _covers_dir(cache_dir, mode)
    for f in d.glob(_safe_key(key) + ".*"):
        try:
            return _to_data_uri(f.read_bytes())
        except Exception:
            return None
    return None


def fetch_cover(cfg: dict, cache_dir: Path, key: str, game: dict,
                mode: str = "icon", prefer_portrait: bool = False) -> dict:
    """Pobiera (lub czyta z cache) grafikę gry dla trybu.

    icon  → KWADRAT (kwadratowe gridy → ikony → portret jako ostateczność)
    steam → PORTRET (grid 600x900 → kwadrat jako ostateczność)
    prefer_portrait (tryb ikon): portret najpierw — dla ROM-ów z grzbietem
    (portret + pionowy grzbiet = kwadrat bez marginesów).
    """
    mode = _mode(mode)
    hit = raw_cover(cache_dir, key, mode)     # kształt robi CSS — zwracamy surowy
    if hit:
        return {"key": key, "found": True, "data_uri": hit, "cached": True}

    sgdb_key = (cfg.get("api_keys", {}) or {}).get("sgdb_key", "")
    client = SgdbClient(sgdb_key)
    if not client.ok:
        return {"key": key, "found": False, "data_uri": None, "cached": False,
                "reason": "brak klucza SGDB"}

    ref = _resolve_ref(client, game, "")
    if not ref:
        return {"key": key, "found": False, "data_uri": None, "cached": False}
    kind_id, val = ref

    def _grids(dims):
        return (client.grids_for_appid(val, dims=dims) if kind_id == "appid"
                else client.grids_for_id(val, dims=dims))

    def _icons():
        return (client.icons_for_appid(val) if kind_id == "appid"
                else client.icons_for_id(val))

    if mode == "steam" or prefer_portrait:
        grids = _grids(_STEAM_DIMS) or _grids(_SQUARE_DIMS) or _icons()
    else:
        grids = _grids(_SQUARE_DIMS) or _icons() or _grids(_STEAM_DIMS)
    if not grids:
        return {"key": key, "found": False, "data_uri": None, "cached": False}

    for g in grids:
        url = g.get("thumb") or g.get("url")
        if not url:
            continue
        data = fetch(url) or (fetch(g.get("url")) if g.get("url") else None)
        if not data:
            continue
        try:
            out = _covers_dir(cache_dir, mode) / (_safe_key(key) + _ext(_mime(data)))
            out.write_bytes(data)     # zapisujemy SUROWY obraz; kształt robi CSS
        except Exception:
            pass
        return {"key": key, "found": True,
                "data_uri": _to_data_uri(data), "cached": False}

    return {"key": key, "found": False, "data_uri": None, "cached": False}


def _safe_key(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", key)[:180]


def clear_cover(cache_dir: Path, key: str, mode: str = "icon") -> None:
    """Usuwa grafikę z cache dyskowego danego trybu (reset do automatycznej)."""
    d = _covers_dir(cache_dir, mode)
    for f in d.glob(_safe_key(key) + ".*"):
        try:
            f.unlink()
        except Exception:
            pass


# ── wybór okładki: kandydaci + zapis ───────────────────────────────────────
def parse_sgdb_ref(text: str) -> int | None:
    """Wyłuskuje SGDB game id z URL-a (…/game/12345) albo z gołej liczby."""
    text = (text or "").strip()
    if not text:
        return None
    m = re.search(r"/game/(\d+)", text)
    if m:
        return int(m.group(1))
    if text.isdigit():
        return int(text)
    return None


def _resolve_ref(client: SgdbClient, game: dict, query: str):
    """Ustala co odpytać: ('appid', id) lub ('game', sgdb_id) albo None.

    query pusty → appid gry, inaczej szukanie po nazwie.
    query = URL/ID SGDB → to id. query = tekst → szukanie po frazie.
    """
    ref = parse_sgdb_ref(query)
    if ref is not None:
        return ("game", ref)
    if query.strip():
        res = client.search_with_fallback(query.strip())
        return ("game", res[0].get("id")) if res else None
    appid = game.get("appid")
    if appid:
        return ("appid", appid)
    res = client.search_with_fallback(game.get("name", ""))
    return ("game", res[0].get("id")) if res else None


def candidates_for_game(cfg: dict, game: dict, query: str = "",
                        mode: str = "icon", limit: int = 40) -> list[dict]:
    """Kandydaci do okna wyboru IKONY (kwadrat) — jak w oryginale:
    źródło = endpoint /icons + kwadratowe gridy 512/1024, POSORTOWANE MALEJĄCO
    po realnej rozdzielczości (duże pierwsze). Bez plakatów/landscape.
    Plakat dochodzi tylko jako ostateczność, gdy nie ma nic kwadratowego.
    Każdy kandydat: {id, url, thumb (data-URI), w, h, style, kind, square}.
    """
    client = SgdbClient((cfg.get("api_keys", {}) or {}).get("sgdb_key", ""))
    if not client.ok:
        return []
    ref = _resolve_ref(client, game, query)
    if not ref:
        return []
    kind_id, val = ref

    def _grids(dims):
        return (client.grids_for_appid(val, per_page=limit, dims=dims)
                if kind_id == "appid"
                else client.grids_for_id(val, per_page=limit, dims=dims))

    def _icons():
        return (client.icons_for_appid(val) if kind_id == "appid"
                else client.icons_for_id(val))

    # ikony + kwadraty + POSTERY. Każdy rodzaj ma WŁASNĄ pulę, żeby dużo ikon
    # nie wypychało kwadratów/plakatów (picker się przewija, więc jest miejsce).
    kind_prio = {"ikona": 0, "kwadrat": 1, "plakat": 2}
    kind_cap = {"ikona": 30, "kwadrat": 24, "plakat": 24}
    groups = [("ikona", _icons()),
              ("kwadrat", _grids(_SQUARE_DIMS)),
              ("plakat", _grids(_STEAM_DIMS))]

    # zbierz unikaty, pobierz miniatury, ZMIERZ realny rozmiar (PIL)
    seen: set[str] = set()
    out: list[dict] = []
    for kind, items in groups:
        got = 0
        for it in items:
            if got >= kind_cap.get(kind, 24):
                break
            i = str(it.get("id", ""))
            if not i or i in seen:
                continue
            seen.add(i)
            thumb_url = it.get("thumb") or it.get("url")
            if not thumb_url:
                continue
            data = fetch(thumb_url)
            if not data:
                continue
            w, h = it.get("width", 0) or 0, it.get("height", 0) or 0
            if PIL_OK:
                try:
                    w, h = Image.open(BytesIO(data)).size
                except Exception:
                    pass
            got += 1
            out.append({
                "id": i, "url": it.get("url", ""), "thumb": _to_data_uri(data),
                "w": w, "h": h, "style": (it.get("style") or ""),
                "kind": kind,
                "square": (kind in ("ikona", "kwadrat")) or bool(
                    w and h and abs(int(w) - int(h)) <= 2),
            })
    # sort: najpierw rodzaj (ikona→kwadrat→plakat), w obrębie rodzaju duże pierwsze
    out.sort(key=lambda c: (kind_prio.get(c["kind"], 9),
                            -(min(c["w"], c["h"]) if (c["w"] and c["h"]) else 0)))
    return out


# ── tryb Steam: kilka typów grafik (grid + hero/tło + logo) ────────────────
_STEAM_ASSETS = ("grid", "hero", "logo")


def _dl_variants(items: list[dict], limit: int) -> list[dict]:
    """Pobiera miniatury wariantów jako data-URI (z URL-em pełnym do wyboru)."""
    out: list[dict] = []
    for a in items[:limit]:
        turl = a.get("thumb") or a.get("url")
        if not turl:
            continue
        data = fetch(turl)
        if not data:
            continue
        out.append({
            "url": a.get("url", ""), "thumb": _to_data_uri(data),
            "w": a.get("width", 0) or 0, "h": a.get("height", 0) or 0,
            "style": (a.get("style") or ""),
        })
    return out


def _steam_asset_name(key: str, asset: str) -> str:
    safe = _safe_key(key)
    return safe if asset == "grid" else f"{safe}__{asset}"


def cached_steam_asset(cache_dir: Path, key: str, asset: str) -> str | None:
    d = _covers_dir(cache_dir, "steam")
    for f in d.glob(_steam_asset_name(key, asset) + ".*"):
        try:
            return _to_data_uri(f.read_bytes())
        except Exception:
            return None
    return None


def steam_art_options(cfg: dict, game: dict, query: str = "",
                      limit: int = 20) -> dict:
    """Wszystkie warianty grafik Steam naraz: grid (okładka), hero (tło), logo.

    Zwraca {"ok", "sgdb_id", "grid":[...], "hero":[...], "logo":[...]}.
    Każdy wariant: {url, thumb (data-URI), w, h, style}.
    """
    client = SgdbClient((cfg.get("api_keys", {}) or {}).get("sgdb_key", ""))
    if not client.ok:
        return {"ok": False, "reason": "brak klucza SGDB",
                "grid": [], "hero": [], "logo": []}
    ref = _resolve_ref(client, game, query)
    if not ref:
        return {"ok": False, "reason": "brak dopasowania w SGDB",
                "grid": [], "hero": [], "logo": []}
    kind_id, val = ref
    if kind_id == "appid":
        grids = client.grids_for_appid(val, per_page=limit, dims=_STEAM_DIMS)
        heroes = client.heroes_for_appid(val)
        logos = client.logos_for_appid(val)
    else:
        grids = client.grids_for_id(val, per_page=limit, dims=_STEAM_DIMS)
        heroes = client.heroes_for_id(val)
        logos = client.logos_for_id(val)
    return {
        "ok": True,
        "sgdb_id": val if kind_id == "game" else None,
        "grid": _dl_variants(grids, limit),
        "hero": _dl_variants(heroes, limit),
        "logo": _dl_variants(logos, limit),
    }


def save_steam_asset(cache_dir: Path, key: str, asset: str, url: str) -> dict:
    """Zapisuje wybrany wariant danego typu (grid/hero/logo) do cache Steam."""
    if asset not in _STEAM_ASSETS or not url:
        return {"found": False, "data_uri": None}
    data = fetch(url)
    if not data:
        return {"found": False, "data_uri": None}
    d = _covers_dir(cache_dir, "steam")
    name = _steam_asset_name(key, asset)
    for f in d.glob(name + ".*"):
        try:
            f.unlink()
        except Exception:
            pass
    try:
        (d / (name + _ext(_mime(data)))).write_bytes(data)
    except Exception:
        pass
    return {"found": True, "data_uri": _to_data_uri(data)}


def clear_steam_asset(cache_dir: Path, key: str, asset: str) -> None:
    d = _covers_dir(cache_dir, "steam")
    for f in d.glob(_steam_asset_name(key, asset) + ".*"):
        try:
            f.unlink()
        except Exception:
            pass


def suggest_games(cfg: dict, query: str, limit: int = 25) -> list[dict]:
    """Podpowiedzi tytułów (autouzupełnianie SGDB) — {id, name, year}.

    SGDB autocomplete zwraca do ~10 trafień posortowanych wg trafności —
    NIE cappujemy poniżej tego (poprzednio limit 8 gubił 9./10. tytuł,
    np. „Avatar: Frontiers of Pandora").
    """
    query = (query or "").strip()
    if len(query) < 2:
        return []
    client = SgdbClient((cfg.get("api_keys", {}) or {}).get("sgdb_key", ""))
    if not client.ok:
        return []
    out: list[dict] = []
    for g in client.search(query, max_results=limit):
        rel = g.get("release_date")
        year = ""
        try:
            if rel:
                from datetime import datetime, timezone
                year = str(datetime.fromtimestamp(int(rel), timezone.utc).year)
        except Exception:
            year = ""
        out.append({"id": g.get("id"), "name": g.get("name", ""), "year": year})
    return out


def save_chosen_cover(cache_dir: Path, key: str, full_url: str,
                      mode: str = "icon") -> dict:
    """Pobiera pełną grafikę z URL-a i zapisuje jako wybraną (nadpisuje) w
    cache danego trybu. Zwraca {"found", "data_uri"}.
    """
    if not full_url:
        return {"found": False, "data_uri": None}
    data = fetch(full_url)
    if not data:
        return {"found": False, "data_uri": None}
    # usuń stare warianty (inne rozszerzenie) i zapisz nowy
    d = _covers_dir(cache_dir, mode)
    safe = _safe_key(key)
    for f in d.glob(safe + ".*"):
        try:
            f.unlink()
        except Exception:
            pass
    try:
        out = d / (safe + _ext(_mime(data)))
        out.write_bytes(data)
    except Exception:
        pass
    return {"found": True, "data_uri": _to_data_uri(data)}
