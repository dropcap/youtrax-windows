#!/usr/bin/env python3
"""app.py — Flask web UI for ytdl.py."""

import glob
import json
import os
import threading
import uuid
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_file
import app_updater
from version import get_changelog, get_version
from tagger import apply_tags
from bpm import detect_bpm
from ytdl import (
    EXTRACTOR_ARGS, download_excerpt, forbidden_hint, sanitize_filename,
)

SETTINGS_FILE = Path.home() / '.config' / 'ytdl' / 'settings.json'


def _load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text())
    except Exception:
        return {}


def _save_settings(data: dict) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    current = _load_settings()
    current.update(data)
    SETTINGS_FILE.write_text(json.dumps(current))


try:
    import yt_dlp
except ImportError as exc:
    raise SystemExit("yt-dlp is required. Run: pip install yt-dlp") from exc

from ytdl import download_audio

app = Flask(__name__)

# job_id -> job state dict
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Windows shell helpers
# ---------------------------------------------------------------------------

def reveal_in_explorer(path: str) -> None:
    """Open Windows Explorer with *path* selected."""
    import subprocess
    subprocess.run(['explorer', '/select,', os.path.normpath(path)], check=False)


def find_platinum_notes() -> str | None:
    """Locate the Platinum Notes executable, wherever it was installed."""
    roots = [
        os.environ.get('ProgramFiles', r'C:\Program Files'),
        os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'),
    ]
    for root in roots:
        matches = sorted(
            glob.glob(os.path.join(root, 'Platinum Notes*', '*.exe')),
            reverse=True,
        )
        exes = [m for m in matches if 'unins' not in os.path.basename(m).lower()]
        if exes:
            return exes[0]
    return None


def open_in_platinum_notes(path: str) -> bool:
    """Open *path* in Platinum Notes; returns False when it isn't installed."""
    import subprocess
    exe = find_platinum_notes()
    if not exe:
        return False
    subprocess.Popen([exe, os.path.normpath(path)], close_fds=True)
    return True


# ---------------------------------------------------------------------------
# Background download worker
# ---------------------------------------------------------------------------

def _run_download(job_id: str, url: str, output_dir: str, tags: dict) -> None:
    def progress_hook(d: dict) -> None:
        with _lock:
            job = _jobs[job_id]
            if d['status'] == 'downloading':
                downloaded = d.get('downloaded_bytes') or 0
                total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                speed = d.get('speed') or 0
                eta = d.get('eta') or 0
                job.update(
                    status='downloading',
                    downloaded=downloaded,
                    total=total,
                    speed=round(speed),
                    eta=eta,
                    percent=round(downloaded / total * 100, 1) if total else None,
                )
            elif d['status'] == 'finished':
                job['status'] = 'processing'

    with _lock:
        _jobs[job_id] = {
            'status': 'starting',
            'percent': 0,
            'downloaded': 0,
            'total': 0,
            'speed': 0,
            'eta': 0,
            'file': None,
            'filename': None,
            'bpm': None,
            'error': None,
        }

    try:
        output_file = download_audio(
            url, output_dir=output_dir, progress_hook=progress_hook, verbose=False
        )

        # Detect tempo unless the user typed one. The audio is already on
        # disk, so this costs a fraction of a second and no extra bandwidth.
        if not str(tags.get('bpm') or '').strip():
            with _lock:
                _jobs[job_id]['status'] = 'analyzing'
            bpm, _confidence = detect_bpm(output_file)
            if bpm:
                tags['bpm'] = bpm
                with _lock:
                    _jobs[job_id]['bpm'] = bpm

        # Apply user-supplied tags + fetch artwork if URL provided
        with _lock:
            _jobs[job_id]['status'] = 'tagging'

        artwork_bytes = None
        artwork_url = tags.pop('artwork_url', None)
        if artwork_url:
            try:
                if artwork_url.startswith('data:'):
                    # Base64-encoded image dragged from Explorer or browser
                    import base64
                    _, data = artwork_url.split(',', 1)
                    artwork_bytes = base64.b64decode(data)
                else:
                    import urllib.request
                    req = urllib.request.Request(
                        artwork_url, headers={'User-Agent': 'ytdl-mp3-downloader/1.0'}
                    )
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        artwork_bytes = resp.read()
            except Exception:
                pass

        apply_tags(output_file, tags, artwork=artwork_bytes)

        # Rename to "Title - Artist.mp3" using user-supplied tags
        title = (tags.get('title') or '').strip()
        artist = (tags.get('artist') or '').strip()
        if title and artist:
            new_name = sanitize_filename(f"{title} - {artist}") + '.mp3'
            old_path = Path(output_file)
            new_path = old_path.parent / new_name
            if new_path != old_path and not new_path.exists():
                old_path.rename(new_path)
                output_file = str(new_path)

        with _lock:
            _jobs[job_id].update(
                status='done',
                percent=100,
                file=output_file,
                filename=Path(output_file).name,
            )
    except yt_dlp.utils.DownloadError as exc:
        msg = str(exc).lower()
        if 'http error 403' in msg or 'forbidden' in msg:
            error = forbidden_hint()
        elif 'unavailable' in msg or 'private' in msg:
            error = 'Video is unavailable or private.'
        elif 'unsupported url' in msg or 'not a valid url' in msg:
            error = 'Invalid or unsupported URL.'
        elif 'network' in msg or 'unable to download' in msg:
            error = 'Network error — check your internet connection.'
        else:
            error = f'Download failed: {exc}'
        with _lock:
            _jobs[job_id].update(status='error', error=error)
    except Exception as exc:  # noqa: BLE001
        with _lock:
            _jobs[job_id].update(status='error', error=str(exc))


