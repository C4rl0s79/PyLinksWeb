"""core.log — prosty dziennik zdarzeń obok aplikacji.

Po co: gdy eksport arcade kończył się komunikatem „zestaw niekompletny", nie było
GDZIE sprawdzić, czego zabrakło — program nie zapisywał niczego. Plik leży obok
`PyLinksWeb.exe`, żeby dało się go znaleźć bez szukania po katalogach systemowych.

Świadomie bez `logging`: potrzebny jest jeden dopisywany plik z limitem rozmiaru,
a nie konfiguracja handlerów w aplikacji, która i tak ma jeden wątek roboczy.
"""

from __future__ import annotations

import threading
from datetime import datetime

from pylinks.app_paths import SCRIPT_DIR

LOG_PATH = SCRIPT_DIR / "PyLinksWeb.log"
_MAX_BYTES = 2 * 1024 * 1024        # przy przekroczeniu: .log → .log.old
_LOCK = threading.Lock()


def _rotate() -> None:
    try:
        if LOG_PATH.is_file() and LOG_PATH.stat().st_size > _MAX_BYTES:
            old = LOG_PATH.with_suffix(".log.old")
            try:
                old.unlink()
            except OSError:
                pass
            LOG_PATH.replace(old)
    except OSError:
        pass


def write(tag: str, msg: str) -> None:
    """Dopisuje wiersz. Nigdy nie rzuca — dziennik nie może wywrócić operacji."""
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} [{tag}] {msg}\n"
    try:
        with _LOCK:
            _rotate()
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass


def path() -> str:
    return str(LOG_PATH)


def tail(lines: int = 200) -> str:
    """Ostatnie wiersze — do podejrzenia wprost w interfejsie."""
    try:
        with open(LOG_PATH, encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-max(1, lines):])
    except OSError:
        return ""
