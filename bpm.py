#!/usr/bin/env python3
"""bpm.py — Estimate the tempo of an audio file.

Decodes with the bundled ffmpeg, builds a spectral-flux onset envelope, and
picks the tempo whose comb of harmonics best explains the envelope's
autocorrelation. Needs only numpy on top of ffmpeg, which the app already
ships, so it works offline inside the packaged .app.
"""

import logging
import math
import os
import subprocess

log = logging.getLogger(__name__)

SR = 22050          # analysis sample rate; ample for percussive onsets
N_FFT = 1024
HOP = 128
BLOCK = 2048        # STFT frames per chunk, to bound peak memory

MIN_BPM, MAX_BPM = 70.0, 200.0
BPM_STEP = 0.02

# Tempo prior: a log-Gaussian over BPM. Without it, a track's eighth-note
# pulse is often a stronger periodicity than its actual beat, and the
# estimate lands an octave high. Tuned against a labelled clip set.
PRIOR_CENTER, PRIOR_WIDTH = 120.0, 0.9

# Prefer the half-tempo reading unless the double scores clearly better.
HALF_PREFERENCE = 0.6

# Analysis window. A few minutes is plenty, and skipping the intro avoids
# beatless ambient openings that are common on extended mixes.
WINDOW_START = 30.0
WINDOW_LENGTH = 180.0

# Harmonics of the candidate period, and how much each one counts.
_COMB = ((1, 1.0), (2, 0.8), (3, 0.5), (4, 0.4), (6, 0.25), (8, 0.15))

# The app runs windowless on Windows; without this every ffmpeg spawn
# flashes a console window in front of the UI.
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0


def _ffmpeg() -> str:
    exe = os.environ.get('YTDL_FFMPEG_BINARY')
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return 'ffmpeg'


def _decode(path: str):
    """Decode the analysis window to mono float32 at SR."""
    import numpy as np

    cmd = [
        _ffmpeg(), '-v', 'quiet',
        '-ss', str(WINDOW_START), '-t', str(WINDOW_LENGTH),
        '-i', path, '-f', 'f32le', '-ac', '1', '-ar', str(SR), '-',
    ]
    raw = subprocess.run(
        cmd, capture_output=True, timeout=300, creationflags=_NO_WINDOW
    ).stdout

    # A track shorter than WINDOW_START decodes to nothing — retry from 0.
    if len(raw) < SR * 4 * 10:
        cmd[3] = '0'
        raw = subprocess.run(
            cmd, capture_output=True, timeout=300, creationflags=_NO_WINDOW
        ).stdout

    return np.frombuffer(raw, dtype=np.float32)


def _onset_envelope(y):
    """
    Spectral flux: total positive change in the magnitude spectrum per frame.

    Computed in blocks; materialising every frame at once costs hundreds of
    megabytes on a long track.
    """
    import numpy as np

    n_frames = 1 + (len(y) - N_FFT) // HOP
    if n_frames < 4:
        return np.zeros(0)

    window = np.hanning(N_FFT).astype(np.float32)
    out = np.empty(n_frames, dtype=np.float32)
    prev = None
    at = 0

    for start in range(0, n_frames, BLOCK):
        count = min(BLOCK, n_frames - start)
        offsets = HOP * np.arange(start, start + count)
        idx = offsets[:, None] + np.arange(N_FFT)[None, :]
        mag = np.abs(np.fft.rfft(y[idx] * window[None, :], axis=1))
        mag = np.log1p(mag * 100.0)          # compress dynamics

        if prev is not None:
            mag = np.vstack((prev, mag))
        flux = np.maximum(0.0, np.diff(mag, axis=0)).sum(axis=1)

        out[at:at + len(flux)] = flux
        at += len(flux)
        prev = mag[-1:]

    out = out[:at]
    return out - out.mean() if len(out) else out


def _autocorrelate(x):
    import numpy as np

    n = len(x)
    size = 1 << int(np.ceil(np.log2(2 * n)))
    spectrum = np.fft.rfft(x, size)
    ac = np.fft.irfft(spectrum * np.conj(spectrum), size)[:n]
    return ac / (ac[0] or 1.0)


def _best_tempo(ac, rate):
    """Score every candidate BPM by how well its harmonic comb fits *ac*."""
    import numpy as np

    bpms = np.arange(MIN_BPM, MAX_BPM + BPM_STEP / 2, BPM_STEP)
    lags = rate * 60.0 / bpms
    scores = np.zeros_like(bpms)

    for k, weight in _COMB:
        pos = lags * k
        usable = pos < len(ac) - 1
        low = np.floor(pos[usable]).astype(int)
        frac = pos[usable] - low
        # Interpolate so BPM precision is not limited by frame quantisation.
        scores[usable] += weight * (ac[low] * (1 - frac) + ac[low + 1] * frac)

    scores *= np.exp(-0.5 * (np.log2(bpms / PRIOR_CENTER) / PRIOR_WIDTH) ** 2)

    best = int(np.argmax(scores))
    half = bpms[best] / 2.0
    if half >= MIN_BPM:
        j = int(np.argmin(np.abs(bpms - half)))
        if scores[j] >= HALF_PREFERENCE * scores[best]:
            best = j

    return float(bpms[best]), float(scores[best])


def detect_bpm(path: str):
    """
    Estimate the tempo of *path*.

    Returns (bpm, confidence) with bpm as a whole number, or (None, 0.0) when
    the file cannot be analysed. Never raises — tempo is a nice-to-have and must
    not break a download.
    """
    try:
        import numpy as np  # noqa: F401
    except ImportError:
        log.warning('numpy unavailable — skipping BPM detection')
        return None, 0.0

    try:
        y = _decode(path)
        if len(y) < SR * 10:
            return None, 0.0

        envelope = _onset_envelope(y)
        rate = SR / HOP
        if len(envelope) < rate * 10:
            return None, 0.0

        bpm, confidence = _best_tempo(_autocorrelate(envelope), rate)
        # Reported as a whole number: what the tag stores and DJ software shows.
        return int(math.floor(bpm + 0.5)), round(confidence, 3)
    except Exception as exc:
        log.warning('BPM detection failed for %s: %s', path, exc)
        return None, 0.0


if __name__ == '__main__':
    import sys
    for arg in sys.argv[1:]:
        b, c = detect_bpm(arg)
        print(f'{b if b else "n/a":>8}  conf={c:5.3f}  {os.path.basename(arg)}')
