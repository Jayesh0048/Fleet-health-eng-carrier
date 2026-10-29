
from __future__ import annotations

import math
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

try:
    from dotenv import load_dotenv

    # Load this project's .env file without overriding variables
    # already explicitly set in the process environment.
    load_dotenv(PROJECT_ROOT / ".env", override=False)
except ImportError:
    pass


DEFAULT_DB = PROJECT_ROOT / "data" / "dvir_learning.sqlite3"
DEFAULT_ARTIFACTS = PROJECT_ROOT / "models" / "dvir_candidates"

SUBSYSTEM_LABELS = {
    "AFTERTREATMENT",
    "AIR_SYSTEM",
    "BRAKES",
    "COOLING",
    "ELECTRICAL",
    "NONE",
    "TIRES",
}

URGENCY_LABELS = {
    "NORMAL",
    "MINOR",
    "MODERATE",
    "CRITICAL",
}


def _env_int(name: str, default: int) -> int:
    """Read an integer setting and report invalid configuration clearly."""
    raw = os.getenv(name)

    if raw is None or not raw.strip():
        return default

    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(
            f"{name} must be an integer; received {raw!r}"
        ) from exc


def _env_float(name: str, default: float) -> float:
    """Read a finite numeric setting and report invalid configuration."""
    raw = os.getenv(name)

    if raw is None or not raw.strip():
        return default

    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(
            f"{name} must be a number; received {raw!r}"
        ) from exc

    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number.")

    return value


def db_path() -> Path:
    value = os.getenv("DVIR_LEARNING_DB")

    path = Path(value).expanduser() if value else DEFAULT_DB
    return path.resolve()


def artifacts_dir() -> Path:
    value = os.getenv("DVIR_MODEL_CANDIDATES_DIR")

    path = Path(value).expanduser() if value else DEFAULT_ARTIFACTS
    return path.resolve()


def collection_days() -> float:
    return max(0.0, _env_float("DVIR_COLLECTION_DAYS", 3.0))


def min_reviewed_samples() -> int:
    return max(30, _env_int("DVIR_MIN_REVIEWED_SAMPLES", 300))


def min_samples_per_class() -> int:
    return max(2, _env_int("DVIR_MIN_SAMPLES_PER_CLASS", 5))


def retrain_new_samples() -> int:
    return max(1, _env_int("DVIR_RETRAIN_NEW_SAMPLES", 100))


def poll_seconds() -> int:
    return max(30, _env_int("DVIR_TRAINER_POLL_SECONDS", 300))


def min_macro_f1() -> float:
    value = _env_float("DVIR_MIN_MACRO_F1", 0.60)

    if not 0.0 <= value <= 1.0:
        raise ValueError(
            "DVIR_MIN_MACRO_F1 must be between 0.0 and 1.0."
        )

    return value

def train_retry_seconds() -> int:
    import os
    return int(os.getenv('DVIR_TRAIN_RETRY_SECONDS', '900'))

