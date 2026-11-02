"""loudness.py — Normalize downloads to a target loudness (EBU R128 / LUFS).

Ported from DJ Normalizer. Runs in place of yt-dlp's MP3 conversion: the
downloaded source audio is measured, then encoded to 320 kbps MP3 exactly
once with the gain (and, for quiet tracks, an oversampled peak limiter)
applied. Normalizing the finished MP3 instead would cost a second lossy
generation.

Only the ffmpeg binary is needed (no ffprobe), so it works with the static
build that imageio-ffmpeg bundles into the app.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Default target loudness for new installs; users change it in Settings.
DEFAULT_TARGET = -11.5

PRESETS = {'club': -8.0, 'standard': -10.0, 'mine': -11.5, 'dynamic': -14.0}


@dataclass
class Settings:
    enabled: bool = True
    target_lufs: float = DEFAULT_TARGET
    ceiling_dbtp: float = -1.0
    max_limit_db: float = 4.0       # most limiter gain reduction allowed
    pure_gain: bool = False         # never limit; quiet tracks rise only to the ceiling
    tolerance_lu: float = 0.5       # within this of target: leave the level alone

    @classmethod
    def from_saved(cls, data: dict) -> 'Settings':
        """Build from the app's settings.json, clamping anything out of range."""
        def num(key, default, lo, hi):
            try:
                return min(hi, max(lo, float(data.get(key, default))))
            except (TypeError, ValueError):
                return default
        return cls(
            enabled=bool(data.get('normalize', True)),
            target_lufs=num('normalize_target', DEFAULT_TARGET, -18.0, -6.0),
            ceiling_dbtp=num('normalize_ceiling', -1.0, -3.0, 0.0),
            max_limit_db=num('normalize_max_limit', 4.0, 0.0, 8.0),
            pure_gain=bool(data.get('normalize_pure_gain', False)),
        )


_LOUDNORM_JSON = re.compile(r'\{[^{}]*"input_i"[^{}]*\}', re.S)
_SAMPLE_RATE = re.compile(r'Audio: [^\n]*?, (\d+) Hz')


def measure(ffmpeg: str, path: str) -> tuple[float, float, int]:
    """Return (integrated LUFS, true peak dBTP, sample rate) for *path*."""
    proc = subprocess.run(
        [ffmpeg, '-hide_banner', '-nostats', '-i', path, '-map', '0:a:0',
         '-af', 'loudnorm=print_format=json', '-f', 'null', '-'],
        capture_output=True, text=True, **_no_window(),
    )
    m = _LOUDNORM_JSON.search(proc.stderr)
    if proc.returncode != 0 or not m:
        raise RuntimeError('Loudness analysis failed.')
    d = json.loads(m.group(0))
    lufs, tp = float(d['input_i']), float(d['input_tp'])
    if lufs < -70:
        raise RuntimeError('Track is silent.')
    sr = _SAMPLE_RATE.search(proc.stderr)
    return lufs, tp, int(sr.group(1)) if sr else 44100


def plan(lufs: float, true_peak: float, s: Settings) -> dict:
    """Gain and limiting needed to bring a track to the target."""
    gain = s.target_lufs - lufs
    if abs(gain) < s.tolerance_lu:
        return {'gain_db': 0.0, 'limit_db': 0.0, 'capped': False}
    headroom = s.ceiling_dbtp - true_peak
    if gain <= headroom:
        return {'gain_db': gain, 'limit_db': 0.0, 'capped': False}
    max_limit = 0.0 if s.pure_gain else s.max_limit_db
    if gain - headroom <= max_limit:
        return {'gain_db': gain, 'limit_db': gain - headroom, 'capped': False}
    # Not reachable within the limiter cap: raise only as far as it allows.
    return {'gain_db': max(0.0, headroom + max_limit), 'limit_db': max_limit, 'capped': True}


