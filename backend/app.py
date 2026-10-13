"""VioletStem backend — FastAPI app: upload validation, job API, downloads, health."""
from __future__ import annotations
import os
import threading
import time
from collections import defaultdict

from fastapi import FastAPI, File, UploadFile, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.config import settings, BRAND
from backend.audio_utils import is_valid_mp3_header, probe_audio, safe_stem_name
from backend import job_manager
from backend.engines import engine_factory

settings.validate()
if settings.torch_threads > 0:
    try:
        import torch as _torch

        _torch.set_num_threads(settings.torch_threads)
        try:
            _torch.set_num_interop_threads(max(1, min(settings.torch_threads, 4)))
        except RuntimeError:
            pass  # interop pool already started; intra-op setting still holds
    except Exception as e:
        raise RuntimeError(f"TORCH_THREADS={settings.torch_threads} could not be applied: {e}")
os.makedirs(os.path.abspath(settings.storage_dir), exist_ok=True)
os.makedirs(os.path.join(os.path.abspath(settings.storage_dir), "jobs"), exist_ok=True)

app = FastAPI(title="VioletStem", version="1.0.0",
              description="AI music separator: vocals + instrumental.")

from backend.auth import BasicAuthMiddleware

# Always installed; internally a no-op unless AUTH_ENABLED=true.
app.add_middleware(BasicAuthMiddleware)

# --- simple in-memory rate limiter (per-IP sliding window) ---
_hits: dict[str, list[float]] = defaultdict(list)
_rl_lock = threading.Lock()

def _check_rate_limit(ip: str) -> None:
    now = time.time()
    with _rl_lock:
        window = _hits[ip]
        window[:] = [t for t in window if now - t < settings.rate_limit_window]
        if len(window) >= settings.rate_limit_requests:
            raise HTTPException(status_code=429, detail="Too many requests. Please wait a minute and try again.")
        window.append(now)

def _client_ip(request: Request) -> str:
    # deployment-aware: respect X-Forwarded-For only first hop is not trusted here;
    # use direct client host.
    return request.client.host if request.client else "unknown"

def _job_public(job) -> dict:
    p = job_manager.job_paths(job)
    return {
        "job_id": job.id,
        "status": job.status,
        "stage": job.stage,
        "message": job.message,
        "engine": job.engine,
        "original_name": job.original_name,
        "duration": job.duration,
        "vocals": {"size": job.vocals_size, "duration": job.vocals_duration,
                   "url": f"/api/separations/{job.id}/vocals" if job.status == "completed" else None},
        "instrumental": {"size": job.instrumental_size, "duration": job.instrumental_duration,
                         "url": f"/api/separations/{job.id}/instrumental" if job.status == "completed" else None},
        "zip_url": f"/api/separations/{job.id}/download-all" if job.status == "completed" else None,
        "error": job.error,
        "retention_seconds": settings.retention_seconds,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }

@app.get("/api/health")
def health():
    eng = engine_factory.engine_status()
    return {"status": "ok", "product": BRAND["name"], "version": "1.0.0",
            "engine": eng, "max_upload_mb": settings.max_upload_mb,
            "max_concurrent_jobs": settings.max_concurrent_jobs,
            "jobs_active": job_manager.active_processing_count(),
            "retention_seconds": settings.retention_seconds}

@app.get("/api/engine")
def engine():
    return engine_factory.engine_status()

