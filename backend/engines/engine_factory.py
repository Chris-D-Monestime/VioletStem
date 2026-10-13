"""Engine selection with honest reporting. Demucs (neural) is primary whenever
its dependencies are installed; the built-in DSP engine is the fallback."""
from __future__ import annotations
from backend.config import settings

_configured_device = settings.demucs_device
_resolved_device = _configured_device
_device_fell_back = False

try:
    import torch as _torch
    _torch_cuda = bool(_torch.cuda.is_available())
except Exception:
    _torch_cuda = False

if _configured_device.startswith("cuda") and not _torch_cuda:
    # CPU stays functional: fall back openly (reported in status + job engine).
    _resolved_device = "cpu"
    _device_fell_back = True


def resolved_device() -> str:
    return _resolved_device

def engine_status() -> dict:
    from backend.engines import demucs_engine
    ok, reason = demucs_engine.is_available()
    preferred = settings.engine
    dev = _resolved_device
    if ok and preferred != "DSP_ONLY":
        detail = f"Demucs '{settings.demucs_model}' on {dev}"
        if _device_fell_back:
            detail += f" (configured '{_configured_device}' unavailable, fell back to CPU)"
        return {"active": f"demucs-{settings.demucs_model}", "kind": "neural",
                "device": dev,
                "demucs_available": True, "detail": detail}
    if ok and preferred == "DSP_ONLY":
        return {"active": "dsp-midside-v1", "kind": "dsp", "demucs_available": True,
                "detail": "DSP forced by ENGINE=DSP_ONLY (Demucs installed but not used)"}
    return {"active": "dsp-midside-v1", "kind": "dsp", "demucs_available": False,
            "detail": f"Demucs unavailable ({reason}). Using built-in DSP separation. "
                      f"Install torch+demucs (requirements-ml.txt). See MODEL.md."}

def run_separation(wav_in: str, vocals_out: str, instrumental_out: str, progress_cb=None) -> dict:
    from backend.engines import demucs_engine
    from backend.engines import dsp_engine
    ok, _ = demucs_engine.is_available()
    if ok and settings.engine != "DSP_ONLY":
        # Demucs attempted; on failure surface the honest error, never fake success.
        info = demucs_engine.separate_demucs(
            wav_in, vocals_out, instrumental_out,
            model=settings.demucs_model, device=_resolved_device,
            progress_cb=progress_cb)
        info["device"] = _resolved_device
        return info
    return dsp_engine.separate_dsp(wav_in, vocals_out, instrumental_out, progress_cb)
