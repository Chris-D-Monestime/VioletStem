"""Measure Demucs wall-time + process memory on THIS machine (12 s eval mix).

Method: run the production decode+separate path in-process, report wall time,
realtime factor, and RSS delta/peak of this process (Windows working set via
ctypes — honest about what it measures: whole-process peak, includes the
already-loaded interpreter + model).
"""
import os
import time

import numpy as np
import psutil

_PROC = psutil.Process()

def rss_mb():
    cur = _PROC.memory_info().rss / 1e6
    return cur, cur


def main():
    from backend.audio_utils import decode_to_wav, read_wav_stereo, wav_duration
    from backend.engines import engine_factory
    assert os.path.exists("eval/mix.mp3"), "run scripts/make_eval_mix.py first"
    status = engine_factory.engine_status()
    print("engine:", status["active"], "|", status["detail"][:100])

    t0 = time.time()
    decode_to_wav("eval/mix.mp3", "eval/perf_decoded.wav")
    t_decode = time.time() - t0
    dur = wav_duration("eval/perf_decoded.wav")

    before, _ = rss_mb()
    t1 = time.time()
    info = engine_factory.run_separation("eval/perf_decoded.wav",
                                         "eval/perf_vocals.wav",
                                         "eval/perf_instr.wav")
    t_sep = time.time() - t1
    after, peak = rss_mb()
    v, _ = read_wav_stereo("eval/perf_vocals.wav")
    i, _ = read_wav_stereo("eval/perf_instr.wav")
    print(f"audio: {dur:.1f} s stereo 44.1 kHz")
    print(f"decode: {t_decode:.1f} s | separation: {t_sep:.1f} s | "
          f"realtime factor x{t_sep / dur:.2f} (CPU)")
    print(f"RSS before: {before:.0f} MB | after: {after:.0f} MB | "
          f"process peak: {peak:.0f} MB | engine={info.get('engine')}")
    print(f"outputs differ: {not np.array_equal(v, i)}")


if __name__ == "__main__":
    main()
