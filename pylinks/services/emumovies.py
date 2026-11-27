"""services.emumovies — grafiki z biblioteki EmuMovies (konto abonenta, FTPS).

EmuMovies nie ma publicznego API (`api.emumovies.com` nie istnieje w DNS).
Abonenci dostają FTPS na `files2.emumovies.com`, a biblioteka jest tam w PACZKACH:
`/Official/<kategoria>/<system>/<system> (Boxes-Spines)(ReDump)(EM 2.2).zip` —
pojedyncza paczka to zwykle setki MB, a bywa i kilka GB w częściach RAR/7z.

Nie pobieramy paczek. ZIP ma spis treści na KOŃCU pliku, a FTP potrafi czytać od
wskazanego bajtu (REST), więc paczkę otwieramy jak plik z dostępem swobodnym:
spis 4812 grafik czyta się w ~0,7 s (3 odczyty), a pojedyncza okładka w ~0,4 s.
Spis członków trafia do `Cache/emumovies.db`, pobrane grafiki do `Cache/emumovies`.

Paczki RAR/7z (wieloczęściowe) tak się nie da czytać — wymagałyby ściągnięcia
całości, więc je pomijamy i wypisujemy, żeby było wiadomo, czego nie widać.
"""

from __future__ import annotations

import io
import os
import re
import sqlite3
import time
import zipfile
from pathlib import Path

from pylinks.services.launchbox import PLATFORMS as LB_PLATFORMS
from pylinks.services.launchbox import norm_title

DB_NAME = "emumovies.db"
DEFAULT_HOST = "files2.emumovies.com"
LEGACY_HOSTS = {"", "ftp.emumovies.com"}      # stary wpis w config → nowy host
ART_CATEGORIES = ("Artwork", "Bezels")        # kategorie z grafikami (reszta to wideo/PDF)
IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
MAX_PER_GAME = 40
SEP = "\n"                                    # paczka + członek w jednym tokenie

# rodzaje grafik warte pokazania najpierw (fragment nazwy paczki, małymi literami)
MEDIA_ORDER = ["boxes-2d", "boxes-3d", "boxes-2.5d", "box", "cart", "game media",
               "disc", "logo", "spine", "title", "snap", "marquee", "cabinet",
               "artwork", "background", "banner", "bezel"]

_LIST_RE = re.compile(r"^\S+\s+\S+\s+(<DIR>|\d+)\s+(.+?)\s*$")
_MEDIA_RE = re.compile(r"\(([^)]+)\)")


def cfg_creds(cfg: dict) -> tuple:
    src = (cfg or {}).get("extra_sources", {}) or {}
    host = (src.get("em_host") or "").strip()
    if host in LEGACY_HOSTS:
        host = DEFAULT_HOST
    return host, (src.get("em_user") or "").strip(), src.get("em_pass") or ""


def enabled(cfg: dict) -> bool:
    src = (cfg or {}).get("extra_sources", {}) or {}
    _h, u, p = cfg_creds(cfg)
    return bool(src.get("emumovies")) and bool(u and p)


def connect(cfg: dict, timeout: int = 40):
    """FTPS (serwer EmuMovies wymaga szyfrowania); zwykły FTP jako zapas."""
    import ftplib
    host, user, pwd = cfg_creds(cfg)
    if not (user and pwd):
        raise ValueError("brak loginu/hasła EmuMovies")
    try:
        ftp = ftplib.FTP_TLS(host, timeout=timeout)
        ftp.login(user, pwd)
        ftp.prot_p()
        return ftp
    except Exception:
        ftp = ftplib.FTP(host, timeout=timeout)
        ftp.login(user, pwd)
        return ftp


class Session:
    """Połączenie FTPS, które samo wstaje po zerwaniu.

    Serwer EmuMovies rozłącza długie sesje („[Errno 10054] połączenie zamknięte
    przez zdalnego hosta") — przy spisie kilkunastu platform to kwestia czasu.
    Każda operacja jest więc ponawiana na ŚWIEŻYM połączeniu; dla czytania
    paczek to przezroczyste, bo i tak czytamy od wskazanego bajtu.
    """

    RETRIES = 3

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.ftp = None

    def _ensure(self):
        if self.ftp is None:
            self.ftp = connect(self.cfg)
        return self.ftp

    def _drop(self):
        if self.ftp is not None:
            try:
                self.ftp.close()
            except Exception:
                pass
            self.ftp = None

    def run(self, fn, *a, **kw):
        """Wywołuje `fn(ftp, …)`; po zerwaniu łączy się ponownie i próbuje dalej."""
        import time as _t
        ostatni = None
        for proba in range(self.RETRIES):
            try:
                return fn(self._ensure(), *a, **kw)
            except Exception as e:
                ostatni = e
                self._drop()
                if proba + 1 < self.RETRIES:
                    _t.sleep(1.0 + proba)
        raise ostatni

    def listdir(self, path: str) -> list:
        return self.run(_listdir, path)

    def size(self, path: str) -> int:
        return self.run(lambda f, p: f.size(p) or 0, path)

    def read_at(self, path: str, pos: int, n: int) -> bytes:
        return self.run(_read_at, path, pos, n)

    def close(self):
        if self.ftp is not None:
            try:
                self.ftp.quit()
            except Exception:
                self._drop()
            self.ftp = None


