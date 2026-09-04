# PyLinks Web

Jedno miejsce, z którego robisz **ładne skróty do wszystkich swoich gier** —
z komputera (Steam / GOG / Epic / dowolny folder) i z emulatorów (31 systemów
ROM). PyLinks skanuje biblioteki, pobiera okładki i generuje skróty `.lnk`/`.url`
z podmienioną ikoną, pogrupowane per platforma — a jak chcesz, wrzuca je też do
biblioteki Steam. Interfejs to strona (HTML/CSS/JS) w natywnym oknie (pywebview).

Następca jednoplikowej wersji na tkinter — powstał, bo tamta miała stałe problemy
ze scrollowaniem i trudno było w niej cokolwiek zmienić w wyglądzie.

## Po co, skoro gry i tak są w launcherach

- **Jeden pulpit na wszystko** — Steam, GOG, Epic, gogi z folderu i ROM-y obok
  siebie, z jednakową, ładną ikoną (surowa okładka, nie 32-pikselowy shell-icon).
- **Ikony, jakich launcher nie daje** — okładki 2:3 albo kwadrat, z opcjonalnym
  **grzbietem platformy** (jak grzbiet pudełka), żeby odróżnić tę samą grę na
  różnych systemach.
- **ROM-y jak gry PC** — skrót uruchamia właściwy emulator z właściwymi
  argumentami; multi-disc dostaje playlistę `.m3u` tam, gdzie emulator ją czyta.
- **Steam jako pilot do wszystkiego** — całą kolekcję (z grafikami grid/hero/logo
  i kolekcjami) wysyłasz do biblioteki Steam, więc odpalasz wszystko także
  z kanapy przez Big Picture.

## Jak to działa

Trzy kroki, zawsze te same:

1. **Skanuj** — PyLinks znajduje gry (Steam/GOG/Epic/Extra) i ROM-y (foldery
   emulatorów), scala gry wielopłytowe, rozpoznaje sety MAME.
2. **Grafiki** — dla każdej gry pobiera okładki i ikony ze źródeł internetowych,
   a Ty wybierasz/kadrjesz tę właściwą (albo zostawiasz automat).
