# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=2"]
# ///
"""Synthesize an original, royalty-free soundtrack with a known beat grid.

Usage:
    uv run editing/music.py editing/assets/music/renfest_reel.wav

The track is a 120 BPM "medieval festival" piece in A minor (i-VII-VI-VII): plucked lute,
bagpipe-style lead and drone, frame drum and modern drums. Every section starts on a bar line,
so an edit can cut on beats. A <name>.json sidecar records the bpm and section times.

Structure (bar = 2 s):
    bars 1-2   intro   drone + lute, shaker
    bars 3-6   build   + kick, bass, frame drum
    bars 7-8   riser   snare roll + noise sweep, gap on the last half beat
    bars 9-16  drop    full drums, pumping bass, bagpipe melody (crash on bar 9)
    bar 17     ending  final hit and reverb tail
"""

import json
import sys
from pathlib import Path

import numpy as np

SR = 44100
BPM = 120
BEAT = 60 / BPM
BAR = 4 * BEAT
BARS = 16
TAIL_S = 3.0
LENGTH_S = BARS * BAR + TAIL_S

RNG = np.random.default_rng(7)

A1, G1, F1 = 55.0, 49.0, 43.65
PROGRESSION = [  # (bass root Hz, lute chord tones as semitones from A4)
    (A1, [-12, -9, -5, 0]),   # Am: A3 C4 E4 A4
    (G1, [-14, -10, -7, -2]),  # G:  G3 B3 D4 G4
    (F1, [-16, -12, -9, -4]),  # F:  F3 A3 C4 F4
    (G1, [-14, -10, -7, -2]),
]


def hz(semitones_from_a4: float) -> float:
    return 440.0 * 2 ** (semitones_from_a4 / 12)


def t_of(n: int) -> np.ndarray:
    return np.arange(n) / SR


def add(buf: np.ndarray, sig: np.ndarray, at_s: float, gain: float = 1.0, pan: float = 0.0) -> None:
    """Mix a mono signal into a stereo buffer at a time, with constant-power pan (-1..1)."""
    i = int(round(at_s * SR))
    if i >= len(buf):
        return
    sig = sig[: len(buf) - i] * gain
    angle = (pan + 1) * np.pi / 4
    buf[i: i + len(sig), 0] += sig * np.cos(angle)
    buf[i: i + len(sig), 1] += sig * np.sin(angle)


def one_pole_lowpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    a = np.exp(-2 * np.pi * cutoff / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc = (1 - a) * v + a * acc
        y[i] = acc
    return y


def highpass(x: np.ndarray) -> np.ndarray:
    return np.diff(x, prepend=0.0)


# --- instruments -------------------------------------------------------------------------

def pluck(freq: float, dur: float, brightness: float = 0.5) -> np.ndarray:
    """Karplus-Strong string, computed one delay-line period at a time."""
    n = int(dur * SR)
    period = max(2, int(SR / freq))
    out = np.zeros(n + period)
    out[:period] = RNG.uniform(-1, 1, period)
    out[:period] = brightness * out[:period] + (1 - brightness) * np.convolve(out[:period], [0.5, 0.5], "same")
    decay = 0.996
    for start in range(period, n + period, period):
        prev = out[start - period: start]
        shifted = np.concatenate(([prev[-1]], prev[:-1]))
        block = decay * 0.5 * (prev + shifted)
        end = min(start + period, len(out))
        out[start:end] = block[: end - start]
    return out[:n] * np.exp(-t_of(n) * 2.5)


def saw(freq: np.ndarray | float, n: int, phase0: float = 0.0) -> np.ndarray:
    f = np.broadcast_to(freq, (n,))
    phase = phase0 + np.cumsum(f) / SR
    return 2 * (phase % 1.0) - 1


def bagpipe(freq: float, dur: float) -> np.ndarray:
    n = int(dur * SR)
    t = t_of(n)
    vibrato = 1 + 0.004 * np.sin(2 * np.pi * 5.5 * t)
    raw = 0.6 * saw(freq * vibrato, n) + 0.4 * np.sign(np.sin(2 * np.pi * freq * vibrato * t))
    nasal = highpass(raw) * 0.6 + raw * 0.4
    tone = one_pole_lowpass(nasal, 3200)
    attack = np.minimum(1, t / 0.015)
    release = np.minimum(1, (dur - t) / 0.03).clip(0)
    return tone * attack * release


def drone(freqs: list[float], dur: float) -> np.ndarray:
    n = int(dur * SR)
    sig = sum(saw(f * (1 + 0.0015 * i), n, phase0=0.3 * i) for i, f in enumerate(freqs))
    return one_pole_lowpass(sig / len(freqs), 900)


def kick() -> np.ndarray:
    n = int(0.45 * SR)
    t = t_of(n)
    f = 48 + 110 * np.exp(-t * 28)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 7)
    click = RNG.uniform(-1, 1, n) * np.exp(-t * 400) * 0.3
    return np.tanh(1.6 * (body + click))


