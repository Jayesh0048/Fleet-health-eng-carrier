from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def suggest_from_active_model(text: str, active_model_path: str | None = None) -> dict[str, Any] | None:
    """Return a suggestion from manually promoted model, or None if no model exists.

    This function is advisory only. Never use its output to establish or clear a safety hold.
    """
    model_path = Path(active_model_path or os.getenv("DVIR_ACTIVE_MODEL", "models/dvir_active/dvir_text_model.joblib")).expanduser().resolve()
    if not model_path.is_file():
        return None
    from joblib import load
    payload = load(model_path)
    normalized = " ".join(text.casefold().split())
    subsystem = str(payload["subsystem_model"].predict([normalized])[0])
    urgency = str(payload["urgency_model"].predict([normalized])[0])
    return {
        "source": "reviewed_text_classifier",
        "subsystem_suggestion": subsystem,
        "urgency_suggestion": urgency,
        "requires_human_review": True,
        "dispatch_hold_decision": None,
    }
