from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.database import get_db
from app.models import Vehicle, DiagnosticEvent
from app.services.data_collector import FleetDataCollector
from app.services.risk_engine import evaluate_vehicle

router = APIRouter(prefix="/api/v1/ingestion", tags=["Data Ingestion"])

@router.post("/samsara/sync")
def sync_live_samsara_data(db: Session = Depends(get_db)):
    """Pulls live assets, fault codes, and DVIRs, then runs the Risk Engine."""
    tenant_id = "US-FLEET-LOGISTICS"
    
    collector = FleetDataCollector(db)
    result = collector.sync_samsara_data(account_id=tenant_id)
    
    if not result.get("success"):
        error_msg = result.get("message") or result.get("error")
        print(f"\n[!] SAMSARA SYNC FAILED: {error_msg}\n")
        raise HTTPException(status_code=500, detail=error_msg)
        
    vehicles = db.scalars(select(Vehicle).where(Vehicle.tenant_id == tenant_id)).all()
    
    alerts_generated = 0
    for vehicle in vehicles:
        try:
            alerts = evaluate_vehicle(db=db, tenant_id=tenant_id, vehicle_id=vehicle.vehicle_id)
            alerts_generated += len(alerts)
        except Exception as e:
            print(f"Failed to evaluate vehicle {vehicle.vehicle_id}: {e}")

    result["risk_alerts_evaluated"] = alerts_generated
    return result

@router.get("/ui-state")
def get_dashboard_state(db: Session = Depends(get_db)):
    """Serves the live database state directly to the frontend dashboard."""
    tenant_id = "US-FLEET-LOGISTICS"
    vehicles = db.scalars(select(Vehicle).where(Vehicle.tenant_id == tenant_id)).all()
    
    fleet_state = {}
    for v in vehicles:
        # Get active DTCs for this specific truck
        dtcs = db.scalars(select(DiagnosticEvent).where(
            DiagnosticEvent.vehicle_id == v.vehicle_id,
            DiagnosticEvent.is_active == True
        )).all()
        
        # Calculate Risk dynamically based on active Samsara faults
        is_critical = any(d.severity == "CRITICAL" for d in dtcs)
        risk_score = 0.85 if is_critical else (0.45 if dtcs else 0.08)
        
        fleet_state[v.vehicle_id] = {
            "id": v.vehicle_id,
            "unit_number": v.unit_number,
            "make": v.make,
            "model": v.model,
            "year": v.model_year,
            "vin": v.vin,
            "status": "MAINTENANCE_REQUIRED" if risk_score >= 0.70 else "ACTIVE",
            "odometer": v.odometer_miles or 0,
            "odometer_updated_at": (v.odometer_updated_at or v.created_at or datetime.now(timezone.utc)).isoformat(),
            "next_pm_odo": (v.odometer_miles or 0) + 20000,
            "next_pm_due_at": "2026-12-31",
            "calibrated_risk": risk_score,
            "primary_subsystem": "ENGINE" if dtcs else "NONE",
            "unresolved_safety_holds": [],
            "active_dtcs": [
                {
                    "spn": d.spn,
                    "fmi": d.fmi,
                    "severity": d.severity,
                    "subsystem": d.subsystem,
                    "description": d.description,
                    "is_active": d.is_active
                } for d in dtcs
            ],
            "work_order": None,
            "latest_dvir": "Live sync active. Awaiting recent driver notes."
        }
        
    return fleet_state