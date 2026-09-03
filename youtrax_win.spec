# youtrax_win.spec — PyInstaller build spec for Windows standalone app.
# Run:  pyinstaller youtrax_win.spec

import os
from importlib.metadata import version as _pkg_version
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Record which yt-dlp is being frozen in, so the runtime self-updater
# (ytdlp_updater.py) can tell whether a downloaded release is actually newer.
os.makedirs('build', exist_ok=True)
_ytdlp_version_file = os.path.join('build', 'ytdlp_bundled_version.txt')
with open(_ytdlp_version_file, 'w') as _f:
    _f.write(_pkg_version('yt_dlp'))

# Bundled JS runtime for yt-dlp's YouTube signature challenges.
# vendor_deno.ps1 places it; the build still succeeds without it, but the
# packaged app then depends on the user having Deno/Node installed.
_deno = os.path.join('vendor', 'deno.exe')
_deno_datas = [(_deno, '.')] if os.path.isfile(_deno) else []

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('templates', 'templates'),
        *collect_data_files('yt_dlp'),
        *collect_data_files('imageio_ffmpeg'),
        *_deno_datas,
        (_ytdlp_version_file, '.'),
        # App version and release notes (version.py, /api/changelog)
        ('VERSION', '.'),
        ('CHANGELOG.md', '.'),
        ('update_config.json', '.'),
    ],
    hiddenimports=[
        'flask',
        'werkzeug',
        'werkzeug.serving',
        'werkzeug.debug',
        'jinja2',
        'jinja2.ext',
        *collect_submodules('yt_dlp'),
        'imageio_ffmpeg',
        'webview',
        'ytdlp_updater',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['hook_ffmpeg.py', 'hook_deno.py', 'hook_ytdlp_update.py'],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# Single-file mode: binaries and datas are bundled directly into the .exe.
# PyInstaller extracts to a temp folder on first run — no separate folder needed.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name='YouTrax',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,           # no terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='youtrax.ico',
    onefile=True,
)
