import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal, Base, engine
from app.models import ServiceCenter, DiagnosticCode, DVIRLog
from app.services.data_collector import FleetDataCollector
from app.services.risk_engine import FleetRiskEngine


def run_seed():
    print("[*] Initializing Fleet Health database schema...")
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    try:
        collector = FleetDataCollector(db)
        risk_engine = FleetRiskEngine(db)

        if db.query(ServiceCenter).count() == 0:
            shops = [
                ServiceCenter(name="Love's Truck Care - Dallas", city="Dallas", state="TX", latitude=32.6518, longitude=-96.7460, hourly_labor_rate=135.0, is_preferred_partner=True, contact_phone="214-555-0192"),
                ServiceCenter(name="TA Truck Service - Atlanta", city="Atlanta", state="GA", latitude=33.6815, longitude=-84.4090, hourly_labor_rate=142.0, is_preferred_partner=True, contact_phone="404-555-0148"),
                ServiceCenter(name="Freightliner Premier - Columbus", city="Columbus", state="OH", latitude=39.9612, longitude=-82.9988, hourly_labor_rate=155.0, is_preferred_partner=True, contact_phone="614-555-0183"),
                ServiceCenter(name="Petro Stopping Center - West Memphis", city="West Memphis", state="AR", latitude=35.1465, longitude=-90.1845, hourly_labor_rate=138.0, is_preferred_partner=True, contact_phone="870-555-0119"),
            ]
            db.add_all(shops)
            db.commit()
            print(f"[✓] Seeded {len(shops)} preferred service centers.")

        vehicles = [
            {"unit_number": "TRK-101", "account_id": "acc1", "vin": "1FUJHHDR1NLAA101", "make": "Freightliner", "model": "Cascadia", "year": 2022, "odometer_miles": 288400.0, "engine_hours": 6120.0, "latitude": 32.7767, "longitude": -96.7970},
            {"unit_number": "TRK-102", "account_id": "acc1", "vin": "1XKYDP9X2MJAA102", "make": "Kenworth", "model": "T680", "year": 2021, "odometer_miles": 412500.0, "engine_hours": 8940.0, "latitude": 33.7490, "longitude": -84.3880},
            {"unit_number": "TRK-103", "account_id": "acc1", "vin": "3AKJHHDR8NSAA103", "make": "Freightliner", "model": "Cascadia", "year": 2023, "odometer_miles": 145200.0, "engine_hours": 3100.0, "latitude": 35.1495, "longitude": -90.0490},
            {"unit_number": "TRK-201", "account_id": "acc2", "vin": "1NP5DB9X9PDAA201", "make": "Peterbilt", "model": "579", "year": 2023, "odometer_miles": 162000.0, "engine_hours": 3450.0, "latitude": 39.9612, "longitude": -82.9988},
            {"unit_number": "TRK-202", "account_id": "acc2", "vin": "4V4NC9EH5NNAA202", "make": "Volvo", "model": "VNL 860", "year": 2022, "odometer_miles": 319000.0, "engine_hours": 7100.0, "latitude": 32.7555, "longitude": -97.3308},
        ]
        v_count = collector.ingest_vehicles(vehicles)
        print(f"[✓] Ingested {v_count} fleet vehicles.")

        if db.query(DiagnosticCode).count() == 0:
            dtcs = [
                {"unit_number": "TRK-101", "account_id": "acc1", "spn": 3226, "fmi": 18, "description": "Aftertreatment 1 Outlet NOx Sensor Derate", "subsystem": "AFTERTREATMENT", "severity": "CRITICAL"},
                {"unit_number": "TRK-102", "account_id": "acc1", "spn": 110, "fmi": 0, "description": "Engine Coolant Temperature High - Most Severe Level", "subsystem": "COOLING", "severity": "CRITICAL"},
                {"unit_number": "TRK-102", "account_id": "acc1", "spn": 1087, "fmi": 18, "description": "Service Brake Circuit 1 Air Pressure Low", "subsystem": "BRAKES", "severity": "MEDIUM"},
                {"unit_number": "TRK-202", "account_id": "acc2", "spn": 520, "fmi": 7, "description": "Air Compressor Governor Cut-Out Failure", "subsystem": "AIR_SYSTEM", "severity": "MEDIUM"},
            ]
            c_count = collector.ingest_dtc_codes(dtcs)
            print(f"[✓] Ingested {c_count} J1939 telematics fault codes.")

        if db.query(DVIRLog).count() == 0:
            dvirs = [
                {"unit_number": "TRK-101", "account_id": "acc1", "driver_id": "DRV-101", "notes": "DEF light flashing and SCR derate active, strong exhaust smell on highway."},
                {"unit_number": "TRK-102", "account_id": "acc1", "driver_id": "DRV-104", "notes": "Radiator leak near bottom hose, engine overheating on grade and soft brake pedal."},
                {"unit_number": "TRK-103", "account_id": "acc1", "driver_id": "DRV-108", "notes": "Pre-trip inspection complete. Checked brakes, no air leak, no brake squeal, no coolant leak."},
                {"unit_number": "TRK-201", "account_id": "acc2", "driver_id": "DRV-201", "notes": "Passed inspection without any vibration or low psi. All tires good."},
                {"unit_number": "TRK-202", "account_id": "acc2", "driver_id": "DRV-205", "notes": "Slow build on air tank pressure, psi drop when idling."},
            ]
            d_count = collector.ingest_dvirs(dvirs)
            print(f"[✓] Ingested and NLP-scored {d_count} DVIR driver inspection logs.")

        audit = risk_engine.audit_fleet()
        print("\n--- INITIAL FLEET HEALTH AUDIT ---")
        for r in audit:
            print(f"• {r['unit_number']} ({r['account_id']}): Health={r['health_score']}/100 | Status={r['status']} | Primary={r['primary_concern']}")

    finally:
        db.close()


if __name__ == "__main__":
    run_seed()