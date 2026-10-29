from dataclasses import dataclass

import pytest

from dvir_learning.store import connect, record_parse, reviewed_counts, set_state, get_state
from dvir_learning.worker import run_iteration


@dataclass
class Parsed:
    subsystem: str = "BRAKES"
    affected_subsystems: list[str] | None = None
    severity: float = 0.5
    urgency: str = "MODERATE"
    has_defect: bool = True
    detected_symptoms: list[str] | None = None


def test_capture_record_and_dedupe_source_id(tmp_path):
    db = tmp_path / "learning.db"
    parsed = Parsed(affected_subsystems=["BRAKES"], detected_symptoms=["BRAKES: soft brakes"])
    a = record_parse("Soft brakes", parsed, source_record_id="dvir-123", path=db)
    b = record_parse("Soft brakes", parsed, source_record_id="dvir-123", path=db)
    assert a == b
    conn = connect(db)
    try:
        assert conn.execute("select count(*) from dvir_samples").fetchone()[0] == 1
        assert get_state(conn, "collection_started_at") is not None
    finally:
        conn.close()


def test_empty_and_too_long_text_rejected(tmp_path):
    with pytest.raises(ValueError):
        record_parse("  ", Parsed(), path=tmp_path / "learning.db")
    with pytest.raises(ValueError):
        record_parse("x" * 10001, Parsed(), path=tmp_path / "learning.db")


def test_worker_waits_during_3_day_window(tmp_path, monkeypatch):
    monkeypatch.setenv("DVIR_COLLECTION_DAYS", "3")
    db = tmp_path / "learning.db"
    from datetime import datetime, timezone
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    conn = connect(db)
    try:
        from dvir_learning.store import ensure_collection_started, record_parse, set_state
        record_parse("One persisted sample", Parsed(), source_record_id="start-test", path=db)
        set_state(conn, "collection_started_at", now.isoformat())
    finally:
        conn.close()
    result = run_iteration(str(db), str(tmp_path / "artifacts"), now=now)
    assert result["action"] == "collecting"
    assert result["hours_remaining"] == 72


def test_worker_waits_for_reviewed_labels_after_window(tmp_path, monkeypatch):
    monkeypatch.setenv("DVIR_COLLECTION_DAYS", "3")
    monkeypatch.setenv("DVIR_MIN_REVIEWED_SAMPLES", "30")
    db = tmp_path / "learning.db"
    from datetime import datetime, timezone, timedelta
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    conn = connect(db)
    try:
        from dvir_learning.store import record_parse, set_state
        record_parse("One persisted sample", Parsed(), source_record_id="elapsed-test", path=db)
        set_state(conn, "collection_started_at", start.isoformat())
    finally:
        conn.close()
    result = run_iteration(str(db), str(tmp_path / "artifacts"), now=start + timedelta(days=3, minutes=1))
    assert result["action"] == "waiting_for_reviewed_labels"
