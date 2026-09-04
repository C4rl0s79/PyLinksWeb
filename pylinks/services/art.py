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

    def search_with_fallback(self, query: str, first=None) -> list[dict]:
        """Skraca zapytanie, gdy autocomplete zawodzi (stare/rzadkie tytuły).

        `first` = wynik JUŻ wykonanego wyszukiwania pełnej frazy; podaj go, gdy
        wołający sam odpytał API — inaczej to samo zapytanie leci dwa razy."""
        res = self.search(query) if first is None else first
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

    def _grids(self, path: str, per_page: int, dims: str = _PORTRAIT_DIMS,
               page: int = 0) -> list[dict]:
        url = f"{self.BASE}/{path}?"
        if dims:
            url += f"dimensions={dims}&"
        url += f"limit={per_page}&page={max(0, int(page))}"
        d = fetch_api(url, hdrs=self._hdr())
        if not d:
            return []
        try:
            obj = json.loads(d)
            return obj.get("data", []) if obj.get("success") else []
        except Exception:
            return []

    def grids_for_id(self, sgdb_id, per_page: int = 8, dims: str = _PORTRAIT_DIMS,
                     page: int = 0) -> list[dict]:
        return self._grids(f"grids/game/{sgdb_id}", per_page, dims, page)

    def grids_for_appid(self, appid, per_page: int = 8, dims: str = _PORTRAIT_DIMS,
                        page: int = 0) -> list[dict]:
        return self._grids(f"grids/steam/{appid}", per_page, dims, page)

    def _icons(self, path: str, per_page: int = 50, page: int = 0) -> list[dict]:
        """Endpoint ikon/hero/logo SGDB. Dokłada limit+page (bez tego API zwraca
        garstkę — hero/logo miały „jedno-dwa", mimo że na stronie jest więcej)."""
        sep = "&" if "?" in path else "?"
        url = (f"{self.BASE}/{path}{sep}limit={max(1, min(50, per_page))}"
               f"&page={max(0, int(page))}")
        d = fetch_api(url, hdrs=self._hdr())
        if not d:
            return []
        try:
            obj = json.loads(d)
            return obj.get("data", []) if obj.get("success", True) else []
        except Exception:
            return []

    def icons_for_id(self, sgdb_id, per_page: int = 50, page: int = 0) -> list[dict]:
        return self._icons(f"icons/game/{sgdb_id}", per_page, page)

    def icons_for_appid(self, appid, per_page: int = 50, page: int = 0) -> list[dict]:
        return self._icons(f"icons/steam/{appid}", per_page, page)

    # heroes (tło/banner) i logos (przezroczyste logo) — do trybu Steam
    def heroes_for_id(self, sgdb_id, per_page: int = 50,
                      types: str = "static,animated", page: int = 0) -> list[dict]:
        return self._icons(f"heroes/game/{sgdb_id}?types={types}", per_page, page)

    def heroes_for_appid(self, appid, per_page: int = 50,
                         types: str = "static,animated", page: int = 0) -> list[dict]:
        return self._icons(f"heroes/steam/{appid}?types={types}", per_page, page)

    def logos_for_id(self, sgdb_id, per_page: int = 50, page: int = 0) -> list[dict]:
        return self._icons(f"logos/game/{sgdb_id}", per_page, page)

    def logos_for_appid(self, appid, per_page: int = 50, page: int = 0) -> list[dict]:
        return self._icons(f"logos/steam/{appid}", per_page, page)


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


def raw_cover(cache_dir: Path, key: str, mode: str = "icon") -> str | None:
    """Zwraca data-URI SUROWEGO obrazu z cache (do okienka kadrowania)."""
    f = _raw_path(cache_dir, key, mode)
    if not f:
        return None
    try:
        return _to_data_uri(f.read_bytes())
    except OSError:
        return None


