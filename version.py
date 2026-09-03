#!/usr/bin/env python3
"""version.py — Single source of truth for the app's own version.

The version lives in the top-level VERSION file. The build spec bundles that
file into the .app, so the same lookup works in a frozen build (next to the
executable under sys._MEIPASS) and in a source checkout (repo root).
release.sh is the only thing that ever changes VERSION.
"""

import os
import sys

_FALLBACK = '0.0.0'


def _version_path() -> str:
    if getattr(sys, 'frozen', False):
        base = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, 'VERSION')


def get_version() -> str:
    try:
        with open(_version_path(), encoding='ascii') as f:
            return f.read().strip() or _FALLBACK
    except OSError:
        return _FALLBACK


def get_changelog() -> str:
    """The bundled CHANGELOG.md, for the in-app What's New view."""
    path = os.path.join(os.path.dirname(_version_path()), 'CHANGELOG.md')
    try:
        with open(path, encoding='utf-8') as f:
            return f.read()
    except OSError:
        return ''


def parse(version: str) -> tuple:
    """'1.2.10' -> (1, 2, 10), for numeric comparison."""
    return tuple(int(p) for p in version.strip().lstrip('v').split('.'))


def is_newer(candidate: str, current: str) -> bool:
    try:
        return parse(candidate) > parse(current)
    except (ValueError, AttributeError):
        return False
