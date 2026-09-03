#!/usr/bin/env python3
"""ytdlp_updater.py — Keep the bundled yt-dlp current without rebuilding.

The packaged app freezes yt-dlp at build time, and YouTube changes its player
often enough that a frozen copy goes stale within weeks; the symptom is an
HTTP 403 on every download. yt-dlp is pure Python, so a newer release can
simply be placed ahead of the frozen one on sys.path:

  - activate() runs from a PyInstaller runtime hook, before any user code has
    imported yt_dlp. If a previously downloaded release newer than the bundled
    one exists under Application Support, its directory is prepended to
    sys.path; stale downloads (e.g. after installing a newer app build) are
    pruned.
  - start_background_check() asks PyPI for the latest version once per launch
    and downloads it if newer, so the *next* launch runs it. update_now() does
    the same synchronously; the 403 error path calls it so the failure message
    can say an update is already waiting.

Wheels are fetched from PyPI over HTTPS and verified against the sha256 digest
PyPI reports before anything is unpacked. Everything is best-effort: no
network, a bad download, or an unwritable disk must never break the app, which
always still has its frozen copy. Setting YTDL_NO_SELF_UPDATE=1 disables the
whole mechanism.
"""

import json
import logging
import os
import re
import sys
import threading

log = logging.getLogger(__name__)

_PYPI_URL = 'https://pypi.org/pypi/yt-dlp/json'
_VERSION_FILE = 'ytdlp_bundled_version.txt'  # written into the bundle by the spec
_VERSION_DIR_RE = re.compile(r'^\d+(\.\d+)*$')

_lock = threading.Lock()
_active_version: str | None = None   # version the running process imports
_checked = False                     # a check already ran this launch


def _enabled() -> bool:
    return getattr(sys, 'frozen', False) and not os.environ.get('YTDL_NO_SELF_UPDATE')


def _runtime_root() -> str:
    if sys.platform == 'darwin':
        base = os.path.expanduser('~/Library/Application Support/YouTrax')
    elif sys.platform == 'win32':
        base = os.path.join(
            os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'YouTrax'
        )
    else:
        base = os.path.join(
            os.environ.get('XDG_DATA_HOME', os.path.expanduser('~/.local/share')),
            'YouTrax',
        )
    return os.path.join(base, 'yt-dlp-runtime')


def _parse(version: str):
    return tuple(int(p) for p in version.strip().split('.'))


def _bundled_version() -> str | None:
    try:
        path = os.path.join(sys._MEIPASS, _VERSION_FILE)  # type: ignore[attr-defined]
        with open(path, encoding='ascii') as f:
            return f.read().strip()
    except Exception:
        return None


def _dir_version(path: str) -> str | None:
    """Version of the yt_dlp package under *path*, or None if it looks broken."""
    pkg = os.path.join(path, 'yt_dlp')
    if not os.path.isfile(os.path.join(pkg, '__init__.py')):
        return None
    try:
        with open(os.path.join(pkg, 'version.py'), encoding='utf-8') as f:
            match = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", f.read())
        return match.group(1) if match else None
    except Exception:
        return None


def _downloaded_releases() -> list[tuple[tuple, str, str]]:
    """Valid downloads as (parsed, version, path), best first."""
    root = _runtime_root()
    releases = []
    try:
        entries = os.listdir(root)
    except OSError:
        return []
    for name in entries:
        path = os.path.join(root, name)
        if not (_VERSION_DIR_RE.match(name) and os.path.isdir(path)):
            continue
        version = _dir_version(path)
        if version:
            try:
                releases.append((_parse(version), version, path))
            except ValueError:
                pass
    releases.sort(reverse=True)
    return releases


def _prune(keep: str | None) -> None:
    """Delete every runtime dir except *keep* (leftover downloads, tmp dirs)."""
    import shutil

    root = _runtime_root()
    try:
        entries = os.listdir(root)
    except OSError:
        return
    for name in entries:
        path = os.path.join(root, name)
        if path != keep:
            shutil.rmtree(path, ignore_errors=True)