def _saved_choice(cfg: dict, key: str, mode: str = "icon"):
    """URL grafiki wybranej wcześniej przez użytkownika (z configu) albo None.

    Tryb ikon → `selected_icon_keys[key]`; tryb Steam → `steam_art_by_key[key].grid`.
    To „stub" gry: sam odnośnik, nie obraz — przeżywa usunięcie gry z dysku
    i czyszczenie cache.
    """
    if _mode(mode) == "steam":
        entry = (cfg.get("steam_art_by_key") or {}).get(key)
        url = entry.get("grid") if isinstance(entry, dict) else entry
    else:
        url = (cfg.get("selected_icon_keys") or {}).get(key)
    url = url if isinstance(url, str) else None
    return url if (url or "").startswith(("http://", "https://")) else None


def _arcade_title_candidates(game: dict) -> list[str]:
    """Nazwy, pod jakimi automat może figurować w zbiorze Libretro — od najbardziej
    szczegółowej. Najpierw WYBRANY wariant (jego okładka jest najtrafniejsza),
    potem tytuł grupy, pozostałe warianty i na końcu nazwa bez nawiasu."""
    out, chosen = [], (game.get("chosen") or "").lower()
    vars_ = game.get("variants") or []
    for v in vars_:
        if (v.get("name") or "").lower() == chosen and v.get("title"):
            out.append(v["title"])
            break
    for k in ("name", "clean"):
        if game.get(k):
            out.append(game[k])
    out.extend(v["title"] for v in vars_ if v.get("title"))
    seen, uniq = set(), []
    for t in out:
        if t and t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def _libretro_arcade_cover(cache_dir: Path, key: str, game: dict,
                           mode: str) -> dict | None:
    """Okładka automatu ze zbioru Libretro — dopasowanie po OPISIE Z MAME.

    To źródło pierwszego wyboru dla arcade: plik nazywa się dokładnie tak, jak gra
    w MAME, więc trafienie jest tożsamością, a nie zgadywaniem. SGDB szuka po
    tytule i przy krótkich nazwach automatów myli je z grami konsolowymi.
    """
    if game.get("source") != "mame":
        return None
    from pylinks.services import libretro_mame as _lm
    try:
        _lm.refresh_index(cache_dir)          # pobiera raz na miesiąc, potem z dysku
        urls = _lm.urls_for(cache_dir, _arcade_title_candidates(game))
    except Exception:
        return None
    for url in urls:
        data = fetch(url, timeout=20)
        if not data:
            continue
        try:
            out = _covers_dir(cache_dir, mode) / (_safe_key(key) + _ext(_mime(data)))
            out.write_bytes(data)
        except Exception:
            pass
        return {"key": key, "found": True, "data_uri": _to_data_uri(data),
                "cached": False, "source": "libretro"}
    return None


def _screenscraper_cover(cfg: dict, cache_dir: Path, key: str, game: dict,
                         mode: str) -> dict | None:
    """Okładka automatu ze ScreenScraper — szuka po NAZWIE SETU MAME.

    SGDB indeksuje po tytule, więc automaty o nietypowych nazwach („Cyber Troopers
    Virtual-On 4", „beatmania IIDX with DDR") zostają bez okładki. ScreenScraper
    pyta `romnom=<set>.zip`, czyli o rzecz jednoznaczną — stąd tu jako zapas.
    """
    e = (cfg.get("extra_sources", {}) or {})
    if not e.get("screenscraper") or game.get("source") != "mame":
        return None
    setname = (game.get("chosen") or game.get("set") or "").strip()
    if not setname:
        return None
    from pylinks.services.screenscraper import ScreenScraper
    ss = ScreenScraper(e.get("ss_devid", ""), e.get("ss_devpassword", ""),
                       e.get("ss_user", ""), e.get("ss_password", ""))
    if not ss.ok:
        return None
    # ścieżka wskazuje set RODZICA; dla wybranego wariantu podmieniamy nazwę pliku
    zip_path = ""
    base = game.get("path") or ""
    if base:
        zip_path = str(Path(base).with_name(setname + ".zip"))
        if not Path(zip_path).is_file():
            zip_path = ""
    jeu = ss.game_by_rom(setname, rom_path=zip_path)
    if not jeu:
        return None
    for url in ss.media_urls(jeu, "box"):
        data = fetch(url, timeout=20)
        if not data:
            continue
        try:
            out = _covers_dir(cache_dir, mode) / (_safe_key(key) + _ext(_mime(data)))
            out.write_bytes(data)
        except Exception:
            pass
        return {"key": key, "found": True, "data_uri": _to_data_uri(data),
                "cached": False, "source": "screenscraper"}
    return None


