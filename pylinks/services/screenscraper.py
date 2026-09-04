"""services.screenscraper — okładki z ScreenScraper.fr (przede wszystkim arcade).

Po co, skoro jest SteamGridDB: SGDB indeksuje gry po TYTULE, więc automaty, których
tytułu nie zna („Dance Dance Revolution 4th Mix Plus", „Cyber Troopers Virtual-On 4"),
zostają bez okładki. ScreenScraper szuka **po nazwie setu MAME** (`romnom=avsp.zip`,
`systemeid=75`) — czyli po tym, co mamy pewne i jednoznaczne. Dlatego jest tu
UZUPEŁNIENIEM SGDB dla trybu arcade, a nie jego zamiennikiem.

UWAGA — dostęp: samo konto użytkownika NIE wystarczy. Każdy endpoint wymaga też
danych DEWELOPERA (`devid`/`devpassword`), które ScreenScraper przydziela autorowi
programu na wniosek (forum → „Demande d'accès API"). Sprawdzone empirycznie:
bez nich API zwraca „Vérifier vos identifiants développeur" na każdym zapytaniu,
włącznie z listą systemów. Konto użytkownika podnosi tylko limit zapytań na dobę.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.screenscraper.fr/api2/"
SYSTEM_MAME = 75           # „Arcade" w nomenklaturze ScreenScraper

# typ grafiki ScreenScraper → nasze przeznaczenie
_BOX = ("box-2D", "box-2D-side", "box-texture", "flyer", "screenmarquee",
        "sstitle", "ss")
_WHEEL = ("wheel", "wheel-hd", "wheel-carbon", "wheel-steel")


def _crc32_size(path: str) -> tuple[str, int]:
    """CRC32 (hex, wielkimi literami) i rozmiar pliku — tak podpisuje je API.

    Czytamy porcjami: zipy MAME bywają duże, a i tak potrzebujemy tylko sumy.
    """
    import zlib
    try:
        crc, size = 0, 0
        with open(path, "rb") as f:
            while True:
                b = f.read(1 << 20)
                if not b:
                    break
                size += len(b)
                crc = zlib.crc32(b, crc)
        return f"{crc & 0xFFFFFFFF:08X}", size
    except OSError:
        return "", 0


class ScreenScraper:
    def __init__(self, devid: str = "", devpassword: str = "",
                 ssid: str = "", sspassword: str = "",
                 softname: str = "PyLinksWeb"):
        self.devid = (devid or "").strip()
        self.devpassword = (devpassword or "").strip()
        self.ssid = (ssid or "").strip()
        self.sspassword = (sspassword or "").strip()
        self.softname = softname

    @property
    def ok(self) -> bool:
        """API bez danych dewelopera odrzuca WSZYSTKO — konto usera nie wystarcza."""
        return bool(self.devid and self.devpassword)

    def _params(self, **kw) -> str:
        p = {"output": "json", "softname": self.softname,
             "devid": self.devid, "devpassword": self.devpassword}
        if self.ssid:
            p["ssid"] = self.ssid
            p["sspassword"] = self.sspassword
        p.update({k: v for k, v in kw.items() if v not in (None, "")})
        return urllib.parse.urlencode(p)

    def _call(self, endpoint: str, timeout: int = 20, **kw):
        if not self.ok:
            return None
        url = BASE + endpoint + "?" + self._params(**kw)
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    url, headers={"User-Agent": "Mozilla/5.0"}), timeout=timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            return {"__error__": e.code, "__body__": (e.read() or b"")[:200]
                    .decode("utf-8", "replace")}
        except Exception as e:
            return {"__error__": type(e).__name__}
        # API bywa uprzejme i zwraca 200 z tekstem błędu zamiast JSON-a
        try:
            return json.loads(raw)
        except Exception:
            return {"__error__": "not-json",
                    "__body__": raw[:200].decode("utf-8", "replace")}

    # ── właściwe zapytania ─────────────────────────────────────────────────
    def game_by_rom(self, setname: str, system_id: int = SYSTEM_MAME,
                    rom_path: str = "") -> dict | None:
        """Gra po nazwie setu MAME. To główna przewaga nad SGDB.

        Gdy znamy plik, dokładamy jeszcze CRC i rozmiar — dokumentacja API prosi
        o sumę kontrolną, a ta rozstrzyga pewniej niż nazwa (ten sam set bywa
        w kilku wersjach romsetu).
        """
        extra = {}
        if rom_path:
            crc, size = _crc32_size(rom_path)
            if crc:
                extra = {"crc": crc, "romtaille": str(size)}
        r = self._call("jeuInfos.php", romnom=f"{setname}.zip",
                       romtype="rom", systemeid=str(system_id), **extra)
        if not r or "__error__" in r:
            return None
        return ((r.get("response") or {}).get("jeu")) or None

    def media_urls(self, jeu: dict, kind: str = "box") -> list[str]:
        """Adresy grafik danego rodzaju, od najlepszej. `kind`: box | wheel."""
        want = _WHEEL if kind == "wheel" else _BOX
        by_type: dict[str, list[str]] = {}
        for m in (jeu or {}).get("medias") or []:
            t, u = m.get("type"), m.get("url")
            if t and u:
                by_type.setdefault(t, []).append(u)
        out: list[str] = []
        for t in want:
            out.extend(by_type.get(t, []))
        return out

    def user_quota(self) -> dict:
        """Stan konta: zużycie limitu dobowego (i dowód, że dane działają)."""
        r = self._call("ssuserInfos.php")
        if not r or "__error__" in r:
            return {"ok": False, "reason": (r or {}).get("__body__")
                    or str((r or {}).get("__error__", "brak odpowiedzi"))}
        u = (r.get("response") or {}).get("ssuser") or {}
        return {"ok": True, "id": u.get("id", ""), "level": u.get("niveau", ""),
                "today": u.get("requeststoday", ""),
                "max_day": u.get("maxrequestsperday", ""),
                "threads": u.get("maxthreads", "")}
