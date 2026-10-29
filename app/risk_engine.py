"""Explainable rule-based fleet risk detection for the initial MVP.

This module deliberately does not claim to predict failure probability. The
priority_score is a triage score. Safety hold policies must be configured and
approved for the operator's jurisdiction and vehicle class.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditEvent, Breakdown, DiagnosticEvent, HealthAlert, Inspection, Vehicle
from app.services.nlp_parser import AdvancedDVIRNLPParser

_nlp_parser = AdvancedDVIRNLPParser()

# Free-text NLP output is capped below CRITICAL/dispatch-hold severity on purpose.
# Only an approved structured defect_code (see rule 1) may require a dispatch hold or
# reach CRITICAL severity -- an LLM/regex parse of driver free text never does.
_NLP_URGENCY_TO_SEVERITY = {"CRITICAL": "HIGH", "MODERATE": "MEDIUM"}
_NLP_URGENCY_TO_PRIORITY = {"CRITICAL": 65, "MODERATE": 45}
_NLP_LOOKBACK_DAYS = 30


@dataclass(frozen=True)
class AlertCandidate:
    dedupe_key: str
    rule_code: str
    title: str
    description: str
    recommendation: str
    severity: str
    priority_score: int
    dispatch_hold_required: bool
    evidence: dict[str, Any]


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def build_candidates(
    vehicle: Any,
    inspections: Iterable[Any],
    breakdowns: Iterable[Any],
    diagnostic_events: Iterable[Any] = (),
    now: datetime | None = None,
) -> list[AlertCandidate]:
    """Build deterministic alerts from current state and the available history."""
    current = as_utc(now or datetime.now(timezone.utc))
    assert current is not None
    inspections = list(inspections)
    breakdowns = list(breakdowns)
    diagnostic_events = list(diagnostic_events)
    candidates: list[AlertCandidate] = []

    # 1) Explicitly approved safety-hold mapping on unresolved inspections.
    # Group same-code inspections into one alert so repeat evaluation cannot return
    # duplicate candidates for the same dedupe key. A hold is never auto-cleared.
    safety_groups: dict[str, list[Any]] = {}
    for item in inspections:
        if item.is_resolved or not bool(item.safety_hold_required):
            continue
        code = (item.defect_code or "UNCLASSIFIED").strip().upper()
        safety_groups.setdefault(code, []).append(item)

    for code, items in safety_groups.items():
        latest = max(items, key=lambda item: as_utc(getattr(item, "occurred_at", None)) or current)
        candidates.append(
            AlertCandidate(
                dedupe_key=f"{vehicle.vehicle_id}:SAFETY_HOLD:{code}",
                rule_code="SAFETY_HOLD_REQUIRED",
                title=f"Unresolved safety hold: {code}",
                description=(
                    f"{len(items)} unresolved inspection defect record(s) ({code}) are marked by "
                    "the approved defect policy as requiring a dispatch hold."
                ),
                recommendation=(
                    "Keep the vehicle out of dispatch under the configured safety policy. "
                    "Obtain authorized inspection/repair clearance and record it in the source TMS."
                ),
                severity="CRITICAL",
                priority_score=100,
                dispatch_hold_required=True,
                evidence={
                    "inspection_ids": [getattr(item, "id", None) for item in items],
                    "defect_code": code,
                    "latest_reported_at": str(getattr(latest, "occurred_at", None)),
                    "source_systems": sorted({str(getattr(item, "source_system", None)) for item in items}),
                },
            )
        )

    # 2) Repeated defect pattern in a 90-day window. Counts inspections and breakdowns.
    window_90 = current - timedelta(days=90)
    defect_events: dict[str, list[dict[str, Any]]] = {}
    for item in inspections:
        occurred = as_utc(getattr(item, "occurred_at", None))
        code = (getattr(item, "defect_code", None) or "").strip().upper()
        if code and occurred and occurred >= window_90:
            defect_events.setdefault(code, []).append({"type": "inspection", "id": getattr(item, "id", None), "at": occurred.isoformat()})
    for item in breakdowns:
        occurred = as_utc(getattr(item, "occurred_at", None))
        code = (getattr(item, "fault_code", None) or "").strip().upper()
        if code and occurred and occurred >= window_90:
            defect_events.setdefault(code, []).append({"type": "breakdown", "id": getattr(item, "id", None), "at": occurred.isoformat()})

    for code, events in defect_events.items():
        if len(events) < 2:
            continue
        candidates.append(
            AlertCandidate(
                dedupe_key=f"{vehicle.vehicle_id}:RECURRING_DEFECT:{code}",
                rule_code="RECURRING_DEFECT_90D",
                title=f"Recurring defect detected: {code}",
                description=f"{len(events)} inspection/breakdown records for defect code {code} were found in the last 90 days.",
                recommendation="Review prior work orders and repair outcomes; inspect for root cause before replacing parts repeatedly.",
                severity="HIGH",
                priority_score=80,
                dispatch_hold_required=False,
                evidence={"defect_code": code, "event_count_90d": len(events), "events": events[:10]},
            )
        )

    # 3) Recent breakdown alert to make repeat downtime visible.
    window_30 = current - timedelta(days=30)
    recent = [b for b in breakdowns if (as_utc(getattr(b, "occurred_at", None)) or current) >= window_30]
    if recent:
        latest = max(recent, key=lambda b: as_utc(getattr(b, "occurred_at", None)) or current)
        code = (getattr(latest, "fault_code", None) or "UNCLASSIFIED").strip().upper()
        latest_at = as_utc(getattr(latest, "occurred_at", None))
        candidates.append(
            AlertCandidate(
                dedupe_key=f"{vehicle.vehicle_id}:RECENT_BREAKDOWN:{code}",
                rule_code="RECENT_BREAKDOWN_30D",
                title=f"Recent breakdown requires follow-up: {code}",
                description=f"A breakdown was reported within the last 30 days{f' on {latest_at.date().isoformat()}' if latest_at else ''}.",
                recommendation="Verify repair completion and review whether a follow-up inspection or preventive maintenance is due.",
                severity="HIGH",
                priority_score=75,
                dispatch_hold_required=False,
                evidence={"breakdown_id": getattr(latest, "id", None), "fault_code": code, "occurred_at": latest_at.isoformat() if latest_at else None},
            )
        )

    # 4) Free-text DVIR symptom pattern (NLP-derived). Unresolved inspections only;
    # resolved inspections' free text is no longer current. This NEVER sets
    # dispatch_hold_required and is capped below CRITICAL -- see module docstring note
    # and the comment on Inspection.safety_hold_required in app/models.py.
    window_30 = current - timedelta(days=_NLP_LOOKBACK_DAYS)
    nlp_hits_by_subsystem: dict[str, list[dict[str, Any]]] = {}
    for item in inspections:
        if item.is_resolved:
            continue
        occurred = as_utc(getattr(item, "occurred_at", None))
        if not occurred or occurred < window_30:
            continue
        description = getattr(item, "description", None)
        if not description:
            continue
        parsed = _nlp_parser.parse(description)
        if not parsed.has_defect or parsed.urgency not in _NLP_URGENCY_TO_SEVERITY:
            continue
        nlp_hits_by_subsystem.setdefault(parsed.subsystem, []).append({
            "inspection_id": getattr(item, "id", None),
            "urgency": parsed.urgency,
            "severity_score": parsed.severity,
            "symptoms": parsed.detected_symptoms,
            "occurred_at": occurred.isoformat(),
        })

    for subsystem, hits in nlp_hits_by_subsystem.items():
        worst_urgency = max(hits, key=lambda h: h["severity_score"])["urgency"]
        candidates.append(
            AlertCandidate(
                dedupe_key=f"{vehicle.vehicle_id}:NLP_PATTERN:{subsystem}",
                rule_code="DVIR_FREE_TEXT_PATTERN",
                title=f"Driver notes suggest a {subsystem.lower()} issue",
                description=(
                    f"{len(hits)} unresolved DVIR note(s) in the last {_NLP_LOOKBACK_DAYS} days "
                    f"mention {subsystem.lower()} symptoms (parsed from free text, not a structured code)."
                ),
                recommendation="Review the driver's written notes directly and confirm with a structured inspection before any dispatch decision.",
                severity=_NLP_URGENCY_TO_SEVERITY[worst_urgency],
                priority_score=_NLP_URGENCY_TO_PRIORITY[worst_urgency],
                dispatch_hold_required=False,
                evidence={"subsystem": subsystem, "source": "nlp_free_text", "hits": hits[:10]},
            )
        )

    # Active high/critical J1939 faults generate explainable maintenance alerts.
    # They are NOT dispatch holds by default; that requires separately approved policy.
    high_active: dict[tuple[int, int, str], list[Any]] = {}
    for event in diagnostic_events:
        if not bool(getattr(event, "is_active", False)):
            continue
        severity = str(getattr(event, "severity", "MEDIUM") or "MEDIUM").upper()
        if severity not in {"CRITICAL", "HIGH"}:
            continue
        spn = int(getattr(event, "spn", 0))
        fmi = int(getattr(event, "fmi", 0))
        subsystem = str(getattr(event, "subsystem", "GENERAL") or "GENERAL").strip().upper()
        high_active.setdefault((spn, fmi, subsystem), []).append(event)

    for (spn, fmi, subsystem), events in high_active.items():
        critical = any(str(getattr(event, "severity", "")).upper() == "CRITICAL" for event in events)
        severity = "CRITICAL" if critical else "HIGH"
        candidates.append(
            AlertCandidate(
                dedupe_key=f"{vehicle.vehicle_id}:J1939_DTC:{spn}:{fmi}:{subsystem}",
                rule_code="ACTIVE_J1939_DTC",
                title=f"Active J1939 fault: SPN {spn} / FMI {fmi}",
                description=(
                    f"{len(events)} active high/critical diagnostic event(s) for {subsystem}; "
                    f"SPN {spn}, FMI {fmi}. Review the source fault definition and vehicle symptoms."
                ),
                recommendation=(
                    "Review the OEM fault definition and live vehicle condition, validate severity, "
                    "and create/approve a maintenance work order as appropriate. This alert alone "
                    "does not establish a dispatch hold."
                ),
                severity=severity,
                priority_score=95 if critical else 82,
                dispatch_hold_required=False,
                evidence={
                    "spn": spn,
                    "fmi": fmi,
                    "subsystem": subsystem,
                    "event_ids": [getattr(event, "id", None) for event in events],
                    "events": [
                        {
                            "source_system": getattr(event, "source_system", None),
                            "source_record_id": getattr(event, "source_record_id", None),
                            "occurred_at": (as_utc(getattr(event, "occurred_at", None)).isoformat()
                                            if as_utc(getattr(event, "occurred_at", None)) else None),
                            "description": getattr(event, "description", None),
                            "severity": getattr(event, "severity", None),
                        }
                        for event in events[:10]
                    ],
                },
            )
        )

    # 4) Preventive maintenance due by date or odometer.
    due_at = as_utc(getattr(vehicle, "next_pm_due_at", None))
    odometer = getattr(vehicle, "odometer_miles", None)
    due_odometer = getattr(vehicle, "next_pm_due_odometer_miles", None)
    date_overdue = bool(due_at and due_at < current)
    miles_overdue = bool(odometer is not None and due_odometer is not None and float(odometer) >= float(due_odometer))
    if date_overdue or miles_overdue:
        reasons = []
        if date_overdue:
            reasons.append(f"PM due date {due_at.date().isoformat()} has passed")
        if miles_overdue:
            reasons.append(f"odometer {odometer:g} mi has reached the PM threshold {due_odometer:g} mi")
        candidates.append(
            AlertCandidate(
                dedupe_key=f"{vehicle.vehicle_id}:PM_OVERDUE",
                rule_code="PREVENTIVE_MAINTENANCE_OVERDUE",
                title="Preventive maintenance is overdue",
                description="; ".join(reasons) + ".",
                recommendation="Review maintenance history and schedule the required service. Recalculate the next due date/odometer only after the service record is verified.",
                severity="HIGH",
                priority_score=70,
                dispatch_hold_required=False,
                evidence={"next_pm_due_at": due_at.isoformat() if due_at else None, "odometer_miles": odometer, "next_pm_due_odometer_miles": due_odometer},
            )
        )

    # 5) Data-quality warning: stale odometer is not itself a mechanical fault.
    odometer_updated = as_utc(getattr(vehicle, "odometer_updated_at", None))
    if odometer is None or odometer_updated is None or (current - odometer_updated) > timedelta(days=7):
        candidates.append(
            AlertCandidate(
                dedupe_key=f"{vehicle.vehicle_id}:ODOMETER_DATA_STALE",
                rule_code="ODOMETER_DATA_STALE",
                title="Vehicle odometer data is missing or stale",
                description="Current mileage cannot be trusted for mileage-based maintenance decisions.",
                recommendation="Sync a trusted odometer reading and timestamp; do not interpret this data-quality alert as proof of vehicle failure.",
                severity="MEDIUM",
                priority_score=35,
                dispatch_hold_required=False,
                evidence={"odometer_miles": odometer, "odometer_updated_at": odometer_updated.isoformat() if odometer_updated else None},
            )
        )

    # Highest priority first; deterministic tie-break for stable UI results.
    return sorted(candidates, key=lambda c: (-c.priority_score, c.rule_code, c.dedupe_key))


def evaluate_vehicle(db: Session, tenant_id: str, vehicle_id: str, now: datetime | None = None) -> list[HealthAlert]:
    vehicle = db.scalar(select(Vehicle).where(Vehicle.tenant_id == tenant_id, Vehicle.vehicle_id == vehicle_id))
    if vehicle is None:
        raise LookupError("Vehicle not found for this tenant")

    inspections = list(db.scalars(select(Inspection).where(Inspection.tenant_id == tenant_id, Inspection.vehicle_id == vehicle_id)))
    breakdowns = list(db.scalars(select(Breakdown).where(Breakdown.tenant_id == tenant_id, Breakdown.vehicle_id == vehicle_id)))
    diagnostic_events = list(db.scalars(select(DiagnosticEvent).where(DiagnosticEvent.tenant_id == tenant_id, DiagnosticEvent.vehicle_id == vehicle_id)))
    current = as_utc(now) or datetime.now(timezone.utc)
    candidates = build_candidates(vehicle, inspections, breakdowns, diagnostic_events=diagnostic_events, now=current)
    # Defensive de-duplication if a future rule emits the same key twice.
    candidates_by_key = {candidate.dedupe_key: candidate for candidate in candidates}
    candidates = sorted(candidates_by_key.values(), key=lambda c: (-c.priority_score, c.rule_code, c.dedupe_key))
    candidate_keys = {candidate.dedupe_key for candidate in candidates}
    results: list[HealthAlert] = []

    existing_alerts = list(db.scalars(select(HealthAlert).where(
        HealthAlert.tenant_id == tenant_id,
        HealthAlert.vehicle_id == vehicle_id,
    )))
    existing_by_key = {alert.dedupe_key: alert for alert in existing_alerts}

    # Clear a non-safety alert only when its deterministic rule no longer fires.
    # Safety holds and alerts already linked to a work order are never auto-cleared.
    for old_alert in existing_alerts:
        if (
            old_alert.dedupe_key not in candidate_keys
            and not old_alert.dispatch_hold_required
            and old_alert.status in {"OPEN", "REVIEWED"}
        ):
            old_alert.status = "CONDITION_CLEARED"
            db.add(AuditEvent(
                tenant_id=tenant_id,
                actor_id="system:rule-engine",
                action="FLEET_HEALTH_ALERT_CONDITION_CLEARED",
                entity_type="HealthAlert",
                entity_id=str(old_alert.id),
                details={"rule_code": old_alert.rule_code, "dedupe_key": old_alert.dedupe_key},
                occurred_at=current,
            ))

    for candidate in candidates:
        alert = existing_by_key.get(candidate.dedupe_key)
        if alert is None:
            alert = HealthAlert(
                tenant_id=tenant_id,
                vehicle_id=vehicle_id,
                dedupe_key=candidate.dedupe_key,
                rule_code=candidate.rule_code,
                title=candidate.title,
                description=candidate.description,
                recommendation=candidate.recommendation,
                severity=candidate.severity,
                priority_score=candidate.priority_score,
                dispatch_hold_required=candidate.dispatch_hold_required,
                status="OPEN",
                evidence=candidate.evidence,
                first_detected_at=current,
                last_detected_at=current,
            )
            db.add(alert)
            db.flush()
            db.add(AuditEvent(
                tenant_id=tenant_id,
                actor_id="system:rule-engine",
                action="FLEET_HEALTH_ALERT_CREATED",
                entity_type="HealthAlert",
                entity_id=str(alert.id),
                details={"rule_code": candidate.rule_code, "dedupe_key": candidate.dedupe_key},
                occurred_at=current,
            ))
        else:
            # Reopen only if the same condition genuinely recurs after a resolution/clear.
            if alert.status in {"RESOLVED", "CONDITION_CLEARED"}:
                previous_status = alert.status
                alert.status = "OPEN"
                alert.resolved_at = None
                alert.resolution_notes = None
                alert.first_detected_at = current
                db.add(AuditEvent(
                    tenant_id=tenant_id,
                    actor_id="system:rule-engine",
                    action="FLEET_HEALTH_ALERT_REOPENED",
                    entity_type="HealthAlert",
                    entity_id=str(alert.id),
                    details={"rule_code": candidate.rule_code, "previous_status": previous_status},
                    occurred_at=current,
                ))
            alert.title = candidate.title
            alert.description = candidate.description
            alert.recommendation = candidate.recommendation
            alert.severity = candidate.severity
            alert.priority_score = candidate.priority_score
            alert.dispatch_hold_required = candidate.dispatch_hold_required
            alert.evidence = candidate.evidence
            alert.last_detected_at = current
        results.append(alert)

    db.commit()
    for alert in results:
        db.refresh(alert)
    return results