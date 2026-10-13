"""Concurrent throughput: 2 parallel separations in ONE process (like the server)."""
import os
import time
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("TORCH_THREADS", os.environ.get("TORCH_THREADS", "16"))
import torch
torch.set_num_threads(int(os.environ["TORCH_THREADS"]))

from backend.engines import demucs_engine
from backend.audio_utils import decode_to_wav

DEVICE = os.environ.get("BENCH_DEVICE", "cpu")
decode_to_wav("eval/mix.mp3", "eval/cc_dec.wav")
IN_WAV = "eval/cc_dec.wav"
demucs_engine.separate_demucs(IN_WAV, "eval/cc_v0.wav",
                              "eval/cc_i0.wav", device=DEVICE)  # warm/cache model
t0 = time.time()
with ThreadPoolExecutor(max_workers=2) as ex:
    f1 = ex.submit(demucs_engine.separate_demucs, IN_WAV,
                   "eval/cc_v1.wav", "eval/cc_i1.wav", "htdemucs", DEVICE)
    f2 = ex.submit(demucs_engine.separate_demucs, IN_WAV,
                   "eval/cc_v2.wav", "eval/cc_i2.wav", "htdemucs", DEVICE)
    f1.result()
    f2.result()
print(f"2x concurrent 12s jobs: {time.time() - t0:.1f}s wall "
      f"(threads={torch.get_num_threads()})")
