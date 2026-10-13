# VioletStem — AI Music Separator (vocals + instrumental)

Dark-purple single-page app + FastAPI backend with real source separation.

## Engine strategy (honest)

- **Preferred: Demucs neural model** (`htdemucs`, Meta, MIT license) when `torch` + `demucs`
  are installed. The app auto-detects and uses it (`/api/engine` reports `kind: neural`).
- **Built-in fallback: real DSP mid-side spectral separation** (NumPy + bundled ffmpeg).
  Genuine signal processing on the actual upload — time-aligned vocals + instrumental WAVs,
  verified non-silent and duration-compatible. Quality is karaoke-grade, below Demucs.
  The active engine is always reported per job (`engine` field) and in the hero pill.

No mock processing: a job reports `completed` only after both outputs exist, are
readable audio, non-silent, and duration-compatible. Progress is honest stages
(queued → uploading → preparing → separating → finishing → completed); the bar is
an indeterminate indicator because the backend reports stages, not measured %.

## Quick start (Windows PowerShell)

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r requirements-ml.txt   # Demucs neural engine (torch CPU + weights)
Copy-Item .env.example .env   # optional; defaults work
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
# open http://127.0.0.1:8000
```

Health: `GET /api/health` (includes `jobs_active`, `retention_seconds`) · Engine: `GET /api/engine`

> **Security note for contributors:** never commit `.env`, `*.bak` backups,
> password files, or credential verifiers. Configure secrets only via local
> environment / untracked `.env` (see `.env.example` for the format). Test
> credentials in `tests/` are fake by design.

### Start / stop / restart
```powershell
# start (foreground, logs visible):
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
# stop: Ctrl+C, or: Stop-Process -Name python
# restart: stop, then start again. NOTE: jobs live in memory — after a restart
#   previous job IDs return 404 "Invalid or expired" (files orphaned on disk
#   under STORAGE_DIR/jobs/ are NOT auto-reclaimed until retention cleanup runs
#   for tracked jobs; remove unknown dirs manually if needed).
```

### Verify Demucs + weights
```powershell
python -c "from backend.engines import engine_factory; print(engine_factory.engine_status())"
# expect: {'active': 'demucs-htdemucs', 'kind': 'neural', ...}
# weights file: %USERPROFILE%\.cache\torch\hub\checkpoints\*.th (~80 MB)
```
First run with Demucs downloads weights automatically (~80 MB for `htdemucs`).

## Enable the neural (Demucs) engine

```powershell
pip install -r requirements-ml.txt   # pinned: torch 2.5.1+cpu, demucs 4.0.1
# optional: set DEMUCS_MODEL=htdemucs_ft and DEMUCS_DEVICE=cpu|cuda in .env
```

### GPU (optional, same model, ~13× faster, identical quality)
```powershell
pip install -r requirements-ml-cuda.txt  # torch 2.5.1+cu121 (needs NVIDIA driver with CUDA ≥12.1)
# .env: DEMUCS_DEVICE=cuda
# revert to CPU any time: pip install -r requirements-ml.txt  (+ DEMUCS_DEVICE=cpu)
```
If `cuda` is configured but unavailable, the app openly falls back to CPU
(reported in `/api/engine` and per job). CPU always stays functional.

Measured speed, same files (12 s / 3 min stereo; server loads the model once):

| audio | CPU 8 hilos | CPU 16 hilos | GPU RTX 4060 8 GB |
|---|---|---|---|
| 12 s | 6.5 s | 4.7 s | **0.5 s** |
| 3 min | — | 55 s | **5.4 s** |
| 10 min | — | 172 s | no medido |
| 2×12 s concurrentes | — | 9.3 s pared | **1.1 s pared** |

GPU quality re-verified identical (SI-SDR 21.11/11.24 vs 21.23/11.31 dB CPU).
`TORCH_THREADS` (default 0 = PyTorch default; 16 measured best on 8C/16T) applies
at startup. First job after start adds ~4–5 s one-time model load. Details: `MODEL.md`.

## Private tester access (disabled by default — external access stays OFF)

Local use needs nothing. To let one trusted tester in from another computer:

1. **Transport (choose one, do NOT enable yet without approval):**
   - *Recommended — Tailscale:* install Tailscale on host + tester PC, both join
     your tailnet (free personal plan), tester opens `http://<host-tailnet-ip>:8000`.
     True private network, no public URL. Tester installs the Tailscale app.
   - *Fallback — Cloudflare Quick Tunnel:* `cloudflared tunnel --url http://127.0.0.1:8000`
     gives a public random URL. Tester needs only a browser, but the URL is
     public — app authentication below is then MANDATORY, never the URL alone.
2. **App authentication (works with either transport):**
   ```powershell
   python -c "import hashlib; print(hashlib.sha256(b'CHOOSE-A-STRONG-PASSWORD').hexdigest())"
   # .env: AUTH_ENABLED=true, AUTH_USER=tester, AUTH_PASS_SHA256=<hex>
   # restart the server. The browser asks for user+password once; it then
   # protects the page, the API and all downloads. No passwords in the repo.
   ```
3. **Disable:** set `AUTH_ENABLED=false` (+ stop tunnel/Tailscale sharing) and restart.

