# Changelog

Wszystkie istotne zmiany w PyLinksWeb. Format oparty na
[Keep a Changelog](https://keepachangelog.com/pl/1.1.0/).

## [9.6.0] — 2026-08-21

### Zmienione
- **Stan per-gra przeniesiony z `config.json` do SQLite** (`Cache/pylinks.db`).
  Dane per-gra stanowiły **85 % configu** (626 gier → 66,9 KB) i rosły z biblioteką;
  każde kliknięcie ikony przepisywało CAŁY plik, a zapis szedł przez `write_text()`
  bez pliku tymczasowego — awaria w trakcie mogła obciąć config i zabrać klucze API
  oraz systemy ROM. Teraz: jeden UPSERT po indeksie zamiast przepisywania pliku.
  Migracja jednorazowa przeniosła **753 wybory, 63 kadry i 13 nadpisań**;
  `config.json` **66,9 KB → 8,5 KB (−87 %)` i przestaje rosnąć. Kopia zapasowa
  `config.json.bak-*`. Odczyty w całej aplikacji zostały bez zmian — dane z bazy
  są nakładane na te same klucze configu (`_overlay_store`).
- **Zapis ustawień jest atomowy** (`tmp` + `os.replace`) — koniec ryzyka obcięcia.
- Usunięty legacy `selected_indices` (71 wpisów, nigdzie nieczytane).

### Naprawione
- **Gry ze Steama trafiały do biblioteki Steam po raz DRUGI.** Eksport dodawał je
  jako skróty „Non-Steam" (`steam.exe -applaunch <appid>`) — a te gry Steam ma już
  u siebie, więc każda widniała dwa razy. Skrót na pulpicie ma sens, wpis
  w bibliotece nigdy: eksport pomija teraz gry o źródle `steam`. Nowa akcja
  `steam_cleanup()` usuwa te, które już powstały (u nas: **63 wpisy**).
- **Auto-wykrywanie emulatorów brało plik z folderu aktualizacji.** PCSX2 rozpakowuje
  nową wersję do `UPDATE_STAGING` i kasuje ten katalog po restarcie — wykryty stamtąd
  `pcsx2-qt.exe` **przestawał istnieć**, a wpisy Steam wskazywały na martwą ścieżkę
  (i miały inny `appid`, więc dublowały działające). Skanowanie pomija teraz katalogi
  `UPDATE_STAGING`/`updates`/`backup`/`temp`… i bierze wyłącznie istniejące pliki.
  Naprawa objęła 149 gier PS2.

- **Eksport do Steam tworzył DRUGI wpis dla gier już dodanych.** `appid` skrótu to
  `crc32(exe + nazwa)`, więc o tożsamości wpisu decyduje sam **zapis ścieżki**:
  Steam (i poprzednia wersja PyLinks) trzyma `Exe` z ukośnikami w przód, a my
  podawaliśmy backslashe z configu — ta sama gra dostawała inny appid i pojawiała
  się w bibliotece dwa razy. Ścieżka jest teraz normalizowana (`_norm_exe`), więc
  eksport trafia w **istniejące** wpisy: dla 110 gier PS1 — 110 trafień, 0 nowych.
  Przy okazji ratuje to pobrane wcześniej grafiki (pliki `grid` mają appid w nazwie).
  Eksport scala też bliźniaki powstałe wcześniej (`_collapse_twins`), ale wyłącznie
  przy identycznej nazwie, ścieżce **i argumentach** — osobne pozycje tej samej gry
  (np. płyta bonusowa „The Making of") zostają nietknięte.

- **Zapamiętany wybór grafiki nigdy nie wracał.** „Stuby" (odnośnik do wybranej
  grafiki, nie sam obraz) miały sprawiać, że po usunięciu gry z dysku i jej
  powrocie — albo po wyczyszczeniu cache — wraca DOKŁADNIE ta ikona, którą wybrał
  użytkownik. Dane się zapisywały, ale **nic ich nie czytało**, więc leciało nowe
  wyszukiwanie w SGDB i pierwszy lepszy wynik. Odtwarzanie działa teraz w trzech
  formatach naraz: URL (szybka ścieżka, bez API), gołe ID SteamGridDB (442 wpisy
  ze starej wersji, gdzie mapowanie ID→URL trzymała baza) oraz identyfikatory
  IGDB/TGDB/SteamCDN (77) — dopasowywane po `id` wśród kandydatów gry.
  Odzyskany wpis jest **dopisywany jako URL**, więc następnym razem idzie szybka
  ścieżka. Dotyczy też grafik Steam (grid/hero/logo).

## [9.5.0] — 2026-08-21

### Naprawione
- **PS2/PCSX2 dostawał `.m3u` i nie startował.** Skan scala gry wielopłytowe do
  playlisty, ale PCSX2, Dolphin, RPCS3, Xenia i Cemu playlist **nie czytają** —
  kończyło się „Failed to open CDVD …m3u: Unable to identify the ISO image type".
  Nowy `core/discs.py` dobiera ścieżkę pod konkretny emulator, **dwukierunkowo**:
  emulator bez obsługi playlist dostaje konkretny dysk (preferowany Disc 1, ale
  **weryfikowany na dysku** — gdy go brak, najniższy istniejący), a emulator
  z obsługą (DuckStation, RetroArch, mednafen, PPSSPP, mGBA) dostaje `.m3u`,
  nawet gdy gra wskazywała pojedynczą płytę. Nieznany emulator → pojedynczy dysk
  (bezpieczniej). Realnie naprawiło 3 gry PS2: Grandia III, Shadow Hearts —
  Covenant, Star Ocean — Till the End of Time.
- **Skróty `.lnk` leżące w folderze ROM-ów tworzyły duplikaty gier.** Skrót
  „pokrywa" teraz plik, który uruchamia (`_lnk_refs` czyta jego zawartość), więc
  `Halo.lnk` obok `Halo (USA) [XGD3].iso` to jedna gra, a nie działający skrót
  plus drugi wpis bez emulatora. Dotyczy folderów X360/Xenia i PS3/RPCS3.
  Bloki tekstu w `.lnk` bywają na nieparzystym offsecie, więc dekodujemy oba
  wyrównania UTF-16 — inaczej część skrótów „nie miała" ścieżki.
- **Explorer pokazywał STARĄ ikonę po zmianie grafiki.** Windows buforuje ikonę
  per **ścieżkę**, a `.ico` miał stałą nazwę `<klucz>.ico` — treść się zmieniała,
  ścieżka nie, więc shell serwował ikonę z bazy iconcache (restart explorera tego
  nie naprawia). Plik nazywa się teraz `<klucz>_<md5(treści)>.ico`: inna grafika =
  inna ścieżka = świeża ikona od razu, bez czyszczenia cache. Ta sama treść daje
  tę samą nazwę (bez śmiecenia), a starsze warianty tego klucza — wraz z plikiem
  w starym formacie — są sprzątane. Dotyczy skrótów `.lnk/.url` i eksportu Steam.
- **Skan pokazywał gry „duchy", których nie ma na dysku.** GOG (rejestr) i Epic
  (manifesty `.item`) były czytane bez sprawdzenia, czy katalog instalacji nadal
  istnieje — launchery nie sprzątają tych wpisów po ręcznym skasowaniu gry.
  Skutek był podwójny: gra pojawiała się na liście, a jej skrót **nie** trafiał do
  osieroconych, tylko był odtwarzany przy eksporcie (np. „FRONT MISSION 3: Remake").
  Teraz takie wpisy są pomijane, a skrót jest zgłaszany do usunięcia.
  Zabezpieczenie: gdy weryfikacja jest niemożliwa (brak ścieżki albo **dysk
  niedostępny** — odpięty zewnętrzny, niezamapowany sieciowy) wpis zostaje,
  żeby nie zniknęły gry, których chwilowo nie widać.
- **Okno wyboru grafik Steam zamierało przy dużych tłach.** Pasek „Wybrane"
  przesyłał przez most Python↔JS pełne pliki — hero bywa animowanym webp na
  45 MB, co po zakodowaniu base64 dawało **59 MB** na jedno otwarcie okna.
  Teraz idą miniatury: **59,1 MB → 0,34 MB** (174×). Ten sam błąd dotyczył
  *wyboru* wariantu (`choose_steam_asset`) — też naprawiony.
- **Hero i logo miały tylko 1–2 warianty do wyboru.** Endpointy hero/logo/icons
  szły bez `limit`/`page`, więc API zwracało garstkę. Limit działa teraz
  **per kategoria**: 40 grid / 40 hero / 40 logo, niezależnie.
- **Wyszukiwanie krótkich tytułów trafiało w zły wynik** — „D" dawało
  „D.I.P.R.I.P. Warm Up", bo brany był pierwszy wynik autocomplete. Dodano
  preferencję dokładnego tytułu oraz dopasowanie po tytule bazowym (część przed
  `:` / ` - `), dzięki czemu „D" → „D: The Game".
- **Brakujące grzbiety platform.** Nazwy typu „Sega - Saturn", „Dreamcast",
  „3DO Interactive Multiplayer" nie mapowały się na klucz pliku logo —
  rozwiązywanie idzie teraz przez presety ROM (`dir_names`/`display`/`name`).
- **NAOMI i NAOMI 2 dostawały logo Dreamcasta.** Usunięto alias `NAOMI→DC`,
  dodano własne targety. Dopasowanie wariantów jest tokenowe, więc
  „Sega Naomi -20" (indeks 20) nie jest mylone z „Sega Naomi 2".
- **Warianty logo nie były znajdowane** (np. Saturn: 0). Pliki zestawu
  console-logos mają kwalifikatory w nawiasach („(Japan)", „(Alt Orange)")
  i różne formaty indeksu. Po przejściu na dopasowanie tokenowe: Saturn 16,
  3DO 14, Dreamcast 14, NAOMI 24, MSX 4.
- **Okno grafik zamykało się przy zmianie rozmiaru/przeciąganiu.** Klik w tło
  zamykał okno także wtedy, gdy kursor tylko *kończył* tam ruch rozpoczęty na
  oknie. Teraz wymagane jest wciśnięcie **i** puszczenie na tle (wszystkie okna).
- **Uszkodzony plik logo blokował pozostałych kandydatów** — `return None`
  zamiast `continue` w wyszukiwaniu logo platformy.
- **`.btn.mini` był zdefiniowany dwukrotnie**, przez co gubił `flex:0 0 auto`
  i przyciski 📁 w Ustawieniach mogły się kurczyć.

### Dodane
- **„Pobierz wszystko" i „Stwórz wszystko"** w pasku górnym — obejmują PC + ROM
  oraz tryb ikon + Steam, ze wspólnym paskiem postępu dla całości operacji
  i przyciskiem zatrzymania. Prawy klik na grupie „Źródła PC" / „Systemy ROM"
  uruchamia to samo dla wybranej grupy.
- **Dociąganie kompletu grafik Steam** (grid + hero + logo) — wcześniej pobierana
  była wyłącznie okładka, więc eksport do Steam szedł bez tła i logo, o ile nie
  odwiedziło się okna wyboru każdej gry. Uruchamiane **na żądanie**: „⬇ Pobierz
  wszystko" lub prawy klik na grupie. **Skan sam z siebie nic nie dociąga** —
  okładki w siatce doczytują się leniwie przy wejściu na platformę, jak dotąd.
- **Pasek „Wybrane grafiki"** w oknie trybu Steam — pokazuje, co realnie trafi
  do biblioteki; wybrany wariant ma wyraźną obwódkę i znacznik ✓, a lista
  przewija się do niego automatycznie.
- **Przeciąganie okien** za nagłówek; pozycja i rozmiar zapamiętywane.
- **Pakowanie do przenośnego .exe** (Faza 3): `pylinks_web.spec` + `build.ps1`
  → `dist\PyLinksWeb.exe` (onefile, ~30 MB). Aplikacja pozostaje portable —
  `config.json`, `Cache/` i `LINKS/` powstają obok exe. Diagnostyka startu:
  `PyLinksWeb.exe --debug` lub `PLW_DEBUG=1`.
- **Wybór dodatkowych folderów „Extra"** przez natywne okno wyboru katalogu
  (lista z przyciskiem usuwania) zamiast ręcznego wpisywania ścieżek.

### Zmienione
- **Prefetch pobiera wyłącznie statyczne tła**, preferując szerokość zbliżoną do
  1920 px (Steam i tak nie animuje tła w bibliotece). Dla trzech sprawdzonych
  gier: **108 MB → 1,7 MB** (63×); komplet grafik to teraz ok. **1,9 MB na grę**
  zamiast kilkudziesięciu. W oknie wyboru animowane warianty nadal są dostępne.
- **Szybsze pieczenie grzbietów** — gotowe logo (wczytanie + korekta kontrastu)
  jest cache'owane per plik, z unieważnianiem po `mtime`; presety i kandydaci
  kluczy objęci `lru_cache`. Ok. 1,6× szybciej.
- **Tańsze sprawdzanie cache okładek** w zadaniach tła — sam `glob` zamiast
  pełnego dekodowania miniatury (28× szybciej na 60 grach).
- **Bez podwójnego zapytania do SteamGridDB** przy nietrafionym tytule —
  fallback nie powtarza już zapytania wykonanego przez wołającego.
- Usunięto pole „Dodatkowe biblioteki Steam" — biblioteki są wykrywane
  automatycznie z `libraryfolders.vdf`.

### Usunięte (sprzątanie, bez zmiany zachowania)
- Martwy kod: `spine._cover`, `spine_logos._VENDOR`, `art.cached_cover`,
  `art.cached_steam_asset`, `art.make_display_thumb`,
  `matching.needs_disambiguation`, `secure.is_protected`, `scan_roms._DISC_RE`,
  `app_paths.REPORTS_DIR`, `app_paths._LEGACY_CONFIG_PATH`,
  `constants.MATCH_THRESHOLD`/`ICON_TYPES`/`ICON_SHAPES`/`MAX_ICONS_CHOICES`/
  `UNLIMITED_ICONS_CAP`/`_LEGACY_PC_DIRS`, `api._job_thread`,
  nieużywana zmienna `window` w `main.py`, `resetCover()` w `app.js`.
- Zduplikowane reguły CSS (`.btn.mini`, `.pk-item.chosen`, `.pk-row .pk-item`)
  i powielony ręcznie strażnik zamykania okna wyboru (teraz wspólny helper).

## [9.4.0] — 2026-08-20

### Dodane
- **Edytor grzbietu platformy**: suwak **szerokości grzbietu** (12–40 % ikony)
  z podglądem na żywo — wpięty w kafelki, pieczenie `.ico` i eksport do Steam.
  Dodatkowo wybór wariantu logo z lokalnego cache zestawu console-logos oraz
  suwak rozmiaru logo wewnątrz grzbietu.

---

Wcześniejsze wersje powstały przed założeniem tego pliku — historia zmian
znajduje się w komunikatach commitów.
