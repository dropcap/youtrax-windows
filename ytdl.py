#!/usr/bin/env python3
"""ytdl.py — Download YouTube audio as 320kbps MP3 files."""

import argparse
import functools
import os
import re
import sys
from pathlib import Path

try:
    import yt_dlp
except ImportError:
    print("Error: yt-dlp is not installed. Run: pip install yt-dlp", file=sys.stderr)
    sys.exit(1)


# YouTube's default innertube clients (android_vr et al.) now hand out media
# URLs that answer HTTP 403 on the actual byte range request. The *_embedded
# clients still serve signed, audio-only DASH formats, so pin those first and
# leave the yt-dlp defaults as a trailing fallback.
YOUTUBE_PLAYER_CLIENTS = ['web_embedded', 'tv_embedded', 'default']

EXTRACTOR_ARGS = {'youtube': {'player_client': YOUTUBE_PLAYER_CLIENTS}}

# The app runs windowless; without this flag every helper spawn (deno probe,
# ffmpeg) flashes a console window in front of the UI on Windows.
_NO_WINDOW = 0x08000000 if os.name == 'nt' else 0  # CREATE_NO_WINDOW

_EXE = '.exe' if os.name == 'nt' else ''

# When the uploader enabled downloads, SoundCloud exposes the original
# uploaded file (often WAV/AIFF/FLAC) as format_id "download". Its bitrate is
# unlisted, so plain bestaudio can rank a 160k transcode above it; name it
# explicitly. Other extractors have no such id and fall through untouched.
AUDIO_FORMAT = 'bestaudio[format_id=download]/bestaudio/best'


def is_soundcloud(url: str) -> bool:
    return bool(re.match(r'https?://([^/]+\.)?(soundcloud\.com|snd\.sc)/', url))


# Shared with the Flask app's settings endpoints; defined here so the
# download layer can read the SoundCloud token without importing app.py.
SETTINGS_FILE = Path.home() / '.config' / 'ytdl' / 'settings.json'

# The token is a credential, so on macOS it lives in the Keychain rather
# than the plaintext settings file (which stays as the non-mac fallback).
_KEYCHAIN_SERVICE = 'YouTrax'
_KEYCHAIN_ACCOUNT = 'soundcloud-oauth'

# SoundCloud tokens are digits, dashes and alphanumerics. Refusing anything
# else keeps pasted garbage out of the Keychain and the quoting below safe.
_TOKEN_RE = re.compile(r'^[\w.-]{8,512}$')


def _keychain_read_token() -> str:
    if sys.platform != 'darwin':
        return ''
    try:
        import subprocess
        proc = subprocess.run(
            ['security', 'find-generic-password', '-s', _KEYCHAIN_SERVICE,
             '-a', _KEYCHAIN_ACCOUNT, '-w'],
            capture_output=True, text=True, timeout=10,
        )
        return proc.stdout.strip() if proc.returncode == 0 else ''
    except Exception:
        return ''


def store_soundcloud_token(token: str) -> bool:
    """
    Put *token* in the macOS Keychain ('' deletes the entry).

    Returns True when the Keychain now holds the truth, False when the
    caller must fall back to the settings file (non-mac, or Keychain error).
    """
    if sys.platform != 'darwin':
        return False
    if token and not _TOKEN_RE.match(token):
        return False
    try:
        import subprocess
        if not token:
            subprocess.run(
                ['security', 'delete-generic-password', '-s', _KEYCHAIN_SERVICE,
                 '-a', _KEYCHAIN_ACCOUNT],
                capture_output=True, timeout=10,
            )
            return True  # absent entry and deleted entry are the same truth
        # Batch mode reads the command from stdin, keeping the secret out of
        # the process list that `ps` could expose.
        cmd = (f'add-generic-password -U -s "{_KEYCHAIN_SERVICE}" '
               f'-a "{_KEYCHAIN_ACCOUNT}" -w "{token}"\n')
        proc = subprocess.run(
            ['security', '-i'], input=cmd, capture_output=True, text=True,
            timeout=10,
        )
        return proc.returncode == 0
    except Exception:
        return False


