"""services.emulators — auto-detekcja emulatorów i systemów ROM (port).

Po wskazaniu wspólnych folderów ROM/emulatorów:
- podfoldery ROM → preset systemu (po dir_names),
- exe emulatora z base_emu (ROM_EMU_PREF standalone + rdzenie RetroArch),
- domyślne argumenty.
"""

from __future__ import annotations

import re
from pathlib import Path

from pylinks.roms.presets import ROM_SYSTEM_PRESETS
from pylinks.core.spine import spine_canon_key

# preferowane standalone emulatory per platforma (podłańcuch nazwy exe → args)
ROM_EMU_PREF: dict = {
    "PS1": [("duckstation", "-fullscreen -nogui"), ("epsxe", ""), ("mednafen", "")],
    "PS2": [("pcsx2", "-fullscreen -nogui")],
    "PS3": [("rpcs3", "--no-gui")],
    "PSP": [("ppsspp", "")],
    "PS4": [("shadps4", "")],
    "PSVITA": [("vita3k", "")],
    "GCN": [("dolphin", "-b -e")],
    "WII": [("dolphin", "-b -e")],
    "WIIU": [("cemu", "-g")],
    "NSW": [("ryujinx", ""), ("eden", ""), ("citron", ""), ("yuzu", "")],
    "3DS": [("azahar", ""), ("citra", "")],
    "NDS": [("melonds", ""), ("desmume", "")],
    "N64": [("ares", ""), ("project64", "")],
    "SNES": [("snes9x", ""), ("ares", "")],
    "SNESMSU1": [("snes9x", ""), ("ares", "")],
    "NES": [("ares", ""), ("fceux", ""), ("nestopia", "")],
    "GBA": [("mgba", "")], "GB": [("mgba", "")], "GBC": [("mgba", "")],
    "MD": [("ares", "")],
    "DC": [("flycast", ""), ("redream", "")],
    "SATURN": [("mednafen", ""), ("ares", "")],
    "X360": [("xenia", "")],
    "MAME": [("mame", "")],
}

_EMU_PLAT_ALIAS = {"NAOMI": "DC", "NAOMI2": "DC", "ATOMISWAVE": "DC",
                   "HIKARU": "DC", "TRIFORCE": "GCN"}

# rdzeń RetroArch → system(y)
RETROARCH_CORE_SYSTEMS: dict = {
    "mednafen_psx": "PS1", "mednafen_psx_hw": "PS1", "pcsx_rearmed": "PS1",
    "swanstation": "PS1", "duckstation": "PS1", "pcsx2": "PS2", "ppsspp": "PSP",
    "nestopia": "NES", "fceumm": "NES", "mesen": "NES", "quicknes": "NES",
    "mesen-s": "SNES", "snes9x": "SNES", "snes9x2002": "SNES", "snes9x2005": "SNES",
    "snes9x2010": "SNES", "bsnes": "SNES", "bsnes_hd_beta": "SNES",
    "bsnes_mercury_accuracy": "SNES", "bsnes_mercury_balanced": "SNES",
    "bsnes_mercury_performance": "SNES", "mednafen_supafaust": "SNES",
    "mupen64plus_next": "N64", "parallel_n64": "N64",
    "gambatte": ["GB", "GBC"], "sameboy": ["GB", "GBC"], "tgbdual": ["GB", "GBC"],
    "mgba": ["GBA", "GB", "GBC"], "vba_next": ["GBA", "GB", "GBC"], "vbam": ["GBA", "GB", "GBC"],
    "desmume": "NDS", "desmume2015": "NDS", "melonds": "NDS",
    "dolphin": ["GCN", "WII"],
    "mednafen_saturn": "SATURN", "yabause": "SATURN", "yabasanshiro": "SATURN", "kronos": "SATURN",
    "flycast": "DC", "flycast_gles2": "DC", "redream": "DC",
    "genesis_plus_gx": "MD", "genesis_plus_gx_wide": "MD",
    "picodrive": ["MD", "SMS", "GG"], "blastem": "MD", "gearsystem": ["SMS", "GG"],
    "mednafen_pce": "PCENGINE", "mednafen_pce_fast": "PCENGINE",
    "mame": "MAME", "mame2000": "MAME", "mame2003": "MAME", "mame2003_plus": "MAME",
    "mame2010": "MAME", "mame2015": "MAME", "mame2016": "MAME",
    "fbneo": ["MAME", "NEOGEO"], "fbalpha2012": ["MAME", "NEOGEO"],
    "fbalpha2012_neogeo": "NEOGEO",
    "stella": "ATARI2600", "stella2014": "ATARI2600", "opera": "3DO",
    "mednafen_ngp": "NGP", "race": "NGP",
}


