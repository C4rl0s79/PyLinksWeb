"""services.series — rozpoznawanie serii gier (IGDB) do układania ich obok siebie.

Po co: z samej nazwy nie da się wyczytać przynależności do serii. „Yakuza 0",
„Yakuza Kiwami" i „Like a Dragon: Infinite Wealth" to jedna seria mimo zmiany
nazwy, a „Judgment" to jej gałąź boczna — przy tym „Castlevania Judgment" nie ma
z nimi nic wspólnego, choć dzieli słowo. IGDB trzyma to jawnie:

* franczyza (`franchises`) — najszersza rodzina (Yakuza + Judgment),
* kolekcja (`collections`) — konkretna podseria; gra bywa w kilku naraz
  („Judgment" jest i w serii głównej, i w podserii Judgment),
* `version_parent` — wskazuje grę bazową dla EDYCJI (GOTY, Director's Cut),
* `remakes` / `remasters` / `expanded_games` / `ports` — listy po stronie
  ORYGINAŁU (Yakuza → Kiwami, Vesperia → Definitive Edition, Symphonia → port
  PC); nowsze wydanie nie wskazuje oryginału samo, trzeba go odszukać odwrotnie.

Wynik trafia do tabeli `game_series` w `Cache/pylinks.db`, skąd czyta go
PyLinksDeck (tylko do odczytu). PyLinksWeb sam z niego nie korzysta.

Kolejność: IGDB zna tylko DATĘ WYDANIA. Dla serii takich jak Yakuza to nie
jest kolejność fabularna (Yakuza 0 wyszło po piątce) — dlatego remake dostaje
datę oryginału, a resztę poprawia się ręcznie po stronie Decka.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from pylinks.constants import IGDB_TGDB_MATCH_MIN
from pylinks.core.matching import name_similarity

# Identyfikatory platform IGDB — pobrane z endpointu `platforms`, nie z pamięci.
IGDB_PLATFORMS: dict = {
    "PC": 6, "PS1": 7, "PS2": 8, "PS3": 9, "PS4": 48, "PSP": 38, "PSVITA": 46,
    "N64": 4, "SNES": 19, "SNESMSU1": 19, "NES": 18, "GB": 33, "GBC": 22, "GBA": 24,
    "NDS": 20, "3DS": 37, "GCN": 21, "WII": 5, "WIIU": 41, "NSW": 130,
    "DC": 23, "NAOMI": 52, "NAOMI2": 52, "SATURN": 32, "MD": 29, "SEGACD": 78,
    "SMS": 64, "GG": 35, "XBOX": 11, "X360": 12, "3DO": 50, "PCENGINE": 86,
    "NEOGEO": 80, "MSX": 27, "MSX2": 53, "ARCADE": 52, "MAME": 52,
    "JAGUAR": 62, "AMIGA": 16, "ATARI2600": 59, "PC98": 149,
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS game_series (
    key           TEXT PRIMARY KEY,   -- klucz gry PyLinksWeb (api._game_key)
    name          TEXT NOT NULL,      -- nazwa gry w bibliotece
    igdb_id       INTEGER,            -- NULL = szukano, nie znaleziono
    igdb_name     TEXT,
    franchise_id  INTEGER,
    franchise     TEXT,
    collection_id INTEGER,            -- NAJWĘŻSZA podseria, w której jest gra
    collection    TEXT,
    release_ts    INTEGER,            -- data wydania tej gry
    order_ts      INTEGER,            -- data do układania: remake = oryginał
    similarity    REAL,
    ts            REAL NOT NULL
);
"""

def _db(cache_dir) -> sqlite3.Connection:
    p = Path(cache_dir)
    p.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p / "pylinks.db"), timeout=10)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(_SCHEMA)
    return con


def known_keys(cache_dir) -> set:
    """Klucze już sprawdzone (także te bez trafienia) — żeby nie pytać ponownie."""
    try:
        with _db(cache_dir) as con:
            return {r[0] for r in con.execute("SELECT key FROM game_series")}
    except Exception:
        return set()


_REWIZJA = None


def _bez_rewizji(name: str) -> str:
    """„Tekken 2 Ver.B" → „Tekken 2"; „Street Fighter II (Rev A)" → „Street Fighter II"."""
    import re
    global _REWIZJA
    if _REWIZJA is None:
        _REWIZJA = re.compile(r"[ ]*([(][^)]*[)]|[-]?[ ]*(ver|version|rev|revision)[.]?[ ]*[a-z0-9.]+)[ ]*$",
                              re.IGNORECASE)
    wynik = (name or "").strip()
    for _ in range(3):                      # zdejmij kilka dopisków z rzędu
        nowy = _REWIZJA.sub("", wynik).strip()
        if nowy == wynik:
            break
        wynik = nowy
    return wynik


