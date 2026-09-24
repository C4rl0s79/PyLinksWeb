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

from pylinks.constants import IGDB_TGDB_MATCH_MIN
from pylinks.core.matching import name_similarity
from pylinks.services.art import fetch_api, PIL_OK

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


def _interleave(lists) -> list:
    """Przeplata listy po jednym elemencie (round-robin), pomijając wyczerpane.

    Dzięki temu jedno źródło/gra z ogromną liczbą grafik nie wypycha pozostałych.
    """
    out, idx = [], 0
    lists = [x for x in lists if x]
    while lists:
        nxt = []
        for L in lists:
            if idx < len(L):
                out.append(L[idx])
                nxt.append(L)
        if not nxt:
            break
        lists, idx = nxt, idx + 1
    return out


def _cand(kind: str, url: str, style: str, rid: str) -> dict | None:
    """META kandydata (URL miniatury — bez pobierania)."""
    if not url:
        return None
    return {
        "id": rid, "url": url, "thumb": url,
        "w": 0, "h": 0, "style": style, "kind": kind, "square": False,
    }


LIBRETRO_BASE = "https://raw.githubusercontent.com/libretro-thumbnails"
LIBRETRO_SYSTEM_MAP = {
    "PS1": "Sony_-_PlayStation", "PS2": "Sony_-_PlayStation_2",
    "PS3": "Sony_-_PlayStation_3", "PSP": "Sony_-_PlayStation_Portable",
    "N64": "Nintendo_-_Nintendo_64",
    "SNES": "Nintendo_-_Super_Nintendo_Entertainment_System",
    "SNESMSU1": "Nintendo_-_Super_Nintendo_Entertainment_System",
    "NES": "Nintendo_-_Nintendo_Entertainment_System",
    "GB": "Nintendo_-_Game_Boy", "GBA": "Nintendo_-_Game_Boy_Advance",
    "GBC": "Nintendo_-_Game_Boy_Color", "NDS": "Nintendo_-_Nintendo_DS",
    "GCN": "Nintendo_-_GameCube", "WII": "Nintendo_-_Wii",
    "SATURN": "Sega_-_Saturn", "DC": "Sega_-_Dreamcast",
    "NAOMI": "Sega_-_Dreamcast", "NAOMI2": "Sega_-_Dreamcast",
    "MD": "Sega_-_Mega_Drive_-_Genesis", "SEGACD": "Sega_-_Mega_Drive_-_Genesis",
    "SMS": "Sega_-_Master_System_-_Mark_III", "GG": "Sega_-_Game_Gear",
    "ARCADE": "MAME", "MAME": "MAME", "NEOGEO": "SNK_-_Neo_Geo",
    "NGP": "SNK_-_Neo_Geo_Pocket", "ATARI2600": "Atari_-_2600",
    "ATARI7800": "Atari_-_7800", "3DO": "3DO_Interactive_Multiplayer",
    "PCENGINE": "NEC_-_PC_Engine_-_TurboGrafx_16",
}


