from datetime import datetime, timedelta, timezone

from dvir_learning.store import connect, record_parse, set_state
from dvir_learning.worker import run_iteration


class Parsed:
    subsystem = "BRAKES"
    affected_subsystems = ["BRAKES"]
    severity = 0.8
    urgency = "CRITICAL"
    has_defect = True
    detected_symptoms = ["air leak"]


def test_worker_retries_failed_training_after_cooldown(tmp_path, monkeypatch):
    monkeypatch.setenv("DVIR_COLLECTION_DAYS", "3")
    monkeypatch.setenv("DVIR_MIN_REVIEWED_SAMPLES", "30")
    monkeypatch.setenv("DVIR_MIN_SAMPLES_PER_CLASS", "5")
    monkeypatch.setenv("DVIR_TRAIN_RETRY_SECONDS", "900")
    db = tmp_path / "retry.db"
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    sample_ids = []
    for i in range(30):
        sample_id = record_parse(
            f"review test note unique phrase sequence {i}", Parsed(),
            source_record_id=f"retry-{i}", path=db,
        )
        sample_ids.append((i, sample_id))

    conn = connect(db)
    try:
        for i, sample_id in sample_ids:
            subsystem = "BRAKES" if i % 2 == 0 else "TIRES"
            urgency = "CRITICAL" if i % 2 == 0 else "MINOR"
            conn.execute(
                "UPDATE dvir_samples SET reviewed_subsystem=?, reviewed_urgency=?, "
                "reviewer='test-reviewer', reviewed_at=? WHERE sample_id=?",
                (subsystem, urgency, start.isoformat(), sample_id),
            )
        set_state(conn, "collection_started_at", (start - timedelta(days=4)).isoformat())
        conn.commit()
    finally:
        conn.close()

    calls = {"count": 0}

    def fail_training(*args, **kwargs):
        calls["count"] += 1
        raise RuntimeError("synthetic temporary training failure")

    monkeypatch.setattr("dvir_learning.worker.train_candidate", fail_training)

    first = run_iteration(str(db), str(tmp_path / "models"), now=start + timedelta(days=4))
    assert first["action"] == "training_failed"
    assert calls["count"] == 1

    second = run_iteration(
        str(db), str(tmp_path / "models"), now=start + timedelta(days=4, minutes=1)
    )
    assert second["action"] == "waiting_for_training_retry"
    assert calls["count"] == 1

    third = run_iteration(
        str(db), str(tmp_path / "models"), now=start + timedelta(days=4, minutes=16)
    )
    assert third["action"] == "training_failed"
    assert calls["count"] == 2
