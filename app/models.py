"""Reference SQLAlchemy models for the Fleet Health MVP.

Map these concepts onto the existing TMS models during integration; do not create
parallel Driver/Vehicle/WorkOrder tables if equivalent tables already exist.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Vehicle(Base):
    __tablename__ = "fh_vehicles"
    __table_args__ = (UniqueConstraint("tenant_id", "unit_number", name="uq_fh_vehicle_tenant_unit"),)

    vehicle_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True, default="demo")
    unit_number: Mapped[str] = mapped_column(String(80), nullable=False)
    vin: Mapped[str | None] = mapped_column(String(40), nullable=True)
    make: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="ACTIVE", nullable=False)
    odometer_miles: Mapped[float | None] = mapped_column(Float, nullable=True)
    odometer_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_pm_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_pm_due_odometer_miles: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class Inspection(Base):
    __tablename__ = "fh_inspections"
    __table_args__ = (
        Index("ix_fh_inspection_vehicle_occurred", "tenant_id", "vehicle_id", "occurred_at"),
        UniqueConstraint("tenant_id", "source_system", "source_record_id", name="uq_fh_inspection_source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True, default="demo")
    vehicle_id: Mapped[str] = mapped_column(ForeignKey("fh_vehicles.vehicle_id"), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    inspection_type: Mapped[str] = mapped_column(String(40), default="DVIR", nullable=False)
    defect_code: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(20), default="MEDIUM", nullable=False)
    is_resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set only by a reviewed, approved defect-severity policy or authorized staff.
    # Never let an LLM or untrusted free-text directly set this flag.
    safety_hold_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_system: Mapped[str] = mapped_column(String(80), default="TMS", nullable=False)
    source_record_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Breakdown(Base):
    __tablename__ = "fh_breakdowns"
    __table_args__ = (
        Index("ix_fh_breakdown_vehicle_occurred", "tenant_id", "vehicle_id", "occurred_at"),
        UniqueConstraint("tenant_id", "source_system", "source_record_id", name="uq_fh_breakdown_source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True, default="demo")
    vehicle_id: Mapped[str] = mapped_column(ForeignKey("fh_vehicles.vehicle_id"), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    fault_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    downtime_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    repair_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_system: Mapped[str] = mapped_column(String(80), default="TMS", nullable=False)
    source_record_id: Mapped[str | None] = mapped_column(String(120), nullable=True)


class MaintenanceRecord(Base):
    __tablename__ = "fh_maintenance_records"
    __table_args__ = (
        Index("ix_fh_maintenance_vehicle_performed", "tenant_id", "vehicle_id", "performed_at"),
        UniqueConstraint("tenant_id", "source_system", "source_record_id", name="uq_fh_maintenance_source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True, default="demo")
    vehicle_id: Mapped[str] = mapped_column(ForeignKey("fh_vehicles.vehicle_id"), index=True)
    performed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    maintenance_type: Mapped[str] = mapped_column(String(100), nullable=False)
    odometer_miles: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_due_odometer_miles: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_system: Mapped[str] = mapped_column(String(80), default="TMS", nullable=False)
    source_record_id: Mapped[str | None] = mapped_column(String(120), nullable=True)


class OdometerReading(Base):
    """Immutable source snapshot history; Vehicle holds only the latest reading."""
    __tablename__ = "fh_odometer_readings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source_system", "source_record_id", name="uq_fh_odometer_source"),
        Index("ix_fh_odometer_vehicle_reading", "tenant_id", "vehicle_id", "reading_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True, default="demo")
    vehicle_id: Mapped[str] = mapped_column(ForeignKey("fh_vehicles.vehicle_id"), index=True)
    source_system: Mapped[str] = mapped_column(String(80), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(120), nullable=False)
    reading_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    odometer_miles: Mapped[float] = mapped_column(Float, nullable=False)


class DiagnosticEvent(Base):
    """Idempotently ingested J1939 diagnostic event; an event is not a safety hold by itself."""
    __tablename__ = "fh_diagnostic_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source_system", "source_record_id", name="uq_fh_dtc_source"),
        Index("ix_fh_dtc_vehicle_time", "tenant_id", "vehicle_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True, default="demo")
    vehicle_id: Mapped[str] = mapped_column(ForeignKey("fh_vehicles.vehicle_id"), index=True)
    source_system: Mapped[str] = mapped_column(String(80), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(120), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    spn: Mapped[int] = mapped_column(Integer, nullable=False)
    fmi: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    subsystem: Mapped[str] = mapped_column(String(80), default="GENERAL", nullable=False)
    severity: Mapped[str] = mapped_column(String(20), default="MEDIUM", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    odometer_miles: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class HealthAlert(Base):
    __tablename__ = "fh_health_alerts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dedupe_key", name="uq_fh_alert_tenant_dedupe"),
        Index("ix_fh_alert_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True, default="demo")
    vehicle_id: Mapped[str] = mapped_column(ForeignKey("fh_vehicles.vehicle_id"), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(240), nullable=False)
    rule_code: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    priority_score: Mapped[int] = mapped_column(Integer, nullable=False)  # Priority score, not failure probability.
    dispatch_hold_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="OPEN", nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    first_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class WorkOrder(Base):
    __tablename__ = "fh_work_orders"
    __table_args__ = (
        Index("ix_fh_work_order_tenant_status", "tenant_id", "status"),
        UniqueConstraint("tenant_id", "alert_id", name="uq_fh_work_order_tenant_alert"),
        UniqueConstraint("tenant_id", "request_key", name="uq_fh_work_order_request_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True, default="demo")
    vehicle_id: Mapped[str] = mapped_column(ForeignKey("fh_vehicles.vehicle_id"), index=True)
    alert_id: Mapped[int | None] = mapped_column(ForeignKey("fh_health_alerts.id"), nullable=True)
    request_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(20), default="MEDIUM", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="OPEN", nullable=False)
    requested_by: Mapped[str] = mapped_column(String(120), nullable=False)
    assigned_to: Mapped[str | None] = mapped_column(String(120), nullable=True)
    service_center_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completion_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class AuditEvent(Base):
    __tablename__ = "fh_audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True, default="demo")
    actor_id: Mapped[str] = mapped_column(String(120), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(120), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