def libretro_candidates(system: str, title: str) -> list:
    """Box art / snap / title ze zbiorów Libretro Thumbnails (GitHub, bez klucza)."""
    repo = LIBRETRO_SYSTEM_MAP.get((system or "").upper(), "")
    if not repo or not title:
        return []
    import urllib.request as _u
    enc = _u.quote(title, safe=" -.'()!").replace(" ", "%20")
    out = []
    for folder, kind in (("Named_Boxarts", "boxart"), ("Named_Titles", "title"),
                         ("Named_Snaps", "snap")):
        url = f"{LIBRETRO_BASE}/{repo}/master/{folder}/{enc}.png"
        out.append({"id": f"libretro_{repo}_{folder}", "url": url, "thumb": url,
                    "w": 0, "h": 0, "style": f"libretro-{kind}",
                    "kind": "libretro", "square": False})
    return out


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
        # IGDB/TGDB nie mają stronicowania jak SGDB — „dociągnij więcej" tu nie
        # zadziała, więc całą pulę bierzemy od razu. Ile: ustawienie użytkownika.
        self.max_per_source = max(4, min(80, int(src.get("max_per_source", 12) or 12)))
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
            c = _cand("steam", url, label, f"steamcdn_{appid}_{url.split('/')[-1]}")
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

    def igdb_candidates(self, title: str, platform: str = "PC",
                        limit: int = 12) -> list[dict]:
        """Kandydaci z IGDB. `limit` = ile grafik maksymalnie oddać.

        IGDB nie ma stronicowania jak SGDB, więc „dociągnij więcej" tu nie zadziała
        — całą pulę trzeba wziąć od razu. Przy większym limicie bierzemy grafiki
        także z KOLEJNYCH dobrze dopasowanych gier (inne edycje/regiony), bo jedna
        gra oddaje najwyżej okładkę + kilka artworków i zrzutów.
        """
        if not self.use_igdb or not self.igdb_client_id or not title:
            return []
        limit = max(4, min(80, int(limit or 12)))
        esc = title.replace('"', '\\"')
        games = self._igdb_query(
            "games",
            f'search "{esc}"; '
            f'fields id,name,platforms,cover.url,artworks.url,screenshots.url; '
            f'limit {max(12, min(50, limit))};')
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
                game = max(pm, key=_sim)       # zgodna platforma ma pierwszeństwo
        if game is None:
            game = max(named, key=_sim)

        def _img(raw_url, rid):
            url = raw_url.replace("t_thumb", "t_1080p")
            if not url.startswith("http"):
                url = "https:" + url
            return _cand("igdb", url, "igdb", rid)

        # Grafiki JEDNEJ, najlepiej dopasowanej gry — mieszanie z innymi tytułami
        # dałoby okładkę cudzej gry. Problemem jest za to skrajna nierówność
        # kategorii w IGDB (bywa 3 artworki i 300 zrzutów): przy zwykłym „bierz po
        # kolei" zrzuty wypychały wszystko inne. Dlatego okładka zawsze pierwsza,
        # a resztę puli dzielimy przeplotem artwork/zrzut.
        out = []
        cover = game.get("cover")
        if cover and cover.get("url"):
            c = _img(cover["url"], f"igdb_cover_{game['id']}")
            if c:
                out.append(c)
        arts = [_img(x["url"], f"igdb_art_{game['id']}_{i}")
                for i, x in enumerate(game.get("artworks") or []) if x.get("url")]
        shots = [_img(x["url"], f"igdb_ss_{game['id']}_{i}")
                 for i, x in enumerate(game.get("screenshots") or []) if x.get("url")]
        mixed = _interleave([[c for c in arts if c], [c for c in shots if c]])
        out += mixed[:max(0, limit - len(out))]
        return out[:limit]

    # ── TheGamesDB ──────────────────────────────────────────────────────────
    def tgdb_candidates(self, title: str, platform: str = "PC",
                        limit: int = 12) -> list[dict]:
        """Kandydaci z TheGamesDB. Przy wyższym limicie dopuszczamy też zrzuty
        i ekrany tytułowe — bez tego pula kończy się na okładce, fanarcie,
        logo i banerze."""
        if not self.use_tgdb or not self.tgdb_key or not title:
            return []
        limit = max(4, min(80, int(limit or 12)))
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
        if limit > 12:
            wanted |= {"screenshot", "titlescreen"}
        prio = {"boxart": 0, "clearlogo": 1, "fanart": 2, "banner": 3,
                "titlescreen": 4, "screenshot": 5}
        out = []
        for img in sorted(images, key=lambda i: prio.get(i.get("type", ""), 9)):
            if len(out) >= limit:
                break
            itype, side, fn = img.get("type", ""), img.get("side", ""), img.get("filename", "")
            if itype not in wanted or not fn:
                continue
            if itype == "boxart" and side not in ("", "front"):
                continue
            full = base_url + fn
            c = _cand("tgdb", full, f"tgdb-{itype}", f"tgdb_{gid}_{img.get('id', '')}")
            if c:
                out.append(c)
        return out

    # ── agregacja dla gry PC ────────────────────────────────────────────────
    def candidates(self, title: str, appid: str = "", platform: str = "PC",
                   limit: int = 0) -> list[dict]:
        """`limit` = maks. grafik z KAŻDEGO źródła (0 → wartość z configu)."""
        if not limit:
            limit = self.max_per_source
        out: list[dict] = []
        if appid:
            out += self.steam_cdn_candidates(appid)
        out += self.igdb_candidates(title, platform, limit=limit)
        out += self.tgdb_candidates(title, platform, limit=limit)
        return out