def _filter(p: dict, s: Settings, sample_rate: int) -> str | None:
    if abs(p['gain_db']) < 0.01:
        return None
    chain = [f"volume={p['gain_db']:.3f}dB"]
    if p['limit_db'] > 0:
        # 4x oversampling lets alimiter (a sample-peak limiter) hold true
        # peaks; the 0.3 dB margin covers what MP3 encoding adds back.
        ceiling = 10 ** ((s.ceiling_dbtp - 0.3) / 20)
        chain = [f'aresample={sample_rate * 4}', *chain,
                 f'alimiter=limit={ceiling:.5f}:level=false:attack=5:release=80:asc=1:latency=1',
                 f'aresample={sample_rate}']
    return ','.join(chain)


def encode_mp3(ffmpeg: str, src: str, dest: str, af: str | None = None,
               sample_rate: int | None = None) -> None:
    """Encode *src* to 320 kbps MP3 at *dest*, optionally through filter *af*."""
    tmp = str(Path(dest).with_name(f'.{Path(dest).stem}.normalizing.mp3'))
    cmd = [ffmpeg, '-hide_banner', '-nostats', '-loglevel', 'error', '-y',
           '-i', src, '-map', '0:a:0', '-map_metadata', '-1']
    if af:
        cmd += ['-af', af]
    if sample_rate:
        cmd += ['-ar', str(sample_rate)]
    cmd += ['-c:a', 'libmp3lame', '-b:a', '320k', tmp]
    proc = subprocess.run(cmd, capture_output=True, text=True, **_no_window())
    if proc.returncode != 0:
        Path(tmp).unlink(missing_ok=True)
        last = proc.stderr.strip().splitlines()[-1:] or ['ffmpeg failed']
        raise RuntimeError(f'MP3 encoding failed: {last[0]}')
    os.replace(tmp, dest)


def convert_to_mp3(ffmpeg: str, src: str, dest: str, s: Settings) -> dict:
    """Encode *src* to a 320 kbps MP3 at *dest*, normalized per *s*.

    Returns a summary for the UI: input and output loudness, the gain and
    limiting applied, and whether the target was out of reach.
    """
    lufs, tp, sr = measure(ffmpeg, src)
    p = plan(lufs, tp, s)
    encode_mp3(ffmpeg, src, dest, _filter(p, s, sr), sr)
    out_lufs, out_tp, _ = measure(ffmpeg, dest)

    # Limiting trims a little loudness from peaky tracks, so the first pass
    # can land short of target. Re-encode from the source (still a single
    # generation) with a little more gain, within the limiter cap.
    for _ in range(2):
        short = s.target_lufs - out_lufs
        room = s.max_limit_db - p['limit_db']
        if p['limit_db'] <= 0 or p['capped'] or short < 0.08 or room < 0.05:
            break
        bump = min(short, room)
        p['gain_db'] += bump
        p['limit_db'] += bump
        encode_mp3(ffmpeg, src, dest, _filter(p, s, sr), sr)
        out_lufs, out_tp, _ = measure(ffmpeg, dest)

    return {
        'target': s.target_lufs,
        'in_lufs': round(lufs, 1), 'in_true_peak': round(tp, 1),
        'out_lufs': round(out_lufs, 1), 'out_true_peak': round(out_tp, 1),
        'gain_db': round(p['gain_db'], 1), 'limit_db': round(p['limit_db'], 1),
        'capped': p['capped'],
    }


def _no_window() -> dict:
    """Keep ffmpeg from flashing a console window on Windows."""
    if os.name == 'nt':
        return {'creationflags': subprocess.CREATE_NO_WINDOW}
    return {}


# ---------------------------------------------------------------------------
# Existing files (the Normalize Files panel)
# ---------------------------------------------------------------------------
# Files already on disk keep their format, sample rate and quality, and every
# tag — including cue/beatgrid data stored in tags (e.g. Serato GEOB frames) —
# is copied across with mutagen rather than through ffmpeg.

FILE_EXTS = {'.mp3', '.m4a', '.flac', '.wav', '.aif', '.aiff'}

_AUDIO_STREAM = re.compile(r'Stream #\d+:\d+[^\n]*?: Audio: (\w+)([^\n]*)')


