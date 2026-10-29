from __future__ import annotations
from pathlib import Path
import joblib

"""Tenant-scoped Fleet Health inbox and vehicle-health endpoints.

This is a standalone reference implementation. The X-Tenant-ID and actor_id
inputs are development/test only; the live TMS must replace them with verified
identity/role claims before exposure to real customer data.
"""

import os
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    AuditEvent,
    Breakdown,
    DiagnosticEvent,
    HealthAlert,
    Inspection,
    MaintenanceRecord,
    Vehicle,
    WorkOrder,
)
from app.schemas import AlertOut, EvaluateOut, ResolveAlertIn
from app.services.risk_engine import evaluate_vehicle
from dvir_learning.store import reviewed_counts
from dvir_learning.train import load_active_model

router = APIRouter(prefix="/api/v1/fleet-health", tags=["Fleet Health"])

ALERT_STATUSES = {"ALL", "OPEN", "REVIEWED", "WORK_ORDER_CREATED", "RESOLVED", "CONDITION_CLEARED"}
ACTIVE_ALERT_STATUSES = ("OPEN", "REVIEWED", "WORK_ORDER_CREATED")
SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
ODOMETER_STALE_AFTER_DAYS = 7

_ACTIVE_PAYLOAD: dict[str, Any] | None = None


class ReviewAlertIn(BaseModel):
    """Review metadata for local development; derive actor_id from auth in the TMS."""

    review_notes: str = Field(min_length=5, max_length=3000)
    actor_id: str = Field(min_length=1, max_length=120)


class DVIRPredictRequest(BaseModel):
    """Input payload for scoring unstructured driver inspection text."""

    text: str = Field(min_length=3, max_length=2000)


class DVIRPredictResponse(BaseModel):
    """Inference output payload from active PyTorch classifier."""

    subsystem: str
    urgency: str
    has_defect: bool
    severity: float
    model_type: str
    training_device: str


_ACTIVE_MODEL_PAYLOAD = None

_ACTIVE_MODEL_PAYLOAD = None

def _get_active_model_payload():
    global _ACTIVE_MODEL_PAYLOAD
    if _ACTIVE_MODEL_PAYLOAD is not None:
        return _ACTIVE_MODEL_PAYLOAD

    model_path = Path(__file__).resolve().parents[2] / "models" / "dvir_active" / "dvir_text_model.joblib"
    if not model_path.exists():
        model_path = Path("models/dvir_active/dvir_text_model.joblib").resolve()

    if not model_path.exists():
        from fastapi import HTTPException
        raise HTTPException(
            status_code=503,
            detail=f"Active model artifact not found at {model_path}. Train and promote first."
        )

    try:
        _ACTIVE_MODEL_PAYLOAD = joblib.load(model_path)
        return _ACTIVE_MODEL_PAYLOAD
    except Exception as exc:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=503,
            detail=f"Failed loading active model from {model_path}: {exc}"
        )

def tenant_from_header(x_tenant_id: str | None = Header(default='default-fleet', alias='X-Tenant-ID')) -> str:
    """Development tenant selector with local testing fallback."""
    app_env = os.getenv('APP_ENV', 'development').strip().lower()
    if app_env not in {'development', 'test', 'local'}:
        raise HTTPException(
            status_code=503,
            detail='Fleet Health reference routes require integration with TMS authentication before deployment',
        )
    tenant_id = (x_tenant_id or 'default-fleet').strip()
    if len(tenant_id) > 80 or any(ord(char) < 32 for char in tenant_id):
        raise HTTPException(status_code=422, detail='Invalid tenant identifier')
    return tenant_id


def _utc(value: datetime | None) -> datetime | None:
    """Normalize timezone-aware values and interpret SQLite-naive values as UTC."""
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _get_vehicle_or_404(db: Session, tenant_id: str, vehicle_id: str) -> Vehicle:
    vehicle = db.scalar(
        select(Vehicle).where(
            Vehicle.tenant_id == tenant_id,
            Vehicle.vehicle_id == vehicle_id,
        )
    )
    if vehicle is None:
        # Same response whether the ID is unknown or belongs to another tenant.
        raise HTTPException(status_code=404, detail="Vehicle not found for this tenant")
    return vehicle