# ---------------------------------------------------------------------------
# Tempo preview
# ---------------------------------------------------------------------------

_bpm_cache: dict[str, float] = {}
_bpm_cache_lock = threading.Lock()
_BPM_CACHE_MAX = 200


def bpm_for_url(url: str):
    """
    Estimate a URL's tempo from a short excerpt, so the tag editor can be
    pre-filled before the user commits to a download.

    Cached per URL: re-opening the same track is free, and the download job
    reuses nothing here, so the cost is paid at most once per link.
    """
    with _bpm_cache_lock:
        if url in _bpm_cache:
            return _bpm_cache[url]

    import shutil
    import tempfile

    tmp = tempfile.mkdtemp(prefix='ytdl-bpm-')
    try:
        path = download_excerpt(url, tmp)
        bpm, _confidence = detect_bpm(path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if bpm:
        with _bpm_cache_lock:
            if len(_bpm_cache) >= _BPM_CACHE_MAX:
                _bpm_cache.clear()
            _bpm_cache[url] = bpm
    return bpm


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return render_template('index.html')


@app.get('/info')
def get_info():
    """Fetch YouTube video title + thumbnail without downloading."""
    url = request.args.get('url', '').strip()
    if not url:
        return jsonify(error='URL is required'), 400
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'extractor_args': EXTRACTOR_ARGS,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return jsonify(
            title=info.get('title', ''),
            thumbnail=info.get('thumbnail', ''),
            uploader=info.get('uploader', ''),
        )
    except yt_dlp.utils.DownloadError as exc:
        msg = str(exc).lower()
        if 'unavailable' in msg or 'private' in msg:
            return jsonify(error='Video is unavailable or private.'), 400
        if 'unsupported url' in msg or 'not a valid url' in msg:
            return jsonify(error='Invalid or unsupported URL.'), 400
        return jsonify(error=str(exc)), 400
    except Exception as exc:
        return jsonify(error=str(exc)), 400


@app.get('/bpm')
def get_bpm():
    """Estimate tempo for a URL without downloading the full track."""
    url = request.args.get('url', '').strip()
    if not url:
        return jsonify(error='URL is required'), 400
    try:
        bpm = bpm_for_url(url)
    except Exception as exc:
        return jsonify(error=str(exc)), 400
    if not bpm:
        return jsonify(error='Could not determine BPM'), 422
    return jsonify(bpm=bpm)


@app.post('/download')
def start_download():
    data = request.get_json(silent=True) or {}
    url = (data.get('url') or '').strip()
    default_dir = str(Path.home() / 'Downloads')
    output_dir = (data.get('output_dir') or '').strip() or default_dir
    tags = data.get('tags') or {}

    if not url:
        return jsonify(error='URL is required'), 400

    job_id = str(uuid.uuid4())
    threading.Thread(
        target=_run_download, args=(job_id, url, output_dir, tags), daemon=True
    ).start()
    return jsonify(job_id=job_id)


@app.get('/status/<job_id>')
def job_status(job_id: str):
    """Server-Sent Events stream for real-time progress."""
    import time

    def generate():
        while True:
            with _lock:
                job = _jobs.get(job_id)

            if job is None:
                yield f"data: {json.dumps({'status': 'not_found'})}\n\n"
                return

            payload = {k: v for k, v in job.items() if k != 'file'}
            yield f"data: {json.dumps(payload)}\n\n"

            if job['status'] in ('done', 'error'):
                return

            time.sleep(0.4)

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@app.get('/settings')
def get_settings():
    return jsonify(_load_settings())


@app.post('/settings')
def save_settings():
    data = request.get_json(silent=True) or {}
    _save_settings(data)
    return jsonify(ok=True)


@app.get('/artwork/search')
def artwork_search():
    """Search iTunes for album artwork. Returns list of results with image URLs."""
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify(error='Query is required'), 400
    try:
        import urllib.request, urllib.parse
        params = urllib.parse.urlencode({'term': q, 'media': 'music', 'limit': 12, 'entity': 'song'})
        url = f'https://itunes.apple.com/search?{params}'
        req = urllib.request.Request(url, headers={'User-Agent': 'ytdl-mp3-downloader/1.0'})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
        results = []
        seen = set()
        for item in data.get('results', []):
            img = item.get('artworkUrl100', '')
            if not img or img in seen:
                continue
            seen.add(img)
            # Upgrade to highest available resolution
            img_hq = img.replace('100x100bb', '600x600bb')
            results.append({
                'title':  item.get('trackName', ''),
                'artist': item.get('artistName', ''),
                'album':  item.get('collectionName', ''),
                'thumb':  img.replace('100x100bb', '300x300bb'),
                'full':   img_hq,
            })
        return jsonify(results=results)
    except Exception as exc:
        return jsonify(error=str(exc)), 500


@app.get('/api/version')
def api_version():
    return jsonify(version=get_version())


@app.get('/api/changelog')
def api_changelog():
    return jsonify(changelog=get_changelog())


@app.get('/api/update/state')
def api_update_state():
    return jsonify(app_updater.get_state())


@app.post('/api/update/check')
def api_update_check():
    return jsonify(app_updater.check_for_update())


@app.post('/api/update/download')
def api_update_download():
    return jsonify(app_updater.start_download())


@app.post('/api/update/install')
def api_update_install():
    # In-app install only exists in the packaged Windows app (main.py).
    return jsonify(app_updater.get_state())


@app.get('/reveal/<job_id>')
def reveal_file(job_id: str):
    with _lock:
        job = _jobs.get(job_id)
    if not job or job['status'] != 'done' or not job.get('file'):
        return jsonify(error='File not ready'), 404
    reveal_in_explorer(job['file'])
    return jsonify(ok=True)


@app.get('/platinum/<job_id>')
def open_in_platinum(job_id: str):
    """Open the downloaded file in Platinum Notes for audio enhancement."""
    with _lock:
        job = _jobs.get(job_id)
    if not job or job['status'] != 'done' or not job.get('file'):
        return jsonify(error='File not ready'), 404
    if not open_in_platinum_notes(job['file']):
        return jsonify(error='Platinum Notes is not installed'), 404
    return jsonify(ok=True)


if __name__ == '__main__':
    import os as _os

    # No-op outside a frozen build; there, fetch a newer yt-dlp for next launch.
    from ytdlp_updater import start_background_check
    start_background_check()

    app.run(debug=True, port=int(_os.environ.get('PORT', 5000)))
