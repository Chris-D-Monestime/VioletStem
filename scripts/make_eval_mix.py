"""Generate a lawful synthetic evaluation song with KNOWN reference stems.

Design (stereo 44.1 kHz, 12 s) — deliberately challenging for naive
center-cancellation, music-like enough for a neural separator:
- VOCAL: harmonic-complex melody w/ vibrato + syllable-like tremolo gating,
  center-panned with a stereo slap echo (side energy), silent during bar 3.
- ACCOMP: centered kick + bass (classic DSP trap: center low-end leaks into
  'vocals'), off-center hats, wide detuned pad, right-panned guitar riff in
  the vocal formant range.
Outputs: eval/vocal_ref.wav, eval/accomp_ref.wav, eval/mix.mp3 (128k, same
encoding path as user uploads).
"""
import os
import numpy as np

SR = 44100
DUR = 12.0
N = int(SR * DUR)
t = np.arange(N) / SR

VOWELS = {  # (F1,F2,F3,F4, BW) typical adult male-ish singing
    "a": (730, 1090, 2440, 3300, 90),
    "e": (530, 1840, 2480, 3400, 90),
    "i": (270, 2290, 3010, 3500, 100),
    "o": (570, 840, 2410, 3200, 90),
    "u": (300, 870, 2240, 3100, 100),
}

def resonator(x, freq, bw, sr=SR):
    r = float(np.exp(-np.pi * bw / sr))
    c = 2 * r * np.cos(2 * np.pi * freq / sr)
    y = np.zeros_like(x)
    for n in range(2, len(x)):
        y[n] = x[n] + c * y[n - 1] - r * r * y[n - 2]
    return y

def formant_note(f0, start, dur, vowel, rng, glide_from=None):
    n = int(dur * SR)
    tt = np.arange(n) / SR
    # f0 contour: portamento from previous note + vibrato
    f = np.full(n, f0)
    ng = min(int(0.08 * SR), n)
    if glide_from is not None and ng > 1:
        f[:ng] = np.linspace(glide_from, f0, ng)
    f = f * (1.0 + 0.008 * np.sin(2 * np.pi * 5.2 * tt + rng.uniform(0, 6)))
    ph = 2 * np.pi * np.cumsum(f) / SR
    src = np.zeros(n)
    for h in range(1, 25):  # bandlimited-ish saw source
        if h * f0 > 12000:
            break
        src += np.sin(h * ph + rng.uniform(0, 0.3)) / h
    src *= 0.6
    F = VOWELS[vowel]
    y = np.zeros(n)
    for i, Ff in enumerate(F[:4]):
        y += resonator(src, Ff, F[4]) * (1.0 / (1 + i * 0.7))
    y /= 4.0
    # consonant burst at onset (plosive/fricative-like noise)
    nc = min(int(0.05 * SR), n)
    burst = rng.standard_normal(nc)
    burst = resonator(resonator(burst, 2500, 600), 4500, 900) * 0.4
    y[:nc] += burst
    # natural envelope
    env = np.ones(n)
    na, nr = int(0.03 * SR), min(int(0.12 * SR), n)
    env[:na] = np.linspace(0, 1, na)
    env[-nr:] = np.linspace(1, 0, nr)
    y *= env
    # breath noise bed
    y += rng.standard_normal(n) * 0.004
    out = np.zeros(N)
    i0 = int(start * SR)
    out[i0:i0 + n] = y
    return out

def adsr(n, a=0.02, r=0.08):
    env = np.ones(n)
    na, nr = int(a * SR), int(r * SR)
    if na > 0:
        env[:na] = np.linspace(0, 1, na)
    if nr > 0:
        env[-nr:] = np.linspace(1, 0, nr)
    return env

def note(freq, start, dur, kind="bass"):
    n = int(dur * SR)
    tt = np.arange(n) / SR
    s = np.zeros(n)
    if kind == "bass":
        ph = 2 * np.pi * freq * tt
        s = np.sin(ph) + 0.4 * np.sin(2 * ph) + 0.2 * np.sin(3 * ph)
        s /= 1.6
    elif kind == "pad":
        for det in (-4, 3):
            f = freq * 2 ** (det / 1200)
            ph = 2 * np.pi * f * tt
            saw = 2 * ((ph / (2 * np.pi)) % 1.0) - 1.0
            s += saw / 2.0
        s *= 0.25
    elif kind == "guitar":
        ph = 2 * np.pi * freq * tt
        sq = np.sign(np.sin(ph) + 0.5 * np.sin(2 * ph)) * 0.5
        s = sq * (0.6 + 0.4 * np.sin(2 * np.pi * 6.0 * tt))
    s *= adsr(n)
    out = np.zeros(N)
    i0 = int(start * SR)
    out[i0:i0 + n] = s
    return out