# ── miniatury do SIATKI ────────────────────────────────────────────────────
# Kafelek ma ~200 px, a okładki bywają 600×900 i większe. Przesyłanie ich przez
# most w pełnej rozdzielczości to ~500 KB na grafikę — przy bibliotece arcade
# ćwierć giga na jedno przewinięcie. Do WYŚWIETLANIA wystarczy miniatura;
# pełny obraz zostaje tam, gdzie jest potrzebny: w oknie kadrowania i przy
# pieczeniu .ico.
THUMB_BOX = 360


def _thumbs_dir(cache_dir: Path, mode: str) -> Path:
    d = Path(cache_dir) / "thumbs" / _mode(mode)
    d.mkdir(parents=True, exist_ok=True)
    return d


def clear_thumb(cache_dir: Path, key: str, mode: str = "icon") -> None:
    """Kasuje miniaturę — po zmianie okładki musi powstać na nowo."""
    _unlink_all(_thumbs_dir(cache_dir, mode), key)


def thumb_path(cache_dir: Path, key: str, mode: str = "icon",
               box: int = THUMB_BOX):
    """Plik miniatury (tworzy przy pierwszym użyciu). Bez base64 — do serwera HTTP."""
    dst = _thumbs_dir(cache_dir, mode) / (_safe_key(key) + ".webp")
    if dst.is_file():
        return dst
    src = _raw_path(cache_dir, key, mode)
    if not src:
        return None
    if not PIL_OK:
        return src
    try:
        with Image.open(src) as im:
            im.load()
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
            if max(im.size) > box:
                im.thumbnail((box, box), Image.LANCZOS)
            buf = BytesIO()
            im.save(buf, "WEBP", quality=80, method=4)
        dst.write_bytes(buf.getvalue())
        return dst
    except Exception:
        return src


def cover_index(cache_dir: Path, mode: str = "icon") -> dict:
    """{„bezpieczny klucz": znacznik czasu} dla okładek w cache.

    JEDNO przejście po katalogu zamiast globa na każdy klucz. Znacznik trafia do
    adresu miniatury, dzięki czemu przeglądarka może trzymać ją w cache bezterminowo,
    a po podmianie okładki i tak pobierze nową — adres się zmieni.
    """
    import os
    out = {}
    try:
        with os.scandir(_covers_dir(cache_dir, mode)) as it:
            for e in it:
                if e.is_file():
                    try:
                        out[Path(e.name).stem] = int(e.stat().st_mtime)
                    except OSError:
                        out[Path(e.name).stem] = 0
    except OSError:
        pass
    return out


def thumb_uri(cache_dir: Path, key: str, mode: str = "icon",
              box: int = THUMB_BOX) -> str | None:
    """Miniatura jako data-URI — droga awaryjna, gdy nie działa serwer HTTP.

    Skalowanie jest JEDNO, w `thumb_path()`; tutaj dochodzi wyłącznie kodowanie.
    """
    f = thumb_path(cache_dir, key, mode, box)
    if not f:
        return None
    try:
        return _to_data_uri(f.read_bytes())
    except OSError:
        return None