def _read_at(ftp, path: str, pos: int, n: int) -> bytes:
    """`n` bajtów pliku od pozycji `pos` (RETR + REST)."""
    buf = bytearray()
    conn = ftp.transfercmd(f"RETR {path}", rest=pos)
    try:
        while len(buf) < n:
            chunk = conn.recv(min(1 << 16, n - len(buf)))
            if not chunk:
                break
            buf += chunk
    finally:
        conn.close()
        # Przerywamy transfer w połowie pliku, więc odpowiedź sterująca bywa
        # błędem albo nie przychodzi wcale — to NIE jest powód do awarii,
        # ale połączenia po tym już nie używamy (woła o to serwer EmuMovies).
        try:
            ftp.voidresp()
        except Exception:
            raise ConnectionError("transfer przerwany — ponawiam na nowym połączeniu")
    return bytes(buf)


def _listdir(ftp, path: str) -> list:
    """[(nazwa, czy_katalog, rozmiar)] — serwer EmuMovies nie zna MLSD."""
    lines: list = []
    ftp.retrlines(f"LIST {path}", lines.append)
    out = []
    for ln in lines:
        m = _LIST_RE.match(ln)
        if not m:                                   # format uniksowy (zapasowo)
            parts = ln.split(maxsplit=8)
            if len(parts) >= 9 and parts[8] not in (".", ".."):
                out.append((parts[8], ln[0:1] == "d", int(parts[4] or 0)))
            continue
        rozmiar, nazwa = m.group(1), m.group(2)
        if nazwa in (".", ".."):
            continue
        out.append((nazwa, rozmiar == "<DIR>", 0 if rozmiar == "<DIR>" else int(rozmiar)))
    return out


def test(cfg: dict) -> dict:
    """Loguje się i pokazuje, ile systemów widać w kategoriach z grafikami."""
    sess = Session(cfg)
    try:
        host, _u, _p = cfg_creds(cfg)
        kat = [n for n, d, _s in sess.listdir("/Official") if d]
        sysy: set = set()
        for k in ART_CATEGORIES:
            if k in kat:
                sysy |= {n for n, d, _s in sess.listdir(f"/Official/{k}") if d}
        return {"ok": True, "host": host, "categories": len(kat),
                "systems": len(sysy), "sample": sorted(sysy)[:10]}
    except Exception as e:
        return {"ok": False, "reason": str(e)}
    finally:
        sess.close()


# ── paczka ZIP na FTP jako plik z dostępem swobodnym ────────────────────────
class FtpFile(io.RawIOBase):
    """Zdalny plik czytany od wskazanego bajtu — przez `Session` (z wznawianiem).

    Dzięki temu `zipfile` otwiera paczkę z serwera tak, jakby leżała na dysku:
    czyta sam spis treści z końca pliku, a potem wyłącznie te bajty, w których
    siedzi wybrana grafika."""

    def __init__(self, sess: "Session", path: str, size: int = 0):
        self.sess = sess
        self.path = path
        self.pos = 0
        self.size = size or sess.size(path)

    def seek(self, off, whence=0):
        self.pos = (off if whence == 0 else
                    self.pos + off if whence == 1 else self.size + off)
        return self.pos

    def tell(self):
        return self.pos

    def seekable(self):
        return True

    def readable(self):
        return True

    def read(self, n=-1):
        if n is None or n < 0:
            n = max(0, self.size - self.pos)
        if n <= 0:
            return b""
        dane = self.sess.read_at(self.path, self.pos, n)
        self.pos += len(dane)
        return dane

    readinto = None       # RawIOBase domyślnie użyje read()


def open_pack(sess: "Session", path: str, size: int = 0) -> zipfile.ZipFile:
    return zipfile.ZipFile(FtpFile(sess, path, size))