Kept limits for the tester: MP3 only (extension + header + decode validated),
50 MB, 10 min, 2 concurrent jobs, 1 h retention + auto-cleanup, unguessable job
IDs (one tester cannot open another's jobs — IDs are never listed).
Admin/internal endpoints: none exist beyond `/api/*` (all auth-gated when on).
**If the host sleeps/loses Internet:** the tester sees connection errors; running
jobs fail; after restart old IDs 404 (in-memory registry) — re-upload needed.
Verified by `tests/test_auth.py` (5 tests). The server binds `127.0.0.1` only —
nothing here opens router ports or publishes anything.

## API

- `POST /api/separations` (multipart `file`, MP3) → `201 {job_id, status, ...}`
  (`400` bad audio · `413` over size · `429` rate-limited · `503` all workers busy)
- `GET /api/separations/{id}` → status + result metadata/URLs (`404` unknown/expired ID)
- `GET /api/separations/{id}/vocals` → WAV download (`409` until job completes)
- `GET /api/separations/{id}/instrumental` → WAV download
- `GET /api/separations/{id}/download-all` → ZIP with both
- `DELETE /api/separations/{id}` → cancel (if processing) or delete files
- `GET /api/health`, `GET /api/engine`

## Tests

```powershell
pip install -r requirements.txt
python -m pytest tests/ -v
```

- `tests/test_api.py`: full pipeline with real Demucs (upload → separation →
  downloads → ZIP), stems differ, durations compatible, invalid jobs 404,
  delete cleans files.
- `tests/test_hardening.py`: oversize 413, valid-header-but-undecodable 400,
  inference failure → `failed` (useful message, no stack-trace leak, 409 on
  downloads), consecutive-job independence, concurrency cap (3rd job → 503,
  then recovery), download bytes == stored labeled files, malicious IDs 404,
  retention expiry deletes files, restart forgets in-memory jobs (404) while
  health survives.
- `tests/test_auth.py`: local use stays open with auth off; with auth on every
  route (page/API/downloads) 401s without credentials, wrong creds rejected,
  correct creds complete a full Demucs job, guessed IDs still 404.
- `scripts/eval_separation.py`: objective SI-SDR/SDR vs known reference stems
  (DSP vs Demucs on the same lawful synthetic mix). No invented metrics.
- `scripts/measure_perf.py`: wall-time + memory on your machine.

## Configuration

See `.env.example`: `MAX_UPLOAD_MB`, `MAX_DURATION_SECONDS`, `STORAGE_DIR`,
`RETENTION_SECONDS`, `PROCESSING_TIMEOUT_SECONDS`, `MAX_CONCURRENT_JOBS`
(bounded thread pool; over-limit uploads fail fast with 503 — no waiting
queue by design, documented limit), `ENGINE`
(`PREFERRED_DEMUCS_FALLBACK_DSP`|`DSP_ONLY`), `DEMUCS_MODEL`,
`RATE_LIMIT_*`. No secrets in code; keys (if an external provider is ever
added) go in env only.

### Logs, errors & manual cleanup
- Request/error logs: uvicorn stdout. Per-job errors: `GET /api/separations/{id}`
  (`message` + `error`, never stack traces).
- Auto-cleanup: background thread deletes completed/failed/cancelled jobs older
  than `RETENTION_SECONDS` (default 3600). Active jobs are never deleted.
- Manual: `DELETE /api/separations/{id}` per job, or remove
  `STORAGE_DIR/jobs/<id>/` dirs (only for jobs already gone from the API).

### Minimum resources (measured here, not guessed)
CPU-only: 2+ cores, **4 GB RAM** (1.2 GB observed peak + headroom), ~80 MB
weights + ~1 GB packages, temp disk ≈ 3× largest song per concurrent job.
With GPU (RTX 4060 8 GB measured): +~1 GB VRAM worst case, minutes → seconds.

## Deployment

- Local/private server: `uvicorn backend.app:app --host 0.0.0.0 --port 8000` behind
  HTTPS (reverse proxy). Set `MAX_CONCURRENT_JOBS` to CPU capacity, retention ~1h.
- Docker: `docker compose up --build` (see `Dockerfile`, `docker-compose.yml`).
  NOTE: the Docker image currently installs only `requirements.txt` (no Demucs);
  for neural quality add `requirements-ml.txt` to the build (extra ~1 GB).
- Public hosting must support background threads + temp disk + (for Demucs) ≥4 GB RAM.
  Unauthenticated + unguessable job IDs + per-IP rate limits + quotas are built in;
  add your own WAF/auth for high exposure.

### Still missing for public launch (NOT implemented)
- No user accounts by design (unguessable IDs + rate limits instead) — assess
  abuse risk before opening to the Internet.
- No persistent job store: restart loses job registry (404s; files orphaned).
- No waiting queue: burst traffic gets 503 (client must retry).
- No HTTPS termination in-app (use reverse proxy), no centralized log files,
  no metrics/alerting, no automated backups (nothing worth backing up: temp audio only).

## Troubleshooting

- `ffmpeg` errors → `imageio-ffmpeg` bundles the binary via pip; ensure install succeeded.
- Slow separation on CPU → expected; use Demucs+GPU or shorter files.
- `503 Server is busy` → max concurrent jobs reached; wait/retry.
- `410 expired` → retention cleanup removed files; re-upload.
- Demucs not used → check `/api/engine`; install torch+demucs per MODEL.md.

## Limitations

- DSP fallback quality ≠ studio isolation; dense center-panned mixes leak.
- MP3 input only in v1 (WAV-ready pipeline; extension allowlist is one line).
- Outputs are WAV (no re-encoding loss); MP3 export not included.
