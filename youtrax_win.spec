# youtrax_win.spec — PyInstaller build spec for Windows standalone app.
# Run:  pyinstaller youtrax_win.spec

import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('templates', 'templates'),
        *collect_data_files('yt_dlp'),
        *collect_data_files('imageio_ffmpeg'),
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
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['hook_ffmpeg.py'],
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