# Próg podobieństwa nazwy DLA SERII. Źródła okładek biorą 0,5 celowo luźno,
# ale błędna przynależność do serii jest gorsza niż jej brak: przy 0,5 „Sim City"
# trafiało w „Russian City Sim" (0,57), a „R-Type III" w „R-Type Dimensions III".
# 0,80 zostawia warianty zapisu („MTV's Beavis and Butt-Head" 0,92, nazwy No-Intro
# „Legend of Zelda, The - …" 0,98) i odrzuca inne gry.
SERIES_MATCH_MIN = 0.80

# typy wpisów IGDB (endpoint game_types): 5 = Mod, 12 = Fork — fanowskie przeróbki
_MODY = {5, 12}
_GLOWNA = 0


def _sim_row(title: str, alt: str, row: dict) -> float:
    """Podobieństwo do wpisu — z nazwą główną ORAZ nazwami alternatywnymi.

    IGDB trzyma grę pod jedną nazwą, a resztę jako alternatywne: „Seiken Densetsu 3"
    to w IGDB „Trials of Mana", a „Mother 2" to „EarthBound". Bez nazw
    alternatywnych takie gry nie miały żadnego trafienia. `alt` to fraza, którą
    faktycznie szukano (np. bez dopisku rewizji: „Tekken 2 Ver.B" → „Tekken 2").
    """
    nazwy = [row.get("name", "")] + [a.get("name", "") for a in (row.get("alternative_names") or [])
                                     if isinstance(a, dict)]
    frazy = [t for t in (title, alt) if t]
    return max((name_similarity(f, n) for f in frazy for n in nazwy if n), default=0.0)


def _pick_game(title: str, platform: str, rows: list, alt: str = ""):
    """Najlepiej pasująca gra — w kolejności ważności:

    1. podobieństwo nazwy ≥ SERIES_MATCH_MIN,
    2. zgodna platforma,
    3. NIE mod ani fork — „Chrono Trigger+" (hack z 2022) ma identyczne
       podobieństwo 1,00 co oryginał z 1995 i wygrywał, choć to inna gra,
    4. przy równym podobieństwie: gra główna, potem NAJWCZEŚNIEJSZE wydanie.
    """
    named = [(_sim_row(title, alt, r), r) for r in rows]
    named = [(s, r) for s, r in named if s >= SERIES_MATCH_MIN]
    if not named:
        return None, 0.0
    pid = IGDB_PLATFORMS.get((platform or "").upper())
    if pid is not None:
        zgodne = [(s, r) for s, r in named if pid in (r.get("platforms") or [])]
        if zgodne:
            named = zgodne
    bez_modow = [(s, r) for s, r in named if r.get("game_type") not in _MODY]
    if bez_modow:
        named = bez_modow
    najlepsze = max(s for s, _ in named)
    czolowka = [(s, r) for s, r in named if s >= najlepsze - 0.02]
    s, r = min(czolowka, key=lambda x: (
        -x[0],                                         # wyższe podobieństwo
        0 if x[1].get("game_type") == _GLOWNA else 1,  # gra główna przed wydaniami
        x[1].get("first_release_date") or 10**12,      # wcześniejsze wydanie
    ))
    return r, s


def _warianty_nazwy(name: str) -> list:
    """Kolejne zapytania do wyszukiwarki, od najbardziej dosłownego.

    Wyszukiwarka IGDB nie rozumie zapisu No-Intro: „Legend of Zelda, The - A Link
    to the Past" nie zwraca nic, choć sama nazwa pasuje w 98 %. Porównanie nazw
    zawsze idzie z ORYGINAŁEM — warianty służą tylko do znalezienia kandydatów.
    """
    import re
    n = (name or "").strip()
    out = [n]
    m = re.match(r"^(.*?), (The|A|An)( - |: |$)(.*)$", n)
    if m:
        head, art, sep, tail = m.groups()
        out.append(f"{art} {head}" + (f": {tail}" if tail else ""))
    for x in list(out):
        if " - " in x:
            out.append(x.replace(" - ", ": "))
    # „Battletoads-Double Dragon": wyszukiwarka nie rozdziela słów spiętych łącznikiem
    if re.search(r"[A-Za-z]-[A-Za-z]", n):
        out.append(re.sub(r"(?<=[A-Za-z])-(?=[A-Za-z])", " ", n))
    krotsza = _bez_rewizji(n)
    if krotsza and krotsza not in out:
        out.append(krotsza)
    pary = [(x, True) for x in out]
    # Sam tytuł przed podtytułem — „Mother 2 - Giygas Strikes Back" zwraca pustkę,
    # a „Mother 2" znajduje EarthBounda. Tej frazy NIE używamy do porównania nazw,
    # tylko do szukania: inaczej „Castlevania - Dracula X" pasowałoby do samej
    # „Castlevanii" w 100 %.
    if " - " in n:
        head = n.split(" - ", 1)[0].strip()
        if head and len(head) >= 3:
            pary.append((head, False))
    uniq, widziane = [], set()
    for x, porownuj in pary:
        if x and x not in widziane:
            widziane.add(x)
            uniq.append((x, porownuj))
    return uniq


