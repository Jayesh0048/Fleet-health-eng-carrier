from __future__ import annotations

import logging
from typing import Any

from .store import record_parse

logger = logging.getLogger(__name__)


def capture_dvir_parse(raw_text: str, parsed: Any, source_record_id: str | None) -> str | None:
    """Capture one normal parser result without blocking the primary DVIR transaction.

    The captured parser labels are weak labels for review only. Training uses only
    reviewed_subsystem/reviewed_urgency, never the parser's own predictions.
    """
    try:
        return record_parse(
            raw_text=raw_text,
            parsed=parsed,
            source_record_id=source_record_id,
            parser_version=f"{parsed.__class__.__module__}.{parsed.__class__.__name__}",
        )
    except Exception:
        logger.exception("DVIR learning capture failed; primary DVIR processing should continue")
        return None
