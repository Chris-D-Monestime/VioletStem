"""Speed benchmark: decode + model-load vs inference, configurable device/threads.

Usage: bench_speed.py <mp3> [--device cpu|cuda] [--threads N] [--warm 1]
Env TORCH_THREADS also honored. Prints one line per stage; cold load measured
by running in a fresh process (first separate_demucs call in this process).
"""
import os
import sys
import time

mp3 = sys.argv[1]
device = "cpu"
threads = int(os.environ.get("TORCH_THREADS", "0") or "0")
warm = 1
for i, a in enumerate(sys.argv[2:]):
    if a == "--device":
        device = sys.argv[3 + i]
    if a == "--threads":
        threads = int(sys.argv[3 + i])
    if a == "--warm":
        warm = int(sys.argv[3 + i])

import torch
if threads > 0:
    torch.set_num_threads(threads)
    torch.set_num_interop_threads(max(1, min(threads, 4)))

from backend.audio_utils import decode_to_wav, wav_duration
from backend.engines import demucs_engine

tag = os.path.basename(mp3)
t0 = time.time()
decode_to_wav(mp3, "eval/bench_dec.wav")
t_dec = time.time() - t0
dur = wav_duration("eval/bench_dec.wav")

# cold: first call includes model load
t0 = time.time()
demucs_engine.separate_demucs("eval/bench_dec.wav", "eval/bench_v.wav",
                              "eval/bench_i.wav", device=device)
t_cold = time.time() - t0
# warm repeats = pure inference
t_inf = 0.0
for _ in range(max(1, warm)):
    t0 = time.time()
    demucs_engine.separate_demucs("eval/bench_dec.wav", "eval/bench_v.wav",
                                  "eval/bench_i.wav", device=device)
    t_inf += time.time() - t0
t_inf /= max(1, warm)
vram = ""
if device.startswith("cuda") and torch.cuda.is_available():
    vram = f" vram_peak={torch.cuda.max_memory_allocated() / 1e6:.0f}MB"
print(f"[{tag}] device={device} threads={torch.get_num_threads()} "
      f"audio={dur:.1f}s decode={t_dec:.1f}s cold_first={t_cold:.1f}s "
      f"infer={t_inf:.1f}s rt=x{t_inf / dur:.2f}{vram}")