class _Limiter:
    """Wspólny ogranicznik tempa dla wszystkich wątków (IGDB: 4 zapytania/s).

    Każde wywołanie rezerwuje kolejną wolną chwilę i czeka na nią — dzięki temu
    kilka wątków razem nigdy nie przekroczy limitu, a pojedynczy nie stoi dłużej
    niż musi. Wcześniej jedno zapytanie + stała przerwa szły szeregowo i 563 gry
    trwały 6–8 minut.
    """

    def __init__(self, per_second: float = 3.8) -> None:
        import threading
        self._gap = 1.0 / per_second
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next)
            self._next = slot + self._gap
        delay = slot - time.monotonic()
        if delay > 0:
            time.sleep(delay)


def detect(igdb_query, games: list, cache_dir, force: bool = False,
           progress=None, cancel=None, batch: int = 40, workers: int = 4) -> dict:
    """Rozpoznaje serie dla gier. `igdb_query(endpoint, apicalypse) -> list`.

    Pracuje PORCJAMI: dopasowanie porcji → dociągnięcie oryginałów i nazw →
    ZAPIS do bazy. Dawniej wszystko zapisywało się dopiero na końcu, więc przez
    kilka minut nic nie było widać, a przerwanie traciło całą pracę.
    """
    from concurrent.futures import ThreadPoolExecutor

    limiter = _Limiter()

    def q(endpoint, body):
        limiter.wait()
        try:
            return igdb_query(endpoint, body) or []
        except Exception:
            return []

    done_before = set() if force else known_keys(cache_dir)
    todo = [g for g in games if g.get("key") and g["key"] not in done_before]
    razem = {"checked": 0, "in_series": 0, "no_match": 0,
             "skipped_known": len(games) - len(todo)}
    zrobione = 0

    def dopasuj(g):
        # Pula 50, bo popularne tytuły mają dziesiątki wydań — oryginalny
        # „Final Fantasy IV" (podobieństwo 1,00) nie mieścił się w 15 wynikach.
        best, sim = None, 0.0
        for fraza, porownuj in _warianty_nazwy(g.get("name", "")):
            esc = fraza.replace('"', " ")
            rows = q("games",
                     f'search "{esc}"; fields id,name,platforms,collections,franchises,'
                     f'franchise,first_release_date,version_parent,parent_game,game_type,'
                     f'alternative_names.name; limit 50;')
            best, sim = _pick_game(g.get("name", ""), g.get("platform", ""), rows,
                                   alt=fraza if porownuj else "")
            if best is not None:
                break
        return g, best, sim

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for off in range(0, len(todo), max(1, batch)):
            if cancel and cancel():
                break
            porcja = todo[off:off + batch]
            wyniki = []
            for g, best, sim in pool.map(dopasuj, porcja):
                wyniki.append((g, best, sim))
                zrobione += 1
                if progress:
                    progress(zrobione, len(todo), g.get("name", ""))
            _zapisz_porcje(q, wyniki, cache_dir, razem)
            if cancel and cancel():
                break
    return razem


