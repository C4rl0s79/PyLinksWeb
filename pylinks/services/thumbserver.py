"""services.thumbserver — miniatury podawane po HTTP zamiast przez most.

Dlaczego nie data-URI: most pywebview jest SYNCHRONICZNY, więc każdy obraz szedł
przez niego w base64 (+33 % narzutu), blokując okno, i to od nowa przy KAŻDYM
przerysowaniu siatki. Podane po HTTP z pętli lokalnej obrazy ładuje sama
przeglądarka — równolegle, asynchronicznie i z własnym cache, więc powrót do
obejrzanego już widoku nie kosztuje ani jednego bajtu.

Serwer słucha wyłącznie na 127.0.0.1, na porcie przydzielonym przez system,
a ścieżki niosą losowy token na czas życia procesu: inne programy na tej maszynie
nie zgadną adresu, a i tak wychodzą stąd wyłącznie miniatury okładek.
"""

from __future__ import annotations

import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

_STATE: dict = {"srv": None, "base": "", "cache": None, "token": ""}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):          # cisza — mamy własny dziennik
        pass

    def _deny(self, code: int = 404):
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _emumovies(self, token: str):
        """Plik z FTP EmuMovies: pobierany raz, potem prosto z cache.

        Okno nie otworzy adresu ftp://, więc grafiki tego źródła idą przez nas.
        Ścieżka zdalna jedzie w adresie zakodowana base64 — razem z tokenem
        serwera nie da się tu podstawić czegokolwiek z zewnątrz."""
        import base64
        try:
            remote = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        except Exception:
            return self._deny()
        try:
            from pylinks.api import _load_config
            from pylinks.services import emumovies as _em
            path = _em.fetch(_load_config(), _STATE["cache"], remote)
        except Exception:
            path = None
        if not path or not path.is_file():
            return self._deny()
        try:
            data = path.read_bytes()
        except OSError:
            return self._deny(500)
        typy = {".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
                ".bmp": "image/bmp"}
        self.send_response(200)
        self.send_header("Content-Type", typy.get(path.suffix.lower(), "image/jpeg"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parts = [p for p in urlparse(self.path).path.split("/") if p]
        # /t/<token>/<tryb>/<klucz>
        if len(parts) != 4 or parts[0] != "t" or parts[1] != _STATE["token"]:
            return self._deny(403)
        mode, key = parts[2], unquote(parts[3])
        if mode == "em":
            return self._emumovies(key)
        if mode not in ("icon", "steam"):
            return self._deny()
        from pylinks.services import art
        cache = _STATE["cache"]
        path = art.thumb_path(cache, key, mode)     # tworzy, jeśli trzeba
        if not path or not path.is_file():
            return self._deny()
        try:
            data = path.read_bytes()
        except OSError:
            return self._deny(500)
        self.send_response(200)
        self.send_header("Content-Type", "image/webp")
        self.send_header("Content-Length", str(len(data)))
        # Miniatura zmienia się tylko razem z okładką, a wtedy zmienia się też
        # znacznik w adresie — więc może leżeć w cache przeglądarki dowolnie długo.
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.end_headers()
        self.wfile.write(data)


def start(cache_dir) -> str:
    """Uruchamia serwer (raz) i zwraca bazowy adres, np. http://127.0.0.1:51234/t/<token>."""
    if _STATE["base"]:
        return _STATE["base"]
    _STATE["cache"] = Path(cache_dir)
    _STATE["token"] = secrets.token_urlsafe(16)
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    except OSError:
        return ""                       # brak serwera = zostaje ścieżka data-URI
    srv.daemon_threads = True
    _STATE["srv"] = srv
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _STATE["base"] = f"http://127.0.0.1:{srv.server_address[1]}/t/{_STATE['token']}"
    return _STATE["base"]


def base_url() -> str:
    return _STATE["base"]
