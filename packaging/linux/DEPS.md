# PyLinksWeb na Linuksie — zależności systemowe

`install.sh` stawia środowisko Pythona (`.venv`) i instaluje `pywebview`
oraz `Pillow`. Tego, co poniżej, skrypt **nie** zainstaluje sam — wymaga
`sudo` i różni się między dystrybucjami.

## Minimum

| Co | Po co | Bez tego |
|---|---|---|
| Python 3.10+ z `venv` | całość | nic nie ruszy |
| PyGObject + WebKitGTK | okno aplikacji | brak GUI (albo użyj `--qt`) |
| `xdg-utils` | skróty do Steama/Epica | te skróty nie zadziałają |

## Instalacja

### Debian / Ubuntu / Linux Mint / Pop!\_OS

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-webkit2-4.1 xdg-utils
```

Na Ubuntu 22.04 i starszych zamiast `gir1.2-webkit2-4.1` jest
`gir1.2-webkit2-4.0` — działa tak samo.

### Fedora / Nobara

```bash
sudo dnf install -y python3 python3-pip python3-gobject webkit2gtk4.1 xdg-utils
```

### Arch / Manjaro / EndeavourOS

```bash
sudo pacman -S --needed python python-pip python-gobject webkit2gtk-4.1 xdg-utils
```

### openSUSE

```bash
sudo zypper install python311 python311-pip python311-gobject typelib-1_0-WebKit2-4_1 xdg-utils
```

### Bez GTK — backend Qt

```bash
./install.sh --qt
```

Pip dociągnie backend Qt do `.venv` (~150 MB), bez pakietów systemowych poza
samymi bibliotekami Qt. To też droga na **Steam Decku**, gdzie system plików
jest tylko do odczytu i `pacman` odpada.

## Weryfikacja

```bash
./install.sh --check
```

## Rzeczy specyficzne dla Linuksa

**Skróty to `.desktop`.** Zamiast `.lnk` (binarny format powłoki Windows,
tworzony przez COM) powstają pliki `.desktop` z bitem wykonywalnym. Argumenty
są cytowane wg specyfikacji Desktop Entry, a literalny `%` podwajany — bez tego
`Sim City 100%` uruchamiałby się z podstawionym polem `%f`.

**Skróty do adresów.** Gry Steama bez wskazanej binarki i gry z Epica jadą po
protokole (`steam://`, `com.epicgames.launcher://`). Na Windows to plik `.url`;
na Linuksie — `.desktop` z `Exec=xdg-open <adres>`, bo handler protokołu
rejestruje sam launcher.

**Ikony jako PNG.** Klucz `Icon=` w `.desktop` chce PNG, więc zamiast
wielorozmiarowego `.ico` zapisywany jest `.png` 256 px. Pieczenie okładki
(kadr, grzbiet platformy) działa identycznie.

**Skan bibliotek PC.** Bez rejestru:

- **Steam** — katalogi danych: `~/.steam/steam`, `~/.local/share/Steam`,
  wariant Flatpaka i Snapa; z nich `steamapps/` i `libraryfolders.vdf`.
  Brak binarki `steam` w PATH (typowe przy Flatpaku) nie blokuje skanu.
- **GOG** — katalogi instalacji (`~/GOG Games`, `~/GOG/games`, `~/Games/gog`)
  rozpoznawane po `start.sh` w korzeniu gry, plus biblioteka Heroica
  (`gog_store/installed.json`).
- **Epic** — nie ma launchera na Linuksa, więc czytane jest `installed.json`
  Heroica/legendary.

**Emulatory.** Binarki nie mają rozszerzenia, więc w katalogu emulatorów
kandydaci są wybierani po bicie wykonywalnym (z odsianiem danych i bibliotek),
a dodatkowo szukamy w `PATH` — także pod nazwami z sufiksem interfejsu
(`pcsx2-qt`, `mgba-qt`, `dolphin-emu`). Rdzenie RetroArcha są brane również
z `~/.config/retroarch/cores`, katalogu Flatpaka i `/usr/lib/libretro`.

**Eksport do Steama.** Działa tak samo (`shortcuts.vdf` + grafiki w
`userdata/<id>/config/grid`), ale wymaga **zamkniętego Steama** — działającą
instancję wykrywamy po `~/.steam/steam.pid` zweryfikowanym w `/proc`. Gry PS3
dodane jako gotowe `.lnk` z RPCS3 są przy tym pomijane: tego formatu nie da się
odczytać poza Windowsem.

**Sekrety.** Windows szyfruje klucze API przez TPM → DPAPI. Na Linuksie nie ma
ich odpowiednika, więc klucze leżą w `config.json` jawnym tekstem, a plik
dostaje prawa `0600` (do odczytu tylko dla Ciebie). Okno Ustawień pokazuje to
wprost jako „plik 0600 (bez szyfrowania)”, zamiast obiecywać szyfrowanie.