def snare() -> np.ndarray:
    n = int(0.25 * SR)
    t = t_of(n)
    noise = highpass(RNG.uniform(-1, 1, n)) * np.exp(-t * 22)
    tone = np.sin(2 * np.pi * 190 * t) * np.exp(-t * 30)
    return 0.8 * noise + 0.5 * tone


def clap() -> np.ndarray:
    n = int(0.3 * SR)
    t = t_of(n)
    env = sum(np.exp(-np.clip(t - d, 0, None) * 60) * (t >= d) for d in (0, 0.011, 0.023))
    return highpass(RNG.uniform(-1, 1, n)) * env * np.exp(-t * 10) * 0.7


def hat(open_: bool = False) -> np.ndarray:
    n = int((0.25 if open_ else 0.05) * SR)
    t = t_of(n)
    return highpass(highpass(RNG.uniform(-1, 1, n))) * np.exp(-t * (14 if open_ else 90)) * 0.5


def frame_drum() -> np.ndarray:
    n = int(0.5 * SR)
    t = t_of(n)
    f = 85 + 40 * np.exp(-t * 20)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 6)
    skin = one_pole_lowpass(RNG.uniform(-1, 1, n), 1200) * np.exp(-t * 25)
    return body + 0.4 * skin


def crash() -> np.ndarray:
    n = int(2.5 * SR)
    t = t_of(n)
    return highpass(RNG.uniform(-1, 1, n)) * np.exp(-t * 1.6) * 0.5


def shaker() -> np.ndarray:
    n = int(0.08 * SR)
    t = t_of(n)
    return highpass(RNG.uniform(-1, 1, n)) * np.sin(np.pi * t / t[-1]) * 0.25


def riser(dur: float) -> np.ndarray:
    n = int(dur * SR)
    t = t_of(n)
    noise = RNG.uniform(-1, 1, n)
    sweep = np.zeros(n)
    acc = 0.0
    cut = 300 + 7000 * (t / dur) ** 2
    a = np.exp(-2 * np.pi * cut / SR)
    for i in range(n):
        acc = (1 - a[i]) * noise[i] + a[i] * acc
        sweep[i] = acc
    return sweep * (t / dur) ** 1.5 * 1.8


def bass_note(freq: float, dur: float) -> np.ndarray:
    n = int(dur * SR)
    t = t_of(n)
    tone = np.sin(2 * np.pi * freq * t) + 0.35 * saw(freq, n)
    tone = one_pole_lowpass(tone, 420)
    return tone * np.minimum(1, t / 0.01) * np.minimum(1, (dur - t) / 0.02).clip(0)


# --- melody (semitones from A4, beats) -----------------------------------------------------

MELODY = [
    [(7, 1), (12, 0.5), (10, 0.5), (7, 1), (5, 0.5), (3, 0.5)],      # Am: E5 A5 G5 E5 D5 C5
    [(5, 1), (2, 0.5), (5, 0.5), (10, 1), (5, 1)],                  # G:  D5 B4 D5 G5 D5
    [(3, 1), (0, 0.5), (3, 0.5), (8, 1), (7, 0.5), (5, 0.5)],        # F:  C5 A4 C5 F5 E5 D5
    [(2, 1), (5, 0.5), (10, 0.5), (7, 2)],                          # G:  B4 D5 G5 E5
]
MELODY_END = [(5, 1), (2, 1), (0, 2)]                               # resolve: D5 B4 A4