def _zapisz_porcje(q, wyniki: list, cache_dir, razem: dict) -> None:
    """Dociąga oryginały, remake'i i nazwy dla porcji, po czym ją zapisuje."""
    parents = {int(b["version_parent"]) for _, b, _ in wyniki
               if b and b.get("version_parent")}
    parent_info = {}
    for part in _chunks(sorted(parents), 100):
        rows = q("games", f'fields id,first_release_date,collections,franchises,franchise;'
                          f' where id = ({",".join(map(str, part))}); limit 100;')
        parent_info.update({r["id"]: r for r in rows})

    # REMAKE'I, REMASTERY, EDYCJE ROZSZERZONE, PORTY. IGDB nie opisuje ich przez
    # `version_parent` (to pole jest dla edycji GOTY/Director's Cut), tylko listami
    # po stronie ORYGINAŁU — trzeba je odszukać odwrotnie. Bez tego „Yakuza Kiwami"
    # stałoby w 2016, Vesperia DE w 2019, a port PC Symphonii w 2013.
    ids = sorted({int(b["id"]) for _, b, _ in wyniki if b})
    oryginal_ts = {}
    for part in _chunks(ids, 50):
        lista = ",".join(map(str, part))
        rows = q("games", f'fields id,first_release_date,remakes,remasters,expanded_games,ports; '
                          f'where remakes = ({lista}) | remasters = ({lista}) '
                          f'| expanded_games = ({lista}) | ports = ({lista}); limit 100;')
        for r in rows:
            for kopia in [x for k in ("remakes", "remasters", "expanded_games", "ports")
                          for x in (r.get(k) or [])]:
                ts = r.get("first_release_date")
                if ts and (kopia not in oryginal_ts or ts < oryginal_ts[kopia]):
                    oryginal_ts[kopia] = ts

    coll_ids, fr_ids = set(), set()
    for _, best, _ in wyniki:
        if not best:
            continue
        src = parent_info.get(best.get("version_parent")) or {}
        coll_ids.update(best.get("collections") or src.get("collections") or [])
        fr_ids.update(_franchises(best) or _franchises(src))
    colls = _names(q, "collections", coll_ids)
    frans = _names(q, "franchises", fr_ids)

    now = time.time()
    with _db(cache_dir) as con:
        for g, best, sim in wyniki:
            razem["checked"] += 1
            if not best:
                razem["no_match"] += 1
                con.execute(
                    "INSERT OR REPLACE INTO game_series(key,name,similarity,ts) "
                    "VALUES(?,?,?,?)", (g["key"], g.get("name", ""), sim, now))
                continue
            par = parent_info.get(best.get("version_parent")) or {}
            c_ids = best.get("collections") or par.get("collections") or []
            f_ids = _franchises(best) or _franchises(par)
            c_id = min(c_ids, key=lambda c: colls.get(c, ("", 10**9))[1]) if c_ids else None
            f_id = f_ids[0] if f_ids else None
            rel = best.get("first_release_date")
            order = (oryginal_ts.get(best["id"])          # remake/port → data oryginału
                     or par.get("first_release_date")     # edycja → data wydania bazowego
                     or rel)
            if c_id or f_id:
                razem["in_series"] += 1
            con.execute(
                "INSERT OR REPLACE INTO game_series(key,name,igdb_id,igdb_name,"
                "franchise_id,franchise,collection_id,collection,release_ts,order_ts,"
                "similarity,ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (g["key"], g.get("name", ""), best["id"], best.get("name", ""),
                 f_id, frans.get(f_id, ("", 0))[0] if f_id else None,
                 c_id, colls.get(c_id, ("", 0))[0] if c_id else None,
                 rel, order, sim, now))


def _franchises(r: dict) -> list:
    out = list(r.get("franchises") or [])
    if r.get("franchise") and r["franchise"] not in out:
        out.insert(0, r["franchise"])
    return out


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _names(igdb_query, endpoint: str, ids) -> dict:
    """{id: (nazwa, liczba_gier)}"""
    out = {}
    for part in _chunks(sorted(ids), 100):
        try:
            rows = igdb_query(endpoint, f'fields id,name,games; where id = '
                                        f'({",".join(map(str, part))}); limit 100;')
            for r in rows or ():
                out[r["id"]] = (r.get("name", ""), len(r.get("games") or []))
        except Exception:
            pass
    return out


def summary(cache_dir) -> dict:
    """Serie z liczbą gier — do podglądu wyniku."""
    try:
        with _db(cache_dir) as con:
            total = con.execute("SELECT COUNT(*) FROM game_series").fetchone()[0]
            miss = con.execute("SELECT COUNT(*) FROM game_series "
                               "WHERE igdb_id IS NULL").fetchone()[0]
            rows = con.execute(
                "SELECT COALESCE(franchise, collection) AS s, COUNT(*) FROM game_series "
                "WHERE s IS NOT NULL GROUP BY s HAVING COUNT(*) > 1 "
                "ORDER BY COUNT(*) DESC LIMIT 40").fetchall()
    except Exception:
        return {"total": 0, "no_match": 0, "series": []}
    return {"total": total, "no_match": miss,
            "series": [{"name": n, "games": c} for n, c in rows]}
