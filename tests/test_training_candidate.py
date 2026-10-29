from dataclasses import dataclass
from pathlib import Path

from dvir_learning.store import connect, record_parse, set_state
from dvir_learning.worker import run_iteration


@dataclass
class Parsed:
    subsystem: str = "NONE"
    affected_subsystems: list[str] | None = None
    severity: float = 0.1
    urgency: str = "NORMAL"
    has_defect: bool = False
    detected_symptoms: list[str] | None = None


def test_candidate_training_runs_on_reviewed_labels_only(tmp_path, monkeypatch):
    monkeypatch.setenv("DVIR_MIN_REVIEWED_SAMPLES", "30")
    monkeypatch.setenv("DVIR_COLLECTION_DAYS", "3")
    monkeypatch.setenv("DVIR_MIN_SAMPLES_PER_CLASS", "5")
    monkeypatch.setenv("DVIR_MIN_MACRO_F1", "0.0")
    db = tmp_path / "learning.db"
    conn = connect(db)
    try:
        set_state(conn, "collection_started_at", "2026-01-01T00:00:00+00:00")
    finally:
        conn.close()
    reviewed = []
    for subsystem, phrase in [
        ("BRAKES", "brake squeal and soft brakes"),
        ("COOLING", "coolant leak and radiator overheating"),
        ("TIRES", "tire tread low and sidewall damage"),
    ]:
        for urgency, words in [
            ("NORMAL", "no defect routine passed"),
            ("MINOR", "minor wear monitor next service"),
            ("MODERATE", "service soon requires inspection"),
            ("CRITICAL", "fire smoke immediate stop"),
        ]:
            for idx in range(10):
                sample_id = record_parse(
                    f"{phrase}; condition {urgency}; {words}; note sequence {subsystem} {urgency} {idx}",
                    Parsed(subsystem=subsystem, affected_subsystems=[subsystem], severity=0.4, urgency=urgency, has_defect=True),
                    source_record_id=f"{subsystem}-{urgency}-{idx}", path=db,
                )
                reviewed.append((sample_id, subsystem, urgency))
    conn = connect(db)
    try:
        conn.executemany(
            "UPDATE dvir_samples SET reviewed_subsystem=?, reviewed_urgency=?, reviewer='tester', reviewed_at='2026-01-01T00:00:00+00:00' WHERE sample_id=?",
            [(subsystem, urgency, sample_id) for sample_id, subsystem, urgency in reviewed],
        )
        conn.commit()
    finally:
        conn.close()

    from datetime import datetime, timezone, timedelta
    result = run_iteration(str(db), str(tmp_path / "artifacts"), now=datetime(2026, 1, 4, 0, 5, tzinfo=timezone.utc))
    candidate = Path(result["candidate_model"])
    assert result["action"] == "candidate_trained"
    assert candidate.is_file()
    assert "subsystem" in result["metrics"] and "urgency" in result["metrics"]
    assert result["eligible_for_manual_review"] in (True, False)
