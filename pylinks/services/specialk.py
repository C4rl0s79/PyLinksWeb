"""services.specialk — skróty uruchamiane przez Special K (SKIF).

Special K wstrzykuje się do gry przez swój launcher. Skrót zrobiony przez SKIF
wygląda tak (sprawdzone na skrócie z pulpitu, SKIF 1.3.20):

    cel        C:\\Program Files\\Special K\\SKIF.exe
    argumenty  "D:\\SteamLibrary\\steamapps\\common\\CONTROL Resonant\\CONTROLResonant.exe"
               SKIF_SteamAppId=3669870
    katalog    D:\\SteamLibrary\\steamapps\\common\\CONTROL Resonant

Czyli: ścieżka do pliku wykonywalnego gry w cudzysłowie, a dla gier ze Steama
dodatkowo `SKIF_SteamAppId=<appid>` (dzięki temu Special K widzi grę jako
steamową: overlay, osiągnięcia, profil ustawień). Katalog roboczy to folder gry.

Tryb przełącza się dla CAŁEJ platformy PC (ustawienie `pc_launch_mode`), bo
to decyzja „jak uruchamiam gry na pececie", a nie cecha pojedynczej gry.
"""

from __future__ import annotations

import os
from pathlib import Path

MODE_NORMAL = "normal"
MODE_SPECIALK = "specialk"

# typowe miejsca instalacji SKIF-a (instalator kładzie go w Program Files)
_CANDIDATES = (
    r"C:\Program Files\Special K\SKIF.exe",
    r"C:\Program Files (x86)\Special K\SKIF.exe",
)


def find_skif(cfg: dict = None) -> str:
    """Ścieżka do SKIF.exe: z ustawień, z typowych lokalizacji, z rejestru."""
    cfg = cfg or {}
    reczna = (cfg.get("specialk_exe") or "").strip().strip('"')
    if reczna and Path(reczna).is_file():
        return reczna
    for c in _CANDIDATES:
        if Path(c).is_file():
            return c
    try:                                   # instalator zapisuje ścieżkę w rejestrze
        import winreg
        for root, klucz in ((winreg.HKEY_CURRENT_USER, r"Software\Kaldaien\Special K"),
                            (winreg.HKEY_LOCAL_MACHINE, r"Software\Kaldaien\Special K")):
            try:
                with winreg.OpenKey(root, klucz) as k:
                    p = Path(winreg.QueryValueEx(k, "Path")[0]) / "SKIF.exe"
                    if p.is_file():
                        return str(p)
            except OSError:
                continue
    except Exception:
        pass
    return ""


def mode(cfg: dict) -> str:
    return (MODE_SPECIALK if (cfg or {}).get("pc_launch_mode") == MODE_SPECIALK
            else MODE_NORMAL)


def skipped(cfg: dict) -> set:
    """Gry WYŁĄCZONE z Special K (klucze gier).

    Tryb dotyczy całej platformy PC, ale są gry, w których Special K przeszkadza
    — przede wszystkim sieciowe z anty-cheatem (wstrzyknięcie obcej biblioteki
    bywa uznawane za oszustwo i kończy się banem). Takie gry dostają zwykły
    skrót niezależnie od trybu, także przy KOLEJNYCH eksportach."""
    return {str(k) for k in ((cfg or {}).get("specialk_skip") or []) if k}


def is_skipped(cfg: dict, game: dict) -> bool:
    from pylinks.api import _game_key
    return _game_key(game) in skipped(cfg)


