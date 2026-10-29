import csv
from dataclasses import dataclass

from dvir_learning.store import connect, export_review_queue, import_reviews, record_parse, reviewed_counts

@dataclass
class Parsed:
    subsystem: str = "BRAKES"
    affected_subsystems: list[str] | None = None
    severity: float = 0.4
    urgency: str = "MODERATE"
    has_defect: bool = True
    detected_symptoms: list[str] | None = None


def test_export_and_import_reviews(tmp_path):
    db = tmp_path / "learning.db"
    record_parse("Soft brakes at low speed", Parsed(), source_record_id="dvir-1", path=db)
    queue = tmp_path / "queue.csv"
    assert export_review_queue(queue, path=db) == 1
    with queue.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rows[0]["reviewed_subsystem"] = "BRAKES"
    rows[0]["reviewed_urgency"] = "MODERATE"
    rows[0]["reviewer"] = "maintenance-reviewer"
    with queue.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    assert import_reviews(queue, path=db) == 1
    assert reviewed_counts(db)["reviewed_records"] == 1


def test_invalid_label_rejected(tmp_path):
    db = tmp_path / "learning.db"
    record_parse("Soft brakes", Parsed(), source_record_id="dvir-1", path=db)
    queue = tmp_path / "queue.csv"
    export_review_queue(queue, path=db)
    rows = list(csv.DictReader(queue.open(encoding="utf-8")))
    rows[0].update(reviewed_subsystem="BANANA", reviewed_urgency="MODERATE", reviewer="reviewer")
    with queue.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    import pytest
    with pytest.raises(ValueError):
        import_reviews(queue, path=db)