def analyze_file(ffmpeg: str, path: str) -> dict:
    """Loudness plus the stream details needed to re-encode *path* faithfully."""
    proc = subprocess.run(
        [ffmpeg, '-hide_banner', '-nostats', '-i', path, '-map', '0:a:0',
         '-af', 'loudnorm=print_format=json', '-f', 'null', '-'],
        capture_output=True, text=True, **_no_window(),
    )
    stream = _AUDIO_STREAM.search(proc.stderr)
    m = _LOUDNORM_JSON.search(proc.stderr)
    if proc.returncode != 0 or not m or not stream:
        raise RuntimeError('Could not read this file as audio.')
    d = json.loads(m.group(0))
    lufs, tp = float(d['input_i']), float(d['input_tp'])
    if lufs < -70:
        raise RuntimeError('Track is silent.')
    codec, rest = stream.group(1), stream.group(2)
    sr = re.search(r', (\d+) Hz', rest)
    kbps = re.search(r', (\d+) kb/s', rest)
    bits = re.search(r'\((\d+) bit\)', rest) or re.search(r'pcm_[su](\d+)', codec)
    dur = re.search(r'Duration: (\d+):(\d+):([\d.]+)', proc.stderr)
    return {
        'duration': int(dur.group(1)) * 3600 + int(dur.group(2)) * 60 + float(dur.group(3)) if dur else 0.0,
        'codec': codec,
        'sample_rate': int(sr.group(1)) if sr else 44100,
        'kbps': int(kbps.group(1)) if kbps else 0,
        'bits': int(bits.group(1)) if bits else 16,
        'lufs': round(lufs, 2), 'true_peak': round(tp, 2),
        'marked': read_marker(path),
    }


def _encoder_args(ext: str, info: dict) -> list[str]:
    hi_res = info['bits'] > 16
    if ext == '.mp3':
        kbps = info['kbps'] or 320
        return ['-c:a', 'libmp3lame', '-b:a', f"{320 if kbps >= 256 else max(192, kbps)}k"]
    if ext == '.m4a':
        if info['codec'] == 'alac':
            return ['-c:a', 'alac', '-sample_fmt', 's32p' if hi_res else 's16p']
        enc = 'aac_at' if sys.platform == 'darwin' else 'aac'
        return ['-c:a', enc, '-b:a', f"{min(320, max(256, info['kbps'] or 256))}k"]
    if ext == '.flac':
        return ['-c:a', 'flac', '-sample_fmt', 's32' if hi_res else 's16']
    if ext == '.wav':
        return ['-c:a', 'pcm_s24le' if hi_res else 'pcm_s16le']
    if ext in ('.aif', '.aiff'):
        return ['-c:a', 'pcm_s24be' if hi_res else 'pcm_s16be']
    raise RuntimeError(f'Unsupported format: {ext}')


def _render(ffmpeg: str, src: str, tmp: str, af: str | None, info: dict) -> None:
    ext = Path(src).suffix.lower()
    cmd = [ffmpeg, '-hide_banner', '-nostats', '-loglevel', 'error', '-y',
           '-i', src, '-map', '0:a:0', '-map_metadata', '-1']
    if af:
        cmd += ['-af', af]
    cmd += ['-ar', str(info['sample_rate']), *_encoder_args(ext, info)]
    if ext == '.m4a':
        cmd += ['-f', 'mp4']
    elif ext in ('.aif', '.aiff'):
        cmd += ['-f', 'aiff']
    cmd.append(tmp)
    proc = subprocess.run(cmd, capture_output=True, text=True, **_no_window())
    if proc.returncode != 0:
        last = proc.stderr.strip().splitlines()[-1:] or ['ffmpeg failed']
        raise RuntimeError(f'Encoding failed: {last[0]}')