def soundcloud_oauth_token() -> str:
    """The user's SoundCloud OAuth token, or '' when not configured."""
    token = os.environ.get('YTDL_SOUNDCLOUD_TOKEN', '').strip()
    if token:
        return token
    token = _keychain_read_token()
    if token:
        return token
    try:
        import json
        cfg = json.loads(SETTINGS_FILE.read_text())
        return str(cfg.get('soundcloud_token') or '').strip()
    except Exception:
        return ''


def soundcloud_token_from_browser(browser: str):
    """
    The SoundCloud OAuth token from *browser*'s cookies, or None.

    Uses yt-dlp's own cookie extraction, so whichever account is signed in
    at soundcloud.com in that browser is the one that gets connected. On
    macOS, reading Chrome's cookie store asks the user for Keychain access
    through the OS prompt; Safari may require Full Disk Access.
    """
    from yt_dlp.cookies import extract_cookies_from_browser

    jar = extract_cookies_from_browser(browser)
    for cookie in jar:
        if (cookie.name == 'oauth_token'
                and 'soundcloud.com' in (cookie.domain or '') and cookie.value):
            return cookie.value
    return None


def apply_soundcloud_auth(ydl_opts: dict, url: str) -> dict:
    """
    Authenticate SoundCloud requests when the user has saved an OAuth token.

    yt-dlp signs in to SoundCloud with the literal username "oauth" and the
    token as the password, which unlocks the subscriber-only streams (256 kbps
    AAC on Go+/Artist plans) that anonymous requests never see.
    """
    if is_soundcloud(url):
        token = soundcloud_oauth_token()
        if token:
            ydl_opts['username'] = 'oauth'
            ydl_opts['password'] = token
    return ydl_opts

# YouTube signs its media URLs behind a JS challenge, so yt-dlp needs an
# external JavaScript runtime to produce a working download URL. Probe the
# usual per-user install locations too, in yt-dlp's own priority order —
# shutil.which alone misses installs that only a shell profile puts on PATH.
_JS_RUNTIME_CANDIDATES = {
    'deno': (
        '~/.deno/bin/deno' + _EXE,
        '~/scoop/apps/deno/current/deno.exe',
    ),
    'node': (
        'C:/Program Files/nodejs/node.exe',
        '~/AppData/Roaming/nvm/*/node.exe',
    ),
    'bun': ('~/.bun/bin/bun' + _EXE,),
    'quickjs': (),
}


