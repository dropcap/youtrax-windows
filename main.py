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

from app import (
    _jobs, _lock, _run_download, _load_settings, _save_settings, bpm_for_url,
    open_in_platinum_notes, reveal_in_explorer,
)
import app_updater
from version import get_changelog, get_version
from ytdl import EXTRACTOR_ARGS

import json
import urllib.request
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

PORT = 5173

# ── ytmp3:// URL scheme handler ──────────────────────────────────────────────
#
# Windows delivers a protocol click by launching the registered exe with the
# URL as an argument — a brand-new process. When YouTrax is already running,
# that second process forwards the URL to the live instance over localhost and
# exits; otherwise this process keeps it pending until the window has loaded.

# Holds a URL that arrived before the webview finished loading
_pending_url = None


def _inject_url(youtube_url: str):
    """Inject a YouTube URL into the app's input field and trigger Get Info."""
    if _window is None:
        return
    js = """
    (function() {{
        var inp = document.getElementById('url-input');
        var btn = document.getElementById('fetch-btn');
        if (inp && btn) {{
            inp.value = {url};
            btn.click();
        }}
    }})();
    """.format(url=json.dumps(youtube_url))
    _window.evaluate_js(js)


def _scheme_url_from_argv() -> str | None:
    for arg in sys.argv[1:]:
        if arg.startswith('ytmp3://'):
            return arg[len('ytmp3://'):]
    return None


def _forward_to_running_instance(youtube_url: str) -> bool:
    """Hand the URL to an already-running YouTrax, if one owns the port."""
    try:
        req = urllib.request.Request(
            f'http://127.0.0.1:{PORT}/inject-url',
            data=json.dumps({'url': youtube_url}).encode(),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.load(resp).get('ok', False)
    except Exception:
        return False


def _register_url_scheme() -> None:
    """Register ytmp3:// for the current user (no admin rights needed)."""
    if not getattr(sys, 'frozen', False):
        return
    try:
        import winreg

        exe = os.path.realpath(sys.executable)
        root = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\Classes\ytmp3')
        winreg.SetValueEx(root, None, 0, winreg.REG_SZ, 'URL:YouTrax Protocol')
        winreg.SetValueEx(root, 'URL Protocol', 0, winreg.REG_SZ, '')
        cmd = winreg.CreateKey(root, r'shell\open\command')
        winreg.SetValueEx(cmd, None, 0, winreg.REG_SZ, f'"{exe}" "%1"')
        winreg.CloseKey(cmd)
        winreg.CloseKey(root)
    except Exception:
        pass  # best effort — the scheme is a convenience, not a requirement


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
        import webbrowser
        webbrowser.open(url)
        return None

    def set_unsaved_count(self, n: int):
        """Called by JS to keep Python informed of how many tabs have unsaved tags."""
        global _unsaved_tab_count
        _unsaved_tab_count = int(n)
        return None


@flask_app.route('/')
def index():
    return render_template('index.html')


@flask_app.post('/inject-url')
def inject_url():
    """Receive a ytmp3:// payload forwarded by a second app instance."""
    if request.remote_addr not in ('127.0.0.1', '::1'):
        return jsonify(error='forbidden'), 403
    data = request.get_json(silent=True) or {}
    url = (data.get('url') or '').strip()
    if not url:
        return jsonify(error='URL is required'), 400
    _inject_url(url)
    return jsonify(ok=True)


@flask_app.get('/info')
def get_info():
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


@flask_app.get('/bpm')
def get_bpm():
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
    import urllib.parse
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify(error='Query is required'), 400
    try:
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


@flask_app.get('/api/version')
def api_version():
    return jsonify(version=get_version())


@flask_app.get('/api/changelog')
def api_changelog():
    return jsonify(changelog=get_changelog())


@flask_app.get('/api/update/state')
def api_update_state():
    return jsonify(app_updater.get_state())


@flask_app.post('/api/update/check')
def api_update_check():
    return jsonify(app_updater.check_for_update())


@flask_app.post('/api/update/download')
def api_update_download():
    return jsonify(app_updater.start_download())


@flask_app.post('/api/update/install')
def api_update_install():
    """Hand off to the swap helper, then quit so it can replace the exe."""
    state = app_updater.install_and_restart()
    if state.get('status') == 'installing':
        def _quit():
            import time
            time.sleep(0.8)  # let the HTTP response reach the UI first
            try:
                if _window is not None:
                    _window.destroy()
                else:
                    os._exit(0)
            except Exception:
                os._exit(0)
        threading.Thread(target=_quit, daemon=True).start()
    return jsonify(state)


@flask_app.get('/reveal/<job_id>')
def reveal_file(job_id: str):
    """Reveal the downloaded file in Windows Explorer."""
    with _lock:
        job = _jobs.get(job_id)
    if not job or job['status'] != 'done' or not job.get('file'):
        return jsonify(error='File not ready'), 404
    reveal_in_explorer(job['file'])
    return jsonify(ok=True)


@flask_app.get('/platinum/<job_id>')
def open_in_platinum(job_id: str):
    """Open the downloaded file in Platinum Notes for audio enhancement."""
    with _lock:
        job = _jobs.get(job_id)
    if not job or job['status'] != 'done' or not job.get('file'):
        return jsonify(error='File not ready'), 404
    if not open_in_platinum_notes(job['file']):
        return jsonify(error='Platinum Notes is not installed'), 404
    return jsonify(ok=True)


def start_flask():
    flask_app.run(host='127.0.0.1', port=PORT, debug=False, use_reloader=False)


if __name__ == '__main__':
    # A ytmp3:// click while YouTrax is already open lands here as a second
    # process: hand the URL to the running instance and bow out.
    _pending_url = _scheme_url_from_argv()
    if _pending_url and _forward_to_running_instance(_pending_url):
        sys.exit(0)

    _register_url_scheme()

    # See whether a newer yt-dlp exists and fetch it for the next launch, so
    # the frozen copy going stale never leaves the app broken for long.
    from ytdlp_updater import start_background_check
    start_background_check()

    # Likewise check for a newer YouTrax itself, so the UI can offer a
    # one-click in-app update instead of a manual re-download.
    app_updater.start_background_check()

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

    def _on_loaded():
        """If a ytmp3:// URL arrived before the window was ready, inject it now."""
        global _pending_url
        if _pending_url:
            _inject_url(_pending_url)
            _pending_url = None

    _window.events.loaded += _on_loaded

    def _on_closing():
        if _unsaved_tab_count > 0:
            # Run dialog off the main thread so we don't deadlock the close handler
            def _ask():
                confirmed = _window.create_confirmation_dialog(
                    'Unsaved Downloads',
                    "You have tabs with tags that haven't been downloaded yet. Quit anyway?",
                )
                if confirmed:
                    _window.destroy()
            threading.Thread(target=_ask, daemon=True).start()
            return False  # Prevent close for now; destroy() handles it if confirmed
        return True

    _window.events.closing += _on_closing

    webview.start()
