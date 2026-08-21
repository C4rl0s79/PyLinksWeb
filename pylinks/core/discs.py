"""core.discs — wybór właściwej ścieżki dysku/playlisty pod konkretny emulator.

Port poprawki z wersji tkinter (v8.4). Problem: skan scala gry wielopłytowe do
`.m3u`, ale CZĘŚĆ emulatorów playlist NIE czyta — PCSX2 kończy się wtedy błędem
„Failed to open CDVD …m3u: Unable to identify the ISO image type". Decyzja musi
więc zależeć od emulatora i działać DWUKIERUNKOWO:

* emulator z obsługą .m3u + ścieżka do pojedynczego dysku → podstaw .m3u,
* emulator bez obsługi .m3u + ścieżka do .m3u          → podstaw pierwszy DYSK.
"""

from __future__ import annotations

import re
from pathlib import Path

# Emulatory czytające playlisty .m3u (przełączanie płyt w locie)
_M3U_YES = ("duckstation", "swanstation", "retroarch", "mednafen", "beetle",
            "mgba", "ppsspp", "pcsx_rearmed", "picodrive", "genesis_plus")
# Emulatory, które playlist NIE obsługują — muszą dostać konkretny plik dysku
_M3U_NO = ("pcsx2", "dolphin", "rpcs3", "xemu", "cemu", "vita3k", "xenia",
           "flycast", "redream", "ares", "melonds", "citra", "ryujinx", "yuzu")

_DISC_NUM_RE = re.compile(r"[\(\[]\s*(?:disc|disk|cd|side)\s*([0-9]+)\s*[\)\]]",
                          re.IGNORECASE)


def emulator_supports_m3u(exe: str) -> bool:
    """Czy emulator czyta .m3u. NIEZNANY → False (bezpiecznie: pojedynczy dysk)."""
    stem = Path(str(exe or "")).stem.lower()
    if not stem:
        return False
    if any(k in stem for k in _M3U_NO):
        return False
    return any(k in stem for k in _M3U_YES)


def _disc_number(name: str) -> int:
    m = _DISC_NUM_RE.search(str(name or ""))
    return int(m.group(1)) if m else 0


def m3u_entries(m3u: Path) -> list:
    """Ścieżki z playlisty (bez komentarzy), rozwinięte względem jej katalogu."""
    out = []
    try:
        for line in Path(m3u).read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            p = Path(line)
            if not p.is_absolute():
                p = Path(m3u).parent / line
            out.append(p)
    except Exception:
        pass
    return out


def first_existing_disc(m3u: Path):
    """Pierwszy ISTNIEJĄCY dysk z playlisty (preferowany najniższy numer).

    Weryfikacja istnienia jest istotna: gdy Disc 1 został skasowany/przeniesiony,
    skrót nie może celować w martwą ścieżkę — bierzemy najniższy dysk, który JEST.
    """
    items = [p for p in m3u_entries(m3u) if p.is_file()]
    if not items:
        return None
    items.sort(key=lambda p: (_disc_number(p.name) or 9999, p.name.lower()))
    return items[0]


def find_m3u_for(disc: Path):
    """Siostrzana playlista obejmująca podany dysk (czytamy ZAWARTOŚĆ, nie nazwy)."""
    try:
        d = Path(disc)
        target = d.resolve()
        for m in sorted(d.parent.glob("*.m3u")):
            for e in m3u_entries(m):
                try:
                    if e.resolve() == target:
                        return m
                except Exception:
                    continue
    except Exception:
        pass
    return None


def disc_path_for_emulator(rom: str, emu_exe: str) -> str:
    """Zwraca ścieżkę, którą należy podać TEMU emulatorowi. Dwukierunkowo."""
    if not rom:
        return rom
    p = Path(rom)
    is_m3u = p.suffix.lower() == ".m3u"
    supports = emulator_supports_m3u(emu_exe)
    if is_m3u and not supports:
        disc = first_existing_disc(p)          # playlista → konkretny dysk
        return str(disc) if disc else rom
    if not is_m3u and supports:
        m3u = find_m3u_for(p)                  # dysk → playlista (jeśli jest)
        return str(m3u) if m3u else rom
    return rom
