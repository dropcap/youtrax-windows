#!/usr/bin/env python3
"""app_updater.py — In-app updates for the packaged YouTrax.exe.

Windows counterpart of the macOS module: same public API and state machine,
so templates/index.html drives both without changes.

Release side: pushing a v* tag makes the GitHub Actions workflow attach the
built YouTrax.exe to a GitHub Release, so the latest-release API endpoint is
the update feed — nothing extra to publish.

App side:
  - check_for_update() asks the GitHub API for the latest release and compares
    its tag against the running version (version.py).
  - download_and_stage() fetches the release's YouTrax.exe into
    %LOCALAPPDATA%\\YouTrax\\app-update, verifying the sha256 digest when the
    API reports one.
  - install_and_restart() hands off to a detached batch helper that waits for
    this process to exit, copies the staged exe over the installed one, and
    relaunches. The caller is responsible for quitting the app right after.

Everything is best-effort and Windows-only; nothing here may break the running
app. Setting YOUTRAX_NO_APP_UPDATE=1 disables the whole mechanism, and
YOUTRAX_UPDATE_REPO overrides the GitHub repo for testing.
"""

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request

from version import get_version, is_newer

log = logging.getLogger(__name__)

_UA = 'YouTrax-app-updater/1.0'
_CONFIG_FILE = 'update_config.json'  # bundled by the spec

_lock = threading.Lock()
_state: dict = {'status': 'idle'}   # idle | checking | available | downloading
                                    # | staged | error | disabled | current


def _enabled() -> bool:
    return (
        sys.platform == 'win32'
        and getattr(sys, 'frozen', False)
        and not os.environ.get('YOUTRAX_NO_APP_UPDATE')
    )


def _github_repo() -> str | None:
    override = os.environ.get('YOUTRAX_UPDATE_REPO')
    if override:
        return override
    try:
        base = sys._MEIPASS if getattr(sys, 'frozen', False) \
            else os.path.dirname(os.path.abspath(__file__))  # type: ignore[attr-defined]
        with open(os.path.join(base, _CONFIG_FILE), encoding='utf-8') as f:
            return (json.load(f).get('github_repo') or '').strip() or None
    except Exception:
        return None


def _installed_exe_path() -> str | None:
    """Path of the running YouTrax.exe, wherever the user put it."""
    path = os.path.realpath(sys.executable)
    return path if path.lower().endswith('.exe') else None


def _staging_root() -> str:
    base = os.environ.get('LOCALAPPDATA', os.path.expanduser('~'))
    root = os.path.join(base, 'YouTrax', 'app-update')
    os.makedirs(root, exist_ok=True)
    return root


def get_state() -> dict:
    with _lock:
        return dict(_state)


def _set_state(**kwargs) -> None:
    with _lock:
        _state.clear()
        _state.update(kwargs)


