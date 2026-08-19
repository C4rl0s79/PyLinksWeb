"""services.extra_art — dodatkowe źródła grafik: IGDB, TheGamesDB, Steam CDN.

Port z wersji tkinter (ExtraArtSources) — uzupełnia SteamGridDB. Zwraca
kandydatów w formacie pickera: {id, url, thumb (data-URI), w, h, style, kind}.
Wymaga kluczy w config.extra_sources (IGDB: client_id+secret; TGDB: key).
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from io import BytesIO

from pylinks.constants import IGDB_TGDB_MATCH_MIN
from pylinks.core.matching import name_similarity
from pylinks.services.art import fetch, fetch_api, _to_data_uri, PIL_OK

if PIL_OK:
    from PIL import Image


def fetch_post(url: str, body: bytes, hdrs: dict | None = None,
               timeout: int = 12) -> bytes | None:
    try:
        h = {"User-Agent": "Mozilla/5.0"}
        if hdrs:
            h.update(hdrs)
        req = urllib.request.Request(url, data=body, headers=h, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = r.read()
        return d or None
    except Exception:
        return None


def _cand(kind: str, url: str, data: bytes, style: str, rid: str) -> dict | None:
    if not data:
        return None
    w = h = 0
    if PIL_OK:
        try:
            w, h = Image.open(BytesIO(data)).size
        except Exception:
            return None
    return {
        "id": rid, "url": url, "thumb": _to_data_uri(data),
        "w": w, "h": h, "style": style, "kind": kind,
        "square": bool(w and h and abs(w - h) <= max(2, min(w, h) // 8)),
    }


class ExtraArtSources:
    IGDB_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
    IGDB_API_URL = "https://api.igdb.com/v4"

    IGDB_PLATFORM_IDS = {"PC": 6}
    TGDB_PLATFORM_IDS = {"PC": 1}

    def __init__(self, cfg: dict):
        src = cfg.get("extra_sources", {}) or {}
        self.use_steam_cdn = bool(src.get("steam_cdn", True))
        self.use_igdb = bool(src.get("igdb", False))
        self.igdb_client_id = (src.get("igdb_client_id", "") or "").strip()
        self.igdb_client_secret = (src.get("igdb_client_secret", "") or "").strip()
        self.use_tgdb = bool(src.get("tgdb", False))
        self.tgdb_key = (src.get("tgdb_key", "") or "").strip()
        self._igdb_tok = ""
        self._igdb_tok_exp = 0.0
        self._lock = threading.Lock()

    # ── Steam CDN (bez klucza) ──────────────────────────────────────────────
    def steam_cdn_candidates(self, appid: str) -> list[dict]:
        if not self.use_steam_cdn or not appid:
            return []
        base = f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}"
        urls = [
            (f"{base}/library_600x900_2x.jpg", "steam-library"),
            (f"{base}/header.jpg", "steam-header"),
            (f"{base}/library_hero.jpg", "steam-hero"),
            (f"{base}/capsule_616x353.jpg", "steam-capsule"),
        ]
        out = []
        for url, label in urls:
            c = _cand("steam", url, fetch(url, timeout=8), label,
                      f"steamcdn_{appid}_{url.split('/')[-1]}")
            if c:
                out.append(c)
        return out

    # ── IGDB ────────────────────────────────────────────────────────────────
    def _get_igdb_token(self) -> str:
        with self._lock:
            if self._igdb_tok and time.time() < self._igdb_tok_exp - 60:
                return self._igdb_tok
            if not (self.igdb_client_id and self.igdb_client_secret):
                return ""
            try:
                params = (f"client_id={urllib.request.quote(self.igdb_client_id)}"
                          f"&client_secret={urllib.request.quote(self.igdb_client_secret)}"
                          f"&grant_type=client_credentials")
                d = fetch_post(f"{self.IGDB_TOKEN_URL}?{params}", b"",
                               hdrs={"Content-Type": "application/x-www-form-urlencoded"})
                if not d:
                    return ""
                data = json.loads(d)
                self._igdb_tok = data["access_token"]
                self._igdb_tok_exp = time.time() + data.get("expires_in", 3600)
                return self._igdb_tok
            except Exception:
                return ""

    def _igdb_query(self, endpoint: str, apicalypse: str) -> list[dict]:
        token = self._get_igdb_token()
        if not token:
            return []
        d = fetch_post(f"{self.IGDB_API_URL}/{endpoint}", apicalypse.encode(),
                       hdrs={"Client-ID": self.igdb_client_id,
                             "Authorization": f"Bearer {token}",
                             "Content-Type": "text/plain"})
        if not d:
            return []
        try:
            return json.loads(d)
        except Exception:
            return []

    def igdb_candidates(self, title: str, platform: str = "PC") -> list[dict]:
        if not self.use_igdb or not self.igdb_client_id or not title:
            return []
        esc = title.replace('"', '\\"')
        games = self._igdb_query(
            "games",
            f'search "{esc}"; '
            f'fields id,name,platforms,cover.url,artworks.url,screenshots.url; '
            f'limit 12;')
        if not games:
            return []
        named = [g for g in games
                 if name_similarity(title, g.get("name", "")) >= IGDB_TGDB_MATCH_MIN]
        if not named:
            return []
        pid = self.IGDB_PLATFORM_IDS.get((platform or "").upper())
        _sim = lambda g: name_similarity(title, g.get("name", ""))
        game = None
        if pid is not None:
            pm = [g for g in named if pid in (g.get("platforms") or [])]
            if pm:
                game = max(pm, key=_sim)
        if game is None:
            game = max(named, key=_sim)

        def _img(raw_url, rid):
            for size in ("t_1080p", "t_cover_big", "t_720p"):
                url = raw_url.replace("t_thumb", size)
                if not url.startswith("http"):
                    url = "https:" + url
                data = fetch(url, timeout=10)
                if data:
                    return _cand("igdb", url, data, "igdb", rid)
            return None

        out = []
        cover = game.get("cover")
        if cover and cover.get("url"):
            c = _img(cover["url"], f"igdb_cover_{game['id']}")
            if c:
                out.append(c)
        for i, art in enumerate((game.get("artworks") or [])[:4]):
            if art.get("url"):
                c = _img(art["url"], f"igdb_art_{game['id']}_{i}")
                if c:
                    out.append(c)
        for i, ss in enumerate((game.get("screenshots") or [])[:3]):
            if ss.get("url"):
                c = _img(ss["url"], f"igdb_ss_{game['id']}_{i}")
                if c:
                    out.append(c)
        return out

    # ── TheGamesDB ──────────────────────────────────────────────────────────
    def tgdb_candidates(self, title: str, platform: str = "PC") -> list[dict]:
        if not self.use_tgdb or not self.tgdb_key or not title:
            return []
        enc = urllib.request.quote(title)
        p_filter = ""
        pid = self.TGDB_PLATFORM_IDS.get((platform or "").upper())
        if pid:
            p_filter = f"&filter[platform]={pid}"
        url = (f"https://api.thegamesdb.net/v1/Games/ByGameName"
               f"?apikey={self.tgdb_key}&name={enc}"
               f"&fields=overview&include=boxart{p_filter}")
        d = fetch_api(url, timeout=10)
        if not d:
            return []
        try:
            resp = json.loads(d)
        except Exception:
            return []
        if resp.get("code") != 200:
            return []
        games = resp.get("data", {}).get("games", [])
        if not games:
            return []
        game = max(games, key=lambda g: name_similarity(title, g.get("game_title", "")))
        if name_similarity(title, game.get("game_title", "")) < IGDB_TGDB_MATCH_MIN:
            return []
        gid = str(game.get("id", ""))
        box = resp.get("include", {}).get("boxart", {})
        base_url = box.get("base_url", {}).get(
            "original", "https://cdn.thegamesdb.net/images/original/")
        images = box.get("data", {}).get(gid, [])
        if not images:
            d2 = fetch_api(f"https://api.thegamesdb.net/v1/Games/Images"
                           f"?apikey={self.tgdb_key}&games_id={gid}", timeout=10)
            if d2:
                try:
                    ir = json.loads(d2)
                    images = ir.get("data", {}).get("images", {}).get(gid, [])
                    base_url = ir.get("data", {}).get("base_url", {}).get("original", base_url)
                except Exception:
                    pass
        wanted = {"boxart", "fanart", "clearlogo", "banner"}
        out = []
        for img in images:
            itype, side, fn = img.get("type", ""), img.get("side", ""), img.get("filename", "")
            if itype not in wanted or not fn:
                continue
            if itype == "boxart" and side not in ("", "front"):
                continue
            full = base_url + fn
            c = _cand("tgdb", full, fetch(full, timeout=10), f"tgdb-{itype}",
                      f"tgdb_{gid}_{img.get('id', '')}")
            if c:
                out.append(c)
        return out

    # ── agregacja dla gry PC ────────────────────────────────────────────────
    def candidates(self, title: str, appid: str = "", platform: str = "PC") -> list[dict]:
        out: list[dict] = []
        if appid:
            out += self.steam_cdn_candidates(appid)
        out += self.igdb_candidates(title, platform)
        out += self.tgdb_candidates(title, platform)
        return out