def media_of(pack_name: str) -> str:
    """Rodzaj grafiki z nazwy paczki: „… (Boxes-Spines)(ReDump)…" → „Boxes-Spines"."""
    for m in _MEDIA_RE.findall(pack_name):
        t = m.strip()
        if t and not re.fullmatch(r"(EM|ReDump|Redump|No-Intro|TOSEC)[\s\d.]*", t, re.I):
            return t
    return "EmuMovies"


# ── indeks ──────────────────────────────────────────────────────────────────
def db_path(cache_dir) -> Path:
    return Path(cache_dir) / DB_NAME


def _db(cache_dir) -> sqlite3.Connection:
    p = db_path(cache_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p))
    con.executescript("""
        CREATE TABLE IF NOT EXISTS files(system TEXT, media TEXT, norm TEXT,
                                         name TEXT, pack TEXT, member TEXT,
                                         PRIMARY KEY(pack, member));
        CREATE INDEX IF NOT EXISTS ix_em ON files(system, norm);
        CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
    """)
    return con


def status(cache_dir) -> dict:
    p = db_path(cache_dir)
    if not p.is_file():
        return {"ready": False}
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        meta = dict(con.execute("SELECT k, v FROM meta").fetchall())
        n = con.execute("SELECT count(*) FROM files").fetchone()[0]
        sysy = [r[0] for r in con.execute(
            "SELECT system FROM files GROUP BY system ORDER BY count(*) DESC")]
        packs = con.execute("SELECT count(DISTINCT pack) FROM files").fetchone()[0]
        con.close()
    except sqlite3.Error:
        return {"ready": False}
    return {"ready": n > 0, "files": n, "packs": packs, "systems": sysy,
            "updated": meta.get("updated", ""),
            "size_mb": round(p.stat().st_size / (1 << 20), 1)}


# EmuMovies łączy pokrewne platformy w jeden katalog („Sega Genesis - Mega Drive",
# „Sega Naomi - Naomi 2") albo nazywa je inaczej niż LaunchBox („Sony Playstation
# Portable" zamiast „Sony PSP") — tego nie da się wyliczyć, więc wypisujemy wprost.
EM_ALIASES = {
    "MD": ["Sega Genesis - Mega Drive"],
    "GENESIS": ["Sega Genesis - Mega Drive"],
    "SMS": ["Sega Master System - Mark III"],
    "NAOMI": ["Sega Naomi - Naomi 2"],
    "NAOMI2": ["Sega Naomi - Naomi 2"],
    "PSP": ["Sony Playstation Portable"],
    "PCENGINE": ["NEC TurboGrafx-16 - PC Engine"],
    "TG16": ["NEC TurboGrafx-16 - PC Engine"],
    "NEOGEO": ["SNK Neo Geo AES - MVS"],
    "MSX": ["Microsoft MSX - MSX2"],
}


def guess_system(our_system: str, available) -> str:
    """Katalog EmuMovies dla naszego systemu.

    Kolejno: nazwa wprost → nazwa z LaunchBoksa → wypisany alias → katalog
    ZACZYNAJĄCY SIĘ od naszej nazwy (zbiorcze katalogi w rodzaju
    „Sega Naomi - Naomi 2"). Bez tego MD, NAOMI, NAOMI2 i PSP nie miały trafienia."""
    klucz = (our_system or "").upper()
    chce = [norm_title(our_system)]
    lb = LB_PLATFORMS.get(klucz)
    if lb:
        chce.append(norm_title(lb))
    chce += [norm_title(a) for a in EM_ALIASES.get(klucz, ())]
    chce = [c for c in chce if c]
    katalogi = [(norm_title(d), d) for d in (available or ())]
    for c in chce:
        for nd, d in katalogi:
            if nd == c:
                return d
    for c in chce:
        if len(c) < 6:                     # za krótkie, żeby pasować „na początek"
            continue
        for nd, d in katalogi:
            if nd.startswith(c):
                return d
    return ""


