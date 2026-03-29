#!/usr/bin/env python3
"""tagger.py — Fetch metadata from MusicBrainz + Cover Art Archive and write ID3 tags."""

import re
import time
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# MusicBrainz requires a descriptive User-Agent
_MB_USER_AGENT = ('ytdl-mp3-downloader', '1.0', 'https://github.com/local/ytdl')


# ---------------------------------------------------------------------------
# Title parsing
# ---------------------------------------------------------------------------

# Suffixes commonly appended to YouTube video titles
_NOISE = re.compile(
    r'\s*[\(\[](official\s*(music\s*)?video|official\s*audio|audio|lyrics?|'
    r'lyric\s*video|visualizer|hd|hq|4k|remaster(ed)?|extended|clip)[\)\]]',
    re.IGNORECASE,
)

def parse_title(video_title: str) -> tuple[str, str]:
    """
    Split a YouTube video title into (artist, track).

    Handles common patterns:
      "Artist - Title"
      "Artist – Title"   (en-dash)
      "Artist: Title"
    Falls back to (video_title, '') when no separator is found.
    """
    clean = _NOISE.sub('', video_title).strip()

    for sep in (' - ', ' – ', ': '):
        if sep in clean:
            parts = clean.split(sep, 1)
            return parts[0].strip(), parts[1].strip()

    return clean, ''


# ---------------------------------------------------------------------------
# MusicBrainz search
# ---------------------------------------------------------------------------

def _mb_setup():
    import musicbrainzngs as mb
    mb.set_useragent(*_MB_USER_AGENT)
    return mb


def search_musicbrainz(artist: str, title: str) -> Optional[dict]:
    """
    Search MusicBrainz for a recording matching artist + title.

    Returns a dict with keys: title, artist, album, date, release_id,
    track_number, total_tracks, label, or None if no match.
    """
    if not title:
        return None

    try:
        mb = _mb_setup()
        query = f'recording:"{title}"'
        if artist:
            query += f' AND artist:"{artist}"'

        result = mb.search_recordings(query=query, limit=5)
        recordings = result.get('recording-list', [])
        if not recordings:
            return None

        rec = recordings[0]
        release_list = rec.get('release-list', [])
        if not release_list:
            return None

        release = release_list[0]
        release_id = release.get('id', '')

        # Fetch full release for track number, label, etc.
        full = None
        try:
            time.sleep(1)  # MusicBrainz rate limit: 1 req/sec
            full = mb.get_release_by_id(
                release_id,
                includes=['labels', 'recordings', 'media'],
            ).get('release', {})
        except Exception as exc:
            log.warning('Could not fetch full release: %s', exc)

        # Track number within the release
        track_num = None
        track_total = None
        if full:
            for medium in full.get('medium-list', []):
                for track in medium.get('track-list', []):
                    if track.get('recording', {}).get('id') == rec['id']:
                        track_num = track.get('position')
                        track_total = medium.get('track-count')

        # Label
        label = None
        if full:
            label_info = full.get('label-info-list', [])
            if label_info:
                label = label_info[0].get('label', {}).get('name')

        return {
            'title': rec.get('title', title),
            'artist': artist or rec.get('artist-credit-phrase', ''),
            'album': release.get('title', ''),
            'date': release.get('date', ''),
            'release_id': release_id,
            'track_number': track_num,
            'total_tracks': track_total,
            'label': label,
        }

    except Exception as exc:
        log.warning('MusicBrainz search failed: %s', exc)
        return None


# ---------------------------------------------------------------------------
# Cover art
# ---------------------------------------------------------------------------

def fetch_cover_art(release_id: str) -> Optional[bytes]:
    """Fetch the front cover image from Cover Art Archive."""
    if not release_id:
        return None
    try:
        import urllib.request
        url = f'https://coverartarchive.org/release/{release_id}/front-500'
        req = urllib.request.Request(url, headers={'User-Agent': 'ytdl-mp3-downloader/1.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read()
    except Exception as exc:
        log.warning('Cover art fetch failed: %s', exc)
        return None


# ---------------------------------------------------------------------------
# ID3 tag writer
# ---------------------------------------------------------------------------

def apply_tags(mp3_path: str, metadata: dict, artwork: Optional[bytes] = None) -> None:
    """Write ID3 tags to the MP3 file using mutagen."""
    from mutagen.id3 import (
        ID3, ID3NoHeaderError,
        TIT2, TPE1, TALB, TDRC, TRCK, TPUB, TCON, APIC,
    )

    path = Path(mp3_path)
    if not path.exists():
        raise FileNotFoundError(f'MP3 not found: {mp3_path}')

    # Strip all pre-existing tags so only app-set tags end up in the file
    try:
        ID3(str(path)).delete()
    except ID3NoHeaderError:
        pass

    tags = ID3()

    if metadata.get('title'):
        tags['TIT2'] = TIT2(encoding=3, text=metadata['title'])
    if metadata.get('artist'):
        tags['TPE1'] = TPE1(encoding=3, text=metadata['artist'])
    if metadata.get('album'):
        tags['TALB'] = TALB(encoding=3, text=metadata['album'])
    if metadata.get('date'):
        tags['TDRC'] = TDRC(encoding=3, text=metadata['date'][:4])  # year only
    if metadata.get('label'):
        tags['TPUB'] = TPUB(encoding=3, text=metadata['label'])
    if metadata.get('genre'):
        tags['TCON'] = TCON(encoding=3, text=metadata['genre'])

    track = metadata.get('track_number')
    total = metadata.get('total_tracks')
    if track is not None:
        trck = str(track) if total is None else f'{track}/{total}'
        tags['TRCK'] = TRCK(encoding=3, text=trck)

    if artwork:
        tags['APIC'] = APIC(
            encoding=3,
            mime='image/jpeg',
            type=3,       # front cover
            desc='Cover',
            data=artwork,
        )

    tags.save(str(path), v2_version=3)
    log.info('Tags written to %s', path.name)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def tag_file(mp3_path: str, video_title: str) -> dict:
    """
    Full pipeline: parse title → search MusicBrainz → fetch art → write tags.

    Returns a summary dict describing what was applied.
    Always returns (never raises) so it doesn't block the download flow.
    """
    summary = {'tagged': False, 'artist': '', 'album': '', 'artwork': False}
    try:
        artist, track = parse_title(video_title)
        log.info('Searching MusicBrainz for: %r / %r', artist, track)

        metadata = search_musicbrainz(artist, track or artist)
        if not metadata:
            log.info('No MusicBrainz match found — skipping tags')
            return summary

        artwork = fetch_cover_art(metadata.get('release_id', ''))
        apply_tags(mp3_path, metadata, artwork)

        summary.update(
            tagged=True,
            artist=metadata.get('artist', ''),
            album=metadata.get('album', ''),
            artwork=artwork is not None,
        )
    except Exception as exc:
        log.warning('Tagging failed: %s', exc)

    return summary