def fetch_cover(cfg: dict, cache_dir: Path, key: str, game: dict,
                mode: str = "icon", prefer_portrait: bool = False,
                query: str = "") -> dict:
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

    # ODTWORZENIE WCZEŚNIEJSZEGO WYBORU: config trzyma URL wybranej grafiki
    # (a nie sam obraz), żeby po usunięciu gry i powrocie na dysk — albo po
    # wyczyszczeniu cache — wróciła DOKŁADNIE ta ikona, którą wybrał użytkownik,
    # zamiast pierwszego lepszego trafienia z SGDB.
    saved = _saved_choice(cfg, key, mode)
    if saved:
        data = fetch(saved)
        if data:
            try:
                out = _covers_dir(cache_dir, mode) / (_safe_key(key) + _ext(_mime(data)))
                out.write_bytes(data)
            except Exception:
                pass
            return {"key": key, "found": True, "data_uri": _to_data_uri(data),
                    "cached": False, "restored": True}

    # ARCADE: najpierw źródło dopasowywane TOŻSAMOŚCIOWO (opis z MAME), dopiero
    # potem SGDB, który przy krótkich nazwach automatów musi zgadywać.
    if game.get("source") == "mame":
        alt = _libretro_arcade_cover(cache_dir, key, game, mode)
        if alt:
            return alt

    sgdb_key = (cfg.get("api_keys", {}) or {}).get("sgdb_key", "")
    client = SgdbClient(sgdb_key)
    if not client.ok:
        alt = _screenscraper_cover(cfg, cache_dir, key, game, mode)
        return alt or {"key": key, "found": False, "data_uri": None,
                       "cached": False, "reason": "brak klucza SGDB"}

    ref = _resolve_ref(client, game, query)   # query = nadpisanie tytułu/ID (per gra)
    if not ref:
        alt = _screenscraper_cover(cfg, cache_dir, key, game, mode)
        return alt or {"key": key, "found": False, "data_uri": None, "cached": False}
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
        alt = _screenscraper_cover(cfg, cache_dir, key, game, mode)
        return alt or {"key": key, "found": False, "data_uri": None, "cached": False}

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


def _raw_path(cache_dir: Path, key: str, mode: str = "icon"):
    """Ścieżka pliku okładki w cache (bez wczytywania zawartości).

    Jedno miejsce, w którym „klucz gry → plik na dysku" jest rozstrzygane;
    rozszerzenie zależy od tego, co przysłało źródło (png/jpg/webp).
    """
    for f in _covers_dir(cache_dir, mode).glob(_safe_key(key) + ".*"):
        if f.is_file():
            return f
    return None


def _unlink_all(d: Path, key: str) -> int:
    """Kasuje wszystkie pliki danego klucza w katalogu (dowolne rozszerzenie)."""
    n = 0
    try:
        for f in d.glob(_safe_key(key) + ".*"):
            try:
                f.unlink()
                n += 1
            except OSError:
                pass
    except OSError:
        pass
    return n


def clear_cover(cache_dir: Path, key: str, mode: str = "icon") -> None:
    """Usuwa grafikę z cache dyskowego danego trybu (reset do automatycznej)."""
    _unlink_all(_covers_dir(cache_dir, mode), key)


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


def _norm_title(s: str) -> str:
    """Tytuł do porównań: małe litery, tylko alfanumeryczne (bez spacji/interpunkcji)."""
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _pick_best_id(results: list[dict], wanted: str):
    """Z wyników autocomplete wybiera ten o DOKŁADNIE tym tytule (bez względu na
    wielkość liter i interpunkcję); dopiero w ostateczności pierwszy wynik.

    Bez tego krótkie/nietypowe tytuły były błędne — np. „D" łapało „D.I.P.R.I.P.".
    """
    if not results:
        return None
    w_raw = (wanted or "").strip().lower()
    for r in results:                      # 1) dokładny (surowy, ignorując wielkość)
        if r.get("id") and (r.get("name") or "").strip().lower() == w_raw:
            return r.get("id")
    w_norm = _norm_title(wanted)
    if w_norm:
        for r in results:                  # 2) dokładny po normalizacji (alfanum.)
            if r.get("id") and _norm_title(r.get("name")) == w_norm:
                return r.get("id")
        # 3) tytuł bazowy (część przed ': ' / ' - ') == szukany. Trafia np.
        #    „D" → „D: The Game" (SGDB nie ma gołego „D"), zamiast „D.I.P.R.I.P.".
        base_hits = []
        for r in results:
            if not r.get("id"):
                continue
            base = re.split(r"\s*:\s*|\s+-\s+", r.get("name") or "", maxsplit=1)[0]
            if _norm_title(base) == w_norm:
                base_hits.append(r)
        if base_hits:
            base_hits.sort(key=lambda r: len(r.get("name") or ""))
            return base_hits[0].get("id")
    return results[0].get("id")            # 4) fallback: pierwszy