3. **Stwórz** — generuje skróty `.lnk`/`.url` w `LINKS\<platforma>\` z podmienioną
   ikoną `.ico`, i/lub wysyła wszystko do biblioteki Steam.

## Uruchomienie

Wersja przenośna: uruchom **`PyLinksWeb.exe`** (obok niego powstają `config.json`,
`Cache/`, `LINKS/`). Ze źródeł:

```bash
pip install -r requirements.txt
python main.py          # okno aplikacji
python main.py --debug  # + DevTools
```

Do działania potrzebny jest **Microsoft Edge WebView2 Runtime** (na Windows 11
i większości Windows 10 już jest).

## Pasek narzędzi

| Przycisk | Co robi |
| --- | --- |
| **🔍 Skanuj** | wykrywa gry PC i ROM-y na nowo |
| **⬇ Pobierz wszystko** | pobiera grafiki dla całej biblioteki w tle |
| **🖼 Tryb: Ikony / Okładki** | przełącza podgląd między kwadratową ikoną a pełną okładką |
| **📁 Kolekcje** | grupowanie gier w serie/kolekcje (stają się folderami i kolekcjami Steam) |
| **🎮 Grupy Steam** | zarządzanie kolekcjami w bibliotece Steam (patrz niżej) |
| **⚙ Ustawienia** | klucze API, ścieżki, systemy ROM, grzbiety, źródła grafik |

Prawy klik na grze: **Wybierz ikonę…**, **Kadruj**, **Napraw ikony**, ręczne
wskazanie pliku exe. Zaznaczanie do eksportu: Wszystkie / Żadne / Brakujące.

## Grafiki i ikony

Okładki i ikony pobierane są z wielu źródeł, każde konfigurowalne w Ustawieniach:
**SteamGridDB**, **Steam CDN**, **Libretro Thumbnails**, **IGDB**, **TheGamesDB**,
**ScreenScraper**. Okno wyboru pokazuje kandydatów (miniatury) i pozwala ręcznie
doszukać po tytule.

- **Kształt** — ikona kwadratowa albo okładka 2:3.
- **Kadr** (`fit` + `offset`) — decydujesz, która część szerokiej okładki zostaje;
  kadrowanie robi podgląd, a do `.ico` „piecze się" dopiero finał (Pillow).
- **Grzbiet platformy** — pionowy pasek z logo systemu na kwadratowej ikonie ROM-a.
  Szerokość (12–40 %) i wariant logo ustawiasz w **🎨 Grzbiet — szerokość i logo**.
  Logotypy pobierane są do `platform_logos/`.

## Eksport skrótów i do Steam

- **Skróty `.lnk`/`.url`** trafiają do `LINKS\<platforma>\` z podmienioną ikoną.
  ROM-y dostają skrót uruchamiający emulator z argumentami (składnia per emulator);
  gry wielopłytowe — `.m3u` dla emulatorów, które ją czytają (DuckStation,
  RetroArch, PPSSPP…), a dla pozostałych (PCSX2, Dolphin, RPCS3…) konkretny,
  istniejący dysk.
- **🛠 Stwórz wszystko** — skróty **plus** eksport do Steam za jednym razem.
- **Eksport do Steam** — dopisuje gry jako pozycje „Non-Steam" (`shortcuts.vdf`),
  wgrywa grafiki **grid/hero/logo** i tworzy **kolekcje** per platforma. Robi
  kopię zapasową i **wymaga zamkniętego Steama** (inaczej nadpisze pliki przy
  wyjściu). Trafia w istniejące wpisy (normalizacja ścieżki), więc nie dubluje gier.

## Kolekcje i grupy Steam

- **📁 Kolekcje** — układasz gry w serie („Yakuza", „Final Fantasy"); stają się
  podfolderami i kolekcjami w Steam.
- **🎮 Grupy Steam** — porządki w bibliotece Steam: pokazuje każdą grupę z liczbą
  pozycji i rozróżnia **żywe** wpisy, **martwe** (skrót „Non-Steam", którego już
  nie ma) i **zwykłe gry ze Steama** (własne kolekcje nie są liczone jako zepsute).
  **🧹 Wyczyść martwe** kasuje osierocone numery, można też usuwać całe grupy.
- **Osierocone skróty** — skan wykrywa skróty prowadzące donikąd (skasowana gra
  GOG/Epic) i usuwa je razem z odpowiadającym wpisem w Steamie.

## ROM-y i emulatory

Obsługa **31 systemów** (PS1/2/3, PSP, Nintendo NES→Wii, Sega, arcade, 3DO,
Amiga, MSX2, PC-98, MSU-1…). Foldery emulatorów i systemów wykrywane automatycznie
(`rom_autodetect`), z edytowalną tabelą systemów w Ustawieniach. Emulatory i wybór
rdzenia RetroArch rozpoznawane automatycznie.

## Biblioteka arcade (MAME)

Osobny widok z metadanymi prosto z `mame.exe` (nie z DAT-a): grupowanie po tytule,
filtry z catver/genre/mature, weryfikacja setów, okładki arcade, eksport wybranej
wersji. **↻ Odśwież metadane z MAME** i **🖼 Odśwież okładki arcade**.

## Klucze API i bezpieczeństwo

Klucze (SteamGridDB, Steam Web API, IGDB, TheGamesDB, ScreenScraper) wpisujesz
w Ustawieniach — każde źródło z przyciskiem **Testuj**. Klucze są szyfrowane:
sprzętowy **TPM** (Platform Crypto Provider, RSA-2048/OAEP) → **DPAPI** → jawny
fallback; aktualny poziom pokazuje `security_status` w UI.

## Gdzie trzyma dane

Wszystko powstaje **obok programu** (aplikacja przenośna):

- `config.json` — ustawienia, klucze, systemy ROM, wybory grafik per gra,
- `Cache/` — okładki (`covers/`), baza `pylinks.db` (kadry, sety MAME), miniatury,
- `LINKS/<platforma>/` — gotowe skróty z ikonami (to jest „produkt"),
- `platform_logos/` — logotypy platform do grzbietów.

## Wymagania

- **Windows** + **Microsoft Edge WebView2 Runtime**
- Python 3.11+ ze źródeł: `pywebview`, `Pillow`, `pywin32` (patrz `requirements.txt`)
- do pełni: zainstalowane emulatory (dla ROM-ów) i Steam (dla eksportu do biblioteki)

## Historia zmian

Patrz [CHANGELOG.md](CHANGELOG.md). Wersja bieżąca: `pylinks.__version__`.
