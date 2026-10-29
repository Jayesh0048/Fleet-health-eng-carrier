from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.risk_engine import build_candidates

NOW = datetime(2026, 1, 20, 12, 0, tzinfo=timezone.utc)


def vehicle(**overrides):
    data = dict(
        vehicle_id="TRK-1",
        next_pm_due_at=None,
        odometer_miles=100_000,
        next_pm_due_odometer_miles=110_000,
        odometer_updated_at=NOW - timedelta(hours=2),
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def inspection(**overrides):
    data = dict(
        id=1, occurred_at=NOW - timedelta(days=1), defect_code="BRAKE",
        description="test defect", severity="HIGH", is_resolved=False,
        safety_hold_required=False, source_system="TEST",
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def breakdown(**overrides):
    data = dict(
        id=1, occurred_at=NOW - timedelta(days=2), fault_code="ENGINE",
        description="test breakdown", downtime_minutes=30,
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def test_unresolved_approved_safety_hold_is_critical():
    candidates = build_candidates(vehicle(), [inspection(safety_hold_required=True)], [], now=NOW)
    hold = next(c for c in candidates if c.rule_code == "SAFETY_HOLD_REQUIRED")
    assert hold.severity == "CRITICAL"
    assert hold.priority_score == 100
    assert hold.dispatch_hold_required is True


def test_resolved_inspection_does_not_create_safety_hold():
    candidates = build_candidates(vehicle(), [inspection(safety_hold_required=True, is_resolved=True)], [], now=NOW)
    assert not any(c.rule_code == "SAFETY_HOLD_REQUIRED" for c in candidates)


def test_overdue_maintenance_creates_alert():
    candidates = build_candidates(vehicle(next_pm_due_at=NOW - timedelta(days=1)), [], [], now=NOW)
    assert any(c.rule_code == "PREVENTIVE_MAINTENANCE_OVERDUE" for c in candidates)


def test_repeated_defect_creates_recurring_alert():
    candidates = build_candidates(
        vehicle(),
        [inspection(defect_code="BRAKE"), inspection(id=2, defect_code="BRAKE", occurred_at=NOW - timedelta(days=5))],
        [], now=NOW,
    )
    assert any(c.rule_code == "RECURRING_DEFECT_90D" for c in candidates)


def test_stale_odometer_creates_data_quality_alert_not_safety_hold():
    candidates = build_candidates(vehicle(odometer_updated_at=NOW - timedelta(days=10)), [], [], now=NOW)
    stale = next(c for c in candidates if c.rule_code == "ODOMETER_DATA_STALE")
    assert stale.dispatch_hold_required is False