def _arcade_titles(title: str) -> list[str]:
    """Warianty nazwy automatu do odpytania SGDB, od najbardziej dosłownego.

    MAME zapisuje tytuły dwujęzyczne jednym łańcuchem z ukośnikiem —
    „Sengoku 3 / Sengoku Densho 2001", „Puzzle Bobble / Bust-A-Move" (192 z 4249
    tytułów w bibliotece). SGDB nie zna takiej formy, więc szukanie całości nie
    trafiało w nic, choć KAŻDA połowa z osobna trafia bez pudła. Rozbijamy też
    podtytuł po myślniku („Metal Slug - Super Vehicle-001" → „Metal Slug").
    """
    t = (title or "").strip()
    if not t:
        return []
    out = [t]
    parts = [x.strip() for x in re.split(r"\s+/\s+", t) if x.strip()]
    if len(parts) > 1:
        out.extend(parts)
    for cand in list(out):
        head = re.split(r"\s+-\s+", cand, maxsplit=1)[0].strip()
        if head and head != cand:
            out.append(head)
    seen, uniq = set(), []
    for x in out:
        k = _norm_title(x)
        if k and k not in seen:
            seen.add(k)
            uniq.append(x)
    return uniq


def _arcade_close(cand: str, title: str) -> bool:
    """Czy wynik SGDB jest DOŚĆ blisko tytułu automatu, by go przyjąć.

    Sporo automatów po prostu nie istnieje w SGDB. Bez tego progu zwykły fallback
    wciskał pierwszy lepszy wynik — „Sensory Backgammon Computer" dostawało okładkę
    „Sensory Deprivation", a „Crazy War" → „Crazy Taxi". Dla arcade BRAK okładki
    jest lepszy niż cudza.
    """
    a, b = _norm_title(cand), _norm_title(title)
    if not a or not b:
        return False
    if a == b:                       # „Eight Man" == „Eightman"
        return True
    wa = re.findall(r"[a-z0-9]+", (cand or "").lower())
    wb = re.findall(r"[a-z0-9]+", (title or "").lower())
    if not wa or not wb:
        return False
    if wa[:len(wb)] == wb:           # kandydat = tytuł + podtytuł (na granicy słów)
        return True
    if wb[:len(wa)] == wa and len(a) >= 0.7 * len(b):   # skrócony, ale nie ucięty
        return True
    return False


def _pick_arcade(results: list, title: str, maker: str):
    """Wybór wpisu SGDB dla gry arcade — z uwzględnieniem PRODUCENTA.

    SGDB nazywa automaty „Tytuł (Producent)", a MAME podaje producenta wprost,
    więc mamy czym rozstrzygnąć. Kolejność: dokładne „Tytuł (Producent)” →
    dokładny tytuł → tytuł zawierający producenta → None (wtedy zwykła ścieżka).
    """
    if not results:
        return None
    tn = _norm_title(title)
    mk = _norm_title(maker)
    if mk:
        want = _norm_title(f"{title} ({maker})")
        for r in results:
            if r.get("id") and _norm_title(r.get("name")) == want:
                return r.get("id")
        # producent bywa zapisany krócej („Atari Games" vs „Atari”)
        first = _norm_title(maker.split()[0]) if maker.split() else ""
        for r in results:
            nm = _norm_title(r.get("name"))
            if r.get("id") and nm.startswith(tn) and first and first in nm[len(tn):]:
                return r.get("id")
    for r in results:
        if r.get("id") and _norm_title(r.get("name")) == tn:
            return r.get("id")
    return None