def normalize_file(ffmpeg: str, src: str, dest: str, s: Settings, info: dict) -> dict:
    """Normalize *src* into *dest* (which may be *src* itself).

    The new audio is written to a temporary file beside *dest*, tags are
    copied from *src*, and only then is *dest* replaced, so a failure never
    leaves a half-written track behind.
    """
    p = plan(info['lufs'], info['true_peak'], s)
    sr = info['sample_rate']
    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(dest_path.with_name(f'.{dest_path.stem}.normalizing{dest_path.suffix}'))
    try:
        _render(ffmpeg, src, tmp, _filter(p, s, sr), info)
        out_lufs, out_tp, _ = measure(ffmpeg, tmp)
        for _ in range(2):   # same top-up as convert_to_mp3
            short = s.target_lufs - out_lufs
            room = s.max_limit_db - p['limit_db']
            if p['limit_db'] <= 0 or p['capped'] or short < 0.08 or room < 0.05:
                break
            bump = min(short, room)
            p['gain_db'] += bump
            p['limit_db'] += bump
            _render(ffmpeg, src, tmp, _filter(p, s, sr), info)
            out_lufs, out_tp, _ = measure(ffmpeg, tmp)
        verify_alignment(ffmpeg, src, tmp, info)
        copy_tags(src, tmp, s.target_lufs)
        os.replace(tmp, dest)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return {
        'target': s.target_lufs,
        'in_lufs': round(info['lufs'], 1), 'in_true_peak': round(info['true_peak'], 1),
        'out_lufs': round(out_lufs, 1), 'out_true_peak': round(out_tp, 1),
        'gain_db': round(p['gain_db'], 1), 'limit_db': round(p['limit_db'], 1),
        'capped': p['capped'],
    }


_MP4_MARKER = '----:com.apple.iTunes:DJNORM'


def read_marker(path: str) -> float | None:
    """Target (LUFS) recorded in the file's DJNORM tag, if it has one."""
    import mutagen
    try:
        f = mutagen.File(path)
    except Exception:
        return None
    if f is None or f.tags is None:
        return None
    for key in ('TXXX:DJNORM', 'DJNORM', 'djnorm', _MP4_MARKER):
        if key in f.tags:
            v = f.tags[key]
            v = v[0] if isinstance(v, list) else getattr(v, 'text', [v])[0]
            if isinstance(v, bytes):
                v = v.decode('utf-8', 'replace')
            m = re.search(r'-?\d+(\.\d+)?', str(v))
            return float(m.group(0)) if m else None
    return None


def copy_tags(src: str, dest: str, target: float) -> None:
    """Copy every tag (and artwork) from *src* to *dest*, plus the DJNORM marker."""
    import mutagen
    from mutagen.id3 import ID3, TXXX, ID3NoHeaderError

    marker = f'{target:.1f} LUFS'
    ext = Path(src).suffix.lower()

    if ext == '.mp3':
        try:
            tags = ID3(src)
        except ID3NoHeaderError:
            tags = ID3()
        tags.delall('TXXX:DJNORM')
        tags.add(TXXX(encoding=3, desc='DJNORM', text=marker))
        tags.save(dest, v2_version=4 if tags.version >= (2, 4, 0) else 3)
        return

    s_file, d_file = mutagen.File(src), mutagen.File(dest)
    if d_file.tags is None:
        d_file.add_tags()

    if ext in ('.wav', '.aif', '.aiff'):      # ID3 chunk
        if s_file.tags is not None:
            for frame in s_file.tags.values():
                d_file.tags.add(frame)
        d_file.tags.delall('TXXX:DJNORM')
        d_file.tags.add(TXXX(encoding=3, desc='DJNORM', text=marker))
    elif ext == '.flac':
        if s_file.tags is not None:
            for key in set(s_file.tags.keys()):
                d_file.tags[key] = s_file.tags[key]
        for pic in s_file.pictures:
            d_file.add_picture(pic)
        d_file.tags['DJNORM'] = marker
    elif ext == '.m4a':
        from mutagen.mp4 import MP4FreeForm
        if s_file.tags is not None:
            for key, value in s_file.tags.items():
                d_file.tags[key] = value
        d_file.tags[_MP4_MARKER] = [MP4FreeForm(marker.encode())]
    d_file.save()


# ---------------------------------------------------------------------------
# Timing check
# ---------------------------------------------------------------------------
# Cue points and beatgrids are stored as time positions, so a normalized file
# must line up with the original to the sample. Before a file is written, the
# new audio is cross-correlated with the original at the start and the middle
# of the track (catching both an offset and drift). ffmpeg honours the MP3
# gapless (LAME) header, but some DJ software ignores it, so for MP3s the
# lead-in each file would have without that header is compared as well.

