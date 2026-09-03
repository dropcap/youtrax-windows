# build_win.ps1 — Build YouTrax for Windows
# Run from the project root in PowerShell:
#   .\build_win.ps1

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host "==> Installing dependencies..."
pip install -r requirements.txt

Write-Host "==> Vendoring Deno (yt-dlp's JS runtime)..."
.\vendor_deno.ps1

Write-Host "==> Generating icon..."
python generate_icon_win.py

Write-Host "==> Building YouTrax.exe..."
pyinstaller youtrax_win.spec --noconfirm

Write-Host ""
Write-Host "Done! Run dist\YouTrax.exe to launch."