def check_for_update(timeout: float = 15.0) -> dict:
    """Fetch the latest release and record whether a newer version exists."""
    if not _enabled():
        _set_state(status='disabled')
        return get_state()

    repo = _github_repo()
    if not repo:
        _set_state(status='disabled')
        return get_state()

    _set_state(status='checking')
    try:
        url = f'https://api.github.com/repos/{repo}/releases/latest'
        req = urllib.request.Request(
            url,
            headers={'User-Agent': _UA, 'Accept': 'application/vnd.github+json'},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            release = json.load(resp)

        latest = str(release.get('tag_name', '')).strip().lstrip('v')
        asset = next(
            (a for a in release.get('assets', [])
             if a.get('name', '').lower().endswith('.exe')),
            None,
        )
        if latest and asset and is_newer(latest, get_version()):
            # GitHub reports asset digests as "sha256:<hex>" when available.
            digest = str(asset.get('digest') or '')
            sha256 = digest.split(':', 1)[1] if digest.startswith('sha256:') else ''
            _set_state(
                status='available',
                version=latest,
                notes=release.get('body', ''),
                sha256=sha256,
                url=asset.get('browser_download_url', ''),
            )
        else:
            _set_state(status='current')
    except Exception as exc:
        log.warning('update check failed: %s', exc)
        _set_state(status='error', error=f'Update check failed: {exc}')
    return get_state()


def start_background_check() -> None:
    """Kick off a launch-time check without blocking startup."""
    if not _enabled() or not _github_repo():
        return
    threading.Thread(
        target=check_for_update, kwargs={'timeout': 30.0},
        daemon=True, name='youtrax-update-check',
    ).start()


def start_download() -> dict:
    """Begin downloading an available update in the background."""
    state = get_state()
    if state.get('status') != 'available':
        return state
    _set_state(status='downloading', version=state['version'])
    threading.Thread(
        target=download_and_stage, args=(state,),
        daemon=True, name='youtrax-update-download',
    ).start()
    return get_state()


def download_and_stage(state: dict | None = None, timeout: float = 600.0) -> dict:
    """Download the update exe, verify it, and stage it for install."""
    if state is None:
        state = get_state()
        if state.get('status') != 'available':
            return state
        _set_state(status='downloading', version=state['version'])
    url, expected_sha, version = state['url'], state['sha256'], state['version']
    if not url:
        _set_state(status='error', error='Update release has no download URL.')
        return get_state()

    staging = os.path.join(_staging_root(), version)
    staged_exe = os.path.join(staging, 'YouTrax.exe')
    try:
        os.makedirs(staging, exist_ok=True)
        req = urllib.request.Request(url, headers={'User-Agent': _UA})
        digest = hashlib.sha256()
        with urllib.request.urlopen(req, timeout=timeout) as resp, \
                open(staged_exe, 'wb') as out:
            while chunk := resp.read(1 << 20):
                out.write(chunk)
                digest.update(chunk)

        if expected_sha and digest.hexdigest() != expected_sha:
            raise ValueError('downloaded update failed its integrity check')

        with open(staged_exe, 'rb') as f:
            if f.read(2) != b'MZ':
                raise ValueError('staged update is not a valid executable')
        _set_state(status='staged', version=version, staged_app=staged_exe)
    except Exception as exc:
        log.warning('update download failed: %s', exc)
        shutil.rmtree(staging, ignore_errors=True)
        _set_state(status='error', error=f'Update download failed: {exc}')
    return get_state()


# The `ping 127.0.0.1` calls are 1-second sleeps: `timeout` refuses to run
# with redirected stdin, which is exactly how this helper is spawned.
_HELPER = r'''@echo off
setlocal
set "APID=%~1"
set "STAGED=%~2"
set "TARGET=%~3"
rem Wait for the app to exit (up to ~60s).
for /l %%i in (1,1,60) do (
  tasklist /fi "PID eq %APID%" 2>nul | findstr /b /c:"YouTrax" >nul || goto swap
  ping -n 2 127.0.0.1 >nul
)
:swap
rem Windows can hold the exe's lock briefly after exit; retry the copy.
for /l %%i in (1,1,30) do (
  copy /y "%STAGED%" "%TARGET%" >nul 2>&1 && goto done
  ping -n 2 127.0.0.1 >nul
)
exit /b 1
:done
for %%d in ("%STAGED%") do rd /s /q "%%~dpd" >nul 2>&1
start "" "%TARGET%"
'''


def install_and_restart() -> dict:
    """Spawn the swap helper. The app must exit promptly after this returns."""
    state = get_state()
    if state.get('status') != 'staged':
        return state
    target = _installed_exe_path()
    if not target:
        _set_state(status='error', error='Could not locate the installed app.')
        return get_state()

    try:
        fd, helper = tempfile.mkstemp(prefix='youtrax-update-', suffix='.bat')
        with os.fdopen(fd, 'w') as f:
            f.write(_HELPER)
        creationflags = (
            getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
            | getattr(subprocess, 'DETACHED_PROCESS', 0)
        )
        subprocess.Popen(
            ['cmd.exe', '/c', helper, str(os.getpid()), state['staged_app'], target],
            creationflags=creationflags,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        _set_state(status='installing', version=state['version'])
    except Exception as exc:
        log.warning('update install failed: %s', exc)
        _set_state(status='error', error=f'Update install failed: {exc}')
    return get_state()
