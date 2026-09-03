# vendor_deno.ps1 — place a Deno binary at vendor\deno.exe for the PyInstaller build.
#
# yt-dlp needs a JavaScript runtime to solve YouTube's signature challenges.
# A double-clicked exe cannot rely on the user's machine having one, so the
# runtime ships inside the bundle.
#
# Prefers a Deno already installed on this machine; otherwise downloads the
# official x86_64 Windows release.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Dest = "vendor\deno.exe"
New-Item -ItemType Directory -Force -Path "vendor" | Out-Null

if (Test-Path $Dest) {
    try {
        $ver = (& $Dest --version | Select-Object -First 1)
        Write-Host "==> vendor\deno.exe already present ($ver)"
        exit 0
    } catch {
        Remove-Item $Dest -Force
    }
}

# 1. Reuse a local install when present.
$local = Get-Command deno -ErrorAction SilentlyContinue
if ($local) {
    Write-Host "==> Vendoring local Deno: $($local.Source)"
    Copy-Item $local.Source $Dest
    Write-Host "    $((& $Dest --version | Select-Object -First 1))"
    exit 0
}

# 2. Otherwise fetch the official release.
$url = "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip"
Write-Host "==> Downloading Deno for x86_64-pc-windows-msvc..."
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("deno-" + [System.Guid]::NewGuid())
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    Invoke-WebRequest -Uri $url -OutFile "$tmp\deno.zip"
    Expand-Archive -Path "$tmp\deno.zip" -DestinationPath $tmp
    Move-Item "$tmp\deno.exe" $Dest -Force
    Write-Host "    $((& $Dest --version | Select-Object -First 1))"
} finally {
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
