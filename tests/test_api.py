"""Backend tests: validation, job lifecycle, downloads, ZIP, cleanup, rate limits."""
import io, os, time, wave, zipfile
import numpy as np

os.environ.setdefault("STORAGE_DIR", "./data-test")
os.environ.setdefault("RATE_LIMIT_REQUESTS", "1000")

from fastapi.testclient import TestClient
from backend import app as appmod
from backend import job_manager

client = TestClient(appmod.app)

def _make_mp3(path, seconds=2.0, sr=44100):
    """Create a real MP3 via bundled ffmpeg from a synthetic stereo mix (center voice + wide pad)."""
    from backend.audio_utils import ffmpeg_exe
    import subprocess, wave as w, tempfile
    t = np.arange(int(sr * seconds)) / sr
    voice = 0.4 * np.sin(2 * np.pi * 440 * t)          # center-panned "vocal"
    padL = 0.3 * np.sin(2 * np.pi * 220 * t)
    padR = 0.3 * np.sin(2 * np.pi * 277 * t)           # wide accompaniment
    L = voice + padL
    R = voice + padR
    data = np.stack([L, R])
    ints = (np.clip(data, -1, 1).T * 32767).astype(np.int16)
    tmpwav = path + ".tmp.wav"
    with w.open(tmpwav, "wb") as f:
        f.setnchannels(2); f.setsampwidth(2); f.setframerate(sr)
        f.writeframes(ints.tobytes())
    exe = ffmpeg_exe()
    r = subprocess.run([exe, "-y", "-hide_banner", "-loglevel", "error",
                        "-i", tmpwav, "-codec:a", "libmp3lame", "-b:a", "128k", path],
                       capture_output=True, timeout=60)
    os.remove(tmpwav)
    assert r.returncode == 0, f"ffmpeg mp3 encode failed: {r.stderr[:300]}"
    return path

def _wait_done(job_id, timeout=180):
    start = time.time()
    while time.time() - start < timeout:
        r = client.get(f"/api/separations/{job_id}")
        assert r.status_code == 200
        j = r.json()
        if j["status"] in ("completed", "failed", "cancelled"):
            return j
        time.sleep(1.0)
    raise AssertionError("job did not finish in time")

def test_health_and_engine():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "engine" in body

def test_reject_non_mp3():
    r = client.post("/api/separations", files={"file": ("note.txt", b"hello", "text/plain")})
    assert r.status_code == 400

def test_reject_empty():
    r = client.post("/api/separations", files={"file": ("x.mp3", b"", "audio/mpeg")})
    assert r.status_code == 400

def test_reject_fake_mp3():
    r = client.post("/api/separations", files={"file": ("fake.mp3", b"NOT AUDIO" * 100, "audio/mpeg")})
    assert r.status_code == 400

def test_full_pipeline_and_zip(tmp_path=None):
    mp3 = os.path.abspath("tests/assets/sample.mp3")
    if not os.path.exists(mp3):
        os.makedirs("tests/assets", exist_ok=True)
        _make_mp3(mp3)
    with open(mp3, "rb") as f:
        data = f.read()
    r = client.post("/api/separations", files={"file": ("sample.mp3", data, "audio/mpeg")})
    assert r.status_code == 201, r.text
    job = r.json()
    jid = job["job_id"]
    assert len(jid) >= 16
    # unique ids
    r2 = client.post("/api/separations", files={"file": ("sample.mp3", data, "audio/mpeg")})
    assert r2.status_code == 201
    assert r2.json()["job_id"] != jid
    done = _wait_done(jid)
    assert done["status"] == "completed", done
    assert done["vocals"]["duration"] and done["vocals"]["duration"] > 0
    assert done["instrumental"]["duration"] and done["instrumental"]["duration"] > 0
    assert abs(done["vocals"]["duration"] - done["instrumental"]["duration"]) < 0.5
    # downloads
    rv = client.get(f"/api/separations/{jid}/vocals")
    assert rv.status_code == 200 and len(rv.content) > 1000
    ri = client.get(f"/api/separations/{jid}/instrumental")
    assert ri.status_code == 200 and len(ri.content) > 1000
    assert rv.content != ri.content  # stems must differ
    rz = client.get(f"/api/separations/{jid}/download-all")
    assert rz.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(rz.content))
    names = zf.namelist()
    assert len(names) == 2 and any("vocals" in n for n in names) and any("instrumental" in n for n in names)
    # invalid job
    assert client.get("/api/separations/INVALID!!/vocals").status_code in (404,)
    assert client.get("/api/separations/doesnotexist123").status_code == 404

def test_delete_cleans_files():
    mp3 = os.path.abspath("tests/assets/sample.mp3")
    with open(mp3, "rb") as f:
        data = f.read()
    r = client.post("/api/separations", files={"file": ("sample.mp3", data, "audio/mpeg")})
    jid = r.json()["job_id"]
    done = _wait_done(jid)
    assert done["status"] == "completed"
    d = client.delete(f"/api/separations/{jid}")
    assert d.status_code == 200
    assert client.get(f"/api/separations/{jid}").status_code == 404