class AlignmentError(RuntimeError):
    pass


def _decode(ffmpeg: str, path: str, sr: int, secs: float):
    import numpy as np
    cmd = [ffmpeg, '-v', 'error', '-i', path, '-map', '0:a:0', '-t', f'{secs:.3f}',
           '-ac', '1', '-ar', str(sr), '-f', 'f32le', '-']
    out = subprocess.run(cmd, capture_output=True, **_no_window()).stdout
    return np.frombuffer(out, dtype=np.float32)


def _lag(a, b, max_lag: int) -> int | None:
    """Samples by which *b* trails *a*; None when the window is near-silent."""
    import numpy as np
    n = min(len(a), len(b))
    if n < max_lag * 4:
        return None
    a, b = a[:n].astype(np.float64), b[:n].astype(np.float64)
    if np.sqrt(np.mean(a * a)) < 1e-4 or np.sqrt(np.mean(b * b)) < 1e-4:
        return None
    size = 1 << (2 * n - 1).bit_length()
    c = np.fft.irfft(np.fft.rfft(b, size) * np.conj(np.fft.rfft(a, size)), size)
    c = np.concatenate([c[-max_lag:], c[:max_lag + 1]])
    return int(np.argmax(c)) - max_lag


def _mp3_lead_in(path: str) -> int:
    """Samples a decoder that ignores the gapless header plays before the
    point where a gapless-aware decoder starts: the silent Xing/Info frame
    plus the LAME encoder delay and the 529-sample decoder delay. 0 when the
    file has no such header (both kinds of decoder then start in the same place)."""
    from mutagen.mp3 import MP3
    from mutagen.mp3._util import XingHeader
    info = MP3(path).info
    with open(path, 'rb') as fh:
        fh.seek(info.frame_offset + XingHeader.get_offset(info))
        data = fh.read(4 + 4 + 4 + 4 + 100 + 4 + 36)
    if data[:4] not in (b'Xing', b'Info'):
        return 0
    flags = int.from_bytes(data[4:8], 'big')
    pos = 8 + 4 * bool(flags & 1) + 4 * bool(flags & 2) + 100 * bool(flags & 4) + 4 * bool(flags & 8)
    lame = data[pos:pos + 36]
    frame = 1152 if info.version == 1 else 576
    if len(lame) < 24 or not lame[:4].isalnum():
        return frame                          # Xing frame only, no delay info
    delay = (lame[21] << 4) | (lame[22] >> 4)
    return frame + delay + 529


def verify_alignment(ffmpeg: str, src: str, out: str, info: dict) -> None:
    """Raise AlignmentError unless *out* lines up with *src* sample for sample."""
    sr = info['sample_rate']
    dur = info.get('duration') or 0.0
    window = 20.0
    starts = [0.0]
    if dur > window * 3:
        starts.append(dur / 2 - window / 2)
    checked = 0
    for start in starts:
        # Seeking is only sample-accurate when decoding from the top, so the
        # mid-track window is decoded from 0 and then sliced.
        a = _decode(ffmpeg, src, sr, start + window)[int(start * sr):]
        b = _decode(ffmpeg, out, sr, start + window)[int(start * sr):]
        lag = _lag(a, b, max_lag=int(sr * 0.1))
        if lag is None:
            continue
        checked += 1
        if lag != 0:
            where = 'the start' if start == 0 else 'mid-track'
            raise AlignmentError(
                f'Timing would shift by {abs(lag)} samples ({abs(lag) / sr * 1000:.1f} ms) at '
                f'{where}, which would move cue points. The original was left untouched.'
            )
    if Path(src).suffix.lower() == '.mp3':
        diff = _mp3_lead_in(out) - _mp3_lead_in(src)
        if diff:
            raise AlignmentError(
                f'This MP3 was made by a different encoder, so in DJ software that ignores '
                f'MP3 gapless info the timing would shift by {abs(diff)} samples '
                f'({abs(diff) / info["sample_rate"] * 1000:.1f} ms). '
                f'The original was left untouched.'
            )
    if not checked:
        raise AlignmentError('Could not verify timing (the audio is too quiet to compare). '
                             'The original was left untouched.')
