"""PyLinks (web) — nowa wersja na pywebview. Punkt wejścia.

Silnik: pakiet `pylinks/` (skopiowany z wersji tkinter — czysta logika).
Frontend: `web/` (HTML/CSS/JS). Most: `pylinks.api.Api` (js_api).

Uruchomienie:
    pip install -r requirements.txt
    python main.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# WAŻNE: ustaw PRZED importem webview. WebView2 potrafi trzymać stary CSS/JS
# w cache mimo restartu (nawigacja do index.html jest świeża, ale podzasoby
# style.css/app.js są serwowane z cache) — wyłączamy cache HTTP na twardo.
os.environ.setdefault(
    "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
    "--disable-http-cache --disk-cache-size=1",
)

import webview

from pylinks.api import Api
from pylinks import __version__

def _resource_root() -> Path:
    """Katalog zasobów (web/). Frozen (PyInstaller) = rozpakowany bundle
    (sys._MEIPASS); zwykłe uruchomienie = katalog tego pliku."""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else Path(__file__).resolve().parent


_WEB_DIR = _resource_root() / "web"
_WEB = _WEB_DIR / "index.html"


def _build_html() -> str:
    """Wczytuje index.html i WKLEJA inline style.css + app.js.

    Dzięki temu nie ma żadnych podzasobów (css/js) do zcache'owania przez
    WebView2 — każdy start ładuje świeży kod prosto z plików na dysku.
    """
    html = _WEB.read_text(encoding="utf-8")
    css = (_WEB_DIR / "style.css").read_text(encoding="utf-8")
    js = (_WEB_DIR / "app.js").read_text(encoding="utf-8")
    html = html.replace("<!--PYLINKS_CSS-->", f"<style>\n{css}\n</style>")
    html = html.replace("<!--PYLINKS_JS-->", f"<script>\n{js}\n</script>")
    return html


def _single_instance_guard() -> "object | None":
    """Zapobiega uruchomieniu drugiej instancji (koniec bałaganu ze starymi
    oknami). Zwraca gniazdo-blokadę (trzymać referencję) albo None gdy już działa."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 49517))
        s.listen(1)
        return s
    except OSError:
        return None


def _startup_log(msg: str, to_file: bool = True) -> None:
    """Diagnostyka startu → stderr, a przy błędach też plik obok exe. Kluczowe
    dla wersji frozen (console=False): bez tego błędy startu są niewidoczne."""
    if to_file:
        try:
            from pylinks.app_paths import SCRIPT_DIR
            with (SCRIPT_DIR / "startup.log").open("a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass
    try:
        print(msg, file=sys.stderr)
    except Exception:
        pass


def main() -> int:
    lock = _single_instance_guard()
    if lock is None:
        print("PyLinks już działa — zamknij poprzednie okno przed nowym startem.")
        return 0
    _dbg = ("--debug" in sys.argv) or bool(os.environ.get("PLW_DEBUG"))
    if _dbg:
        import logging
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr,
                            format="%(name)s %(levelname)s: %(message)s")
    try:
        if _dbg:
            _startup_log(f"start: frozen={getattr(sys,'frozen',False)} "
                         f"webdir={_WEB_DIR} index_exists={_WEB.exists()}",
                         to_file=False)
        api = Api()
        webview.create_window(
            title=f"PyLinks {__version__}",
            html=_build_html(),      # inline (css+js wklejone) = zero cache podzasobów
            js_api=api,
            width=1280, height=800,
            min_size=(900, 560),
            background_color="#1e1e2e",
        )
        # debug=True → prawy-klik „Inspect" (DevTools) do podglądu HTML/CSS
        webview.start(debug=_dbg)
        return 0
    except Exception:
        import traceback
        _startup_log("BŁĄD STARTU:\n" + traceback.format_exc())
        raise


if __name__ == "__main__":
    raise SystemExit(main())
