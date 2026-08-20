"""core.secure — szyfrowanie sekretów przez Windows DPAPI (win32crypt).

Klucze API trzymane w config.json jako "dpapi:<base64>" — odszyfrowywane tylko
w pamięci przy użyciu (powiązane z kontem Windows użytkownika / TPM). Bez
Windowsa / pywin32 działa przezroczyście (zwraca tekst jak jest).
"""

from __future__ import annotations

import base64

_PREFIX = "dpapi:"

try:
    import win32crypt  # type: ignore
    _OK = True
except Exception:
    _OK = False


def available() -> bool:
    return _OK


def protect(text: str) -> str:
    """Szyfruje tekst → 'dpapi:<base64>'. Puste/nie-Windows → zwraca bez zmian."""
    if not text or not _OK:
        return text or ""
    if text.startswith(_PREFIX):
        return text                      # już zaszyfrowane
    try:
        blob = win32crypt.CryptProtectData(
            text.encode("utf-8"), "PyLinks", None, None, None, 0)
        return _PREFIX + base64.b64encode(blob).decode("ascii")
    except Exception:
        return text


def unprotect(value: str) -> str:
    """Odszyfrowuje 'dpapi:<base64>' → tekst. Zwykły tekst zwraca bez zmian."""
    if not value or not value.startswith(_PREFIX):
        return value or ""
    if not _OK:
        return ""                        # zaszyfrowane, ale brak DPAPI → nie ujawniaj
    try:
        blob = base64.b64decode(value[len(_PREFIX):])
        _desc, data = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
        return data.decode("utf-8")
    except Exception:
        return ""


def is_protected(value: str) -> bool:
    return bool(value) and value.startswith(_PREFIX)
