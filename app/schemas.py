from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
APIModel = BaseModel

Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
AlertStatus = Literal["OPEN", "REVIEWED", "WORK_ORDER_CREATED", "RESOLVED", "CONDITION_CLEARED"]
WorkOrderStatus = Literal["OPEN", "APPROVED", "SCHEDULED", "IN_REPAIR", "COMPLETED", "CANCELLED"]


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    vehicle_id: str
    dedupe_key: str
    rule_code: str
    title: str
    description: str
    recommendation: str
    severity: str
    priority_score: float | int | None
    dispatch_hold_required: bool
    status: AlertStatus
    evidence: dict[str, Any]
    first_detected_at: datetime
    last_detected_at: datetime


class EvaluateOut(BaseModel):
    vehicle_id: str | None = None
    tenant_id: str | None = None
    evaluated_at: str | None = None
    dispatch_hold_recommended: bool = False
    active_alert_count: int = 0
    alerts: list[Any] = []
    note: str | None = None

    class Config:
        extra = "allow"


class DispatchEvaluationOut(BaseModel):
    dispatch_hold_required: bool = False
    confidence: float = 0.0
    reason: str | None = None

    class Config:
        extra = "allow"

class DispatchEvaluationOut(BaseModel):
    vehicle_id: str
    decision: DispatchDecision
    evaluated_at: datetime
    policy_version: str
    blocking_alert_ids: list[int] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    requires_authorized_release: bool
    source_freshness: dict[str, str]
    alerts_created_or_updated: int
    open_alerts: int
    alerts: list["AlertOut"]
    note: str = (
        "Priority scores are rule-based triage values, not probabilities "
        "of failure."
    )


class ResolveAlertIn(BaseModel):
    resolution_notes: str = Field(min_length=5, max_length=3000)
    actor_id: str = Field(min_length=1, max_length=120)


class WorkOrderCreate(BaseModel):
    vehicle_id: str = Field(min_length=1, max_length=80)
    alert_id: int | None = None
    title: str = Field(min_length=3, max_length=180)
    description: str = Field(min_length=5, max_length=5000)
    priority: Severity = "MEDIUM"
    requested_by: str = Field(min_length=1, max_length=120)  # Development-only; derive from auth in the TMS.


class WorkOrderUpdate(BaseModel):
    status: WorkOrderStatus
    actor_id: str = Field(min_length=1, max_length=120)  # Development-only; derive from authentication in the TMS.
    completion_notes: str | None = Field(default=None, max_length=3000)
    assigned_to: str | None = Field(default=None, max_length=120)
    service_center_id: str | None = Field(default=None, max_length=120)
    scheduled_at: datetime | None = None

    @field_validator("scheduled_at")
    @classmethod
    def normalize_schedule_time(cls, value: datetime | None) -> datetime | None:
        return require_aware_utc(value) if value is not None else None


class WorkOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    vehicle_id: str
    alert_id: int | None
    title: str
    description: str
    priority: str
    status: str
    requested_by: str
    assigned_to: str | None
    service_center_id: str | None
    scheduled_at: datetime | None
    completed_at: datetime | None
    completion_notes: str | None
    created_at: datetime
    updated_at: datetime


# Ingestion contracts for the standalone reference slice. Tenant identity is derived
# from the development-only header dependency, never accepted from a request body.
class VehicleIngest(BaseModel):
    vehicle_id: str = Field(min_length=1, max_length=80)
    unit_number: str = Field(min_length=1, max_length=80)
    vin: str | None = Field(default=None, max_length=40)
    make: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=80)
    model_year: int | None = Field(default=None, ge=1900, le=2100)
    status: str = Field(default="ACTIVE", min_length=1, max_length=30)
    odometer_miles: float | None = Field(default=None, ge=0)
    odometer_updated_at: datetime | None = None
    next_pm_due_at: datetime | None = None
    next_pm_due_odometer_miles: float | None = Field(default=None, ge=0)

    @field_validator("odometer_updated_at", "next_pm_due_at")
    @classmethod
    def normalize_optional_times(cls, value: datetime | None) -> datetime | None:
        return require_aware_utc(value) if value is not None else None