def _resolve_ref(client: SgdbClient, game: dict, query: str):
    """Ustala co odpytać: ('appid', id) lub ('game', sgdb_id) albo None.

    query pusty → appid gry, inaczej szukanie po nazwie.
    query = URL/ID SGDB → to id. query = tekst → szukanie po frazie.
    Wybór wyniku preferuje DOKŁADNE dopasowanie tytułu (nie pierwszy z brzegu).
    """
    ref = parse_sgdb_ref(query)
    if ref is not None:
        return ("game", ref)

    def _find(name: str):
        # szersza pula (25) zwiększa szansę na trafienie dokładnego tytułu;
        # gdy autocomplete pusty → skracanie zapytania (stare/rzadkie tytuły).
        # first=res, żeby fallback NIE powtarzał tego samego zapytania do API.
        res = client.search(name, max_results=25)
        if not res:
            res = client.search_with_fallback(name, first=res)
        rid = _pick_best_id(res, name)
        return ("game", rid) if rid else None

    if query.strip():
        return _find(query.strip())

    # ARCADE (MAME): tytuł ma nawias z regionem („(Europe 940520)"), a SGDB
    # rozróżnia gry arcade PRODUCENTEM („Alien vs. Predator (Capcom)"). Bez tego
    # „Alien vs. Predator" trafiało w grę z Game Boya, a „Tetris" w Tetris Ultimate.
    if game.get("source") == "mame":
        base = (game.get("clean") or game.get("name") or "").strip()
        maker = (game.get("maker") or "").strip()
        for base in _arcade_titles(base):
            # Najpierw pytamy WPROST o „Tytuł (Producent)". Autouzupełnianie dla
            # samego „Tetris" zwraca Tetris Ultimate i spółkę — wpisy arcade
            # („Tetris (Sega)", „Tetris (Atari Games)") w ogóle się tam nie mieszczą.
            if maker:
                combo = f"{base} ({maker})"
                rid = _pick_arcade(client.search(combo, max_results=25), base, maker)
                if rid:
                    return ("game", rid)
                short = maker.split()[0] if maker.split() else ""
                if short and short.lower() != maker.lower():
                    rid = _pick_arcade(client.search(f"{base} ({short})", max_results=25),
                                       base, short)
                    if rid:
                        return ("game", rid)
            res = client.search(base, max_results=25)
            rid = _pick_arcade(res, base, maker) if res else None
            if rid:
                return ("game", rid)
            # ostatnia szansa: dowolny DOSTATECZNIE bliski wynik; inaczej nic
            for r in res or ():
                if r.get("id") and _arcade_close(r.get("name") or "", base):
                    return ("game", r["id"])
        return None

    appid = game.get("appid")
    if appid:
        return ("appid", appid)
    return _find(game.get("name", ""))


def candidates_for_game(cfg: dict, game: dict, query: str = "",
                        mode: str = "icon", limit: int = 40,
                        page: int = 0) -> list[dict]:
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
        return (client.grids_for_appid(val, per_page=limit, dims=dims, page=page)
                if kind_id == "appid"
                else client.grids_for_id(val, per_page=limit, dims=dims, page=page))

    def _icons():
        return (client.icons_for_appid(val, page=page) if kind_id == "appid"
                else client.icons_for_id(val, page=page))

    # ikony + kwadraty + POSTERY. Każdy rodzaj ma WŁASNĄ pulę. Zwracamy META
    # (URL miniatury), BEZ pobierania — przeglądarka ładuje równolegle z paskiem.
    kind_prio = {"ikona": 0, "kwadrat": 1, "plakat": 2}
    # przy „pokaż więcej" (page>0) nie przycinamy — chcemy CAŁĄ kolejną porcję
    kind_cap = ({"ikona": 30, "kwadrat": 24, "plakat": 24} if not page
                else {"ikona": limit, "kwadrat": limit, "plakat": limit})
    groups = [("ikona", _icons()),
              ("kwadrat", _grids(_SQUARE_DIMS)),
              ("plakat", _grids(_STEAM_DIMS))]

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
            thumb_url = it.get("thumb") or it.get("url")
            if not thumb_url:
                continue
            seen.add(i)
            got += 1
            w, h = it.get("width", 0) or 0, it.get("height", 0) or 0
            out.append({
                "id": i, "url": it.get("url", ""), "thumb": thumb_url,
                "w": w, "h": h, "style": (it.get("style") or ""),
                "kind": kind,
                "square": (kind in ("ikona", "kwadrat")) or bool(
                    w and h and abs(int(w) - int(h)) <= 2),
            })
    out.sort(key=lambda c: (kind_prio.get(c["kind"], 9),
                            -(min(c["w"], c["h"]) if (c["w"] and c["h"]) else 0)))
    return out


