from dvir_learning.store import connect, record_parse
from dvir_learning.train import training_readiness
from dataclasses import dataclass

@dataclass
class Parsed:
    subsystem: str = "BRAKES"
    affected_subsystems: list[str] | None = None
    severity: float = 0.4
    urgency: str = "MODERATE"
    has_defect: bool = True
    detected_symptoms: list[str] | None = None


def test_no_training_from_unreviewed_predictions(tmp_path, monkeypatch):
    monkeypatch.setenv("DVIR_MIN_REVIEWED_SAMPLES", "30")
    db = tmp_path / "learning.db"
    for idx in range(40):
        record_parse(f"Unique inspection note {idx}", Parsed(), source_record_id=f"src-{idx}", path=db)
    ready, reasons, details = training_readiness(db)
    assert not ready
    assert any("unique reviewed notes" in reason.lower() for reason in reasons)
    assert details["unique_reviewed_notes"] == 0
