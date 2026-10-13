"""Hardening tests: limits, failure modes, concurrency, ID safety, retention, restart.

Uses the 2 s synthetic MP3 (fast) and monkeypatched engines where quality is
irrelevant, so the suite stays quick. Real Demucs path is covered by
test_api.py + scripts/eval_separation.py (SI-SDR vs references).
"""
import io
import os
import secrets
import threading
import time
import zipfile

os.environ.setdefault("STORAGE_DIR", "./data-test")
os.environ.setdefault("RATE_LIMIT_REQUESTS", "10000")

import pytest
from fastapi.testclient import TestClient

from backend import app as appmod
from backend import job_manager
from backend.config import settings
from backend.engines import engine_factory, dsp_engine

SAMPLE = os.path.abspath("tests/assets/sample.mp3")


def _sample_bytes():
    assert os.path.exists(SAMPLE), "tests/assets/sample.mp3 missing (run test_api.py first)"
    with open(SAMPLE, "rb") as f:
        return f.read()


def _wait_done(client, job_id, timeout=180):
    start = time.time()
    while time.time() - start < timeout:
        j = client.get(f"/api/separations/{job_id}").json()
        if j["status"] in ("completed", "failed", "cancelled"):
            return j
        time.sleep(0.5)
    raise AssertionError(f"job {job_id} did not finish in time")


@pytest.fixture()
def client():
    return TestClient(appmod.app)


def test_oversize_rejected(client, monkeypatch):
    # max_upload_bytes is a read-only property over max_upload_mb;
    # setting the knob to 0 forces every real upload over the limit.
    monkeypatch.setattr(settings, "max_upload_mb", 0)
    r = client.post("/api/separations",
                    files={"file": ("big.mp3", _sample_bytes(), "audio/mpeg")})
    assert r.status_code == 413, r.text
    assert "large" in r.json()["detail"].lower()


def test_valid_header_but_undecodable_rejected(client):
    # Passes the ID3 header sniff but ffmpeg cannot decode it.
    payload = b"ID3\x04\x00\x00\x00\x00\x00\x00" + os.urandom(4000)
    r = client.post("/api/separations",
                    files={"file": ("broken.mp3", payload, "audio/mpeg")})
    assert r.status_code == 400, r.text
    assert "decod" in r.json()["detail"].lower()


def test_inference_failure_is_failed_not_completed(client, monkeypatch):
    def boom(wav_in, vocals_out, instrumental_out, progress_cb=None):
        raise RuntimeError("neural weights exploded (simulated)")
    monkeypatch.setattr(engine_factory, "run_separation", boom)
    r = client.post("/api/separations",
                    files={"file": ("sample.mp3", _sample_bytes(), "audio/mpeg")})
    assert r.status_code == 201
    jid = r.json()["job_id"]
    done = _wait_done(client, jid)
    assert done["status"] == "failed"
    assert "exploded" in (done["message"] + (done["error"] or ""))
    assert "Traceback" not in (done["message"] + (done["error"] or ""))
    # failed outputs must NOT be downloadable as if valid
    assert client.get(f"/api/separations/{jid}/vocals").status_code == 409
    assert client.get(f"/api/separations/{jid}/instrumental").status_code == 409
    assert client.get(f"/api/separations/{jid}/download-all").status_code == 409
    # cleanup still works
    assert client.delete(f"/api/separations/{jid}").status_code == 200
    assert client.get(f"/api/separations/{jid}").status_code == 404


def test_failed_job_hides_internal_paths(client, monkeypatch):
    import backend.job_manager as jm

    def boom(wav_in, vocals_out, instrumental_out, progress_cb=None):
        raise RuntimeError(f"ffmpeg exploded at {vocals_out} (storage {os.path.abspath('data-test')})")

    monkeypatch.setattr(engine_factory, "run_separation", boom)
    r = client.post("/api/separations",
                    files={"file": ("sample.mp3", _sample_bytes(), "audio/mpeg")})
    jid = r.json()["job_id"]
    done = _wait_done(client, jid)
    assert done["status"] == "failed"
    blob = (done["message"] or "") + (done["error"] or "")
    assert "[job files]" in blob
    for leak in ("data-test", "Documents", "C:\\", ".mp3"):
        assert leak not in blob, leak
    client.delete(f"/api/separations/{jid}")


def test_two_consecutive_jobs_are_independent(client):
    data = _sample_bytes()
    r1 = client.post("/api/separations", files={"file": ("alpha.mp3", data, "audio/mpeg")})
    r2 = client.post("/api/separations", files={"file": ("beta.mp3", data, "audio/mpeg")})
    assert r1.status_code == 201 and r2.status_code == 201
    j1, j2 = r1.json()["job_id"], r2.json()["job_id"]
    assert j1 != j2
    d1 = _wait_done(client, j1)
    d2 = _wait_done(client, j2)
    assert d1["status"] == "completed" and d2["status"] == "completed"
    z1 = zipfile.ZipFile(io.BytesIO(client.get(f"/api/separations/{j1}/download-all").content))
    z2 = zipfile.ZipFile(io.BytesIO(client.get(f"/api/separations/{j2}/download-all").content))
    assert any("alpha" in n for n in z1.namelist())
    assert any("beta" in n for n in z2.namelist())
    assert job_manager.get_job(j1).dir != job_manager.get_job(j2).dir
    client.delete(f"/api/separations/{j1}")
    client.delete(f"/api/separations/{j2}")


