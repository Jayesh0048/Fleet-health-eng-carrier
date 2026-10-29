from datetime import datetime, timezone

from dvir_learning.store import connect, get_state
from dvir_learning.worker import run_iteration


def test_worker_does_not_start_timer_without_samples(tmp_path):
    db = tmp_path / "empty-learning.db"
    now = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    result = run_iteration(str(db), str(tmp_path / "artifacts"), now=now)

    assert result["action"] == "waiting_for_first_dvir"
    assert result["samples_collected"] == 0
    conn = connect(db)
    try:
        assert get_state(conn, "collection_started_at") is None
    finally:
        conn.close()
