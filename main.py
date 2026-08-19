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

_WEB = Path(__file__).resolve().parent / "web" / "index.html"


def main() -> int:
    api = Api()
    window = webview.create_window(
        title=f"PyLinks {__version__}",
        url=str(_WEB),
        js_api=api,
        width=1280, height=800,
        min_size=(900, 560),
        background_color="#1e1e2e",
    )
    # debug=True → prawy-klik „Inspect" (DevTools) do podglądu HTML/CSS
    webview.start(debug=("--debug" in sys.argv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
