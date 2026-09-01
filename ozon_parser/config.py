from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_path(name: str, default: str) -> Path:
    return Path(os.getenv(name, default))


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc
    if value < 0:
        raise ValueError(f"{name} cannot be negative")
    return value


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value < 0:
        raise ValueError(f"{name} cannot be negative")
    return value


@dataclass(frozen=True)
class Settings:
    cookies_path: Path
    output_path: Path
    errors_path: Path
    debug_dir: Path
    request_timeout: float
    max_retries: int
    retry_backoff: float
    request_delay: float

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            cookies_path=_env_path("OZON_COOKIES_PATH", "data/cookies.json"),
            output_path=_env_path("OZON_OUTPUT_PATH", "output/products.csv"),
            errors_path=_env_path("OZON_ERRORS_PATH", "output/errors.csv"),
            debug_dir=_env_path("OZON_DEBUG_DIR", "debug"),
            request_timeout=_env_float("OZON_REQUEST_TIMEOUT", 30.0),
            max_retries=_env_int("OZON_MAX_RETRIES", 3),
            retry_backoff=_env_float("OZON_RETRY_BACKOFF", 1.0),
            request_delay=_env_float("OZON_REQUEST_DELAY", 1.5),
        )

