"""core.store — trwały stan per-gra w SQLite (Cache/pylinks.db).

Dlaczego baza, a nie config.json:
* dane per-gra rosną z biblioteką (u nas 85 % pliku przy 626 grach) — config ma
  zostać małym, czytelnym plikiem ustawień;
* zapis configu szedł przez `write_text()` bez pliku tymczasowego, więc awaria
  w trakcie mogła OBCIĄĆ plik i zabrać klucze API oraz systemy ROM;
* każdy klik ikony przepisywał CAŁY config (O(n)); tu idzie jeden UPSERT.

„Stub" gry = zapamiętany WYBÓR (odnośnik/identyfikator grafiki), nie sam obraz.
Przeżywa usunięcie gry z dysku i czyszczenie cache — gdy gra wróci, wraca też
dokładnie ta grafika, którą wybrał użytkownik.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS choices (
    key       TEXT NOT NULL,
    mode      TEXT NOT NULL,          -- 'icon' | 'steam'
    asset     TEXT NOT NULL,          -- 'icon' | 'grid' | 'hero' | 'logo'
    source_id TEXT,                   -- stabilny id kandydata (sgdb/igdb/tgdb/steamcdn)
    url       TEXT,                   -- pełny URL, gdy znany
    ts        REAL NOT NULL,
    PRIMARY KEY (key, mode, asset)
);
CREATE TABLE IF NOT EXISTS crops (
    key    TEXT PRIMARY KEY,
    fit    TEXT NOT NULL,
    coff   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS overrides (
    key   TEXT NOT NULL,
    kind  TEXT NOT NULL,              -- 'title' (dopasowanie SGDB) | 'exe'
    value TEXT,
    PRIMARY KEY (key, kind)
);
CREATE INDEX IF NOT EXISTS idx_choices_key ON choices(key);
"""


def _db(cache_dir) -> sqlite3.Connection:
    p = Path(cache_dir)
    p.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p / "pylinks.db"), timeout=10)
    con.execute("PRAGMA journal_mode=WAL")     # trwałość + równoległy odczyt
    con.executescript(_SCHEMA)
    return con


# ── wybory grafik ──────────────────────────────────────────────────────────
def set_choice(cache_dir, key: str, mode: str, asset: str,
               url: str = None, source_id: str = None) -> None:
    if not key:
        return
    try:
        with _db(cache_dir) as con:
            con.execute(
                "INSERT INTO choices(key,mode,asset,source_id,url,ts) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(key,mode,asset) DO UPDATE SET "
                "source_id=excluded.source_id, url=excluded.url, ts=excluded.ts",
                (key, mode, asset, source_id, url, time.time()))
    except Exception:
        pass


def clear_choice(cache_dir, key: str, mode: str, asset: str) -> None:
    try:
        with _db(cache_dir) as con:
            con.execute("DELETE FROM choices WHERE key=? AND mode=? AND asset=?",
                        (key, mode, asset))
    except Exception:
        pass


def get_choice(cache_dir, key: str, mode: str, asset: str):
    """(url, source_id) zapamiętanego wyboru albo (None, None)."""
    try:
        with _db(cache_dir) as con:
            r = con.execute(
                "SELECT url, source_id FROM choices WHERE key=? AND mode=? AND asset=?",
                (key, mode, asset)).fetchone()
        return (r[0], r[1]) if r else (None, None)
    except Exception:
        return (None, None)


def all_choices(cache_dir) -> dict:
    """{key: {mode: {asset: {"url":…, "source_id":…}}}} — do nałożenia na config."""
    out: dict = {}
    try:
        with _db(cache_dir) as con:
            for key, mode, asset, sid, url in con.execute(
                    "SELECT key,mode,asset,source_id,url FROM choices"):
                out.setdefault(key, {}).setdefault(mode, {})[asset] = {
                    "url": url, "source_id": sid}
    except Exception:
        pass
    return out


# ── kadr ikony ─────────────────────────────────────────────────────────────
def set_crop(cache_dir, key: str, fit: str, offset: float) -> None:
    try:
        with _db(cache_dir) as con:
            con.execute("INSERT INTO crops(key,fit,coff) VALUES(?,?,?) "
                        "ON CONFLICT(key) DO UPDATE SET fit=excluded.fit, "
                        "coff=excluded.coff", (key, fit, float(offset)))
    except Exception:
        pass


def clear_crop(cache_dir, key: str) -> None:
    try:
        with _db(cache_dir) as con:
            con.execute("DELETE FROM crops WHERE key=?", (key,))
    except Exception:
        pass


def all_crops(cache_dir) -> dict:
    out: dict = {}
    try:
        with _db(cache_dir) as con:
            for key, fit, coff in con.execute("SELECT key,fit,coff FROM crops"):
                out[key] = {"fit": fit, "offset": coff}
    except Exception:
        pass
    return out


# ── nadpisania (tytuł SGDB / ścieżka exe) ──────────────────────────────────
def set_override(cache_dir, key: str, kind: str, value) -> None:
    try:
        with _db(cache_dir) as con:
            if value in (None, ""):
                con.execute("DELETE FROM overrides WHERE key=? AND kind=?", (key, kind))
            else:
                con.execute("INSERT INTO overrides(key,kind,value) VALUES(?,?,?) "
                            "ON CONFLICT(key,kind) DO UPDATE SET value=excluded.value",
                            (key, kind, str(value)))
    except Exception:
        pass


def all_overrides(cache_dir, kind: str) -> dict:
    out: dict = {}
    try:
        with _db(cache_dir) as con:
            for key, value in con.execute(
                    "SELECT key,value FROM overrides WHERE kind=?", (kind,)):
                out[key] = value
    except Exception:
        pass
    return out


def stats(cache_dir) -> dict:
    try:
        with _db(cache_dir) as con:
            q = lambda s: con.execute(s).fetchone()[0]
            return {"choices": q("SELECT COUNT(*) FROM choices"),
                    "crops": q("SELECT COUNT(*) FROM crops"),
                    "overrides": q("SELECT COUNT(*) FROM overrides")}
    except Exception:
        return {"choices": 0, "crops": 0, "overrides": 0}