def fetch_thumb_uri(url: str) -> "str | None":
    """Fallback: pobiera miniaturę serwerowo → data-URI (gdy przeglądarka
    nie może wczytać zdalnego URL-a)."""
    if not url:
        return None
    data = fetch(url)
    return _to_data_uri(data) if data else None


# ── tryb Steam: kilka typów grafik (grid + hero/tło + logo) ────────────────
_STEAM_ASSETS = ("grid", "hero", "logo")


def _dl_variants(items: list[dict], limit: int) -> list[dict]:
    """META wariantów: URL miniatury + pełny URL (bez pobierania — pasek postępu
    ładuje w przeglądarce)."""
    out: list[dict] = []
    for a in items[:limit]:
        turl = a.get("thumb") or a.get("url")
        if not turl:
            continue
        out.append({
            "url": a.get("url", ""), "thumb": turl,
            "w": a.get("width", 0) or 0, "h": a.get("height", 0) or 0,
            "style": (a.get("style") or ""),
        })
    return out


def _steam_asset_name(key: str, asset: str) -> str:
    safe = _safe_key(key)
    return safe if asset == "grid" else f"{safe}__{asset}"


def _has_steam_asset(cache_dir: Path, key: str, asset: str) -> bool:
    d = _covers_dir(cache_dir, "steam")
    return any(True for _ in d.glob(_steam_asset_name(key, asset) + ".*"))


def cached_steam_asset_thumb(cache_dir: Path, key: str, asset: str,
                             box: int = 320) -> str | None:
    """MINIATURA zapisanej grafiki Steam (data-URI) — do podglądu w UI.

    Nigdy nie zwracamy pełnego pliku: hero bywa animowanym webp na 45 MB, a most
    pywebview przesyła to jako base64 (~59 MB) i okno zamiera. PNG z pierwszej
    klatki, z zachowaniem przezroczystości (logo)."""
    d = _covers_dir(cache_dir, "steam")
    for f in d.glob(_steam_asset_name(key, asset) + ".*"):
        try:
            from io import BytesIO
            im = Image.open(f)
            im.seek(0)                      # animowany webp/gif → pierwsza klatka
            im = im.convert("RGBA")
            im.thumbnail((box, box), Image.LANCZOS)
            buf = BytesIO()
            im.save(buf, "PNG")
            return _to_data_uri(buf.getvalue())
        except Exception:
            continue                        # uszkodzony plik → spróbuj następnego
    return None


def has_cover(cache_dir: Path, key: str, mode: str = "icon") -> bool:
    """TANIA kontrola obecności okładki w cache — sam glob, bez czytania pliku,
    PIL-a i base64 (wcześniej robiło to pełne dekodowanie miniatury)."""
    return _raw_path(cache_dir, key, mode) is not None


_HERO_TARGET_W = 1920          # kanoniczna szerokość tła w bibliotece Steam