@app.post("/api/separations", status_code=201)
async def create_separation(request: Request, file: UploadFile = File(...)):
    _check_rate_limit(_client_ip(request))
    if job_manager.active_processing_count() >= settings.max_concurrent_jobs:
        raise HTTPException(status_code=503, detail="Server is busy. Please wait and retry.")
    filename = file.filename or "song.mp3"
    ext = os.path.splitext(filename)[1].lower()
    if ext != ".mp3":
        raise HTTPException(status_code=400, detail="Unsupported format. Please upload an MP3 file.")
    job = job_manager.create_job(original_name=os.path.basename(filename)[:120],
                                 stem_base=safe_stem_name(filename))
    paths = job_manager.job_paths(job)
    # stream upload to disk with size cap (do not load fully into memory)
    size = 0
    try:
        with open(paths["upload"], "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    out.close()
                    job_manager.delete_job(job.id)
                    raise HTTPException(status_code=413, detail=f"File too large. Limit is {settings.max_upload_mb} MB.")
                out.write(chunk)
    except HTTPException:
        raise
    except Exception:
        job_manager.delete_job(job.id)
        raise HTTPException(status_code=400, detail="Upload interrupted. Please try again.")
    if size == 0:
        job_manager.delete_job(job.id)
        raise HTTPException(status_code=400, detail="Empty file. Please upload a valid MP3.")
    # content validation: real MP3 header + ffmpeg-decodable
    if not is_valid_mp3_header(paths["upload"]):
        job_manager.delete_job(job.id)
        raise HTTPException(status_code=400, detail="Invalid MP3: file header is not recognized audio.")
    probe = probe_audio(paths["upload"])
    if not probe["readable"]:
        job_manager.delete_job(job.id)
        raise HTTPException(status_code=400, detail="Corrupted or invalid MP3: audio could not be decoded.")
    if probe["duration"] and probe["duration"] > settings.max_duration_seconds:
        job_manager.delete_job(job.id)
        raise HTTPException(status_code=400, detail=f"Audio too long ({probe['duration']:.0f}s). Limit is {settings.max_duration_seconds}s.")
    job_manager.submit_processing(job)
    return JSONResponse(status_code=201, content=_job_public(job))

@app.get("/api/separations/{job_id}")
def get_separation(job_id: str):
    job = job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Invalid or expired job ID.")
    return _job_public(job)

def _require_completed_file(job_id: str, key: str) -> tuple:
    job = job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Invalid or expired job ID.")
    if job.status != "completed":
        raise HTTPException(status_code=409, detail=f"Job is {job.status}. Files are available only after successful completion.")
    p = job_manager.job_paths(job)[key]
    if not os.path.exists(p):
        raise HTTPException(status_code=410, detail="Result files expired or were cleaned up.")
    return job, p

@app.get("/api/separations/{job_id}/vocals")
def download_vocals(job_id: str):
    job, p = _require_completed_file(job_id, "vocals")
    return FileResponse(p, media_type="audio/wav", filename=f"{job.stem_base}-vocals.wav")

@app.get("/api/separations/{job_id}/instrumental")
def download_instrumental(job_id: str):
    job, p = _require_completed_file(job_id, "instrumental")
    return FileResponse(p, media_type="audio/wav", filename=f"{job.stem_base}-instrumental.wav")

@app.get("/api/separations/{job_id}/download-all")
def download_all(job_id: str):
    job = job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Invalid or expired job ID.")
    if job.status != "completed":
        raise HTTPException(status_code=409, detail=f"Job is {job.status}. ZIP is available only after completion.")
    try:
        zp = job_manager.build_zip(job)
    except Exception:
        raise HTTPException(status_code=500, detail="ZIP creation failed. Try downloading files individually.")
    return FileResponse(zp, media_type="application/zip", filename=f"{job.stem_base}-stems.zip")

@app.delete("/api/separations/{job_id}")
def delete_separation(job_id: str):
    job = job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Invalid or expired job ID.")
    if job.status == "processing":
        job_manager.request_cancel(job_id)
        return {"job_id": job_id, "status": "cancelling", "message": "Cancellation requested."}
    job_manager.delete_job(job_id)
    return {"job_id": job_id, "status": "deleted"}

# --- background cleanup thread ---
_stop = threading.Event()
_thread = threading.Thread(target=job_manager.cleanup_loop, args=(_stop,), daemon=True)
_thread.start()

# --- serve frontend static build ---
_FRONT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
if os.path.isdir(_FRONT):
    app.mount("/", StaticFiles(directory=_FRONT, html=True), name="frontend")
