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

## Budowa przenośnego .exe

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

albo ręcznie:

```bash
pip install pyinstaller pywebview Pillow pywin32
pyinstaller --noconfirm --clean pylinks_web.spec
```

Wynik: **`dist\PyLinksWeb.exe`** (~30 MB, onefile). Aplikacja jest **portable** —
`config.json`, `Cache/` i `LINKS/` powstają OBOK exe (nie w AppData). Frontend
`web/` jest wpakowany do bundla (odczyt z `sys._MEIPASS`), a DLL-e WebView2 +
pythonnet dokłada spec przez `collect_all("webview")`. Docelowy komputer musi
mieć zainstalowany **WebView2 Runtime** (standard na Windows 11).

Diagnostyka startu: uruchom `PyLinksWeb.exe --debug` (albo ustaw `PLW_DEBUG=1`) —
logi pywebview lecą na stderr; błędy startu trafiają do `startup.log` obok exe.

## Status

**Fazy 0–3 ukończone.** Skan PC/ROM, okładki (SGDB/IGDB/TGDB/Libretro), wybór i
kadrowanie ikon, grzbiety platform (szerokość + wariant/rozmiar logo), eksport
skrótów `.lnk`/`.url` oraz do biblioteki Steam (shortcuts.vdf + grafiki +
kolekcje), kolekcje serii, szyfrowanie kluczy (TPM→DPAPI), pakowanie do
`PyLinksWeb.exe` (PyInstaller). 
