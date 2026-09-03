# hook_ytdlp_update.py — PyInstaller runtime hook
# Runs inside the bundled app before any user code, and therefore before the
# first `import yt_dlp`. If a newer yt-dlp release was downloaded on an
# earlier run, put it ahead of the frozen copy on sys.path.

try:
    from ytdlp_updater import activate
    activate()
except Exception:
    pass  # never let the updater break startup — the frozen copy still works
