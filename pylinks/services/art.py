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
from pathlib import Path

# preferowane wymiary okładek (portret) — ładna biblioteka jak w Steam
_PORTRAIT_DIMS = "600x900,342x482,660x930,512x512,1024x1024"


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


# ── okładka dla gry (z cache dyskowym) ─────────────────────────────────────
def _covers_dir(cache_dir: Path) -> Path:
    d = Path(cache_dir) / "covers"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cached_cover(cache_dir: Path, key: str) -> str | None:
    """Zwraca data-URI z cache dyskowego, jeśli okładka już pobrana."""
    d = _covers_dir(cache_dir)
    safe = _safe_key(key)
    for f in d.glob(safe + ".*"):
        try:
            return _to_data_uri(f.read_bytes())
        except Exception:
            return None
    return None


def fetch_cover(cfg: dict, cache_dir: Path, key: str, game: dict) -> dict:
    """Pobiera (lub czyta z cache) okładkę gry. Zwraca:
        {"key", "found": bool, "data_uri": str|None, "cached": bool}
    """
    # 1) cache dyskowy
    hit = cached_cover(cache_dir, key)
    if hit:
        return {"key": key, "found": True, "data_uri": hit, "cached": True}

    # 2) sieć — SteamGridDB
    sgdb_key = (cfg.get("api_keys", {}) or {}).get("sgdb_key", "")
    client = SgdbClient(sgdb_key)
    if not client.ok:
        return {"key": key, "found": False, "data_uri": None, "cached": False,
                "reason": "brak klucza SGDB"}

    grids: list[dict] = []
    appid = game.get("appid")
    if appid:
        grids = client.grids_for_appid(appid)
    if not grids:
        results = client.search_with_fallback(game.get("name", ""))
        if results:
            grids = client.grids_for_id(results[0].get("id"))
    if not grids:
        return {"key": key, "found": False, "data_uri": None, "cached": False}

    for g in grids:
        url = g.get("thumb") or g.get("url")
        if not url:
            continue
        data = fetch(url) or (fetch(g.get("url")) if g.get("url") else None)
        if not data:
            continue
        # zapis na dysk
        try:
            mime = _mime(data)
            out = _covers_dir(cache_dir) / (_safe_key(key) + _ext(mime))
            out.write_bytes(data)
        except Exception:
            pass
        return {"key": key, "found": True, "data_uri": _to_data_uri(data),
                "cached": False}

    return {"key": key, "found": False, "data_uri": None, "cached": False}


def _safe_key(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", key)[:180]


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


def candidates_for_game(cfg: dict, game: dict, query: str = "",
                        limit: int = 18) -> list[dict]:
    """Zwraca listę okładek-kandydatów dla okna wyboru.

    query pusty → auto (appid gry lub szukanie po nazwie).
    query = URL/ID SGDB → gry po tym id.
    query = tekst → szukanie po tej frazie.
    Każdy kandydat: {id, url, thumb (data-URI), w, h, style}.
    """
    client = SgdbClient((cfg.get("api_keys", {}) or {}).get("sgdb_key", ""))
    if not client.ok:
        return []

    grids: list[dict] = []
    ref = parse_sgdb_ref(query)
    if ref is not None:
        grids = client.grids_for_id(ref, per_page=limit, dims="")
    elif query.strip():
        results = client.search_with_fallback(query.strip())
        if results:
            grids = client.grids_for_id(results[0].get("id"), per_page=limit, dims="")
    else:
        appid = game.get("appid")
        if appid:
            grids = client.grids_for_appid(appid, per_page=limit, dims="")
        if not grids:
            results = client.search_with_fallback(game.get("name", ""))
            if results:
                grids = client.grids_for_id(results[0].get("id"), per_page=limit, dims="")

    out: list[dict] = []
    for g in grids[:limit]:
        thumb_url = g.get("thumb") or g.get("url")
        if not thumb_url:
            continue
        data = fetch(thumb_url)
        if not data:
            continue
        out.append({
            "id": str(g.get("id", "")),
            "url": g.get("url", ""),
            "thumb": _to_data_uri(data),
            "w": g.get("width", 0),
            "h": g.get("height", 0),
            "style": (g.get("style") or ""),
        })
    return out


def suggest_games(cfg: dict, query: str, limit: int = 8) -> list[dict]:
    """Podpowiedzi tytułów (autouzupełnianie SGDB) — {id, name, release}."""
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


def save_chosen_cover(cache_dir: Path, key: str, full_url: str) -> dict:
    """Pobiera pełną okładkę z URL-a i zapisuje jako okładkę gry (nadpisuje).

    Zwraca {"found", "data_uri"}.
    """
    if not full_url:
        return {"found": False, "data_uri": None}
    data = fetch(full_url)
    if not data:
        return {"found": False, "data_uri": None}
    # usuń stare warianty (inne rozszerzenie) i zapisz nowy
    d = _covers_dir(cache_dir)
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