def activate() -> None:
    """
    Prefer the best downloaded yt-dlp over the frozen one, if newer.

    Must run before anything imports yt_dlp — it only adjusts sys.path.
    """
    global _active_version

    if not _enabled():
        return
    try:
        bundled_version = _bundled_version()
        _active_version = bundled_version
        bundled = _parse(bundled_version) if bundled_version else ()

        for parsed, version, path in _downloaded_releases():
            if parsed > bundled:
                sys.path.insert(0, path)
                _active_version = version
                _prune(keep=path)
                log.info('using downloaded yt-dlp %s over bundled %s',
                         version, bundled_version)
                return
        _prune(keep=None)
    except Exception as exc:
        log.warning('yt-dlp self-update activation failed: %s', exc)


def _active() -> str | None:
    """The version this process actually runs, resolving lazily in dev mode."""
    if _active_version:
        return _active_version
    try:
        import yt_dlp
        return yt_dlp.version.__version__
    except Exception:
        return None


def pending_restart_version() -> str | None:
    """A downloaded version newer than the running one, if any."""
    active = _active()
    if not active:
        return None
    try:
        for parsed, version, _path in _downloaded_releases():
            if parsed > _parse(active):
                return version
    except Exception:
        pass
    return None


def _fetch_json(url: str, timeout: float):
    import urllib.request

    req = urllib.request.Request(url, headers={'User-Agent': 'YouTrax-updater/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _download_release(url: str, sha256: str, version: str, timeout: float) -> None:
    """Fetch a yt-dlp wheel, verify its digest, and unpack it for next launch."""
    import hashlib
    import io
    import shutil
    import urllib.request
    import zipfile

    req = urllib.request.Request(url, headers={'User-Agent': 'YouTrax-updater/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = resp.read()

    if hashlib.sha256(payload).hexdigest() != sha256:
        raise ValueError('wheel digest mismatch')

    root = _runtime_root()
    staging = os.path.join(root, f'{version}.partial-{os.getpid()}')
    final = os.path.join(root, version)
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging)
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as wheel:
            for member in wheel.namelist():
                # Only the package itself; refuse anything that could escape
                # the staging dir (absolute paths or ".." components).
                parts = member.split('/')
                if parts[0] != 'yt_dlp' or '..' in parts or member.startswith('/'):
                    continue
                wheel.extract(member, staging)
        # yt-dlp zero-pads its version (2026.08.19) where PyPI normalizes it
        # (2026.8.19), so compare numerically rather than as strings.
        extracted = _dir_version(staging)
        if not extracted or _parse(extracted) != _parse(version):
            raise ValueError('extracted wheel failed validation')
        shutil.rmtree(final, ignore_errors=True)
        os.replace(staging, final)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def update_now(timeout: float = 15.0) -> str | None:
    """
    Check PyPI and download a newer yt-dlp if one exists, synchronously.

    Returns the version now waiting for a restart (whether fetched by this
    call or earlier), or None when already current or anything failed.
    """
    global _checked

    if not _enabled():
        return None

    pending = pending_restart_version()
    if pending:
        return pending

    active = _active()
    if not active:
        return None

    try:
        data = _fetch_json(_PYPI_URL, timeout)
        latest = data['info']['version']
        if _parse(latest) <= _parse(active):
            return None

        wheel = next(
            f for f in data['urls'] if f['filename'].endswith('py3-none-any.whl')
        )
        _download_release(wheel['url'], wheel['digests']['sha256'], latest, timeout)
        log.info('downloaded yt-dlp %s (running %s); active after restart',
                 latest, active)
        return latest
    except Exception as exc:
        log.warning('yt-dlp update check failed: %s', exc)
        return None
    finally:
        with _lock:
            _checked = True


def start_background_check() -> None:
    """Check for a newer yt-dlp without blocking startup; at most once per run."""
    if not _enabled():
        return
    with _lock:
        global _checked
        if _checked:
            return
        _checked = True
    threading.Thread(
        target=update_now, kwargs={'timeout': 30.0},
        daemon=True, name='ytdlp-update-check',
    ).start()
