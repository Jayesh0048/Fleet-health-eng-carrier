from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Vehicle, DiagnosticEvent, Inspection

router = APIRouter(prefix="/api/v1/webhooks", tags=["Shop Webhooks"])

@router.post("/work-order-status")
def handle_work_order_webhook(payload: dict, db: Session = Depends(get_db)):
    """
    Receives shop status updates. When status is COMPLETED, 
    releases dispatch holds and deactivates resolved DTCs.
    """
    unit_number = payload.get("unit_number")
    status = payload.get("status")  # e.g., "COMPLETED"
    
    if status == "COMPLETED":
        # 1. Clear active diagnostic codes for this unit
        active_dtcs = db.query(DiagnosticEvent).filter(
            DiagnosticEvent.vehicle_id == unit_number,
            DiagnosticEvent.is_active == True
        ).all()
        for dtc in active_dtcs:
            dtc.is_active = False
            
        # 2. Mark safety inspections as resolved
        inspections = db.query(Inspection).filter(
            Inspection.vehicle_id == unit_number,
            Inspection.is_resolved == False
        ).all()
        for insp in inspections:
            insp.is_resolved = True
            
        db.commit()
        return {"success": True, "message": f"Dispatch holds released for unit {unit_number}"}
        
    return {"success": True, "status": "acknowledged"}