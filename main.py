#!/usr/bin/env python3
"""main.py — Standalone Windows app for YouTrax using a native window (pywebview)."""

import os
import sys
import threading

# When bundled by PyInstaller, resources live under sys._MEIPASS.
if getattr(sys, 'frozen', False):
    _base = sys._MEIPASS  # type: ignore[attr-defined]
else:
    _base = os.path.dirname(os.path.abspath(__file__))

os.environ['YTDL_TEMPLATE_FOLDER'] = os.path.join(_base, 'templates')

import webview  # pywebview

from flask import Flask, Response, jsonify, render_template, request

try:
    import yt_dlp
except ImportError:
    sys.exit("yt-dlp is required. Run: pip install yt-dlp")

from app import _jobs, _lock, _run_download, _load_settings, _save_settings

import json
import uuid
from pathlib import Path

flask_app = Flask(
    __name__,
    template_folder=os.environ['YTDL_TEMPLATE_FOLDER'],
)

# Will be set after webview.create_window()
_window = None

# Updated by JS via Api.set_unsaved_count() to avoid evaluate_js in closing handler
_unsaved_tab_count = 0


class Api:
    """Exposed to JavaScript as window.pywebview.api"""

    def pick_folder(self):
        """Open a native folder picker and return the chosen path."""
        if _window is None:
            return None
        result = _window.create_file_dialog(webview.FOLDER_DIALOG)
        if result:
            return result[0]

    def open_url(self, url: str):
        """Open a URL in the default system browser."""
        import subprocess
        subprocess.run(['start', '', url], shell=True, check=False)
        return None

    def set_unsaved_count(self, n: int):
        """Called by JS to keep Python informed of how many tabs have unsaved tags."""
        global _unsaved_tab_count
        _unsaved_tab_count = int(n)
        return None


@flask_app.route('/')
def index():
    return render_template('index.html')


@flask_app.get('/info')
def get_info():
    url = request.args.get('url', '').strip()
    if not url:
        return jsonify(error='URL is required'), 400
    try:
        ydl_opts = {'quiet': True, 'no_warnings': True, 'noplaylist': True}
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


@flask_app.post('/download')
def start_download():
    data = request.get_json(silent=True) or {}
    url = (data.get('url') or '').strip()
    default_dir = str(Path.home() / 'Downloads')
    output_dir = (data.get('output_dir') or '').strip() or default_dir
    tags = data.get('tags') or {}
    if not url:
        return jsonify(error='URL is required'), 400
    job_id = str(uuid.uuid4())
    threading.Thread(target=_run_download, args=(job_id, url, output_dir, tags), daemon=True).start()
    return jsonify(job_id=job_id)


@flask_app.get('/status/<job_id>')
def job_status(job_id: str):
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

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@flask_app.get('/artwork/search')
def artwork_search():
    import urllib.request, urllib.parse
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify(error='Query is required'), 400
    try:
        params = urllib.parse.urlencode({'term': q, 'media': 'music', 'limit': 12, 'entity': 'song'})
        url = f'https://itunes.apple.com/search?{params}'
        req = urllib.request.Request(url, headers={'User-Agent': 'youtrax/1.0'})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
        results = []
        seen = set()
        for item in data.get('results', []):
            img = item.get('artworkUrl100', '')
            if not img or img in seen:
                continue
            seen.add(img)
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


@flask_app.get('/settings')
def get_settings():
    return jsonify(_load_settings())


@flask_app.post('/settings')
def save_settings():
    data = request.get_json(silent=True) or {}
    _save_settings(data)
    return jsonify(ok=True)


@flask_app.get('/reveal/<job_id>')
def reveal_file(job_id: str):
    """Reveal the downloaded file in Windows Explorer."""
    import subprocess
    with _lock:
        job = _jobs.get(job_id)
    if not job or job['status'] != 'done' or not job.get('file'):
        return jsonify(error='File not ready'), 404
    subprocess.run(['explorer', '/select,', job['file']], check=False)
    return jsonify(ok=True)


PORT = 5173


def start_flask():
    flask_app.run(host='127.0.0.1', port=PORT, debug=False, use_reloader=False)


if __name__ == '__main__':
    # Start Flask server in background
    t = threading.Thread(target=start_flask, daemon=True)
    t.start()

    # Create a native window with the JS API exposed
    _window = webview.create_window(
        'YouTrax',
        f'http://localhost:{PORT}',
        js_api=Api(),
        width=1250,
        height=920,
        resizable=True,
        min_size=(800, 700),
    )

    def _on_closing():
        if _unsaved_tab_count > 0:
            def _ask():
                confirmed = _window.create_confirmation_dialog(
                    'Unsaved Downloads',
                    "You have tabs with tags that haven't been downloaded yet. Quit anyway?",
                )
                if confirmed:
                    _window.destroy()
            threading.Thread(target=_ask, daemon=True).start()
            return False
        return True

    _window.events.closing += _on_closing

    webview.start()
