#!/usr/bin/env python3
"""ytdl.py — Download YouTube audio as 320kbps MP3 files."""

import argparse
import os
import re
import sys
from pathlib import Path

try:
    import yt_dlp
except ImportError:
    print("Error: yt-dlp is not installed. Run: pip install yt-dlp", file=sys.stderr)
    sys.exit(1)


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


def download_audio(
    url: str,
    output_dir: str = './downloads',
    progress_hook=None,
    verbose: bool = True,
) -> str:
    """
    Download *url* and save as a 320 kbps MP3 inside *output_dir*.

    Returns the absolute path to the saved MP3.
    Raises ``yt_dlp.utils.DownloadError`` on failure.
    """
    out = Path(output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    if progress_hook is None and verbose:
        progress_hook = make_cli_progress_hook()

    hooks = [progress_hook] if progress_hook else []

    ydl_opts: dict = {
        'format': 'bestaudio/best',
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
    }

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

        # 1. Best: postprocessor hook gave us the exact path yt-dlp wrote.
        if captured and Path(captured[-1]).exists():
            return captured[-1]

        # 2. Good: derive from yt-dlp's own filename logic + swap extension.
        expected = Path(ydl.prepare_filename(info)).with_suffix('.mp3')
        if expected.exists():
            return str(expected)

        # 3. Last resort: find the most recently modified .mp3 in the output dir.
        mp3s = sorted(out.glob('*.mp3'), key=lambda p: p.stat().st_mtime, reverse=True)
        if mp3s:
            return str(mp3s[0])

        raise FileNotFoundError(f"Could not locate the downloaded MP3 in {out}")


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
        if 'unavailable' in msg or 'private' in msg:
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
