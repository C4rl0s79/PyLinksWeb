# Changelog

Wszystkie istotne zmiany w PyLinksWeb. Format oparty na
[Keep a Changelog](https://keepachangelog.com/pl/1.1.0/).

## [9.16.1] — 2026-08-24

### Naprawione
- **Przepieczenie ikony ZDEJMOWAŁO grzbiet platformy.** Regułę „grzbiet dostają
  tylko ROM-y i tylko przy włączonej opcji" znał wyłącznie `export_games()`.
  Dodane później ścieżki — naprawa ikony istniejącego skrótu, zmiana grafiki
  i kadru w widoku LINKS, eksport arcade — wołały `bake_ico()` BEZ parametrów
  grzbietu, więc po każdej takiej operacji plik `.lnk` dostawał ikonę bez niego.
  Zgłoszone na Dreamcaście; sprawdzone na `rom::DC::AeroWings` — obrazy pieczone
  z grzbietem i bez faktycznie się różnią.

  Decyzja o grzbiecie jest teraz w JEDNYM miejscu (`export.bake_args()`),
  z którego korzystają wszystkie cztery ścieżki. Przy okazji zniknęło pięć
  zmiennych powielających tę samą regułę w `export_games()`.

  **Skróty ROM zapisane po 9.10 mają ikony bez grzbietu** — przywraca je
  „🖼 Napraw ikony" w widoku LINKS danej platformy.

## [9.16.0] — 2026-08-24

### Zmienione
- **Kategorie arcade oparte na catver.ini, dwupoziomowo.** Dotąd panel boczny
  pokazywał płaską listę z genre.ini (61 wartości). catver.ini opisuje 50 368 setów
  łańcuchem „Kategoria / Podkategoria" i sam oznacza treści dla dorosłych
  („* Mature *") — to znacznie lepiej uporządkowane źródło: **58 kategorii
  głównych, 590 par kategoria/podkategoria, 100 % pokrycia.**

  W bibliotece użytkownika daje to 57 kategorii i 10 448 tytułów, np. Shooter
  rozpada się na 33 podkategorie (Flying Vertical 270, Gallery 166, Gun 163…).
  Panel boczny pokazuje kategorię, a strzałka rozwija jej podkategorie —
  wszystkie naraz byłyby nie do przejrzenia.

  Poziomy dalsze niż drugi („Handheld / Plug n' Play TV Game / Music") sklejamy
  w podkategorię: dotyczą 518 setów z 50 tysięcy, więc trzecie piętro drzewa
  kosztowałoby więcej, niż daje.

### Dodane
- **Okno „Kategorie arcade"** (⚙ przy nagłówku Arcade w panelu bocznym).
  Drzewo z licznikami tytułów i oznaczeniem, ile w danej gałęzi jest treści
  dla dorosłych; wyszukiwarka, „Wszystkie"/„Żadne", rozwijanie podkategorii.
  Odznaczenie kategorii wygasza jej podkategorie, żeby nie sugerować, że coś
  jeszcze przez nią przejdzie. Wybór zapisuje się jako `skip_cats`.

- Filtr treści dla dorosłych uwzględnia teraz OBA źródła: `mature.ini`
  oraz przyrostek „* Mature *" z catver.ini.

## [9.15.0] — 2026-08-24

### Dodane
- **„🧹 Posprzątaj duplikaty w Steam"** w Ustawieniach. Funkcja działała od
  dawna, ale nie miała żadnego przycisku — dało się ją uruchomić tylko ręcznie.
  Znajduje wpisy „Non-Steam", które w rzeczywistości uruchamiają grę ze Steama
  (`steam.exe -applaunch <appid>`), przez co każda taka gra widnieje dwa razy.

  ZAWSZE zaczyna od podglądu: pokazuje listę nazw i liczbę, i dopiero po
  potwierdzeniu rusza plik. Przy uruchomionym Steamie odmawia (nadpisałby zmiany
  przy wyjściu), robi kopię zapasową `shortcuts.vdf` i podmienia go atomowo.

## [9.14.1] — 2026-08-24

### Usunięte
- **Dziewięć metod `Api`, do których nic nie sięgało** (ani Python, ani interfejs):
  `export_pc`, `export_rom`, `get_pc_games`, `get_rom_games`, `get_theme`,
  `list_rom_presets`, `reset_cover`, `reset_steam_asset`, `set_spine`. Pozostałości
  po wcześniejszych wersjach interfejsu — zastąpiły je `export_keys`, `scan_pc`
  oraz osobne `set_spine_*`. Do tego nieużywane `store.stats()` i osiem martwych
  importów.

### Zmienione
- **Sześć kopii „znajdź plik okładki po kluczu" zredukowanych do jednej.**
  `raw_cover`, `has_cover` i `export._raw_bytes` idą teraz przez `_raw_path()`;
  kasowanie plików danego klucza (`clear_cover`, `clear_thumb`) przez wspólne
  `_unlink_all()`. Rozstrzyganie „klucz gry → plik na dysku" jest w JEDNYM miejscu.

- **`thumb_uri()` przestało powielać skalowanie** — 44 wiersze skopiowane
  z `thumb_path()` zastąpione delegacją (16 wierszy). Miniatura powstaje w jednym
  miejscu, tu dochodzi wyłącznie kodowanie do data-URI.

- **Budowanie adresu miniatury** było w dwóch metodach — wyniesione do
  `_thumb_url()`, razem z regułą unieważniania cache przeglądarki.

### Sprawdzone
- Interfejs nie woła żadnej nieistniejącej metody, nie sięga po nieistniejące
  elementy strony i nie ma martwych funkcji. Wszystkie moduły importują się
  poprawnie; biblioteka arcade, okładki, skróty i testy API działają jak przed
  czyszczeniem.

## [9.14.0] — 2026-08-24

### Zmienione
- **Miniatury podawane po HTTP z pętli lokalnej, nie przez most.** Kodowanie
  base64 dokłada 33 %, ale prawdziwym kosztem było przepychanie obrazów przez
  SYNCHRONICZNY most — przy każdym przerysowaniu siatki od nowa. Teraz obrazy
  pobiera sama przeglądarka: równolegle, asynchronicznie (`loading="lazy"`,
  `decoding="async"`) i z własnym cache, więc powrót do obejrzanego widoku
  nie kosztuje ani jednego bajtu.

  `covers_batch` nie czyta już ŻADNEGO obrazu — sprawdza tylko, czy okładka
  istnieje, jednym przejściem po katalogu (zamiast globa na każdy klucz).
  Adres niesie znacznik czasu okładki, dzięki czemu przeglądarka może trzymać
  miniaturę bezterminowo, a po podmianie grafiki i tak pobierze nową.

  Zmierzone na porcji 300 kafelków:

  | | 9.12 | 9.13 (miniatury) | 9.14 (HTTP) |
  |---|---|---|---|
  | ładunek przez most | 119,6 MB | 6,8 MB | **29 KB** |
  | czas wywołania | 1738 ms | 725 ms | **416 ms** |

  Serwer słucha wyłącznie na 127.0.0.1, na porcie od systemu, a ścieżki niosą
  losowy token na czas życia procesu; wychodzą stamtąd wyłącznie miniatury okładek.
  Gdyby silnik zablokował pobieranie z pętli lokalnej, obraz wraca starą drogą
  (przez most) — raz na obraz, żeby błąd samego zapasu się nie zapętlił.

## [9.13.0] — 2026-08-24

### Naprawione
- **Wczytywanie okładek z cache przesyłało setki megabajtów.** Do siatki szedł
  PEŁNY obraz każdej okładki — średnio 508 KB na sztukę, czyli **119,6 MB na porcję
  300 kafelków** i blisko pół giga na tysiąc. Kafelek ma ~200 px, więc to była
  czysta strata. Siatka dostaje teraz miniatury WebP (360 px, ~29 KB):
  **119,6 MB → 6,8 MB**, czyli 17,6× mniej; dla tysiąca grafik ~28 MB zamiast ~496 MB.
  Pełna rozdzielczość zostaje tam, gdzie ma znaczenie: w oknie kadrowania
  i przy pieczeniu `.ico` do skrótów.

- **Szukanie pliku okładki przechodziło CAŁY katalog na każdy klucz** (300 przejść
  po ~1000 plikach). Gotowa miniatura ma znaną nazwę, więc trafiamy w nią wprost.
  Aktualność zapewnia kasowanie miniatury przy każdej zmianie okładki.

- **Tworzenie miniatur zamrażało okno** przy pierwszym wejściu w duży gatunek.
  Skalowanie idzie teraz na czterech wątkach (Pillow zwalnia GIL).

  Zmierzone na porcji 300 kafelków z biblioteki arcade:

  | | przed | po |
  |---|---|---|
  | ładunek przez most | 119,6 MB | **6,8 MB** |
  | pierwsze wejście | 5230 ms | **1844 ms** |
  | kolejne (z dysku) | 1738 ms | **725 ms** |

## [9.12.0] — 2026-08-24

### Dodane
- **„🖼 Napraw ikony" w widoku LINKS** — przepieka ikony istniejących skrótów
  z bieżącej okładki i kadru. Skrót zrobiony ZANIM gra dostała okładkę zostaje
  z gołą ikoną i sam się nie odświeży; dotąd trzeba było eksportować od nowa.
  Brakującą okładkę dociąga po drodze. Idzie paczkami po 8 z paskiem postępu,
  a COM inicjalizuje raz na paczkę, nie na skrót.

### Naprawione
- **Skrót arcade wiązany z GRĄ PO NAZWIE PLIKU trafiał w zbieżny tytuł.**
  „Super Street Fighter II: The New Challengers" wskazywało bootleg na Mega Drive
  (`ssf2mdb`) zamiast automatu (`ssf2`). Skrót niesie nazwę zestawu we własnych
  argumentach — to źródło pewne, więc czytamy je w pierwszej kolejności,
  a dopasowanie po nazwie zostaje wyłącznie jako awaryjne.

## [9.11.2] — 2026-08-24

### Naprawione
- **Konsola nie pokazywała nic — bo w ogóle nie reagowała na przełączanie.**
  Klasa z własnym `display` nadpisuje wbudowane ukrywanie atrybutem `hidden`,
  a nowy panel i pasek stanu nie miały własnej reguły `[hidden]{display:none}`,
  którą w tym pliku stylów ma każdy inny taki element. Efekt: pusta ramka wisiała
  na ekranie na stałe, a przycisk przestawiał tylko atrybut, nic nie wyświetlając.
  Błąd czysto stylistyczny — przechwytywanie wyjątków JS nie miało szans go pokazać.

- **Kadrowanie nie działało dla istniejących skrótów.** W widoku LINKS kafelek
  pokazuje ikonę WYCIĄGNIĘTĄ ZE SKRÓTU, a nie źródłową okładkę, więc zmiana kadru
  przestylowywała gotowy `.ico` (bez efektu) i nie przepiekała pliku. Każda zmiana
  kadru — „Kadruj", „Ramki", „Domyślne" — przepieka teraz ikonę i zapisuje ją
  w pliku `.lnk`/`.url`, po czym odświeża kafelek.

- Ścieżki kadrowania nie połykają już wyjątków po cichu — powód trafia do konsoli.

## [9.11.1] — 2026-08-24

### Naprawione
- **Filtrowanie gubiło zaznaczone gry arcade.** Pole filtra przerysowywało widok,
  a `showMame` zaczynał wybór od nowa — więc przepływ „znajdź → zaznacz → wyczyść
  filtr → znajdź kolejną" tracił wszystko przy każdym naciśnięciu klawisza.
  Zaznaczenie przeżywa teraz filtrowanie; kasuje je dopiero przejście do INNEGO
  gatunku albo innego widoku, gdzie faktycznie zaczyna się nowy wybór.

- **Program stawał przy wpisywaniu i czyszczeniu filtra.** Każdy wciśnięty klawisz
  przerysowywał porcję 300 kafelków i uruchamiał pobieranie okładek. Doszło
  opóźnienie 280 ms, więc przerysowanie następuje raz — po skończeniu pisania.

- **Znane nietrafienia dopytywane przy każdym przerysowaniu.** `covers_batch`
  odróżnia teraz „nie ma w cache" od „wiemy, że nie ma nigdzie" i te drugie
  odpadają od razu, zamiast wracać przez most przy każdym odświeżeniu siatki.

## [9.11.0] — 2026-08-24

### Naprawione
- **Nowa grafika skrótu pobierała się, ale się nie pokazywała** (Vendetta,
  Violent Storm). Podmiana w pliku `.lnk` DZIAŁAŁA — widać ją było w dzienniku —
  ale podgląd ikony jest zapamiętywany po ścieżce skrótu i nigdy nie był
  unieważniany, więc siatka pokazywała w kółko starą. Wpis w pamięci podręcznej
  jest teraz kasowany przy podmianie.

- **Konsola milczała zamiast zgłaszać błąd.** `conRefresh` połykał wyjątek pustym
  `catch`, więc gdy odczyt dziennika zawiódł, panel świecił pustką — narzędzie do
  diagnozy samo nie mówiło, że nie działa. Teraz wypisuje powód.

- **Przycisk konsoli był w UKRYTYM pasku stanu** — dostępny tylko w trakcie
  ładowania, czyli nie wtedy, gdy się go szuka. Doszedł stały przycisk
  „≡ Konsola" na pasku narzędzi (skrót F12 działa jak wcześniej).

- **Seria błędów gubiła wszystkie poza pierwszym.** Strażnik przed pętlą blokował
  cały zapis, a nie samą wysyłkę przez most. Zapis lokalny jest teraz bezwarunkowy.

### Dodane
- Konsola pokazuje DWA źródła: „przeglądarka" (bufor wierszy z warstwy JS,
  widoczny nawet gdy most nie działa — a właśnie wtedy jest najpotrzebniejszy)
  oraz „program" (dziennik z pliku).

## [9.10.0] — 2026-08-24

### Naprawione
- **Okno przestawało odpowiadać przy pobieraniu okładek.** Pobierałem CAŁĄ porcję
  300 kafelków jednym wywołaniem mostu, a most jest synchroniczny — interfejs stał
  zamrożony przez kilkadziesiąt sekund. Teraz paczki po 24, z oddaniem klatki
  między nimi: okno reaguje, widać postęp i można przerwać przełączeniem widoku.

- **Indeks Libretro przeniesiony z JSON-a do SQLite** (`Cache/pylinks.db`, tam gdzie
  reszta trwałego stanu). Plik JSON trzeba było sparsować w CAŁOŚCI (1,9 MB, ~9 ms)
  przy każdym dostępie, a okładek szukamy z sześciu wątków — to ~5 s czystego
  procesora na porcję 300 kafelków. Odczyt po kluczu głównym: **0,6 ms zamiast 9 ms**.

- **W sekcji LINKS nie dało się zmienić ikony istniejącego skrótu.** Kafelki były
  martwe, bo `card()` dostawał nazwę bez obiektu gry, więc nie podpinał żadnego
  kliknięcia. Skróty są teraz wiązane z grą po nazwie pliku, a po wyborze grafiki
  podmieniana jest ikona w SAMYM PLIKU `.lnk`/`.url` — bez tego zmieniałby się
  wyłącznie podgląd, a skrót na dysku wskazywałby starą ikonę. Gdy okładki jeszcze
  nie ma, jest dociągana zamiast odmowy.

### Dodane
- **Konsola w oknie programu** (przycisk „≡" na pasku stanu, albo F12 / Ctrl+Shift+J).
  Pokazuje dziennik na żywo, z automatycznym odświeżaniem, przyciskiem czyszczenia
  i skrótem do pliku. Do tej pory długie operacje szły w ciszy i nie było jak
  zobaczyć, czy program pracuje, czy stanął.
- **Pasek stanu** w rogu okna — pokazuje bieżącą czynność i postęp
  („Pobieram okładki… 72/300").

## [9.9.0] — 2026-08-24

### Dodane
- **Libretro Thumbnails jako GŁÓWNE źródło okładek arcade.** Pliki w tym zbiorze
  nazywają się dokładnie opisem gry z MAME („Alien vs. Predator (Europe 940520).png”),
  więc trafienie jest TOŻSAMOŚCIĄ, a nie zgadywaniem po tytule — czyli dokładnie
  tym, czego brakowało SteamGridDB przy krótkich, wieloznacznych nazwach automatów.
  Sprawdzane przed SGDB; SGDB zostaje dla reszty.

  Nie sondujemy adresów po kolei: nazwy w repozytorium pochodzą z innej wersji MAME
  niż lokalna („Euro 940520” vs „Europe 940520”), więc zgadywanie by chybiało.
  Pobieramy spis całego drzewa jednym zapytaniem (~35 tys. wpisów, ~4 s) i trzymamy
  go miesiąc w cache; dopasowanie jest potem lokalne i natychmiastowe.

  Efekt na bibliotece użytkownika (4249 tytułów): sam Libretro pokrywa **64%**,
  a razem z SGDB **66% na losowej próbce 60 tytułów** — przy 50% wcześniej.
  Do tego SZYBCIEJ (0,7 min na porcję 300 kafelków zamiast 1,2 min), bo trafienie
  to jedno pobranie zamiast serii zapytań do wyszukiwarki.

- „Testuj Libretro” pokazuje też rozmiar indeksu arcade, a „Odśwież okładki arcade”
  odświeża go na siłę.

## [9.8.0] — 2026-08-24

### Naprawione
- **„Większość grafik przestała się wyświetlać".** Nie znikały — ładowały się
  ślimaczo. Brakujące okładki dociągane były POJEDYNCZO, w pętli przez most:
  ~0,5 s na tytuł, czyli 2,5 minuty na porcję 300 kafelków, i za KAŻDYM wejściem
  w widok od nowa, bo nieudane szukanie nie zostawiało śladu. Po wyczyszczeniu
  cache arcade wyglądało to jak awaria. Teraz jedno wywołanie na porcję,
  6 wątków po stronie Pythona: **1,2 min za pierwszym razem, 3,4 s przy kolejnych**
  (zmierzone na 40 losowych tytułach).

- **Nietrafienia są zapamiętywane** (`cover_miss_<tryb>` w bazie). Sporo automatów
  nie istnieje w żadnym źródle — bez tej pamięci program pytał o nie w kółko.
  Kasuje ją „Odśwież okładki arcade” oraz ręczny wybór grafiki, więc nie blokuje
  ponownej próby, gdy coś się zmieni.

- „Odśwież okładki arcade” kasuje też pamięć nietrafień — bez tego usuwał pliki,
  a program i tak nie próbował pobrać ich ponownie.

### Dodane
- **Konsola przeglądarki trafia do dziennika.** WebView2 nie pokazuje konsoli,
  więc błędy JS ginęły bezpowrotnie. `console.error`/`warn`, nieobsłużone wyjątki
  i odrzucone obietnice lecą teraz do `PyLinksWeb.log` z prefiksem `js/`.
  Zabezpieczone przed pętlą, gdyby zawiódł sam most.
- Pobieranie okładek zapisuje w dzienniku podsumowanie każdej porcji
  (ile z cache, ile pobrano, ile bez trafienia).

## [9.7.6] — 2026-08-24

### Naprawione
- **Skróty arcade bez ikony — właściwa przyczyna.** Grafika w cache należy do
  GRUPY tytułu (`mame::avsp`), bo tak pobiera ją siatka, ale eksport szukał jej
  pod kluczem WYBRANEGO wariantu (`mame::avspu`) i zawsze chybiał. Ikonę miały
  więc tylko te skróty, w których wybrana wersja była zarazem setem nadrzędnym
  (baddudes, mk, tekken) — stąd wrażenie przypadkowości. Klucz jest teraz wspólny.
  Poprzedni wpis (9.7.4) mylnie wskazywał brak pobranych okładek: te pobierają się
  w widoku arcade automatycznie, dokładnie jak przy PC i ROM-ach.

## [9.7.5] — 2026-08-24

### Naprawione
- **Klony gier na BIOS-ie eksportowały się bez BIOS-u.** Łańcuch zależności ma
  DWA ogniwa — `sengoku3a` → `sengoku3` → `neogeo` — a plan patrzył tylko o jeden
  poziom w górę. ROM-y BIOS-u Neo Geo (`sp-s2.sp1`, `sp-45.sp1`…) trafiały przez
  to na listę „weź z zipa rodzica”, gdzie ich nie ma: w zestawie split leżą
  w `neogeo.zip`. Powstawał zip bez BIOS-u i gra kończyła się „NOT FOUND”.
  Plan idzie teraz łańcuchem `romof` do końca i wyklucza ROM-y należące do BIOS-u,
  zostawiając go — zgodnie z założeniem — osobnym, współdzielonym zipem.
  Zweryfikowane bez regresji na wszystkich odmianach: klon na BIOS-ie (sengoku3a),
  gra na BIOS-ie bez rodzica (mslug, sengoku3), klon zwykły (avspa, pacman,
  gatedoom), klon samowystarczalny (sf2b) i gra bez zależności (baddudes).

## [9.7.4] — 2026-08-24

### Dodane
- **Dziennik zdarzeń** — `PyLinksWeb.log` obok pliku programu (rotacja po 2 MB).
  Program nie zapisywał dotąd NICZEGO, więc nieudany eksport nie zostawiał śladu,
  po którym dałoby się dojść do przyczyny. W Ustawieniach są „📄 Dziennik zdarzeń”
  (podgląd ostatnich 300 wierszy na miejscu) i „📂 Pokaż plik”.

### Naprawione
- **„Zestaw niekompletny po eksporcie” nie mówił, czego brakuje.** MAME wypisuje
  wiersz na każdy brakujący plik, ale `verify()` zostawiał samo podsumowanie
  i szczegóły przepadały. Doszło `verify_detail()`, a komunikat wskazuje teraz
  konkretną przyczynę, np. „brakuje w bibliotece: sfrush.chd (obraz dysku)”.

- **Braki znikały po cichu.** Eksport miał trzy miejsca z `except: pass`:
  ROM nieobecny w zipie źródłowym lub w zipie rodzica, nieskopiowany BIOS albo
  urządzenie, oraz nieznaleziony plik CHD. Powstawał z tego niekompletny zestaw,
  a użytkownik dostawał ogólnik. Każdy z tych przypadków jest teraz zbierany,
  pokazywany w powodzie i zapisywany w dzienniku. Przyczyna („brak w bibliotece”)
  ma pierwszeństwo przed skutkiem (lista „NOT FOUND” z MAME).

- **Skróty arcade bez ikony.** Ikona powstaje z okładki w cache, a przy eksporcie
  zwykle jeszcze jej nie było — użytkownik nie klikał „Pobierz”, więc skrót
  wychodził z gołą ikoną MAME. Eksport dociąga teraz brakującą okładkę raz,
  na żądanie; gdy mimo to jej nie ma (tytuł nieznany źródłom), zapisuje to
  w dzienniku zamiast milczeć.

## [9.7.3] — 2026-08-24

### Naprawione
- **Gry o podwójnej nazwie nie dostawały okładki.** MAME zapisuje tytuły
  dwujęzyczne jednym łańcuchem — „Sengoku 3 / Sengoku Densho 2001”,
  „Aero Fighters 2 / Sonic Wings 2” — a SGDB takiej formy nie zna, więc szukanie
  całości nie trafiało w nic, choć KAŻDA połowa z osobna trafia bez pudła.
  Dotyczyło to 192 z 4249 tytułów w bibliotece. Pytamy teraz po kolei: całość,
  potem każdy człon, a na koniec sam początek przed podtytułem po myślniku
  („Metal Slug - Super Vehicle-001” → „Metal Slug”). Zweryfikowane: sengoku3,
  3countb, sonicwi2, aodk, alpham2, area51mx, mslug — wszystkie trafiają.

- **Nazwa pliku skrótu z ukośnikiem.** Ukośnik jest w nazwie pliku niedozwolony,
  więc powstawało „Sengoku 3 _ Sengoku Densho 2001.lnk”. Plik nosi teraz człon
  główny (`primary_title`), bo MAME podaje tam nazwę międzynarodową. Do szukania
  okładek zostaje PEŁNY tytuł — tam obie połowy są przydatne.

## [9.7.2] — 2026-08-24

### Naprawione
- **Skróty arcade nie uruchamiały gry - "Required files are missing".** Skrót
  przekazywał sam identyfikator zestawu (`mame.exe baddudes`), w założeniu, że
  katalog do grania jest domyślnym `roms/` MAME. Ale to ustawienie użytkownika:
  eksport szedł do `D:\emu\roms\Mame`, a MAME szukał we własnym `roms`
  obok `mame.exe`. Podstępne było to, że `-verifyroms` DOSTAWAŁ `-rompath`,
  więc kontrola przechodziła i skrót powstawał - a gra i tak kończyła się listą
  "NOT FOUND". Skrót niesie teraz `-rompath` jawnie.
- `_lnk_set()` (wykrywanie kolizji nazw skrótów) czyta zestaw jako OSTATNI człon
  argumentów - po dodaniu `-rompath` porównanie całego łańcucha nigdy by nie
  pasowało i kolizje przestałyby być wykrywane. Starsze skróty z samym zestawem
  nadal są rozpoznawane.

## [9.7.1] — 2026-08-24

### Naprawione
- **Filtr nazwy działał tylko na liście PC.** Pole tekstowe zawsze przerysowywało
  widok PC, więc w bibliotece arcade (4 tys. tytułów, tam gdzie szukanie jest
  najpotrzebniejsze) nie robiło nic, a przy ROM-ach było w ogóle schowane.
  Teraz filtruje AKTYWNY widok — PC, ROM albo arcade — zachowując podświetlenie
  w panelu bocznym.

- **Zaznaczonych gier arcade nie dało się wyeksportować.** Przycisk „📦 Utwórz
  skróty" był w tym widoku ukryty (została po mnie niedokończona gałąź), a jedyną
  drogą był prawy klik → „Wybierz wersję" → Eksportuj, po jednej grze — przy czym
  ten wpis jest ukrywany dla tytułów mających jedną wersję, więc ich nie dało się
  wyeksportować wcale. Teraz przycisk działa na całym zaznaczeniu i prowadzi
  właściwą drogą (złożenie zestawu non-merged → `-verifyroms` → skrót tylko dla
  wersji, która przeszła kontrolę), z paskiem postępu i podsumowaniem błędów.
  Zwykły eksport skrótów by tu nie wystarczył: gry nie ma jeszcze w katalogu
  do grania, więc skrót celowałby w pustkę.

- **Widok arcade dziedziczył zaznaczenie z poprzedniego widoku** (klucze gier PC/ROM).
  Wejście w bibliotekę arcade zaczyna teraz z pustym zaznaczeniem — eksport kopiuje
  pliki na dysk, więc wybór ma być świadomy, a nie „wszystko domyślnie".

- „Wszystkie" w widoku arcade zaznaczało tylko wyrenderowaną porcję 300 kafelków
  zamiast całego (przefiltrowanego) zbioru.

- `build.ps1` zapisany jako UTF-8 **z BOM** — PowerShell 5.1 czyta skrypty jako ANSI
  i polskie znaki rozwalały parser, przez co build w ogóle się nie uruchamiał.

## [9.7.0] — 2026-08-24

### Naprawione
- **Okładki automatów trafiały w cudze gry.** SGDB szuka po tytule, a tytuły
  automatów są krótkie i wieloznaczne, więc „Alien vs. Predator" dostawało okładkę
  gry z Game Boya, a „Tetris" — „Tetris Ultimate". MAME podaje jednak PRODUCENTA,
  a SGDB dokładnie nim rozróżnia wpisy arcade, więc pytamy teraz wprost o
  „Tytuł (Producent)". Sama nazwa nie wystarczy: autouzupełnianie dla „Tetris"
  w ogóle nie zwraca wpisów arcade — trzeba o nie zapytać osobno. Zweryfikowane:
  `avsp` → „Alien vs. Predator (Capcom)", `atetris` → „Tetris (Atari Games)",
  `tetris` → „Tetris (Sega)".

- **Brak okładki zamiast cudzej.** Wielu automatów po prostu nie ma w SGDB, a
  dotychczasowy fallback brał pierwszy lepszy wynik: „Sensory Backgammon Computer"
  dostawało „Sensory Deprivation", „Crazy War" → „Crazy Taxi", „Chicken Shift" →
  „Chicken". Wynik musi teraz być DOŚĆ blisko tytułu (zgodność po pominięciu
  interpunkcji, albo tytuł + podtytuł na granicy słów); inaczej kafelek zostaje
  pusty. Na próbce 20 losowych tytułów: było 11 trafień i 9 pomyłek, jest
  10 trafień, 10 świadomie pustych i **0 pomyłek**.

- **„🖼 Odśwież okładki arcade"** w Ustawieniach — kasuje okładki dobrane
  AUTOMATYCZNIE, żeby pobrały się z nową logiką; grafiki wybrane ręcznie zostają
  (poznajemy je po wpisie w bazie wyborów). Bez tego poprawka nie byłaby widoczna,
  bo błędne okładki siedzą już w cache.

### Dodane
- **ScreenScraper.fr jako uzupełnienie okładek arcade** (Ustawienia → źródła).
  Przewaga nad SGDB: szuka po NAZWIE SETU MAME (`romnom=avsp.zip`,
  `systemeid=75`), a nie po tytule — trafia więc tam, gdzie tytuł jest nietypowy
  („Cyber Troopers Virtual-On 4", „beatmania IIDX with DDR"). Wchodzi do gry
  dopiero, gdy SGDB nic nie znajdzie, więc niczego nie psuje.

  **Wymaga danych DEWELOPERA** (`devid`/`devpassword`), przyznawanych autorowi
  programu na wniosek — sprawdzone empirycznie: samo konto użytkownika, nawet
  z wykupionym poziomem donora, dostaje 403 „Vérifier vos identifiants
  développeur" na KAŻDYM endpoincie, łącznie z listą systemów. Konto użytkownika
  jest opcjonalne i podnosi tylko dobowy limit zapytań. Wszystkie cztery pola
  są szyfrowane TPM/DPAPI, jak reszta kluczy.

- Przycisk „Testuj ScreenScraper" oraz pozycja w zbiorczym teście API — pokazuje
  zużycie dobowego limitu konta.

## [9.6.0] — 2026-08-21

### Dodane
- **Rozdzielone nazewnictwo: interfejs pokazuje wersję, plik skrótu — samą grę.**
  Na kafelku widnieje pełny opis WYBRANEJ wersji („Tetris (set 1)"), więc warianty
  da się rozróżnić, a lista rozwijana pod okładką przełącza je w miejscu.
  Skrót dostaje nazwę bez nawiasu z regionem/rewizją: `Alien vs. Predator.lnk`.
  Gdy dwie RÓŻNE gry czyszczą się do tej samej nazwy, kolidująca wraca do
  PEŁNEGO tytułu — bo nawias często niesie tożsamość gry, a nie tylko region:
  „Tetris" to 7 osobnych automatów (Atari, Sega, Mega-Tech…), a „Trivial Pursuit"
  9 edycji. Efekt: `Tetris.lnk`, `Tetris (set 1).lnk`, `Tetris (Mega-Tech).lnk`,
  `Trivial Pursuit (Baby Boomer Edition).lnk` — zamiast cichego nadpisania
  cudzego skrótu albo kryptonimu z nazwą zestawu.

- **Wybór wersji wprost na kafelku arcade** — pod okładką, w miejscu podpisu, jest
  lista rozwijana z wariantami. Pozycje opisane są **wersją gry, nie nazwą pliku**:
  z tytułu odcinana jest nazwa gry (z rodzica), więc zostaje to, co odróżnia —
  `(Europe 940520)`, `(Asia 940520)`, `(bootleg set 6, with UM3482)`. Wariant
  o zupełnie innym tytule zachowuje pełną nazwę (`Gate of Doom (US revision 4)`
  pod „Dark Seal", `Link (Korean bootleg…)` pod „Tetris") — dlatego prefiks bierzemy
  z rodzica, a nie ze wspólnego początku wszystkich wariantów: jedna odmienna nazwa
  zerowałaby go dla całej listy. Od razu widać, **która wersja jest wybrana**, i można
  ją zmienić bez otwierania okna. Wybór zapisuje się trwale w bazie (`mame_set`),
  więc przeżywa restart i ponowny skan; eksport bierze dokładnie tę wersję.

- **Eksport wybranej wersji arcade do katalogu do grania** (faza 2). Kolejność jest
  celowa: złożenie zestawu → `-verifyroms` → **skrót tylko dla wersji, która przeszła
  weryfikację**, żeby w LINKS nie zostawała pozycja, która i tak się nie uruchomi.
  Skrót celuje w `mame.exe <set>` bez `-rompath` — katalog do grania jest domyślnym
  `roms/`, więc biblioteka może zostać odpięta.
  Trzy przypadki rozróżniane z `-listxml`:
  klon dziedziczący po rodzicu → **budowany jest zip non-merged** (ROM-y z zipa
  rodzica trafiają pod nazwami klonu, bo atrybut `merge` bywa inną nazwą);
  gra z BIOS-em (`romof` → zestaw BIOS) → BIOS zostaje **osobnym, współdzielonym
  zipem** (duplikowanie kosztowałoby ~5 MB na każdą grę Neo Geo);
  ROM-y urządzeń → zip nazywa się jak `romof` urządzenia, nie jak samo urządzenie
  (`qsound_hle` → `qsound.zip`) — bez tego weryfikacja padała.
  CHD trafia do podkatalogu `<set>/`. Sprawdzone: avspa (złożony + qsound),
  mslug (+ neogeo BIOS), avsp, sf2b — wszystkie `is good` wg MAME.

- **Biblioteka arcade (MAME)** — przeglądanie wielkiej kolekcji z okładkami i wybór
  konkretnej wersji, zanim powstanie skrót. Metadane pochodzą z **samego `mame.exe`**
  (`-listxml`), więc nie trzeba szukać DAT-a pasującego do wersji emulatora:
  50 368 setów indeksuje się w ~8 s. Sety są **grupowane po tytule** (43 394 plików →
  10 448 tytułów), bo docelowo wybiera się jedną wersję; warianty (`cloneof`) siedzą
  pod prawym klikiem → „Wybierz wersję", z przyciskiem sprawdzającym `-verifyroms`,
  czyli czy dana wersja **naprawdę się uruchomi** (z BIOS-em i ROM-ami urządzeń).
- **Filtrowanie oparte na danych, nie na nazwach.** MAME emuluje nie tylko arcade —
  w pełnym secie 15 851 pozycji to automaty hazardowe, 7 318 urządzenia, 2 675
  komputery i konsole. Odsiewamy je na czterech poziomach: kategoria maszyny
  z `-listxml`, **gatunek** z `genre.ini` (61 wartości, wybierane checkboxami
  w Ustawieniach), **sterowanie** z `<input><control>` (hazard, mahjong, hanafuda,
  spinner) oraz **`mature.ini`**. Efekt: 4 249 tytułów zamiast 43 394 plików.
  `paddle` i `trackball` zostają włączone — inaczej wypadłyby klasyki grywalne
  myszą (Centipede, Missile Command, 720 Degrees).
- Gatunki działają w bocznym panelu jak systemy ROM (klik = filtr siatki), a wielkie
  listy renderują się porcjami po 300 kafelków.

- **Poprawne sortowanie serii w Eksploratorze.** Goły tytuł lądował ZA numerowanymi
  częściami („Tekken 2, Tekken 3, … Tekken"). Powód nie leży w numeracji, tylko
  w rozszerzeniu: porównanie rozjeżdża się na 7. znaku, gdzie jedna nazwa ma już
  KROPKĘ (`Tekken.lnk`), a druga jeszcze spację nazwy (`Tekken 2.lnk`) — a spacja
  (0x20) sortuje się przed kropką (0x2E). Wspólne `.lnk` nie pomaga, bo kropka
  zaczyna się w innym miejscu; znaki zero-width też nie, bo mają zerową wagę
  w porównywaniu. Gdy w folderze istnieje dłuższe rodzeństwo, do krótszego tytułu
  doklejana jest **spacja** (`Tekken .lnk`) — sortuje się pierwszy, a że Explorer
  nigdy nie pokazuje rozszerzenia `.lnk` (`NeverShowExt`), podpis wygląda tak samo.
  Wykrywanie osieroconych skrótów i kolekcje ignorują tę spację.

- **Przyciski „Testuj" dla wszystkich używanych API** — SteamGridDB, Steam Web API,
  IGDB, TheGamesDB, Libretro i repozytorium logotypów platform, plus zbiorcze
  **🧪 Testuj wszystkie API** z tabelką wyników. Komunikaty są konkretne, nie samo
  „✗": rozróżniają brak klucza, odrzucony klucz, zły Client Secret (IGDB),
  wyczerpany limit miesięczny TheGamesDB (z czasem do odnowy) i limit zapytań
  GitHuba. Przy powodzeniu pokazują dowód działania — liczbę wyników, gier
  na koncie Steam czy plików w repozytorium.

- **Regulowana pula grafik z IGDB / TheGamesDB** (Ustawienia → Źródła dodatkowe,
  domyślnie 12, zakres 4–80). Te źródła **nie mają stronicowania** jak SGDB, więc
  „Pokaż więcej" ich nie dociągnie — całą pulę trzeba wziąć od razu. Wcześniej
  IGDB oddawało sztywno maks. 8 grafik (okładka + 4 artworki + 3 zrzuty).
- **Podział puli między kategorie zamiast „kto pierwszy".** W IGDB liczby są
  skrajnie nierówne — bywa 3 artworki i 300 zrzutów; przy zwykłym limicie zrzuty
  wypychały wszystko inne i potrzebne grafiki ginęły. Teraz okładka jest zawsze
  pierwsza, a resztę puli dzieli przeplot artwork/zrzut (przy limicie 20 i grze
  3 artworki/300 zrzutów: okładka + wszystkie 3 artworki + 16 zrzutów).
  TheGamesDB sortuje po typie (okładka → logo → fanart → baner), a zrzuty
  i ekrany tytułowe dokłada dopiero przy wyższym limicie.

- **„➕ Pokaż więcej" w oknie wyboru grafik** — SGDB zwracał tylko pierwszą porcję
  (40 na kategorię) i reszta była nieosiągalna. Klient obsługuje teraz parametr
  `page`, więc kolejne kliknięcie dokłada następną porcję (41–80, 81–120…)
  **dopisując** ją do listy, bez przeładowania. Powtórki są odsiewane po `id`.
  Działa w obu trybach — ikony i Steam (grid/tło/logo osobno).
- **Suwak wielkości miniatur w oknie wyboru** — rozmiar zapamiętywany między
  otwaniami; steruje jedną zmienną CSS, więc działa i na siatce ikon,
  i na poziomych paskach trybu Steam.

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
- **Platformy z aliasem znikały z edytora grzbietów** — lista kluczowała po nazwie
  KANONICZNEJ, więc `SNESMSU1` chowało się za `SNES`, a `SEGACD` za `MD` (widocznych
  14 z 16 systemów) i nie dało się nadać im własnego logo. Lista idzie teraz po
  własnej nazwie systemu. Przy okazji `install_variant` zapisywał plik pod kluczem
  kanonicznym — wybór wariantu dla SNESMSU1 **nadpisywał logo SNES-a**; teraz
  powstaje `SNESMSU1.png`, a `SNES.png` zostaje nietknięty.
- **Pobieranie wariantów logotypów wyglądało na zawieszone.** Szło synchronicznie
  przez most (okno zamierało) i szeregowo ~4 pliki/s przy 1200–2000 plikach, bez
  żadnego wskaźnika. Teraz leci w tle jako zadanie z paskiem postępu, a pobieranie
  jest równoległe (8 wątków). Po zakończeniu lista wariantów i znaczniki ✓ przy
  kolorystyce odświeżają się same. Uwaga: logotypy arcade (NAOMI) są w katalogu
  `Arcade` — trzecim w kolejce, stąd wrażenie, że „nie pobrały się".

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
