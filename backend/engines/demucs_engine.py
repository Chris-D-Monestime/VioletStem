"""Demucs neural separation engine (primary when dependencies installed).

Model: Meta Hybrid-Transformer Demucs (`htdemucs`, MIT license, 42M params),
4 stems: drums, bass, other, vocals @ 44.1 kHz stereo.
Instrumental = drums + bass + other summed in the TIME domain (documented
correct method — never the original, never a renamed single stem).
Vocals = vocals stem.

Outputs are jointly rescaled (single gain for both files) so relative stem
levels are preserved and neither file clips. Both files are verified by the
caller (readable, non-silent, duration-compatible).
"""
from __future__ import annotations

_MODEL_CACHE: dict = {}

def is_available() -> tuple[bool, str]:
    try:
        import torch  # noqa: F401
    except Exception as e:
        return False, f"torch not installed ({e})"
    try:
        import demucs  # noqa: F401
    except Exception as e:
        return False, f"demucs not installed ({e})"
    return True, "ok"

def _load_model(name: str, device: str):
    from demucs.pretrained import get_model
    key = (name, device)
    if key not in _MODEL_CACHE:
        model = get_model(name)
        model.to(device)
        model.eval()
        _MODEL_CACHE[key] = model
    return _MODEL_CACHE[key]

def separate_demucs(wav_in: str, vocals_out: str, instrumental_out: str,
                    model: str = "htdemucs", device: str = "cpu",
                    progress_cb=None) -> dict:
    import numpy as np
    import torch
    from demucs.apply import apply_model
    from demucs.audio import convert_audio
    from backend.audio_utils import read_wav_stereo, write_wav_stereo

    ok, reason = is_available()
    if not ok:
        raise RuntimeError(f"Demucs unavailable: {reason}")

    if progress_cb:
        progress_cb("preparing", f"Loading Demucs model '{model}' on {device}")
    m = _load_model(model, device)

    data, sr = read_wav_stereo(wav_in)  # float32 (2, N)
    wav = torch.from_numpy(np.ascontiguousarray(data)).float()
    if sr != m.samplerate or wav.shape[0] != m.audio_channels:
        wav = convert_audio(wav, sr, m.samplerate, m.audio_channels)

    if progress_cb:
        progress_cb("separating", "Separating with neural model (this may take a while on CPU)")
    with torch.no_grad():
        sources = apply_model(m, wav.unsqueeze(0).to(device),
                              device=device, progress=False)[0].cpu()  # (n_src, ch, T)

    names = [str(n).lower() for n in m.sources]
    if "vocals" not in names:
        raise RuntimeError(f"Model '{model}' produced no vocals stem (stems: {list(m.sources)})")
    vocals = sources[names.index("vocals")]
    others = [sources[i] for i, n in enumerate(names) if n != "vocals"]
    instrumental = torch.stack(others).sum(dim=0) if others else torch.zeros_like(vocals)

    # Joint rescale: one gain for both stems (preserves relative levels, no clipping)
    peak = float(max(vocals.abs().max().item(), instrumental.abs().max().item(), 1e-6))
    gain = min(1.0, 0.99 / peak)
    vocals = (vocals * gain).numpy()
    instrumental = (instrumental * gain).numpy()
    # ensure stereo float32 (2, N)
    def to_stereo(a):
        a = np.asarray(a, dtype=np.float32)
        if a.ndim == 1:
            a = np.stack([a, a])
        return a[:, :max(a.shape[1], 1)]
    vocals, instrumental = to_stereo(vocals), to_stereo(instrumental)
    n = min(vocals.shape[1], instrumental.shape[1])
    vocals, instrumental = vocals[:, :n], instrumental[:, :n]

    if progress_cb:
        progress_cb("finishing", "Writing output files")
    write_wav_stereo(vocals_out, vocals, m.samplerate)
    write_wav_stereo(instrumental_out, instrumental, m.samplerate)

    def rms(a):
        return float(np.sqrt(np.mean(a.astype(np.float64) ** 2) + 1e-12))
    return {"engine": f"demucs-{model}", "sample_rate": m.samplerate,
            "stems": list(m.sources), "vocal_rms": rms(vocals),
            "instrumental_rms": rms(instrumental)}
