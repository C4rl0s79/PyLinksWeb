# build.ps1 — buduje przenośny PyLinksWeb.exe (PyInstaller, onefile).
# Użycie:  powershell -ExecutionPolicy Bypass -File build.ps1
# Wynik:   dist\PyLinksWeb.exe  (portable — config/Cache/LINKS powstają obok exe)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "== PyLinksWeb: build exe ==" -ForegroundColor Cyan

# 1) zależności buildu
python -m pip install --upgrade pyinstaller pywebview Pillow pywin32 | Out-Host

# 2) czyszczenie poprzedniej budowy
if (Test-Path build) { Remove-Item build -Recurse -Force }
if (Test-Path dist)  { Remove-Item dist  -Recurse -Force }

# 3) budowa wg specu
python -m PyInstaller --noconfirm --clean pylinks_web.spec | Out-Host

$exe = Join-Path $PSScriptRoot "dist\PyLinksWeb.exe"
if (Test-Path $exe) {
    $mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host "OK: $exe ($mb MB)" -ForegroundColor Green
} else {
    Write-Host "BŁĄD: nie powstał dist\PyLinksWeb.exe" -ForegroundColor Red
    exit 1
}
