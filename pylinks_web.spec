# -*- mode: python ; coding: utf-8 -*-
# Budowa przenośnego .exe:  pyinstaller --noconfirm pylinks_web.spec
# Portable: config.json / Cache/ / LINKS/ powstają OBOK exe (app_paths: frozen
# → katalog exe). Frontend web/ jest wpakowany do bundla (odczyt z sys._MEIPASS).

from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

# frontend (index.html + style.css + app.js) — main.py wkleja je inline w runtime
datas = [("web", "web")]
binaries = []
hiddenimports = [
    "webview.platforms.edgechromium",   # backend WebView2 (Win10/11)
    "webview.platforms.winforms",
]

# pywebview + .NET bridge (pythonnet/clr_loader) — DLL-e WebView2 z webview/lib
for pkg in ("webview", "clr_loader", "pythonnet"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

# pywin32 — tworzenie skrótów .lnk (ShortcutCreator) + COM
hiddenimports += collect_submodules("win32com")
hiddenimports += [
    "win32com.client", "win32com.shell", "pythoncom", "pywintypes",
    "win32api", "win32con", "win32timezone",
]

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "test", "unittest", "pytest"],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PyLinksWeb",
    debug=False,
    strip=False,
    upx=False,                 # UPX psuje część DLL WebView2 → wyłączony
    runtime_tmpdir=None,
    console=False,             # GUI bez okna konsoli
    disable_windowed_traceback=False,
    icon="assets/icon.ico",
)
