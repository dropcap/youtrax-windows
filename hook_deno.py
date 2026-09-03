# hook_deno.py — PyInstaller runtime hook
# Runs inside the bundled app before any user code.
#
# Exports the bundled Deno binary's path via YTDL_DENO_BINARY so yt-dlp can
# solve YouTube's JS signature challenges without a host install — a
# double-clicked exe cannot rely on the user's PATH having a JS runtime.

import os
import sys


def _setup():
    base = getattr(sys, '_MEIPASS', None)
    if not base:
        return  # not running from a frozen bundle

    exe = os.path.join(base, 'deno.exe')
    if not os.path.isfile(exe):
        return  # no bundled runtime — fall back to whatever is on the host

    os.environ['YTDL_DENO_BINARY'] = exe


_setup()