def index_systems(cfg: dict, cache_dir, systems, progress=None, cancel=None,
                  sess=None) -> dict:
    """Spisuje zawartość paczek ZIP dla wskazanych systemów.

    Połączenie wstaje samo po zerwaniu, a paczka, której nie udało się odczytać,
    trafia na listę pominiętych — spis idzie dalej zamiast przerywać całość."""
    wlasny = sess is None
    sess = sess or Session(cfg)
    con = _db(cache_dir)
    out = {"ok": True, "systems": [], "files": 0, "packs": 0,
           "skipped_systems": [], "skipped_packs": []}
    try:
        katalogi = {}
        for k in ART_CATEGORIES:
            try:
                katalogi[k] = [n for n, d, _s in sess.listdir(f"/Official/{k}") if d]
            except Exception:
                katalogi[k] = []
        for i, sysname in enumerate(systems or [], 1):
            if cancel and cancel():
                raise InterruptedError("przerwano")
            znalazl = False
            n_sys = 0
            con.execute("DELETE FROM files WHERE system=?", (sysname,))
            for kat, lista in katalogi.items():
                em_dir = guess_system(sysname, lista)
                if not em_dir:
                    continue
                znalazl = True
                baza = f"/Official/{kat}/{em_dir}"
                for nazwa, czy_kat, rozmiar in sess.listdir(baza):
                    if cancel and cancel():
                        raise InterruptedError("przerwano")
                    if czy_kat:
                        continue
                    if not nazwa.lower().endswith(".zip"):
                        out["skipped_packs"].append(f"{sysname}: {nazwa}")
                        continue
                    sciezka = f"{baza}/{nazwa}"
                    media = media_of(nazwa)
                    if progress:
                        progress(i, len(systems or []), f"{sysname}: {media}")
                    try:
                        z = open_pack(sess, sciezka, rozmiar)
                        czlonkowie = [m for m in z.namelist()
                                      if m.lower().endswith(IMG_EXTS)]
                    except Exception as e:
                        out["skipped_packs"].append(f"{sysname}: {nazwa} ({e})")
                        continue
                    con.executemany(
                        "INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?)",
                        [(sysname, media, norm_title(Path(m).stem), Path(m).name,
                          sciezka, m) for m in czlonkowie])
                    n_sys += len(czlonkowie)
                    out["packs"] += 1
            if not znalazl:
                out["skipped_systems"].append(sysname)
            elif n_sys:
                out["systems"].append({"system": sysname, "files": n_sys})
                out["files"] += n_sys
            con.commit()
        con.execute("INSERT OR REPLACE INTO meta VALUES('updated', ?)",
                    (time.strftime("%Y-%m-%d %H:%M"),))
        con.commit()
        return out
    finally:
        con.close()
        if wlasny:
            sess.close()


# ── kandydaci + pobranie pojedynczej grafiki ────────────────────────────────
def candidates(cache_dir, system: str, title: str, mame_set: str = "",
               base_url: str = "", limit: int = MAX_PER_GAME) -> list:
    """Kandydaci ze spisu; adresy prowadzą do naszego serwera miniatur."""
    p = db_path(cache_dir)
    if not p.is_file() or not base_url:
        return []
    normy = [n for n in (norm_title(t) for t in (title, mame_set)) if n]
    if not normy:
        return []
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        q = ",".join("?" * len(normy))
        rows = con.execute(
            f"SELECT media, pack, member FROM files WHERE system=? AND norm IN ({q})",
            [system] + normy).fetchall()
        if not rows:
            rows = con.execute(
                f"SELECT media, pack, member FROM files WHERE norm IN ({q})",
                normy).fetchall()
    finally:
        con.close()

    def _rank(r):
        m = (r[0] or "").lower()
        for i, slowo in enumerate(MEDIA_ORDER):
            if slowo in m:
                return (i, m)
        return (len(MEDIA_ORDER), m)

    rows.sort(key=_rank)
    import base64
    out = []
    for media, pack, member in rows[:limit]:
        token = base64.urlsafe_b64encode(
            (pack + SEP + member).encode("utf-8")).decode("ascii")
        url = f"{base_url}/em/{token}"
        out.append({"id": f"emumovies_{pack}_{member}", "url": url, "thumb": url,
                    "w": 0, "h": 0, "style": f"EM: {media}",
                    "kind": "emumovies", "square": False})
    return out


def fetch(cfg: dict, cache_dir, remote: str) -> "Path | None":
    """Pobiera JEDNĄ grafikę z paczki na FTP (bez ściągania paczki) do cache."""
    import hashlib
    if not remote:
        return None
    pack, _sep, member = remote.partition(SEP)
    if not member:
        return None
    d = Path(cache_dir) / "emumovies"
    d.mkdir(parents=True, exist_ok=True)
    ext = Path(member).suffix.lower() or ".png"
    dst = d / (hashlib.sha1(remote.encode("utf-8")).hexdigest() + ext)
    if dst.is_file() and dst.stat().st_size:
        return dst
    sess = Session(cfg)
    try:
        dane = open_pack(sess, pack).read(member)
        tmp = dst.with_suffix(dst.suffix + ".part")
        tmp.write_bytes(dane)
        os.replace(tmp, dst)
        return dst
    except Exception:
        return None
    finally:
        sess.close()