def test_concurrency_limit_and_fail_fast(client, monkeypatch):
    import numpy as np
    from backend.audio_utils import write_wav_stereo
    gate = threading.Event()

    def slow_but_real(wav_in, vocals_out, instrumental_out, progress_cb=None):
        assert gate.wait(timeout=120), "workers were not released"
        monster = (np.sin(2 * np.pi * 440 * np.arange(22050) / 44100).astype(np.float32))
        stereo = np.stack([monster, monster])
        write_wav_stereo(vocals_out, stereo, 44100)
        write_wav_stereo(instrumental_out, stereo, 44100)
        return {"engine": "test-gate"}

    monkeypatch.setattr(engine_factory, "run_separation", slow_but_real)
    data = _sample_bytes()
    held = []
    try:
        for name in ("a", "b"):
            job = job_manager.create_job(f"{name}.mp3", name)
            with open(job_manager.job_paths(job)["upload"], "wb") as f:
                f.write(data)
            job_manager.submit_processing(job)
            held.append(job.id)
        deadline = time.time() + 60
        while job_manager.active_processing_count() < settings.max_concurrent_jobs:
            assert time.time() < deadline, "workers never became busy"
            time.sleep(0.2)
        # over the limit -> immediate 503, no orphaned upload
        r = client.post("/api/separations", files={"file": ("c.mp3", data, "audio/mpeg")})
        assert r.status_code == 503, r.text
        assert "busy" in r.json()["detail"].lower()
    finally:
        gate.set()
    for jid in held:
        done = _wait_done(client, jid)
        assert done["status"] == "completed", done
        client.delete(f"/api/separations/{jid}")
    # after release, new jobs are accepted again
    r = client.post("/api/separations", files={"file": ("d.mp3", data, "audio/mpeg")})
    assert r.status_code == 201
    done = _wait_done(client, r.json()["job_id"])
    assert done["status"] == "completed"
    client.delete(f"/api/separations/{r.json()['job_id']}")


def test_downloads_match_stored_labeled_files(client):
    data = _sample_bytes()
    r = client.post("/api/separations", files={"file": ("labeled.mp3", data, "audio/mpeg")})
    jid = r.json()["job_id"]
    done = _wait_done(client, jid)
    assert done["status"] == "completed"
    job = job_manager.get_job(jid)
    paths = job_manager.job_paths(job)
    with open(paths["vocals"], "rb") as f:
        disk_v = f.read()
    with open(paths["instrumental"], "rb") as f:
        disk_i = f.read()
    assert client.get(f"/api/separations/{jid}/vocals").content == disk_v
    assert client.get(f"/api/separations/{jid}/instrumental").content == disk_i
    assert disk_v != disk_i
    client.delete(f"/api/separations/{jid}")


def test_malicious_job_ids_are_404(client):
    for bad in ["..", "../x", "a/b", "a b", "!", "x" * 65, secrets.token_urlsafe(24)]:
        assert client.get(f"/api/separations/{bad}").status_code == 404, bad
        assert client.get(f"/api/separations/{bad}/vocals").status_code == 404, bad
        # NOTE: ".." is normalized client-side to /api/ (405: no such route),
        # never reaches the filesystem — job IDs only index an in-memory dict.
        assert client.delete(f"/api/separations/{bad}").status_code in (404, 405), bad


def test_retention_cleanup_removes_expired_results(client):
    data = _sample_bytes()
    r = client.post("/api/separations", files={"file": ("old.mp3", data, "audio/mpeg")})
    jid = r.json()["job_id"]
    done = _wait_done(client, jid)
    assert done["status"] == "completed"
    job = job_manager.get_job(jid)
    job_dir = job.dir
    assert os.path.exists(os.path.join(job_dir, "vocals.wav"))
    job.updated_at = time.time() - settings.retention_seconds - 10
    removed = job_manager.cleanup_expired()
    assert removed >= 1
    assert not os.path.exists(job_dir)
    assert client.get(f"/api/separations/{jid}").status_code == 404
    assert client.get(f"/api/separations/{jid}/vocals").status_code == 404


def test_restart_forgets_in_memory_jobs_but_health_survives(client):
    data = _sample_bytes()
    r = client.post("/api/separations", files={"file": ("gone.mp3", data, "audio/mpeg")})
    jid = r.json()["job_id"]
    done = _wait_done(client, jid)
    assert done["status"] == "completed"
    # simulate backend restart: in-memory registry lost, files orphaned on disk
    job_manager._jobs.clear()
    assert client.get(f"/api/separations/{jid}").status_code == 404
    body = client.get(f"/api/separations/{jid}").json()
    assert "expired" in body["detail"].lower()
    h = client.get("/api/health")
    assert h.status_code == 200 and h.json()["status"] == "ok"