class InspectionIngest(BaseModel):
    vehicle_id: str = Field(min_length=1, max_length=80)
    source_system: str = Field(min_length=1, max_length=80)
    source_record_id: str = Field(min_length=1, max_length=120)
    occurred_at: datetime
    inspection_type: str = Field(default="DVIR", min_length=1, max_length=40)
    defect_code: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=5000)
    severity: Severity = "MEDIUM"
    is_resolved: bool = False
    resolved_at: datetime | None = None
    resolution_notes: str | None = Field(default=None, max_length=3000)

    @field_validator("occurred_at", "resolved_at")
    @classmethod
    def normalize_times(cls, value: datetime | None) -> datetime | None:
        return require_aware_utc(value) if value is not None else None


class BreakdownIngest(BaseModel):
    vehicle_id: str = Field(min_length=1, max_length=80)
    source_system: str = Field(min_length=1, max_length=80)
    source_record_id: str = Field(min_length=1, max_length=120)
    occurred_at: datetime
    fault_code: str | None = Field(default=None, max_length=80)
    description: str = Field(min_length=1, max_length=5000)
    downtime_minutes: int | None = Field(default=None, ge=0)
    repair_cost: float | None = Field(default=None, ge=0)

    @field_validator("occurred_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        result = require_aware_utc(value)
        assert result is not None
        return result


class MaintenanceRecordIngest(BaseModel):
    vehicle_id: str = Field(min_length=1, max_length=80)
    source_system: str = Field(min_length=1, max_length=80)
    source_record_id: str = Field(min_length=1, max_length=120)
    performed_at: datetime
    maintenance_type: str = Field(min_length=1, max_length=100)
    odometer_miles: float | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=5000)
    next_due_at: datetime | None = None
    next_due_odometer_miles: float | None = Field(default=None, ge=0)

    @field_validator("performed_at", "next_due_at")
    @classmethod
    def normalize_times(cls, value: datetime | None) -> datetime | None:
        return require_aware_utc(value) if value is not None else None


class OdometerReadingIngest(BaseModel):
    vehicle_id: str = Field(min_length=1, max_length=80)
    source_system: str = Field(min_length=1, max_length=80)
    source_record_id: str = Field(min_length=1, max_length=120)
    reading_at: datetime
    odometer_miles: float = Field(ge=0)

    @field_validator("reading_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        result = require_aware_utc(value)
        assert result is not None
        return result


class DTCEventIngest(BaseModel):
    """Source-keyed J1939 event. Source record ID is required for safe retries."""
    vehicle_id: str = Field(min_length=1, max_length=80)
    source_system: str = Field(min_length=1, max_length=80)
    source_record_id: str = Field(min_length=1, max_length=120)
    occurred_at: datetime
    spn: int = Field(ge=0, le=524287)
    fmi: int = Field(ge=0, le=31)
    description: str = Field(min_length=1, max_length=5000)
    subsystem: str = Field(default="GENERAL", min_length=1, max_length=80)
    severity: Severity = "MEDIUM"
    is_active: bool = True
    odometer_miles: float | None = Field(default=None, ge=0)

    @field_validator("occurred_at")
    @classmethod
    def normalize_occurred_at(cls, value: datetime) -> datetime:
        result = require_aware_utc(value)
        assert result is not None
        return result


def require_aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must include a timezone offset, e.g. 2026-10-16T10:00:00Z")
    return value.astimezone(timezone.utc)

class DVIRPredictResponse(BaseModel):
    subsystem: str
    urgency: str
    has_defect: bool
    severity: float
    affected_subsystems: list[str] = []
    model_type: str
    training_device: str