def kick(start):
    n = int(0.25 * SR)
    tt = np.arange(n) / SR
    f = 150 * np.exp(-tt * 25) + 45
    s = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-tt * 18)
    out = np.zeros(N)
    i0 = int(start * SR)
    out[i0:i0 + n] = s * 0.9
    return out

def hat(start, open_=False):
    n = int((0.12 if open_ else 0.05) * SR)
    rng = np.random.default_rng(int(start * 1000) + 7)
    s = rng.standard_normal(n) * np.exp(-np.arange(n) / (n / 4))
    # crude highpass
    s = np.diff(s, prepend=0)
    out = np.zeros(N)
    i0 = int(start * SR)
    out[i0:i0 + n] = s * 0.25
    return out

def place(mono, pan):
    """pan 0=L..1=R constant-power. Returns (2, N)."""
    gL, gR = np.cos(pan * np.pi / 2), np.sin(pan * np.pi / 2)
    return np.stack([mono * gL, mono * gR])

def main():
    os.makedirs("eval", exist_ok=True)
    rng = np.random.default_rng(0)
    # --- vocal: formant-sung melody (silent 6-9 s: bar-3 pause) ---
    melody = [(329.63, 0.0, 0.9, "a"), (392.0, 1.0, 0.9, "e"),
              (440.0, 2.0, 0.9, "i"), (523.25, 3.0, 1.4, "o"),
              (440.0, 4.5, 0.7, "u"),
              (392.0, 9.0, 0.9, "a"), (523.25, 10.0, 1.5, "e")]
    vox = np.zeros(N)
    prev = None
    for f, st, d, v in melody:
        vox += formant_note(f, st, d, v, rng, glide_from=prev)
        prev = f
    vox *= 0.5
    vox_st = place(vox, 0.5)
    # slap echo -> side energy (DSP-adversarial)
    dly = int(0.23 * SR)
    echo = np.zeros_like(vox_st)
    echo[:, dly:] = vox_st[:, :-dly] * 0.3
    echo = echo[::-1]  # swap channels: echo answers on opposite side
    vox_st = vox_st + echo
    vox_st /= max(1.0, np.abs(vox_st).max() / 0.89)

    # --- accompaniment ---
    acc = np.zeros((2, N))
    for b in range(24):  # kick on beats (centered)
        acc += place(kick(b * 0.5), 0.5) * 0.8
    bassline = [(110, 0.0, 0.45), (130.81, 0.5, 0.45), (98.0, 1.0, 0.9),
                (110, 2.0, 0.45), (146.83, 3.0, 1.4), (130.81, 5.0, 0.9),
                (98.0, 6.0, 0.9), (110.0, 9.0, 0.9), (164.81, 10.0, 1.4)]
    bass = np.zeros(N)
    for f, st, d in bassline:
        bass += note(f, st, d, "bass")
    acc += place(bass * 0.5, 0.5)
    for h in range(48):  # hats 8ths, off-center
        acc += place(hat(h * 0.25, open_=(h % 4 == 3)), 0.75 if h % 2 else 0.25)
    for ch, st in [([220.0, 277.18, 329.63], 0.0), ([174.61, 220.0, 261.63], 3.0),
                   ([196.0, 246.94, 293.66], 6.0), ([220.0, 277.18, 329.63], 9.0)]:
        pad = sum(note(f, st, 2.9, "pad") for f in ch)
        acc += (place(pad, 0.2) + place(pad, 0.8)) * 0.5
    for i, f in enumerate([659.25, 587.33, 523.25, 587.33] * 3):
        acc += place(note(f, i * 0.9 + 0.1, 0.4, "guitar") * 0.35, 0.8)
    acc /= max(1.0, np.abs(acc).max() / 0.89)

    from backend.audio_utils import write_wav_stereo, ffmpeg_exe
    import subprocess
    write_wav_stereo("eval/vocal_ref.wav", vox_st, SR)
    write_wav_stereo("eval/accomp_ref.wav", acc, SR)
    write_wav_stereo("eval/mix.wav", vox_st + acc, SR)
    exe = ffmpeg_exe()
    r = subprocess.run([exe, "-y", "-hide_banner", "-loglevel", "error",
                        "-i", "eval/mix.wav", "-codec:a", "libmp3lame",
                        "-b:a", "128k", "eval/mix.mp3"],
                       capture_output=True, timeout=120)
    assert r.returncode == 0, r.stderr[:300]
    print("eval files written: vocal_ref.wav, accomp_ref.wav, mix.mp3 (12 s)")

if __name__ == "__main__":
    main()
