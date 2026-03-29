# hook_ffmpeg.py — PyInstaller runtime hook
# Runs inside the bundled .app before any user code.
# Ensures the bundled ffmpeg binary is executable and exports its path
# via YTDL_FFMPEG_BINARY so yt-dlp receives it directly rather than
# relying on PATH (which is unreliable inside a macOS .app bundle).

import os
import stat


def _setup():
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return  # no bundled ffmpeg — fall back to system ffmpeg

    # Restore execute permission (PyInstaller may strip it from data files).
    current = os.stat(exe).st_mode
    if not (current & stat.S_IXUSR):
        os.chmod(exe, current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # Export the path so ytdl.py can pass it directly to yt-dlp.
    os.environ['YTDL_FFMPEG_BINARY'] = exe


_setup()