def _match_preset(folder_name: str):
    fl = re.sub(r"[\s_-]+", "", str(folder_name or "")).lower()
    if not fl:
        return None
    for p in ROM_SYSTEM_PRESETS:
        for dn in [p.get("name", ""), p.get("display", "")] + p.get("dir_names", []):
            if dn and re.sub(r"[\s_-]+", "", dn).lower() == fl:
                return p
    return None


def _find_standalone(plat: str, emu_exes: list) -> tuple:
    for sub, args in ROM_EMU_PREF.get(str(plat).upper(), []):
        for exe in emu_exes:
            if sub in Path(exe).stem.lower():
                return exe, args
    return "", ""


def _find_retroarch_core(plat: str, base_emu: str) -> tuple:
    """Znajdź retroarch.exe + rdzeń pasujący do platformy → (exe, args)."""
    plat_u = str(plat).upper()
    canon = _EMU_PLAT_ALIAS.get(plat_u) or spine_canon_key(plat_u)
    plat_set = {plat_u, canon.upper()}
    base = Path(base_emu)
    for pat in ("retroarch.exe", "*/retroarch.exe", "*/*/retroarch.exe"):
        for ra in base.glob(pat):
            cores = ra.parent / "cores"
            if not cores.is_dir():
                continue
            for core_f in sorted(cores.iterdir()):
                if core_f.suffix.lower() not in (".dll", ".so"):
                    continue
                stem = core_f.stem
                if stem.endswith("_libretro"):
                    stem = stem[:-len("_libretro")]
                systems = RETROARCH_CORE_SYSTEMS.get(stem)
                if not systems:
                    continue
                if isinstance(systems, str):
                    systems = [systems]
                if plat_set & {s.upper() for s in systems}:
                    return str(ra), f'-L "{core_f}" %ROM%'
    return "", ""


def _all_emu_exes(base_emu: str, limit: int = 3000) -> list:
    out = []
    base = Path(base_emu)
    if not base.is_dir():
        return out
    for pat in ("*.exe", "*/*.exe", "*/*/*.exe"):
        try:
            out += [str(p) for p in base.glob(pat)]
        except Exception:
            pass
        if len(out) >= limit:
            break
    return out


RETROARCH_CORE_DISPLAY: dict = {
    "mednafen_psx": "Beetle PSX", "mednafen_psx_hw": "Beetle PSX HW",
    "pcsx_rearmed": "PCSX-ReARMed", "swanstation": "SwanStation", "duckstation": "DuckStation",
    "pcsx2": "PCSX2", "ppsspp": "PPSSPP", "nestopia": "Nestopia UE", "fceumm": "FCEUmm",
    "mesen": "Mesen", "snes9x": "Snes9x", "bsnes": "bsnes", "mednafen_supafaust": "Supafaust",
    "mupen64plus_next": "Mupen64Plus-Next", "parallel_n64": "ParaLLEl N64",
    "gambatte": "Gambatte", "sameboy": "SameBoy", "mgba": "mGBA", "melonds": "melonDS",
    "desmume": "DeSmuME", "dolphin": "Dolphin", "mednafen_saturn": "Beetle Saturn",
    "yabasanshiro": "YabaSanshiro", "kronos": "Kronos", "flycast": "Flycast",
    "genesis_plus_gx": "Genesis Plus GX", "picodrive": "PicoDrive", "blastem": "BlastEm",
    "mednafen_pce": "Beetle PCE", "mame2003_plus": "MAME 2003-Plus", "fbneo": "FinalBurn Neo",
    "stella": "Stella", "opera": "Opera (3DO)",
}

