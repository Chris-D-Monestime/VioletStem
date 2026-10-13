"""Objective separation eval: SI-SDR / SDR of each engine vs known references.

Pipeline mirrors production: mix.mp3 -> decode_to_wav -> engine ->
estimates. Reports per-stem SI-SDR (dB) for DSP and Demucs, plus a
'copy' baseline (mixture scored as if it were each stem) to prove the
outputs are not the original, and a cross-check that the two outputs differ.
"""
import os
import numpy as np

def si_sdr(ref, est):
    ref = ref.astype(np.float64).ravel()
    est = est.astype(np.float64).ravel()
    n = min(len(ref), len(est))
    ref, est = ref[:n], est[:n]
    alpha = float(np.dot(est, ref) / (np.dot(ref, ref) + 1e-12))
    target = alpha * ref
    noise = est - target
    return 10 * np.log10(float(np.dot(target, target) + 1e-12) / float(np.dot(noise, noise) + 1e-12))

def sdr(ref, est):
    ref = ref.astype(np.float64).ravel()
    est = est.astype(np.float64).ravel()
    n = min(len(ref), len(est))
    ref, est = ref[:n], est[:n]
    noise = est - ref
    return 10 * np.log10(float(np.dot(ref, ref) + 1e-12) / float(np.dot(noise, noise) + 1e-12))

def run_engine(name, mix_wav, out_dir):
    from backend.engines import dsp_engine, demucs_engine
    from backend.audio_utils import read_wav_stereo
    os.makedirs(out_dir, exist_ok=True)
    v, i = os.path.join(out_dir, "vocals.wav"), os.path.join(out_dir, "instrumental.wav")
    if name == "dsp":
        info = dsp_engine.separate_dsp(mix_wav, v, i)
    else:
        info = demucs_engine.separate_demucs(mix_wav, v, i, model="htdemucs", device="cpu")
    ve, _ = read_wav_stereo(v)
    ie, _ = read_wav_stereo(i)
    return ve, ie, info

def main():
    from backend.audio_utils import decode_to_wav, read_wav_stereo
    assert os.path.exists("eval/mix.mp3"), "run scripts/make_eval_mix.py first"
    decode_to_wav("eval/mix.mp3", "eval/mix_decoded.wav")
    vref, sr = read_wav_stereo("eval/vocal_ref.wav")
    aref, _ = read_wav_stereo("eval/accomp_ref.wav")
    mixd, _ = read_wav_stereo("eval/mix_decoded.wav")

    print(f"copy baseline (mix as vocals):     SI-SDR={si_sdr(vref, mixd):7.2f} dB  SDR={sdr(vref, mixd):7.2f} dB")
    print(f"copy baseline (mix as instr):      SI-SDR={si_sdr(aref, mixd):7.2f} dB  SDR={sdr(aref, mixd):7.2f} dB")

    results = {}
    for name in ("dsp", "demucs"):
        ve, ie, info = run_engine(name, "eval/mix_decoded.wav", f"eval/out_{name}")
        n = min(ve.shape[1], ie.shape[1], vref.shape[1], aref.shape[1])
        m = {"voc_si": si_sdr(vref[:, :n], ve[:, :n]), "voc_sdr": sdr(vref[:, :n], ve[:, :n]),
             "ins_si": si_sdr(aref[:, :n], ie[:, :n]), "ins_sdr": sdr(aref[:, :n], ie[:, :n]),
             "diff": float(np.mean((ve[:, :n] - ie[:, :n]) ** 2))}
        results[name] = m
        print(f"{name}: vocals SI-SDR={m['voc_si']:7.2f} dB SDR={m['voc_sdr']:7.2f} dB | "
              f"instr SI-SDR={m['ins_si']:7.2f} dB SDR={m['ins_sdr']:7.2f} dB | engine={info.get('engine')}")
    dv = results["demucs"]["voc_si"] - results["dsp"]["voc_si"]
    di = results["demucs"]["ins_si"] - results["dsp"]["ins_si"]
    print(f"Demucs minus DSP: vocals {dv:+.2f} dB, instrumental {di:+.2f} dB")
    assert results["dsp"]["diff"] > 1e-8 and results["demucs"]["diff"] > 1e-8, "stems must differ"

if __name__ == "__main__":
    main()