def _norm(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def best_exe(game_dir: str, name: str = "") -> str:
    """Najbardziej prawdopodobny plik gry w katalogu.

    Sam rozmiar nie wystarcza: w 3DMarku największy jest `3DMarkCPUProfile.exe`,
    a grę uruchamia `3DMark.exe`. Punktujemy więc najpierw zgodność z NAZWĄ gry,
    potem typowe nazwy silników (`…-Win64-Shipping.exe` w Unreal Engine),
    a dopiero na końcu rozmiar."""
    import re
    gd = Path(game_dir or "")
    if not gd.is_dir():
        return ""
    pomin = re.compile(
        r"(unins|setup|redist|vcredist|dxsetup|directx|dotnet|physx|oalinst|"
        r"systemsoftware|crash|report|config|update|patch|touchup|activation|"
        r"benchmark|server|editor|dedicated|helper|cleanup|eula|install)", re.I)
    # katalogi z instalatorami bibliotek — tam nigdy nie ma gry
    pomin_kat = re.compile(
        r"^(_?commonredist|redist|redistributables?|directx|physx|vcredist|"
        r"support|__installer|dotnet|_?installer)$", re.I)
    cel = _norm(name)
    # próg MUSI być „minus nieskończoność": punkty bywają ujemne (kara za
    # zagnieżdżenie), a przy progu 0 wypadały poprawne pliki z podkatalogów
    # (Persona 3 `P3R\Binaries\Win64\P3R.exe`, Castlevania `bin\CLOS2.exe`).
    najlepszy, najlepszy_wynik = "", float("-inf")
    try:
        for exe in gd.rglob("*.exe"):
            rel = exe.relative_to(gd)
            if len(rel.parts) > 5 or pomin.search(exe.name):
                continue
            if any(pomin_kat.match(p) for p in rel.parts[:-1]):
                continue
            try:
                rozmiar = exe.stat().st_size
            except OSError:
                continue
            stem = _norm(exe.stem)
            # plik w korzeniu katalogu gry jest pewniejszy niż schowany głęboko
            wynik = rozmiar / 1e12 - 3 * (len(rel.parts) - 1)
            if cel and stem == cel:
                wynik += 100
            elif cel and len(stem) >= 4 and (stem.startswith(cel) or cel.startswith(stem)):
                wynik += 60
            low = exe.stem.lower()
            if low.endswith(("-win64-shipping", "-win32-shipping", "-shipping")):
                wynik += 40
            if low in ("launcher", "start", "play"):
                wynik -= 30                        # to zwykle tylko okienko startowe
            if wynik > najlepszy_wynik:
                najlepszy, najlepszy_wynik = str(exe), wynik
    except OSError:
        return ""
    return najlepszy


def game_exe(game: dict) -> str:
    """Plik wykonywalny gry — to jego Special K uruchamia.

    Kolejno: ręczne wskazanie użytkownika (`exe_override`), zadanie „graj"
    z GOG-a, ścieżka ze skanu (Epic/Extra ją mają), a dla Steama — wybór
    z katalogu gry (`best_exe`). Zły wybór poprawia się ręcznym wskazaniem
    pliku dla tej gry."""
    from pylinks.services.export import _gog_playtask
    over = (game.get("exe_override") or "").strip()
    if over and Path(over).is_file():
        return over
    if game.get("source") == "gog":
        pt = _gog_playtask(game.get("game_dir", "") or "")
        if pt:
            return pt[0]
    exe = (game.get("launch_exe") or "").strip()
    if exe and Path(exe).is_file():
        return exe
    gd = (game.get("game_dir") or "").strip()
    return best_exe(gd, game.get("name", "")) if gd else ""


def launch_for(cfg: dict, game: dict) -> "dict | None":
    """Opis skrótu przez Special K albo None (gra wyłączona / brak SKIF-a lub exe)."""
    if is_skipped(cfg, game):
        return None                       # np. sieciowa z anty-cheatem
    skif = find_skif(cfg)
    if not skif:
        return None
    exe = game_exe(game)
    if not exe:
        return None
    czesci = [f'"{exe}"']
    wlasne = (game.get("launch_args") or "").strip()
    if wlasne:
        czesci.append(wlasne)
    appid = str(game.get("appid") or "").strip()
    if appid:
        # dzięki temu Special K rozpoznaje grę jako steamową (profil, overlay)
        czesci.append(f"SKIF_SteamAppId={appid}")
    return {"kind": "lnk", "target": skif, "args": " ".join(czesci),
            "work_dir": str(Path(exe).parent)}


def status(cfg: dict, games=None) -> dict:
    """Stan dla interfejsu: tryb, czy SKIF jest, ile gier da się przełączyć."""
    from pylinks.api import _game_key
    skif = find_skif(cfg)
    pomijane = skipped(cfg)
    out = {"mode": mode(cfg), "skif": skif, "ready": bool(skif),
           "skipped": len(pomijane), "skip_keys": sorted(pomijane)}
    if games is not None:
        objete = [g for g in games if _game_key(g) not in pomijane]
        out["games"] = len(games)
        out["excluded"] = sorted(g.get("name", "") for g in games
                                 if _game_key(g) in pomijane)
        out["without_exe"] = [g.get("name", "") for g in objete if not game_exe(g)]
    return out