def _prefer_hero(items: list) -> list:
    """Sortuje tła: najpierw NIEanimowane, potem najbliżej 1920 px szerokości.

    Bez tego prefetch brał „pierwsze z brzegu" — często animowane 3840 px webp
    ważące kilkadziesiąt MB, choć Steam wyświetla statyczne 1920×620."""
    def _rank(a):
        anim = 1 if str(a.get("type") or "").lower() == "animated" else 0
        if not anim:                          # webp/gif bywa animowany bez pola type
            u = str(a.get("url") or "").lower()
            anim = 1 if u.endswith(".webp") or u.endswith(".gif") else 0
        w = int(a.get("width") or 0)
        return (anim, abs(w - _HERO_TARGET_W) if w else 10 ** 6)
    return sorted(items, key=_rank)


def prefetch_steam_art_for_game(cfg: dict, cache_dir: Path, key: str, game: dict,
                                query: str = "") -> dict:
    """Dociąga KOMPLET grafik Steam (grid + hero + logo) danej gry do cache —
    pierwszy (najlepszy) wariant z SGDB. Pomija to, co już jest w cache.
    Zwraca {"ok", "grid","hero","logo": 'cached'|'new'|'none'|'fail'}."""
    out = {"grid": "none", "hero": "none", "logo": "none"}
    # szybka ścieżka: wszystko już jest → bez zapytań do sieci
    need = [a for a in _STEAM_ASSETS if not _has_steam_asset(cache_dir, key, a)]
    for a in _STEAM_ASSETS:
        if a not in need:
            out[a] = "cached"
    if not need:
        return {"ok": True, **out}
    client = SgdbClient((cfg.get("api_keys", {}) or {}).get("sgdb_key", ""))
    if not client.ok:
        return {"ok": False, "reason": "brak klucza SGDB", **out}
    ref = _resolve_ref(client, game, query)
    if not ref:
        return {"ok": False, "reason": "brak dopasowania SGDB", **out}
    kind_id, val = ref

    def _list(asset):
        if asset == "grid":
            return (client.grids_for_appid(val, per_page=8, dims=_STEAM_DIMS)
                    if kind_id == "appid" else
                    client.grids_for_id(val, per_page=8, dims=_STEAM_DIMS))
        if asset == "hero":
            # TYLKO statyczne: Steam i tak nie animuje tła w bibliotece, a
            # animowane webp potrafią mieć 45 MB (cache puchł do ~2 GB).
            return (client.heroes_for_appid(val, per_page=8, types="static")
                    if kind_id == "appid" else
                    client.heroes_for_id(val, per_page=8, types="static"))
        return (client.logos_for_appid(val, per_page=8) if kind_id == "appid"
                else client.logos_for_id(val, per_page=8))

    saved_entry = (cfg.get("steam_art_by_key") or {}).get(key)
    saved_entry = saved_entry if isinstance(saved_entry, dict) else {}
    for asset in need:
        # najpierw ODTWÓRZ wcześniejszy wybór użytkownika (stub w configu),
        # dopiero potem pytaj SGDB o „najlepszy" wariant
        prev = saved_entry.get(asset)
        if isinstance(prev, str) and prev.startswith(("http://", "https://")):
            r = save_steam_asset(cache_dir, key, asset, prev)
            if r.get("found"):
                out[asset] = "new"
                continue
        items = _list(asset) or []
        if asset == "hero":
            items = _prefer_hero(items)
        url = items[0].get("url") if items else None
        if not url:
            out[asset] = "none"
            continue
        r = save_steam_asset(cache_dir, key, asset, url)
        out[asset] = "new" if r.get("found") else "fail"
    return {"ok": True, **out}


def steam_art_options(cfg: dict, game: dict, query: str = "",
                      limit: int = 40, page: int = 0) -> dict:
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
        grids = client.grids_for_appid(val, per_page=limit, dims=_STEAM_DIMS, page=page)
        heroes = client.heroes_for_appid(val, per_page=limit, page=page)
        logos = client.logos_for_appid(val, per_page=limit, page=page)
    else:
        grids = client.grids_for_id(val, per_page=limit, dims=_STEAM_DIMS, page=page)
        heroes = client.heroes_for_id(val, per_page=limit, page=page)
        logos = client.logos_for_id(val, per_page=limit, page=page)
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
