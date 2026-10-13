"""Audio helpers: ffmpeg binary resolution, probing, validation, wav IO."""
from __future__ import annotations
import os
import struct
import subprocess
import wave

def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"

def run_ffmpeg(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    exe = ffmpeg_exe()
    return subprocess.run([exe] + args, capture_output=True, timeout=timeout)

def is_valid_mp3_header(path: str) -> bool:
    """Check for ID3 or MPEG frame sync. Guards against fake extensions."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return False
    if len(head) < 4:
        return False
    if head[:3] == b"ID3":
        return True
    # scan for MPEG frame sync 0xFFE*
    for i in range(min(len(head) - 1, 2048)):
        if head[i] == 0xFF and (head[i + 1] & 0xE0) == 0xE0:
            return True
    return False

def probe_audio(path: str) -> dict:
    """Decode header via ffmpeg to wav/null to verify readability; get duration via ffprobe-like parse.
    Uses ffmpeg -i output parsing (works with imageio-ffmpeg binary)."""
    exe = ffmpeg_exe()
    try:
        p = subprocess.run([exe, "-hide_banner", "-i", path],
                           capture_output=True, text=True, timeout=30)
    except Exception as e:
        return {"readable": False, "error": str(e)}
    stderr = p.stderr or ""
    readable = ("Duration:" in stderr) or ("Audio:" in stderr) or ("Stream" in stderr)
    duration = None
    try:
        for line in stderr.splitlines():
            line = line.strip()
            if "Duration:" in line:
                # Duration: 00:00:05.12, ...
                part = line.split("Duration:")[1].split(",")[0].strip()
                h, m, s = part.split(":")
                duration = int(h) * 3600 + int(m) * 60 + float(s)
                break
    except Exception:
        duration = None
    return {"readable": readable, "duration": duration, "raw": stderr[:2000]}

def decode_to_wav(src: str, dst: str, sample_rate: int = 44100) -> None:
    """Decode any ffmpeg-readable audio to stereo 16-bit WAV."""
    r = run_ffmpeg(["-y", "-hide_banner", "-loglevel", "error",
                    "-i", src, "-ac", "2", "-ar", str(sample_rate),
                    "-sample_fmt", "s16", dst], timeout=180)
    if r.returncode != 0 or not os.path.exists(dst):
        raise RuntimeError(f"ffmpeg decode failed: {(r.stderr or b'')[:500]!r}")

def read_wav_stereo(path: str) -> tuple:
    """Return (float32 stereo array shape (2, N) in [-1,1], sample_rate)."""
    import numpy as np
    with wave.open(path, "rb") as w:
        n_ch = w.getnchannels()
        sr = w.getframerate()
        n = w.getnframes()
        sampwidth = w.getsampwidth()
        raw = w.readframes(n)
    if sampwidth == 1:
        import numpy as np
        arr = np.frombuffer(raw, dtype=np.uint8).astype(np.float32)
        arr = (arr - 128.0) / 128.0
    elif sampwidth == 2:
        import numpy as np
        arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    else:
        raise RuntimeError(f"Unsupported WAV sample width: {sampwidth}")
    import numpy as np
    arr = arr.reshape(-1, n_ch).T  # (ch, N)
    if n_ch == 1:
        arr = np.vstack([arr[0], arr[0]])
    elif n_ch > 2:
        arr = arr[:2]
    return arr, sr

def write_wav_stereo(path: str, data, sample_rate: int) -> None:
    """data: (2, N) float32 in [-1, 1]."""
    import numpy as np
    d = np.clip(np.asarray(data, dtype=np.float64), -1.0, 1.0)
    ints = (d * 32767.0).astype(np.int16)
    interleaved = ints.T.reshape(-1).tobytes()
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(interleaved)

def wav_duration(path: str) -> float:
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate() or 44100)

def safe_stem_name(original: str) -> str:
    base = os.path.basename(original or "song")
    if "." in base:
        base = base.rsplit(".", 1)[0]
    cleaned = "".join(c if (c.isalnum() or c in ("-", "_")) else "-" for c in base).strip("-_")
    return (cleaned or "song")[:60]