_PLAT_EXE_HINTS: dict = {
    "PS1": ["epsxe", "duckstation", "pcsx", "mednafen"], "PS2": ["pcsx2"],
    "PS3": ["rpcs3"], "PSP": ["ppsspp"], "NES": ["fceux", "mesen", "nestopia"],
    "SNES": ["snes9x", "bsnes"], "N64": ["project64", "mupen", "ares"],
    "GB": ["mgba", "vba"], "GBC": ["mgba", "vba"], "GBA": ["mgba", "vba", "visualboy"],
    "NDS": ["desmume", "melon"], "GCN": ["dolphin"], "WII": ["dolphin"],
    "MD": ["fusion", "gens", "blast", "ares"], "SATURN": ["mednafen", "ssf", "ares"],
    "DC": ["redream", "flycast", "demul"], "MAME": ["mame"],
    "PS4": ["shadps4"], "NSW": ["ryujinx", "eden", "citron", "yuzu"],
    "3DS": ["azahar", "citra"], "WIIU": ["cemu"], "X360": ["xenia"],
}


def scan_emulators_for(plat: str, base_emu: str) -> list:
    """Opcje emulatora dla platformy: rdzenie RetroArch + standalone (do WYBORU).
    Każda: {label, exe, args, type}."""
    if not base_emu or not Path(base_emu).is_dir():
        return []
    plat_u = str(plat).upper()
    canon = _EMU_PLAT_ALIAS.get(plat_u) or spine_canon_key(plat_u)
    plat_set = {plat_u, canon.upper()}
    base = Path(base_emu)
    ra_out, st_out = [], []
    seen = set()
    exes = []
    for pat in ("*.exe", "*/*.exe", "*/*/*.exe"):
        try:
            exes += list(base.glob(pat))
        except Exception:
            pass
        if len(exes) >= 2000:
            break
    for exe in sorted(exes):
        s = str(exe)
        if s in seen:
            continue
        seen.add(s)
        if exe.stem.lower() == "retroarch":
            cores = exe.parent / "cores"
            if not cores.is_dir():
                continue
            for cf in sorted(cores.iterdir()):
                if cf.suffix.lower() not in (".dll", ".so"):
                    continue
                stem = cf.stem
                if stem.endswith("_libretro"):
                    stem = stem[:-len("_libretro")]
                systems = RETROARCH_CORE_SYSTEMS.get(stem)
                if not systems:
                    continue
                if isinstance(systems, str):
                    systems = [systems]
                if not (plat_set & {x.upper() for x in systems}):
                    continue
                ra_out.append({
                    "label": f"RetroArch — {RETROARCH_CORE_DISPLAY.get(stem, stem)}",
                    "exe": s, "args": f'-L "{cf}" %ROM%', "type": "retroarch"})
        else:
            hints = _PLAT_EXE_HINTS.get(plat_u) or _PLAT_EXE_HINTS.get(canon.upper(), [])
            if hints and not any(h in exe.stem.lower() for h in hints):
                continue
            st_out.append({"label": f"{exe.stem} (standalone)", "exe": s,
                           "args": "", "type": "standalone"})
    return ra_out + st_out


def autodetect_systems(base_rom: str, base_emu: str) -> list:
    """Podfoldery base_rom → presety + dopasowany emulator z base_emu."""
    out: list = []
    if not base_rom or not Path(base_rom).is_dir():
        return out
    emu_exes = _all_emu_exes(base_emu) if base_emu else []
    for sub in sorted(Path(base_rom).iterdir()):
        if not sub.is_dir():
            continue
        preset = _match_preset(sub.name)
        if not preset:
            continue
        exe, args = _find_standalone(preset["name"], emu_exes)
        if not exe and base_emu and Path(base_emu).is_dir():
            exe, args = _find_retroarch_core(preset["name"], base_emu)
        out.append({
            "name": preset["name"],
            "rom_dir": str(sub),
            "emulator": exe,
            "launch_args": args if exe else preset.get("launch_args", ""),
            "roms_in_subdirs": preset.get("roms_in_subdirs", False),
            "primary_ext": preset.get("primary_ext", "m3u,cue,iso,chd,bin"),
        })
    return out