def reverb(x: np.ndarray, seconds: float = 1.8, mix: float = 0.18) -> np.ndarray:
    n = int(seconds * SR)
    t = t_of(n)
    ir = RNG.standard_normal((n, 2)) * np.exp(-t * 6.9 / seconds)[:, None]
    ir[: int(0.02 * SR)] = 0  # pre-delay
    size = 1 << int(np.ceil(np.log2(len(x) + n)))
    wet = np.stack([
        np.fft.irfft(np.fft.rfft(x[:, c], size) * np.fft.rfft(ir[:, c], size), size)[: len(x)]
        for c in range(2)
    ], axis=1)
    wet *= np.max(np.abs(x)) / (np.max(np.abs(wet)) + 1e-9)
    return x + mix * wet


def build() -> tuple[np.ndarray, dict]:
    total = int(LENGTH_S * SR)
    drums = np.zeros((total, 2))
    music = np.zeros((total, 2))
    bass = np.zeros((total, 2))
    kicks: list[tuple[float, float]] = []  # (time, gain)

    def bar_start(b: int) -> float:  # 1-based bar number
        return (b - 1) * BAR

    for b in range(1, BARS + 1):
        s = bar_start(b)
        root, chord = PROGRESSION[(b - 1) % 4]
        intro, build_, riser_, drop = b <= 2, 3 <= b <= 6, 7 <= b <= 8, b >= 9

        # Lute: eighth-note arpeggio throughout; sixteenths in the riser.
        step = BEAT / 4 if riser_ else BEAT / 2
        pattern = [0, 1, 2, 3, 2, 1, 2, 3]
        for i in range(int(BAR / step)):
            if riser_ and b == 8 and i * step >= 3.5 * BEAT:
                break
            note = chord[pattern[i % len(pattern)]] + (12 if drop and i % 4 == 3 else 0)
            add(music, pluck(hz(note), 0.9, 0.6), s + i * step, 0.32 if not drop else 0.24, pan=-0.35)

        # Drone on A + E under everything except the gap before the drop.
        if not drop:
            d = drone([110, 164.81, 220], BAR)
            fade = np.minimum(1, t_of(len(d)) / 1.5) if b == 1 else 1
            if b == 8:
                d = d * (t_of(len(d)) < 3.5 * BEAT)
            add(music, d * fade, s, 0.10)
        else:
            add(music, drone([110, 164.81], BAR), s, 0.07, pan=0.2)

        if intro:
            for i in range(8):
                add(drums, shaker(), s + i * BEAT / 2, 0.8 if i % 2 else 0.5, pan=0.4)
            add(drums, frame_drum(), s, 0.35)

        if build_ or riser_:
            for beat in (0, 2):
                kicks.append((s + beat * BEAT, 0.55))
            for beat in (1.5, 3.5):
                add(drums, frame_drum(), s + beat * BEAT, 0.5, pan=-0.2)
            for i in range(8):
                add(drums, hat(), s + i * BEAT / 2, 0.25 if i % 2 else 0.12, pan=0.3)
            for beat in range(4):
                note_len = BEAT * 0.9
                if riser_ and b == 8 and beat == 3:
                    break
                add(bass, bass_note(root * 2, note_len), s + beat * BEAT, 0.28)

        if riser_:
            div = 2 if b == 7 else 4
            for i in range(4 * div):
                at = s + i * BEAT / div
                if b == 8 and at >= s + 3.5 * BEAT:
                    break
                level = 0.15 + 0.5 * ((b - 7) * BAR + i * BEAT / div) / (2 * BAR)
                add(drums, snare(), at, level, pan=0.1)
            if b == 7:
                add(music, riser(2 * BAR - BEAT / 2), s, 0.22)
            if b == 8:
                kicks.extend([(s + BEAT, 0.6), (s + 3 * BEAT, 0.6)])

        if drop:
            if b == 9:
                add(drums, crash(), s, 0.55, pan=-0.3)
            if b == 13:
                add(drums, crash(), s, 0.4, pan=0.3)
            for beat in range(4):
                kicks.append((s + beat * BEAT, 1.0))
            for beat in (1, 3):
                add(drums, clap(), s + beat * BEAT, 0.7)
                add(drums, snare(), s + beat * BEAT, 0.35)
            for i in range(16):
                at = s + i * BEAT / 4
                if i % 4 == 2:
                    add(drums, hat(open_=True), at, 0.28, pan=0.35)
                elif i % 2 == 1:
                    add(drums, hat(), at, 0.18, pan=0.35)
            for i in range(8):  # offbeat-pumping eighth bass
                add(bass, bass_note(root * (2 if i % 2 == 0 else 4), BEAT / 2 * 0.85), s + i * BEAT / 2, 0.5)
            for beat in (0.75, 2.75):
                add(drums, frame_drum(), s + beat * BEAT, 0.3, pan=-0.3)

            phrase = MELODY[(b - 9) % 4] if b != 16 else MELODY[3][:2] + MELODY_END[1:]
            at = s
            for semis, beats in phrase:
                add(music, bagpipe(hz(semis), beats * BEAT * 0.96), at, 0.22, pan=0.15)
                add(music, bagpipe(hz(semis - 12), beats * BEAT * 0.96), at, 0.08, pan=-0.15)
                at += beats * BEAT

    # Ending hit on bar 17 beat 1.
    end = bar_start(BARS + 1)
    kicks.append((end, 1.0))
    add(drums, crash(), end, 0.6)
    add(bass, bass_note(A1 * 2, 2.0), end, 0.55)
    for semis in (-12, -9, -5, 0, 7):
        add(music, pluck(hz(semis), 2.5, 0.7), end + 0.01 * (semis + 12) / 12, 0.3, pan=-0.2)
    add(music, bagpipe(hz(0), 1.6) * np.linspace(1, 0, int(1.6 * SR)), end, 0.2)

    # Kicks, with sidechain-style ducking of the bass and music beds.
    duck = np.ones(total)
    k = kick()
    for at, gain in kicks:
        add(drums, k, at, gain)
        i = int(at * SR)
        n = min(int(0.22 * SR), total - i)
        duck[i: i + n] = np.minimum(duck[i: i + n], 1 - 0.65 * gain * np.exp(-t_of(n) * 14))
    bass *= duck[:, None]
    music *= (0.4 + 0.6 * duck)[:, None]

    mix = reverb(music, 2.2, 0.28) + reverb(drums, 0.9, 0.08) + bass
    mix = np.tanh(1.3 * mix / np.max(np.abs(mix)))
    mix *= 0.89 / np.max(np.abs(mix))  # about -1 dBFS peak
    fade_n = int(TAIL_S * SR)
    mix[-fade_n:] *= np.linspace(1, 0, fade_n)[:, None] ** 2

    grid = {
        "bpm": BPM,
        "beat_s": BEAT,
        "bar_s": BAR,
        "duration_s": LENGTH_S,
        "sections": [
            {"name": "intro", "start_s": bar_start(1), "end_s": bar_start(3)},
            {"name": "build", "start_s": bar_start(3), "end_s": bar_start(7)},
            {"name": "riser", "start_s": bar_start(7), "end_s": bar_start(9)},
            {"name": "drop", "start_s": bar_start(9), "end_s": bar_start(17)},
            {"name": "ending", "start_s": bar_start(17), "end_s": LENGTH_S},
        ],
    }
    return mix, grid


def write_wav(path: Path, stereo: np.ndarray) -> None:
    import wave

    pcm = (np.clip(stereo, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    out = Path(sys.argv[1])
    out.parent.mkdir(parents=True, exist_ok=True)
    mix, grid = build()
    write_wav(out, mix)
    out.with_suffix(".json").write_text(json.dumps(grid, indent=2))
    print(f"wrote {out} ({grid['duration_s']:.1f}s, {BPM} BPM) and {out.with_suffix('.json').name}")


if __name__ == "__main__":
    main()