@functools.lru_cache(maxsize=None)
def runtime_works(path: str) -> bool:
    """
    Return True if *path* is a JS runtime that can actually be executed.

    Permission bits are not enough — antivirus or policy software can block a
    helper binary at spawn time — so the only reliable test is to run one and
    see whether it answers.
    """
    import subprocess

    try:
        proc = subprocess.run(
            [path, '--version'],
            capture_output=True,
            timeout=20,
            check=False,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return False

    return proc.returncode == 0


def find_js_runtimes() -> dict:
    """
    Locate JavaScript runtimes yt-dlp can use to solve YouTube's challenges.

    Returns a dict shaped for yt-dlp's ``js_runtimes`` option, e.g.
    ``{'deno': {'path': 'C:/.../deno.exe'}}``. Returns an empty dict when
    nothing is found, in which case the caller should leave yt-dlp's own
    default (bare ``deno`` on PATH) in place.
    """
    import glob
    import shutil

    # The bundled runtime wins: it is the only one guaranteed to exist for
    # users who never installed Deno themselves.
    bundled = os.environ.get('YTDL_DENO_BINARY')
    if bundled and runtime_works(bundled):
        return {'deno': {'path': bundled}}

    found: dict = {}
    for name, extra_globs in _JS_RUNTIME_CANDIDATES.items():
        path = shutil.which(name)

        if not path:
            for pattern in extra_globs:
                matches = sorted(glob.glob(os.path.expanduser(pattern)), reverse=True)
                if matches:
                    path = matches[0]
                    break

        if path and runtime_works(path):
            found[name] = {'path': path}

    return found


def sanitize_filename(name: str) -> str:
    """Remove filesystem-unsafe characters and collapse whitespace."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name[:200] or 'unknown'


def make_cli_progress_hook():
    """Return a yt-dlp progress hook that renders a terminal progress bar."""
    def hook(d: dict) -> None:
        if d['status'] == 'downloading':
            downloaded = d.get('downloaded_bytes', 0) or 0
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            speed = d.get('speed') or 0
            eta = d.get('eta') or 0

            speed_str = (
                f"{speed / 1_048_576:.1f} MB/s" if speed >= 1_048_576
                else f"{speed / 1024:.0f} KB/s"
            )

            if total:
                pct = downloaded / total * 100
                filled = int(40 * downloaded / total)
                bar = '█' * filled + '░' * (40 - filled)
                print(
                    f"\r  [{bar}] {pct:5.1f}%  {speed_str}  ETA {eta}s   ",
                    end='', flush=True,
                )
            else:
                mb = downloaded / 1_048_576
                print(f"\r  Downloaded {mb:.1f} MB  {speed_str}   ", end='', flush=True)

        elif d['status'] == 'finished':
            print()  # newline after the bar

    return hook


def rename_by_tags(mp3_path: str, info: dict) -> str:
    """
    Rename an MP3 file to 'Title - Artist.mp3' using yt-dlp metadata.

    Falls back to the original filename if artist or track info is missing.
    Returns the (possibly new) absolute path.
    """
    track = (info.get('track') or '').strip()
    artist = (info.get('artist') or '').strip()

    if not track or not artist:
        return mp3_path

    new_name = sanitize_filename(f"{track} - {artist}") + '.mp3'
    old = Path(mp3_path)
    new = old.parent / new_name

    if new == old:
        return mp3_path

    # Avoid overwriting an existing file
    if new.exists():
        return mp3_path

    old.rename(new)
    return str(new)


class _WarningCatcher:
    """A yt-dlp logger that only collects warnings, printing nothing."""

    def __init__(self):
        self.warnings: list[str] = []

    def debug(self, msg):
        pass

    info = debug
    error = debug

    def warning(self, msg):
        self.warnings.append(str(msg))


def download_audio(
    url: str,
    output_dir: str = './downloads',
    progress_hook=None,
    verbose: bool = True,
    details: dict | None = None,
) -> str:
    """
    Download *url* and save as a 320 kbps MP3 inside *output_dir*.

    After download, the file is renamed to "Track - Artist.mp3" when
    metadata is available from YouTube, otherwise the video title is kept.

    When *details* is given, it is filled with what was actually fetched —
    'format_id', 'abr', and 'sc_token_rejected' (SoundCloud refused the
    configured OAuth token and served guest quality) — so callers can tell
    the user when a download came from a lower-quality source.

    Returns the absolute path to the saved MP3.
    Raises ``yt_dlp.utils.DownloadError`` on failure.
    """
    out = Path(output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    if progress_hook is None and verbose:
        progress_hook = make_cli_progress_hook()

    hooks = [progress_hook] if progress_hook else []

    ydl_opts: dict = {
        'format': AUDIO_FORMAT,
        'outtmpl': str(out / '%(title)s.%(ext)s'),
        'noplaylist': True,            # only download the single video
        'postprocessors': [
            {
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '320',
            }
        ],
        'progress_hooks': hooks,
        'quiet': not verbose,
        'no_warnings': not verbose,
        'extractor_args': EXTRACTOR_ARGS,
    }
    apply_soundcloud_auth(ydl_opts, url)

    # In app mode, capture warnings (e.g. "token is invalid") instead of
    # discarding them; in CLI mode leave yt-dlp's own console output alone.
    catcher = None
    if not verbose:
        catcher = _WarningCatcher()
        ydl_opts['logger'] = catcher
        ydl_opts['no_warnings'] = False

    # Only override yt-dlp's default when we actually located a runtime.
    js_runtimes = find_js_runtimes()
    if js_runtimes:
        ydl_opts['js_runtimes'] = js_runtimes

    # Use bundled ffmpeg when set by the PyInstaller runtime hook,
    # or fall back to imageio-ffmpeg in dev mode, then system ffmpeg.
    ffmpeg_bin = os.environ.get('YTDL_FFMPEG_BINARY')
    if not ffmpeg_bin:
        try:
            import imageio_ffmpeg
            ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            pass
    if ffmpeg_bin:
        ydl_opts['ffmpeg_location'] = ffmpeg_bin

    # Postprocessor hook fires after FFmpegExtractAudio with the real .mp3 path.
    captured: list[str] = []

    def _pp_hook(d: dict) -> None:
        if d['status'] == 'finished':
            fp = d.get('info_dict', {}).get('filepath', '')
            if fp.endswith('.mp3'):
                captured.append(fp)

    ydl_opts['postprocessor_hooks'] = [_pp_hook]

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

        if details is not None:
            details['format_id'] = info.get('format_id') or ''
            details['abr'] = info.get('abr') or 0
            details['sc_token_rejected'] = bool(catcher) and any(
                'token is invalid' in w.lower() for w in catcher.warnings
            )

        # 1. Best: postprocessor hook gave us the exact path yt-dlp wrote.
        if captured and Path(captured[-1]).exists():
            mp3_path = captured[-1]
        # 2. Good: derive from yt-dlp's own filename logic + swap extension.
        elif Path(ydl.prepare_filename(info)).with_suffix('.mp3').exists():
            mp3_path = str(Path(ydl.prepare_filename(info)).with_suffix('.mp3'))
        else:
            # 3. Last resort: find the most recently modified .mp3 in the output dir.
            mp3s = sorted(out.glob('*.mp3'), key=lambda p: p.stat().st_mtime, reverse=True)
            if mp3s:
                mp3_path = str(mp3s[0])
            else:
                raise FileNotFoundError(f"Could not locate the downloaded MP3 in {out}")

        mp3_path = rename_by_tags(mp3_path, info)

        return mp3_path


def progressive_stream_url(url: str):
    """
    Resolve *url* to a direct, byte-seekable audio stream URL, or None.

    SoundCloud's progressive MP3 is CBR over plain HTTP, so ffmpeg can decode
    an arbitrary window straight off the URL with range requests — the tempo
    prefill uses this instead of downloading an excerpt (yt-dlp's range
    downloader comes back empty against SoundCloud's HLS streams, and the
    bundled ffmpeg cannot decode them directly either).
    """
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'format': 'bestaudio[protocol=http]/bestaudio/best',
    }
    apply_soundcloud_auth(ydl_opts, url)
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError:
        return None
    if info.get('protocol') == 'http':
        return info.get('url')
    return None


def forbidden_hint(url: str = '') -> str:
    """
    Explain an HTTP 403, naming the cause we can actually detect.

    From YouTube, a 403 almost always means yt-dlp could not solve the
    signature challenge, which in turn almost always means no JavaScript
    runtime ran. Other sources never involve the JS challenge, so none of
    that diagnosis applies to them.
    """
    if url and is_soundcloud(url):
        return (
            'SoundCloud refused the download (HTTP 403). The track may be '
            'blocked in your region or a SoundCloud change may have outpaced '
            'this build — try again later.'
        )

    base = 'YouTube refused the download (HTTP 403).'

    if find_js_runtimes():
        # A runtime ran fine, so the challenge itself is what failed — which
        # usually means the yt-dlp in this build is too old for a YouTube
        # change. Try to fetch the current release right now so the fix is
        # already in place when the user relaunches.
        if getattr(sys, 'frozen', False):
            try:
                from ytdlp_updater import update_now
                newer = update_now()
            except Exception:
                newer = None
            if newer:
                return (
                    f'{base} A YouTube change outpaced this build, and an '
                    f'update (yt-dlp {newer}) has just been downloaded — '
                    'quit and reopen YouTrax, then try again.'
                )
            return f'{base} A YouTube change may have outpaced this build.'
        return f'{base} Updating yt-dlp usually fixes this: pip install -U yt-dlp'

    if not getattr(sys, 'frozen', False):
        return (
            f'{base} No JavaScript runtime was found — yt-dlp needs one to '
            'unlock YouTube media URLs. Install Deno (https://deno.com) and '
            'try again.'
        )

    # Frozen: a runtime ships inside the bundle, so it was blocked, not absent.
    # On Windows the usual culprit is antivirus or policy software refusing to
    # spawn the helper binary.
    return (
        f'{base} The JavaScript runtime bundled inside YouTrax could not run. '
        'Security software may be blocking it — allow YouTrax in your '
        'antivirus settings, or install Deno (https://deno.com) and try again.'
    )


def ensure_ffmpeg_on_path() -> None:
    """
    Make the bundled ffmpeg discoverable as plain ``ffmpeg`` on PATH.

    Passing ``ffmpeg_location`` covers postprocessing, but yt-dlp's range
    downloader checks availability via a bare FFmpegPostProcessor() that
    ignores that option and simply looks for ``ffmpeg`` on PATH. The bundled
    binary is named ffmpeg-win-<arch>-<version>.exe, so it is never found.
    Copy it under the expected name into a temp dir on PATH (symlinks need
    elevated rights on Windows, so a copy is the portable answer).
    """
    import shutil
    import tempfile

    exe = os.environ.get('YTDL_FFMPEG_BINARY')
    if not exe:
        try:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return
    if not exe or not os.path.isfile(exe):
        return

    bindir = os.path.join(tempfile.gettempdir(), 'youtrax-bin')
    link = os.path.join(bindir, 'ffmpeg' + _EXE)
    try:
        os.makedirs(bindir, exist_ok=True)
        if os.name == 'nt':
            if (not os.path.isfile(link)
                    or os.path.getsize(link) != os.path.getsize(exe)):
                shutil.copyfile(exe, link)
        else:
            if not (os.path.islink(link) and os.readlink(link) == exe):
                if os.path.lexists(link):
                    os.unlink(link)
                os.symlink(exe, link)
    except OSError:
        return

    parts = os.environ.get('PATH', '').split(os.pathsep)
    if bindir not in parts:
        os.environ['PATH'] = os.pathsep.join([bindir] + parts)


def download_excerpt(url: str, output_dir: str, start: float = 30.0,
                     duration: float = 180.0) -> str:
    """
    Download a slice of *url*'s audio for analysis, skipping the intro.

    Returns the path to the downloaded file. Used for tempo detection before
    the user commits to a full download, so it deliberately keeps the source
    codec rather than transcoding to MP3.
    """
    out = Path(output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    ensure_ffmpeg_on_path()

    from yt_dlp.utils import download_range_func

    ydl_opts: dict = {
        'format': 'bestaudio/best',
        'outtmpl': str(out / '%(id)s.%(ext)s'),
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'extractor_args': EXTRACTOR_ARGS,
        'download_ranges': download_range_func(None, [(start, start + duration)]),
        'force_keyframes_at_cuts': False,
    }
    apply_soundcloud_auth(ydl_opts, url)

    js_runtimes = find_js_runtimes()
    if js_runtimes:
        ydl_opts['js_runtimes'] = js_runtimes

    ffmpeg_bin = os.environ.get('YTDL_FFMPEG_BINARY')
    if not ffmpeg_bin:
        try:
            import imageio_ffmpeg
            ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            pass
    if ffmpeg_bin:
        ydl_opts['ffmpeg_location'] = ffmpeg_bin

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='ytdl',
        description='Download YouTube audio as a 320 kbps MP3.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Examples:\n'
            '  python ytdl.py https://youtu.be/dQw4w9WgXcQ\n'
            '  python ytdl.py --output ~/Music https://youtu.be/dQw4w9WgXcQ\n'
        ),
    )
    parser.add_argument('url', help='YouTube URL to download')
    parser.add_argument(
        '--output', '-o',
        default='./downloads',
        metavar='DIR',
        help='Directory to save the MP3 (default: ./downloads)',
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    print(f"Fetching: {args.url}")
    try:
        output_file = download_audio(args.url, output_dir=args.output)
        print(f"Saved to: {output_file}")
    except yt_dlp.utils.DownloadError as exc:
        msg = str(exc).lower()
        if 'http error 403' in msg or 'forbidden' in msg:
            print(f"Error: {forbidden_hint(args.url)}", file=sys.stderr)
        elif 'unavailable' in msg or 'private' in msg:
            print("Error: Video is unavailable or private.", file=sys.stderr)
        elif 'unsupported url' in msg or 'not a valid url' in msg:
            print("Error: Invalid or unsupported URL.", file=sys.stderr)
        elif 'unable to download' in msg or 'network' in msg:
            print("Error: Network error — check your internet connection.", file=sys.stderr)
        else:
            print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        print(f"Unexpected error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
