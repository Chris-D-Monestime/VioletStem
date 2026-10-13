"""Central configuration for VioletStem. All routine settings via env vars."""
from __future__ import annotations
import os
from dataclasses import dataclass, field

def _getenv(name: str, default: str) -> str:
    return os.environ.get(name, default)

def _getint(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        raise RuntimeError(f"Invalid integer for {name}: {os.environ.get(name)!r}")

@dataclass
class Settings:
    app_env: str = field(default_factory=lambda: _getenv("APP_ENV", "development"))
    host: str = field(default_factory=lambda: _getenv("HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _getint("PORT", 8000))
    storage_dir: str = field(default_factory=lambda: _getenv("STORAGE_DIR", "./data"))
    retention_seconds: int = field(default_factory=lambda: _getint("RETENTION_SECONDS", 3600))
    cleanup_interval: int = field(default_factory=lambda: _getint("CLEANUP_INTERVAL_SECONDS", 300))
    max_upload_mb: int = field(default_factory=lambda: _getint("MAX_UPLOAD_MB", 50))
    max_duration_seconds: int = field(default_factory=lambda: _getint("MAX_DURATION_SECONDS", 600))
    processing_timeout: int = field(default_factory=lambda: _getint("PROCESSING_TIMEOUT_SECONDS", 600))
    max_concurrent_jobs: int = field(default_factory=lambda: _getint("MAX_CONCURRENT_JOBS", 2))
    engine: str = field(default_factory=lambda: _getenv("ENGINE", "PREFERRED_DEMUCS_FALLBACK_DSP"))
    demucs_model: str = field(default_factory=lambda: _getenv("DEMUCS_MODEL", "htdemucs"))
    demucs_device: str = field(default_factory=lambda: _getenv("DEMUCS_DEVICE", "cpu"))
    rate_limit_requests: int = field(default_factory=lambda: _getint("RATE_LIMIT_REQUESTS", 30))
    rate_limit_window: int = field(default_factory=lambda: _getint("RATE_LIMIT_WINDOW_SECONDS", 60))

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def validate(self) -> None:
        if self.max_upload_mb < 1 or self.max_upload_mb > 500:
            raise RuntimeError("MAX_UPLOAD_MB must be between 1 and 500")
        if self.max_concurrent_jobs < 1 or self.max_concurrent_jobs > 8:
            raise RuntimeError("MAX_CONCURRENT_JOBS must be between 1 and 8")

settings = Settings()

BRAND = {
    "name": "VioletStem",
    "tagline": "Your Music. Two Perfect Stems.",
    "colors": {
        "bg": "#090711",
        "bg2": "#100D1B",
        "card": "#171225",
        "accent": "#8B5CF6",
        "accent2": "#A855F7",
        "highlight": "#C4B5FD",
        "text": "#F8FAFC",
        "muted": "#A1A1B5",
        "border": "#302342",
    },
}
