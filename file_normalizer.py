"""file_normalizer.py — Normalize tracks already on disk (the Normalize Files panel).

Tracks dropped or chosen in the panel are analyzed straight away; Normalize
then applies the same Loudness settings that downloads use. One list of
tracks is kept for the whole app, shared by app.py and mac_app.py through
the blueprint below.
"""

from __future__ import annotations

import json
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Blueprint, jsonify, request

import loudness
from ytdl import SETTINGS_FILE, find_ffmpeg

bp = Blueprint('file_normalizer', __name__)

# Where "Replace originals" keeps the untouched files unless the user picks
# another folder (settings key normalize_files_backup_dir).
BACKUP_ROOT = Path.home() / 'Music' / 'YouTrax Originals'

_lock = threading.Lock()
_items: list[dict] = []
_processing = False
_analyzer = ThreadPoolExecutor(max_workers=3, thread_name_prefix='loudness-scan')


def _saved() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text())
    except Exception:
        return {}


def _folders(data: dict) -> tuple[Path | None, Path]:
    """(copy folder or None for "next to each track", backup folder)."""
    copy_dir = str(data.get('normalize_files_copy_dir') or '').strip()
    backup_dir = str(data.get('normalize_files_backup_dir') or '').strip()
    return (Path(copy_dir).expanduser() if copy_dir else None,
            Path(backup_dir).expanduser() if backup_dir else BACKUP_ROOT)


def _ffmpeg() -> str:
    return find_ffmpeg() or 'ffmpeg'


def _expand(paths: list[str]) -> list[Path]:
    """Audio files among *paths*, descending into folders."""
    found = []
    for raw in paths:
        p = Path(raw).expanduser()
        candidates = sorted(p.rglob('*')) if p.is_dir() else [p]
        for c in candidates:
            if c.is_file() and c.suffix.lower() in loudness.FILE_EXTS \
                    and not c.name.startswith(('.', '._')) and 'Normalized' not in c.parent.parts[-1:]:
                found.append(c.resolve())
    return found


def add_paths(paths: list[str]) -> int:
    """Queue new tracks for analysis; returns how many were added."""
    new = []
    with _lock:
        known = {i['path'] for i in _items}
        for p in _expand(paths):
            if str(p) in known:
                continue
            known.add(str(p))
            item = {'id': uuid.uuid4().hex[:10], 'path': str(p), 'name': p.name,
                    'folder': p.parent.name, 'status': 'analyzing',
                    'info': None, 'result': None, 'error': None}
            _items.append(item)
            new.append(item)
    for item in new:
        _analyzer.submit(_analyze, item)
    return len(new)


def _analyze(item: dict) -> None:
    try:
        info = loudness.analyze_file(_ffmpeg(), item['path'])
        with _lock:
            item.update(status='ready', info=info)
    except Exception as exc:  # noqa: BLE001 - report per track
        with _lock:
            item.update(status='error', error=str(exc))


def _action(item: dict, s: loudness.Settings) -> dict:
    """What Normalize would do to a ready track under settings *s*."""
    info = item['info']
    if info['marked'] is not None and abs(info['marked'] - s.target_lufs) < 0.05:
        return {'skip': True, 'note': 'Already normalized'}
    p = loudness.plan(info['lufs'], info['true_peak'], s)
    if p['gain_db'] == 0.0:
        return {'skip': True, 'note': 'Already at target'}
    return {'skip': False, **{k: round(v, 1) if isinstance(v, float) else v for k, v in p.items()}}


def _backup_path(src: Path, root: Path) -> Path:
    """A backup location under *root* that never overwrites an earlier backup."""
    dest = root / src.parent.name / src.name
    n = 2
    while dest.exists():
        dest = dest.with_name(f'{src.stem} ({n}){src.suffix}')
        n += 1
    return dest


def _process_one(item: dict, s: loudness.Settings, replace: bool,
                 copy_dir: Path | None, backup_root: Path) -> None:
    src = Path(item['path'])
    with _lock:
        item.update(status='working', error=None)
    backup = None
    try:
        if replace:
            backup = _backup_path(src, backup_root)
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, backup)
            dest = src
        elif copy_dir:
            # Grouped by source folder so same-named tracks from different
            # folders don't collide; re-running replaces the earlier copy.
            dest = copy_dir / src.parent.name / src.name
        else:
            dest = src.parent / 'Normalized' / src.name
        result = loudness.normalize_file(_ffmpeg(), str(src), str(dest), s, item['info'])
        result.update(output=str(dest), backup=str(backup) if replace else None)
        with _lock:
            item.update(status='done', result=result)
    except Exception as exc:  # noqa: BLE001
        # normalize_file only replaces the original once everything passed,
        # so on failure the original is intact and its backup is redundant.
        if backup is not None:
            backup.unlink(missing_ok=True)
        status = 'refused' if isinstance(exc, loudness.AlignmentError) else 'error'
        with _lock:
            item.update(status=status, error=str(exc))


def _process_all(items: list[dict], s: loudness.Settings, replace: bool,
                 copy_dir: Path | None = None, backup_root: Path = BACKUP_ROOT) -> None:
    global _processing
    try:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix='loudness-render') as ex:
            list(ex.map(lambda i: _process_one(i, s, replace, copy_dir, backup_root), items))
    finally:
        with _lock:
            _processing = False


@bp.get('/files')
def files_state():
    data = _saved()
    s = loudness.Settings.from_saved(data)
    copy_dir, backup_root = _folders(data)
    with _lock:
        rows = []
        for item in _items:
            row = dict(item)
            if item['status'] == 'ready':
                row['action'] = _action(item, s)
            rows.append(row)
        return jsonify(items=rows, processing=_processing,
                       copy_dir=str(copy_dir) if copy_dir else '', backup_root=str(backup_root),
                       default_backup_root=str(BACKUP_ROOT),
                       settings={'enabled': s.enabled, 'target': s.target_lufs,
                                 'ceiling': s.ceiling_dbtp, 'max_limit': s.max_limit_db,
                                 'pure_gain': s.pure_gain})


@bp.post('/files/add')
def files_add():
    paths = (request.get_json(silent=True) or {}).get('paths') or []
    return jsonify(added=add_paths([str(p) for p in paths]))


@bp.post('/files/remove')
def files_remove():
    data = request.get_json(silent=True) or {}
    with _lock:
        if _processing:
            return jsonify(error='Normalizing is in progress.'), 409
        if data.get('all'):
            _items.clear()
        elif data.get('finished'):
            _items[:] = [i for i in _items if i['status'] not in ('done', 'error', 'refused')]
        else:
            _items[:] = [i for i in _items if i['id'] != data.get('id')]
    return jsonify(ok=True)


@bp.post('/files/process')
def files_process():
    global _processing
    replace = (request.get_json(silent=True) or {}).get('mode') == 'replace'
    data = _saved()   # snapshot: changes made meanwhile apply to the next run
    s = loudness.Settings.from_saved(data)
    copy_dir, backup_root = _folders(data)
    with _lock:
        if _processing:
            return jsonify(error='Normalizing is already in progress.'), 409
        todo = [i for i in _items if i['status'] == 'ready' and not _action(i, s)['skip']]
        if not todo:
            return jsonify(error='Nothing to normalize.'), 400
        _processing = True
        for i in todo:
            i['status'] = 'queued'
    threading.Thread(target=_process_all, args=(todo, s, replace, copy_dir, backup_root),
                     daemon=True).start()
    return jsonify(count=len(todo))
