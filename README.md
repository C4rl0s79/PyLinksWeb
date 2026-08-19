# PyLinks (web)

Nowa wersja **PyLinks** zbudowana na **pywebview** — frontend HTML/CSS/JS,
backend w Pythonie. Powstała, bo wersja tkinter (`D:\py\PyLinks`) miała stałe
problemy ze scrollowaniem i trudną edycją wyglądu. To **osobna aplikacja** —
stara wersja żyje dalej, niezależnie.

PyLinks skanuje biblioteki gier (Steam / GOG / Epic / Extra) oraz katalogi
ROM/emulatorów, pobiera ikony i grafiki (SteamGridDB / IGDB / TheGamesDB /
Libretro) i generuje skróty `.lnk`/`.url` pogrupowane per platforma, z własnymi
ikonami.

## Architektura

```
PyLinksWeb/
├─ main.py            # punkt wejścia (webview.create_window + Api)
├─ pylinks/           # SILNIK — czysta logika (współdzielony z wersją tkinter)
│  ├─ api.py          #   most Python↔JS (js_api dla pywebview)
│  ├─ app_paths.py    #   ścieżki: Cache/, LINKS/, config.json obok aplikacji
│  ├─ constants.py    #   paleta C (Catppuccin Mocha) + domyślne
│  ├─ config/         #   defaults + migrate
│  ├─ core/           #   compression, matching, naming, rom_args
│  └─ roms/           #   presets systemów ROM
└─ web/               # FRONTEND
   ├─ index.html      #   szkielet: topbar / sidebar / siatka / status
   ├─ style.css       #   motyw Catppuccin, siatka CSS, natywny scroll
   └─ app.js          #   wywołania Api, render siatki, obsługa przycisków
```

Frontend woła Pythona przez `window.pywebview.api.<metoda>(...)`
(zwraca Promise). Metody wystawia klasa `Api` w `pylinks/api.py`.

## Uruchomienie

```bash
pip install -r requirements.txt
python main.py            # okno aplikacji
python main.py --debug    # + DevTools (prawy-klik → Inspect)
```

Wymaga **WebView2** (Windows) — instalowany z Edge / dostępny w systemie.

## Status

**Faza 0** — szkielet: działający stos pywebview + most do silnika,
podgląd istniejących skrótów z `LINKS/<platforma>/`.

Kolejne fazy:
- **Faza 1** — skanowanie źródeł, siatka gier, wybór ikon/grafik.
- **Faza 2** — dialogi (ustawienia, kadrowanie, kolekcje, grzbiety).
- **Faza 3** — pakowanie (PyInstaller + WebView2).
