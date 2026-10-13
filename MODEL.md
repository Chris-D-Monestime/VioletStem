# MODEL.md — Source-separation engine

## Selected engines

### 1. Demucs (primary, neural) — Meta Research, MIT License
- Family: Hybrid Transformer Demucs (`htdemucs` default; also `htdemucs_ft`, `hdemucs_mmi`).
- Source: https://github.com/facebookresearch/demucs (maintained).
- Verified installed versions: torch 2.5.1+cpu, torchaudio 2.5.1+cpu, demucs 4.0.1
  (Python 3.12, Windows, CPU). Weights: `htdemucs` ~80 MB, auto-downloaded on
  first use to the torch hub cache. Model: 42M params, 4 stems
  (drums, bass, other, vocals) @ 44.1 kHz stereo.
- **GPU variant (measured on RTX 4060 Laptop 8 GB, driver CUDA 12.3):**
  torch/torchaudio 2.5.1+cu121 (`requirements-ml-cuda.txt`). Same model, same
  weights, no code or quality change — quality re-verified identical
  (SI-SDR 21.11/11.24 dB vs 21.23/11.31 dB on CPU, numerical noise only).
- Input: MP3 decoded to stereo 44.1 kHz WAV. Output: 4 stems (drums, bass, other, vocals);
  **instrumental = drums + bass + other summed in time domain** (documented correct method —
  never a renamed single stem), vocals = vocals stem. Both written at model sample rate,
  verified readable, non-silent, duration-compatible.
- License: MIT (code). Model weights: released by Meta for research/production use;
  check repo for current weight terms before commercial deployment.
- Compute: CPU supported (slow: ~3–10× realtime on modern CPU for htdemucs);
  CUDA GPU recommended. RAM ≥ 4 GB. Weights ~80–150 MB, cached in torch hub dir.
- Install: `pip install torch --index-url https://download.pytorch.org/whl/cpu && pip install demucs`.
  Set `DEMUCS_MODEL`, `DEMUCS_DEVICE` in `.env`. App auto-uses it; `/api/engine` shows `kind: neural`.

### 2. DSP mid-side spectral engine (built-in fallback) — VioletStem original, same repo license
- Method: STFT (4096/1024 Hann) → Mid=(L+R)/2, Side=(L−R)/2 → soft vocal mask
  |Mid|²/(|Mid|²+|Side|²), exponent 1.5 + temporal smoothing → vocals = mask·Mid
  (center-panned), instrumental = original − vocals → overlap-add iSTFT.
  Implemented in `backend/engines/dsp_engine.py` with NumPy only.
- Honest limits: center-panned drums/bass leak into vocals; hard-panned vocals stay
  partly in instrumental. No accuracy guarantees. Always labeled `dsp-midside-v1`.
- Cost: negligible CPU, no downloads, no external transmission.

## Why this split
Demucs is the primary engine whenever torch+demucs are installed (the app
auto-selects it and reports `kind: neural`). The DSP engine remains only as an
offline fallback, honestly labeled `dsp-midside-v1`.

## Measured speed (same files, `scripts/bench_speed.py`; first job per process
adds ~4–5 s one-time model load — the running server loads once)

| audio | CPU 4 hilos | CPU 8 hilos | CPU 16 hilos | GPU RTX 4060 | VRAM pico |
|---|---|---|---|---|---|
| 2 s | — | 2.4 s (×1.20) | — | 0.2 s (×0.10) | 570 MB |
| 12 s | 11.1 s (×0.93) | 6.5 s (×0.54) | 4.7 s (×0.39) | **0.5 s (×0.04)** | 604 MB |
| 3 min | — | — | 55.2 s (×0.31) | **5.4 s (×0.03)** | 989 MB |
| 10 min | — | — | 171.6 s (×0.29) | no medido | — |
| 2×12 s concurrentes | — | — | 9.3 s pared | **1.1 s pared** | — |

CPU-optimierung: `TORCH_THREADS=16` en este 8C/16T (≈28% más rápido que 8 hilos
en job único y sin colapso con 2 concurrentes). Decode/IO ≈ 0.2–1.0 s
(despreciable). El modelo ya se reutiliza en memoria entre jobs (caché),
así que no hay recarga por trabajo. Conclusión: con GPU el cuello pasa a ser
la subida/descarga, no la inferencia; **sin compromiso de calidad**
(SI-SDR idéntico) y **sin cambiar de modelo**.

## Measured quality (SI-SDR vs known reference stems, same 12 s mix)
Lawful synthetic song (formant-sung vocal + kick/bass/hats/pad/guitar),
mixed and MP3-encoded through the production path (`scripts/make_eval_mix.py`,
`scripts/eval_separation.py`):

| engine | vocals SI-SDR | instrumental SI-SDR |
|---|---|---|
| DSP mid-side | 6.63 dB | **-12.57 dB** (worse than the unprocessed mix) |
| Demucs htdemucs | **21.23 dB** | **11.31 dB** |

Gap: +14.6 dB vocals, +23.9 dB instrumental. The DSP instrumental scores below
the copy baseline because `original − bad vocal estimate` injects the leaked
center low-end back as distortion — the exact failure users heard. These are
synthetic-mix figures (absolute values don't transfer 1:1 to commercial
music); Demucs's standing on the public MUSDB18 benchmark is documented in its
model card. Human listening confirmation on commercial tracks is pending owner
review — no listening claim is made here.

## Updating the model
Change `DEMUCS_MODEL` env var (e.g. `htdemucs_ft` for higher quality), restart.
No code change needed. Validate with `python -m pytest tests/ -v` and a listening check.

## External inference API (not configured)
No third-party API is used by default, so audio never leaves the server. To use one
(e.g. a hosted separator), add an engine module implementing
`separate(wav_in, vocals_out, instrumental_out, progress_cb)`, wire it in
`engine_factory.py`, document provider terms/privacy/cost, and keep keys in env only.
