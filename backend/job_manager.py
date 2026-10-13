"""Job management: creation, async processing, status, cleanup."""
from __future__ import annotations
import os
import secrets
import shutil
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from backend.config import settings

STAGES = ["queued", "uploading", "preparing", "separating", "finishing", "completed", "failed", "cancelled"]

@dataclass
class Job:
    id: str
    original_name: str
    stem_base: str
    status: str = "queued"
    stage: str = "queued"
    message: str = "Job created."
    engine: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    duration: float | None = None
    vocals_size: int | None = None
    instrumental_size: int | None = None
    vocals_duration: float | None = None
    instrumental_duration: float | None = None
    error: str | None = None
    cancel_requested: bool = False
    dir: str = ""

_jobs: dict[str, Job] = {}
_lock = threading.Lock()
_executor: ThreadPoolExecutor | None = None

def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=settings.max_concurrent_jobs,
                                       thread_name_prefix="violetstem")
    return _executor

def active_processing_count() -> int:
    with _lock:
        return sum(1 for j in _jobs.values() if j.status == "processing")

def create_job(original_name: str, stem_base: str) -> Job:
    job_id = secrets.token_urlsafe(24)
    job_dir = os.path.join(os.path.abspath(settings.storage_dir), "jobs", job_id)
    os.makedirs(job_dir, exist_ok=True)
    job = Job(id=job_id, original_name=original_name, stem_base=stem_base, dir=job_dir)
    with _lock:
        _jobs[job_id] = job
    return job

def get_job(job_id: str) -> Job | None:
    # strict id format guard (token_urlsafe 24 -> 32 chars, alnum + -_)
    if not job_id or len(job_id) > 64:
        return None
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
    if any(c not in allowed for c in job_id):
        return None
    with _lock:
        return _jobs.get(job_id)

def job_paths(job: Job) -> dict:
    return {
        "upload": os.path.join(job.dir, "upload.mp3"),
        "decoded": os.path.join(job.dir, "decoded.wav"),
        "vocals": os.path.join(job.dir, "vocals.wav"),
        "instrumental": os.path.join(job.dir, "instrumental.wav"),
        "zip": os.path.join(job.dir, "stems.zip"),
    }

def _set(job: Job, status: str, stage: str, message: str) -> None:
    with _lock:
        job.status = status
        job.stage = stage
        job.message = message
        job.updated_at = time.time()

def request_cancel(job_id: str) -> bool:
    job = get_job(job_id)
    if job is None:
        return False
    with _lock:
        if job.status in ("completed", "failed", "cancelled"):
            return False
        job.cancel_requested = True
    return True

def submit_processing(job: Job) -> None:
    _set(job, "processing", "preparing", "Preparing audio")
    _get_executor().submit(_run_job, job.id)

def _run_job(job_id: str) -> None:
    from backend.audio_utils import decode_to_wav, probe_audio, wav_duration
    from backend.engines import engine_factory
    job = get_job(job_id)
    if job is None:
        return
    paths = job_paths(job)
    started = time.time()
    try:
        if job.cancel_requested:
            _set(job, "cancelled", "cancelled", "Job cancelled.")
            return
        _set(job, "processing", "preparing", "Preparing audio")
        # verify upload readable
        probe = probe_audio(paths["upload"])
        if not probe["readable"]:
            raise RuntimeError("Uploaded file could not be decoded as audio.")
        if probe["duration"] and probe["duration"] > settings.max_duration_seconds:
            raise RuntimeError(f"Audio is too long ({probe['duration']:.0f}s). Limit is {settings.max_duration_seconds}s.")
        with _lock:
            job.duration = probe["duration"]
        decode_to_wav(paths["upload"], paths["decoded"])
        if job.cancel_requested:
            _set(job, "cancelled", "cancelled", "Job cancelled.")
            return
        if time.time() - started > settings.processing_timeout:
            raise RuntimeError("Processing timed out.")
        _set(job, "processing", "separating", "Separating vocals and instrumental")

        def cb(stage, msg):
            with _lock:
                job.stage = stage
                job.message = msg
                job.updated_at = time.time()

        info = engine_factory.run_separation(paths["decoded"], paths["vocals"], paths["instrumental"], progress_cb=cb)
        with _lock:
            job.engine = info.get("engine")
            if info.get("device"):
                job.engine += f" ({info['device']})"
        if job.cancel_requested:
            _set(job, "cancelled", "cancelled", "Job cancelled.")
            return
        _set(job, "processing", "finishing", "Verifying results")
        # verify outputs
        for key in ("vocals", "instrumental"):
            p = paths[key]
            if not os.path.exists(p) or os.path.getsize(p) < 1000:
                raise RuntimeError(f"Separation failed: {key} output missing or empty.")
            d = wav_duration(p)
            if d <= 0:
                raise RuntimeError(f"Separation failed: {key} output has no audio.")
        with _lock:
            job.vocals_size = os.path.getsize(paths["vocals"])
            job.instrumental_size = os.path.getsize(paths["instrumental"])
            job.vocals_duration = wav_duration(paths["vocals"])
            job.instrumental_duration = wav_duration(paths["instrumental"])
        # durations must be compatible (within 0.5s)
        if abs(job.vocals_duration - job.instrumental_duration) > 0.5:
            raise RuntimeError("Separation failed: outputs have incompatible durations.")
        _set(job, "completed", "completed", "Separation complete. Preview and download your stems.")
    except Exception as e:
        # Never leak internal paths (storage dir, job dir) to API consumers.
        safe = _sanitize_error(str(e), job.dir if job else "")
        with _lock:
            job.error = safe[:500]
        _set(job, "failed", "failed", f"Processing failed: {safe[:200]}")

def _sanitize_error(text: str, job_dir: str) -> str:
    """Strip internal filesystem paths from user-facing error text."""
    out = text or ""
    try:
        storage = os.path.abspath(settings.storage_dir)
    except Exception:
        storage = ""
    for secret in (job_dir, storage):
        if secret:
            out = out.replace(secret, "[job files]")
            out = out.replace(secret.replace(os.sep, "/"), "[job files]")
    return out


def build_zip(job: Job) -> str:
    paths = job_paths(job)
    zp = paths["zip"]
    # rebuild if missing or stale
    if os.path.exists(zp):
        return zp
    vname = f"{job.stem_base}-vocals.wav"
    iname = f"{job.stem_base}-instrumental.wav"
    with zipfile.ZipFile(zp, "w", compression=zipfile.ZIP_STORED) as z:
        z.write(paths["vocals"], arcname=vname)
        z.write(paths["instrumental"], arcname=iname)
    return zp

def delete_job(job_id: str) -> bool:
    with _lock:
        job = _jobs.pop(job_id, None)
    if job is None:
        return False
    try:
        shutil.rmtree(job.dir, ignore_errors=True)
    except Exception:
        pass
    return True

def cleanup_expired() -> int:
    now = time.time()
    removed = 0
    with _lock:
        ids = list(_jobs.keys())
    for jid in ids:
        with _lock:
            job = _jobs.get(jid)
            if job is None:
                continue
            expired = (now - job.updated_at) > settings.retention_seconds
            done = job.status in ("completed", "failed", "cancelled")
        if expired and done:
            if delete_job(jid):
                removed += 1
    return removed

def cleanup_loop(stop_event: threading.Event) -> None:
    while not stop_event.wait(settings.cleanup_interval):
        try:
            cleanup_expired()
        except Exception:
            pass