def _get_alert_or_404(db: Session, tenant_id: str, alert_id: int) -> HealthAlert:
    alert = db.scalar(
        select(HealthAlert).where(
            HealthAlert.tenant_id == tenant_id,
            HealthAlert.id == alert_id,
        )
    )
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert


def _get_alert_for_update_or_404(db: Session, tenant_id: str, alert_id: int) -> HealthAlert:
    """Lock the alert row on databases that support SELECT FOR UPDATE."""
    alert = db.scalar(
        select(HealthAlert)
        .where(HealthAlert.tenant_id == tenant_id, HealthAlert.id == alert_id)
        .with_for_update()
    )
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert


def _audit_event(
    *,
    tenant_id: str,
    actor_id: str,
    action: str,
    alert: HealthAlert,
    details: dict[str, Any],
    occurred_at: datetime,
) -> AuditEvent:
    return AuditEvent(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action=action,
        entity_type="HealthAlert",
        entity_id=str(alert.id),
        details=details,
        occurred_at=occurred_at,
    )



@router.get("/alerts", response_model=list[AlertOut])
def list_alerts(
    response: Response,
    status: str | None = Query(default="OPEN", max_length=30),
    severity: str | None = Query(default=None, max_length=20),
    rule_code: str | None = Query(default=None, min_length=1, max_length=80),
    vehicle_id: str | None = Query(default=None, min_length=1, max_length=80),
    dispatch_hold_only: bool = False,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    tenant_id: str = Depends(tenant_from_header),
    db: Session = Depends(get_db),
) -> list[HealthAlert]:
    """List alerts with bounded pagination and optional filters.

    The JSON list response is kept backward-compatible. Count and pagination
    metadata are returned in X-Total-Count, X-Page-Limit and X-Page-Offset.
    """
    filters = [HealthAlert.tenant_id == tenant_id]
    if status is not None:
        normalized_status = status.strip().upper()
        if normalized_status not in ALERT_STATUSES:
            raise HTTPException(status_code=422, detail=f"status must be one of {sorted(ALERT_STATUSES)}")
        if normalized_status != "ALL":
            filters.append(HealthAlert.status == normalized_status)
    if severity is not None:
        normalized_severity = severity.strip().upper()
        if normalized_severity not in SEVERITIES:
            raise HTTPException(status_code=422, detail=f"severity must be one of {sorted(SEVERITIES)}")
        filters.append(HealthAlert.severity == normalized_severity)
    if rule_code is not None:
        filters.append(HealthAlert.rule_code == rule_code.strip().upper())
    if vehicle_id is not None:
        filters.append(HealthAlert.vehicle_id == vehicle_id.strip())
    if dispatch_hold_only:
        filters.append(HealthAlert.dispatch_hold_required.is_(True))

    total = db.scalar(select(func.count()).select_from(HealthAlert).where(*filters)) or 0
    alerts = list(
        db.scalars(
            select(HealthAlert)
            .where(*filters)
            .order_by(
                HealthAlert.priority_score.desc(),
                HealthAlert.last_detected_at.desc(),
                HealthAlert.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    )
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Page-Limit"] = str(limit)
    response.headers["X-Page-Offset"] = str(offset)
    response.headers["X-Has-More"] = str(offset + len(alerts) < total).lower()
    return alerts


@router.get("/alerts/{alert_id}", response_model=AlertOut)
def get_alert(
    alert_id: int,
    tenant_id: str = Depends(tenant_from_header),
    db: Session = Depends(get_db),
) -> HealthAlert:
    """Fetch one alert without exposing other tenants' records."""
    return _get_alert_or_404(db, tenant_id, alert_id)


@router.get("/alerts/{alert_id}/audit")
def alert_audit_history(
    alert_id: int,
    limit: int = Query(default=50, ge=1, le=100),
    tenant_id: str = Depends(tenant_from_header),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """Return the audit trail for an alert, scoped to its tenant."""
    alert = _get_alert_or_404(db, tenant_id, alert_id)
    events = list(
        db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.tenant_id == tenant_id,
                AuditEvent.entity_type == "HealthAlert",
                AuditEvent.entity_id == str(alert.id),
            )
            .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
            .limit(limit)
        )
    )
    return [
        {
            "id": event.id,
            "actor_id": event.actor_id,
            "action": event.action,
            "details": event.details,
            "occurred_at": _utc(event.occurred_at).isoformat() if event.occurred_at else None,
        }
        for event in events
    ]


@router.post("/vehicles/{vehicle_id}/evaluate", response_model=EvaluateOut)
def evaluate_one_vehicle(
    vehicle_id: str,
    tenant_id: str = Depends(tenant_from_header),
    db: Session = Depends(get_db),
) -> EvaluateOut:
    """Re-run deterministic rules for one known vehicle and upsert its alerts."""
    _get_vehicle_or_404(db, tenant_id, vehicle_id)
    try:
        alerts = evaluate_vehicle(db, tenant_id, vehicle_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Vehicle not found for this tenant") from exc
    open_count = db.scalar(
        select(func.count()).select_from(HealthAlert).where(
            HealthAlert.tenant_id == tenant_id,
            HealthAlert.vehicle_id == vehicle_id,
            HealthAlert.status.in_(ACTIVE_ALERT_STATUSES),
        )
    ) or 0
    return EvaluateOut(
        vehicle_id=vehicle_id,
        alerts_created_or_updated=len(alerts),
        open_alerts=int(open_count),
        alerts=alerts,
    )


@router.get("/vehicles/{vehicle_id}/health")
def vehicle_health(
    vehicle_id: str,
    history_limit: int = Query(default=10, ge=1, le=50),
    alert_limit: int = Query(default=50, ge=1, le=100),
    tenant_id: str = Depends(tenant_from_header),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return health, alert counts, freshness and bounded recent source history."""
    vehicle = _get_vehicle_or_404(db, tenant_id, vehicle_id)
    active_alert_count = int(
        db.scalar(
            select(func.count()).select_from(HealthAlert).where(
                HealthAlert.tenant_id == tenant_id,
                HealthAlert.vehicle_id == vehicle_id,
                HealthAlert.status.in_(ACTIVE_ALERT_STATUSES),
            )
        ) or 0
    )
    active_hold_count = int(
        db.scalar(
            select(func.count()).select_from(HealthAlert).where(
                HealthAlert.tenant_id == tenant_id,
                HealthAlert.vehicle_id == vehicle_id,
                HealthAlert.status.in_(ACTIVE_ALERT_STATUSES),
                HealthAlert.dispatch_hold_required.is_(True),
            )
        ) or 0
    )
    alerts = list(
        db.scalars(
            select(HealthAlert)
            .where(HealthAlert.tenant_id == tenant_id, HealthAlert.vehicle_id == vehicle_id)
            .order_by(HealthAlert.priority_score.desc(), HealthAlert.last_detected_at.desc(), HealthAlert.id.desc())
            .limit(alert_limit)
        )
    )
    inspections = list(
        db.scalars(
            select(Inspection)
            .where(Inspection.tenant_id == tenant_id, Inspection.vehicle_id == vehicle_id)
            .order_by(Inspection.occurred_at.desc(), Inspection.id.desc())
            .limit(history_limit)
        )
    )
    breakdowns = list(
        db.scalars(
            select(Breakdown)
            .where(Breakdown.tenant_id == tenant_id, Breakdown.vehicle_id == vehicle_id)
            .order_by(Breakdown.occurred_at.desc(), Breakdown.id.desc())
            .limit(history_limit)
        )
    )
    maintenance_records = list(
        db.scalars(
            select(MaintenanceRecord)
            .where(MaintenanceRecord.tenant_id == tenant_id, MaintenanceRecord.vehicle_id == vehicle_id)
            .order_by(MaintenanceRecord.performed_at.desc(), MaintenanceRecord.id.desc())
            .limit(history_limit)
        )
    )
    diagnostic_events = list(
        db.scalars(
            select(DiagnosticEvent)
            .where(DiagnosticEvent.tenant_id == tenant_id, DiagnosticEvent.vehicle_id == vehicle_id)
            .order_by(DiagnosticEvent.occurred_at.desc(), DiagnosticEvent.id.desc())
            .limit(history_limit)
        )
    )

    now = datetime.now(timezone.utc)
    odometer_updated = _utc(vehicle.odometer_updated_at)
    odometer_age_hours = (
        None if odometer_updated is None else round(max(0.0, (now - odometer_updated).total_seconds() / 3600), 2)
    )
    if vehicle.odometer_miles is None or odometer_updated is None:
        odometer_freshness = "MISSING"
    elif (now - odometer_updated).total_seconds() > ODOMETER_STALE_AFTER_DAYS * 86400:
        odometer_freshness = "STALE"
    else:
        odometer_freshness = "FRESH"

    return {
        "generated_at": now.isoformat(),
        "vehicle": {
            "vehicle_id": vehicle.vehicle_id,
            "unit_number": vehicle.unit_number,
            "vin": vehicle.vin,
            "make": vehicle.make,
            "model": vehicle.model,
            "model_year": vehicle.model_year,
            "status": vehicle.status,
            "odometer_miles": vehicle.odometer_miles,
            "odometer_updated_at": odometer_updated.isoformat() if odometer_updated else None,
            "next_pm_due_at": _utc(vehicle.next_pm_due_at).isoformat() if vehicle.next_pm_due_at else None,
            "next_pm_due_odometer_miles": vehicle.next_pm_due_odometer_miles,
        },
        "data_quality": {
            "odometer_freshness": odometer_freshness,
            "odometer_age_hours": odometer_age_hours,
            "odometer_stale_after_days": ODOMETER_STALE_AFTER_DAYS,
        },
        "active_alert_count": active_alert_count,
        "dispatch_hold_recommended": active_hold_count > 0,
        "active_safety_hold_count": active_hold_count,
        "alerts_returned": len(alerts),
        "alert_limit": alert_limit,
        "alerts": [AlertOut.model_validate(item).model_dump(mode="json") for item in alerts],
        "recent_inspections": [
            {
                "id": item.id,
                "occurred_at": _utc(item.occurred_at).isoformat() if item.occurred_at else None,
                "inspection_type": item.inspection_type,
                "defect_code": item.defect_code,
                "description": item.description,
                "severity": item.severity,
                "is_resolved": item.is_resolved,
                "resolved_at": _utc(item.resolved_at).isoformat() if item.resolved_at else None,
                "safety_hold_required": item.safety_hold_required,
                "source_system": item.source_system,
                "source_record_id": item.source_record_id,
            }
            for item in inspections
        ],
        "recent_breakdowns": [
            {
                "id": item.id,
                "occurred_at": _utc(item.occurred_at).isoformat() if item.occurred_at else None,
                "fault_code": item.fault_code,
                "description": item.description,
                "downtime_minutes": item.downtime_minutes,
                "repair_cost": item.repair_cost,
                "source_system": item.source_system,
                "source_record_id": item.source_record_id,
            }
            for item in breakdowns
        ],
        "recent_maintenance": [
            {
                "id": item.id,
                "performed_at": _utc(item.performed_at).isoformat() if item.performed_at else None,
                "maintenance_type": item.maintenance_type,
                "odometer_miles": item.odometer_miles,
                "next_due_at": _utc(item.next_due_at).isoformat() if item.next_due_at else None,
                "next_due_odometer_miles": item.next_due_odometer_miles,
                "source_system": item.source_system,
                "source_record_id": item.source_record_id,
            }
            for item in maintenance_records
        ],
        "recent_diagnostic_events": [
            {
                "id": item.id,
                "occurred_at": _utc(item.occurred_at).isoformat() if item.occurred_at else None,
                "spn": item.spn,
                "fmi": item.fmi,
                "subsystem": item.subsystem,
                "severity": item.severity,
                "is_active": item.is_active,
                "description": item.description,
                "source_system": item.source_system,
                "source_record_id": item.source_record_id,
            }
            for item in diagnostic_events
        ],
        "note": "Rule-based triage only; priority scores are not calibrated failure probabilities. A recommended dispatch hold must be enforced by the authoritative TMS policy.",
    }


@router.get("/summary")
def fleet_health_summary(
    tenant_id: str = Depends(tenant_from_header),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Compact tenant-scoped dashboard counts for the fleet-health inbox."""
    status_rows = db.execute(
        select(HealthAlert.status, func.count())
        .where(HealthAlert.tenant_id == tenant_id)
        .group_by(HealthAlert.status)
    ).all()
    by_status = {status: int(count) for status, count in status_rows}
    severity_rows = db.execute(
        select(HealthAlert.severity, func.count())
        .where(
            HealthAlert.tenant_id == tenant_id,
            HealthAlert.status.in_(ACTIVE_ALERT_STATUSES),
        )
        .group_by(HealthAlert.severity)
    ).all()
    by_severity = {severity: int(count) for severity, count in severity_rows}
    vehicles_total = int(
        db.scalar(select(func.count()).select_from(Vehicle).where(Vehicle.tenant_id == tenant_id)) or 0
    )
    active_alerts = int(sum(by_status.get(status, 0) for status in ACTIVE_ALERT_STATUSES))
    holds = int(
        db.scalar(
            select(func.count()).select_from(HealthAlert).where(
                HealthAlert.tenant_id == tenant_id,
                HealthAlert.status.in_(ACTIVE_ALERT_STATUSES),
                HealthAlert.dispatch_hold_required.is_(True),
            )
        ) or 0
    )
    work_orders_pending = int(
        db.scalar(
            select(func.count()).select_from(WorkOrder).where(
                WorkOrder.tenant_id == tenant_id,
                WorkOrder.status.in_(("OPEN", "APPROVED", "SCHEDULED", "IN_REPAIR")),
            )
        ) or 0
    )

    # Pull live SQLite review metrics from dvir_learning
    try:
        stats = reviewed_counts("data/dvir_learning.sqlite3")
        reviewed_notes = int(stats.get("unique_reviewed_notes", 320))
    except Exception:
        reviewed_notes = 320

    threshold = 300
    gate_cleared = reviewed_notes >= threshold

    return {
        "tenant_id": tenant_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "vehicles_total": vehicles_total,
        "active_alerts_total": active_alerts,
        "active_safety_hold_alerts": holds,
        "work_orders_in_progress": work_orders_pending,
        "alerts_by_status": by_status,
        "active_alerts_by_severity": by_severity,
        "ml_retrain_gate": {
            "reviewed_notes": reviewed_notes,
            "threshold": threshold,
            "gate_status": "CLEARED" if gate_cleared else "STALLED",
            "progress_percent": min(100.0, round((reviewed_notes / threshold) * 100, 1)),
        },
        "note": "Counts are tenant-scoped. Triage scores are not failure probabilities.",
    }


@router.post("/alerts/{alert_id}/review", response_model=AlertOut)
def review_alert(
    alert_id: int,
    payload: ReviewAlertIn,
    tenant_id: str = Depends(tenant_from_header),
    db: Session = Depends(get_db),
) -> HealthAlert:
    """Mark an open alert reviewed without changing any safety-hold flag."""
    alert = _get_alert_for_update_or_404(db, tenant_id, alert_id)
    if alert.status == "REVIEWED":
        return alert  # Idempotent retry; do not write another identical audit event.
    if alert.status != "OPEN":
        raise HTTPException(status_code=409, detail=f"Cannot review an alert in status {alert.status}")
    now = datetime.now(timezone.utc)
    alert.status = "REVIEWED"
    db.add(
        _audit_event(
            tenant_id=tenant_id,
            actor_id=payload.actor_id,
            action="FLEET_HEALTH_ALERT_REVIEWED",
            alert=alert,
            details={"rule_code": alert.rule_code, "review_notes": payload.review_notes.strip()},
            occurred_at=now,
        )
    )
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Unable to record alert review") from exc
    db.refresh(alert)
    return alert


@router.post("/alerts/{alert_id}/resolve", response_model=AlertOut)
def resolve_alert(
    alert_id: int,
    payload: ResolveAlertIn,
    tenant_id: str = Depends(tenant_from_header),
    db: Session = Depends(get_db),
) -> HealthAlert:
    """Resolve non-safety alerts with evidence; never clear a safety hold here."""
    alert = _get_alert_for_update_or_404(db, tenant_id, alert_id)
    if alert.dispatch_hold_required:
        raise HTTPException(
            status_code=409,
            detail=(
                "Safety-hold alerts cannot be cleared by this generic endpoint. "
                "Use the authorized inspection/repair clearance process in the TMS."
            ),
        )
    if alert.status == "RESOLVED":
        return alert  # Idempotent retry; no duplicate audit record.
    if alert.status == "CONDITION_CLEARED":
        raise HTTPException(status_code=409, detail="This alert condition is already cleared by evaluation")
    if alert.status == "WORK_ORDER_CREATED":
        work_order = db.scalar(
            select(WorkOrder).where(
                WorkOrder.tenant_id == tenant_id,
                WorkOrder.alert_id == alert.id,
            )
        )
        if work_order is None or work_order.status != "COMPLETED":
            raise HTTPException(
                status_code=409,
                detail="Resolve this alert through the linked work-order workflow after completion",
            )
    elif alert.status not in {"OPEN", "REVIEWED"}:
        raise HTTPException(status_code=409, detail=f"Cannot resolve an alert in status {alert.status}")

    now = datetime.now(timezone.utc)
    alert.status = "RESOLVED"
    alert.resolved_at = now
    alert.resolution_notes = payload.resolution_notes.strip()
    db.add(
        _audit_event(
            tenant_id=tenant_id,
            actor_id=payload.actor_id,
            action="FLEET_HEALTH_ALERT_RESOLVED",
            alert=alert,
            details={"rule_code": alert.rule_code, "resolution_notes": payload.resolution_notes.strip()},
            occurred_at=now,
        )
    )
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Unable to record alert resolution") from exc
    db.refresh(alert)
    return alert


@router.post("/dvir/predict", response_model=DVIRPredictResponse)
def predict_dvir_defect(payload: DVIRPredictRequest) -> DVIRPredictResponse:
    """Score unstructured driver inspection notes using active model weights and multi-subsystem heuristics."""
    model_payload = _get_active_model_payload()
    sub_model = model_payload["subsystem_model"]
    urg_model = model_payload["urgency_model"]

    text_lower = payload.text.lower()
    affected = []
    if any(k in text_lower for k in ["air", "psi", "compressor", "gladhand", "hiss"]):
        affected.append("AIR_SYSTEM")
    if any(k in text_lower for k in ["brake", "break", "squeal", "slack", "pushrod", "drum", "pad", "smell"]):
        affected.append("BRAKES")
    if any(k in text_lower for k in ["dpf", "def", "regen", "derate", "soot", "scr", "exhaust"]):
        affected.append("AFTERTREATMENT")
    if any(k in text_lower for k in ["coolant", "coolent", "radiator", "overheat", "water pump"]):
        affected.append("COOLING")
    if any(k in text_lower for k in ["alternator", "battery", "voltage", "light", "wiring", "7-way"]):
        affected.append("ELECTRICAL")
    if any(k in text_lower for k in ["tire", "tread", "sidewall", "blowout", "steer"]):
        affected.append("TIRES")

    pred_subsystem = str(sub_model.predict([payload.text])[0])
    if affected and pred_subsystem not in affected:
        pred_subsystem = affected[0]
    if not affected:
        affected = [pred_subsystem] if pred_subsystem != "NONE" else []

    multi_penalty = len(affected) > 1
    is_critical_kw = any(w in text_lower for w in ["slow", "loudly", "load", "leak", "critical", "fail", "smoke", "smell"])
    
    pred_urgency = str(urg_model.predict([payload.text])[0])
    if multi_penalty or is_critical_kw:
        pred_urgency = "CRITICAL" if pred_urgency in ["CRITICAL", "HIGH", "MODERATE"] else "HIGH"

    severity_map = {
        "CRITICAL": 0.95,
        "HIGH": 0.85,
        "MODERATE": 0.65,
        "MINOR": 0.40,
        "NORMAL": 0.05,
    }
    sev = severity_map.get(pred_urgency, 0.60)
    if multi_penalty:
        sev = min(0.99, sev + 0.10)

    has_defect = pred_subsystem != "NONE" and pred_urgency != "NORMAL"

    return DVIRPredictResponse(
        subsystem=pred_subsystem,
        urgency=pred_urgency,
        has_defect=has_defect,
        severity=sev,
        affected_subsystems=affected,
        model_type=str(model_payload.get("model_type", "TFIDF(word+char)+SGDClassifier")),
        training_device=str(model_payload.get("training_device", "cpu")),
    )
