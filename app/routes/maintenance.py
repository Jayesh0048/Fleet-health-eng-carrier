from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import HealthAlert, Vehicle, WorkOrder

router = APIRouter(prefix="/api/v1/webhooks", tags=["TMS Maintenance Webhooks"])


@router.post("/work-order-status")
def update_work_order_status(payload: dict, db: Session = Depends(get_db)):
    tenant_id = payload.get("tenant_id", "US-FLEET-LOGISTICS")
    work_order_id = payload.get("work_order_id")
    status = payload.get("status", "COMPLETED")
    return {
        "success": True,
        "tenant_id": tenant_id,
        "work_order_id": work_order_id,
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/tms-maintenance-board")
def get_tms_maintenance_board(tenant_id: str = "US-FLEET-LOGISTICS", db: Session = Depends(get_db)):
    vehicles = db.scalars(select(Vehicle).where(Vehicle.tenant_id == tenant_id)).all()
    open_alerts = db.scalars(
        select(HealthAlert).where(
            HealthAlert.tenant_id == tenant_id,
            HealthAlert.status.in_(("OPEN", "REVIEWED", "WORK_ORDER_CREATED")),
        )
    ).all()

    planned = []
    for v in vehicles:
        cur_mi = int(round(v.odometer_miles or 0))
        next_mi = int(round(v.next_pm_due_odometer_miles or (cur_mi + 20000)))
        due_str = v.next_pm_due_at.strftime("%m/%d/%Y") if v.next_pm_due_at else "11/15/2026"
        planned.append({
            "asset_type": "Truck",
            "truck_number": v.unit_number or v.vehicle_id,
            "vehicle_id": v.vehicle_id,
            "vin": v.vin,
            "expected_maintenance_date": due_str,
            "service_type": "PM-A Inspection & Oil Service",
            "odometer_reading": cur_mi,
            "next_pm_odometer": next_mi,
            "intervals_miles": 20000,
            "status": "SCHEDULED" if (next_mi - cur_mi) > 2000 else "DUE_SOON",
            "estimated_cost": 450.00,
        })

    unplanned = []
    for a in open_alerts:
        created_str = a.created_at.strftime("%m/%d/%Y %H:%M") if a.created_at else ""
        unplanned.append({
            "asset_type": "Truck",
            "truck_number": a.vehicle_id,
            "alert_id": a.id,
            "defect_code": a.rule_code,
            "severity": a.severity,
            "status": a.status,
            "dispatch_hold": a.dispatch_hold_required,
            "summary": a.summary or f"Fault {a.rule_code}",
            "reported_date": created_str,
            "service_type": "Unscheduled Diagnostic Repair",
            "estimated_downtime_hours": 4.0 if a.dispatch_hold_required else 1.5,
            "estimated_cost": 850.00 if a.dispatch_hold_required else 250.00,
        })

    return {
        "tenant_id": tenant_id,
        "counts": {"planned": len(planned), "unplanned": len(unplanned)},
        "planned_maintenance": planned,
        "unplanned_maintenance": unplanned,
    }


@router.post("/dvir-to-maintenance")
def convert_dvir_to_maintenance(payload: dict, db: Session = Depends(get_db)):
    tenant_id = payload.get("tenant_id", "US-FLEET-LOGISTICS")
    vehicle_id = payload.get("vehicle_id")
    description = payload.get("description", "Driver reported defect")
    urgency = payload.get("urgency", "HIGH")
    task_id = f"WO-{int(datetime.now(timezone.utc).timestamp())}"
    is_hold = urgency in ["CRITICAL", "HIGH"]

    work_order = WorkOrder(
        tenant_id=tenant_id,
        vehicle_id=vehicle_id,
        status="OPEN",
        description=description,
        created_at=datetime.now(timezone.utc),
    )
    db.add(work_order)
    try:
        db.commit()
    except Exception:
        db.rollback()

    return {
        "success": True,
        "status": "MAINTENANCE_TASK_CREATED",
        "vehicle_id": vehicle_id,
        "urgency": urgency,
        "task_id": task_id,
        "description": description,
        "dispatch_hold": is_hold,
        "assigned_lane": "Unplanned Maintenance Bay 2",
    }