"""Build longer timing mixes by tiling the 12 s eval mix (timing use only)."""
import subprocess
from backend.audio_utils import ffmpeg_exe, read_wav_stereo, write_wav_stereo
import numpy as np

v, sr = read_wav_stereo("eval/mix.wav")

def tile(path_wav, path_mp3, reps):
    long = np.tile(v, (1, reps))
    peak = float(np.abs(long).max())
    long = long * min(1.0, 0.89 / max(peak, 1e-6))
    write_wav_stereo(path_wav, long, sr)
    exe = ffmpeg_exe()
    r = subprocess.run([exe, "-y", "-hide_banner", "-loglevel", "error",
                        "-i", path_wav, "-codec:a", "libmp3lame", "-b:a", "128k",
                        path_mp3], capture_output=True, timeout=300)
    assert r.returncode == 0, r.stderr[:200]
    print(path_mp3, f"{long.shape[1] / sr:.0f}s")

tile("eval/mix3min.wav", "eval/mix3min.mp3", 15)
tile("eval/mix10min.wav", "eval/mix10min.mp3", 50)
