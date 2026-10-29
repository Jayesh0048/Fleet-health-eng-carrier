import hashlib
import os
import sqlite3
import traceback
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import requests
from dotenv import load_dotenv
from sqlalchemy.orm import Session

from app.models import DiagnosticEvent, Inspection, Vehicle
from app.services.nlp_parser import AdvancedDVIRNLPParser
from dvir_learning.settings import db_path
from dvir_learning.train import load_active_model

load_dotenv()


class FleetDataCollector:
    """Gathers live fleet maintenance data, telematics, and DVIRs from Samsara,

    passing raw text to the active PyTorch GPU model and staging unreviewed
    samples into the continuous learning pipeline.
    """

    def __init__(self, db: Session):
        self.db = db
        self.nlp = AdvancedDVIRNLPParser()
        self.samsara_token = os.getenv("SAMSARA_API_TOKEN")
        self.headers = {
            "accept": "application/json",
            "authorization": f"Bearer {self.samsara_token}",
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)

        # Cache the promoted active PyTorch model payload
        try:
            self.active_ml_payload = load_active_model()
        except Exception:
            self.active_ml_payload = None

    def sync_samsara_data(
        self, account_id: str = "US-FLEET-LOGISTICS"
    ) -> Dict[str, Any]:
        """Main orchestrator to pull Assets, Telematics, and DVIRs from Samsara."""
        if not self.samsara_token:
            return {
                "success": False,
                "message": "SAMSARA_API_TOKEN is missing from environment.",
            }

        try:
            # 1. Fetch Master Asset List & Build ID Mapping
            assets_url = "https://api.samsara.com/assets"
            assets_resp = self.session.get(assets_url, timeout=15)
            assets_resp.raise_for_status()

            raw_assets = assets_resp.json().get("data", [])

            # Map Samsara ID -> Samsara Name (Unit Number)
            vehicle_map = {
                str(v.get("id")): str(v.get("name", "")).strip().upper()
                for v in raw_assets
                if v.get("type") == "vehicle" and v.get("id")
            }

            if not vehicle_map:
                return {
                    "success": False,
                    "message": "No vehicles found in Samsara.",
                }

            vehicles_synced = self._upsert_vehicles(raw_assets, account_id)

            # 2. Fetch Live Telematics (Odometer & J1939 Faults)
            vehicle_ids = list(vehicle_map.keys())
            ids_string = ",".join(vehicle_ids)
            stats_url = f"https://api.samsara.com/fleet/vehicles/stats/feed?types=faultCodes,obdOdometerMeters&vehicleIds={ids_string}"

            stats_resp = self.session.get(stats_url, timeout=15)
            stats_resp.raise_for_status()
            stats_data = stats_resp.json().get("data", [])

            dtcs_synced = self._process_telematics(
                stats_data, vehicle_map, account_id
            )

            # 3. Fetch Recent DVIRs for NLP Parsing
            start_time = (
                datetime.now(timezone.utc) - timedelta(days=1)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            dvirs_url = (
                f"https://api.samsara.com/dvirs/stream?startTime={start_time}"
            )

            dvirs_resp = self.session.get(dvirs_url, timeout=15)
            dvirs_resp.raise_for_status()
            dvirs_data = dvirs_resp.json().get("data", [])

            dvirs_synced = self._process_dvirs(dvirs_data, account_id)

            return {
                "success": True,
                "vehicles_synced": vehicles_synced,
                "dtcs_synced": dtcs_synced,
                "dvirs_synced": dvirs_synced,
            }

        except Exception as err:
            print("\n--- SAMSARA SYNC EXCEPTION ---")
            traceback.print_exc()
            print("------------------------------\n")
            return {"success": False, "error": str(err)}

    def _upsert_vehicles(
        self, assets_data: List[Dict[str, Any]], tenant_id: str
    ) -> int:
        count = 0
        seen_units = set()

        for asset in assets_data:
            if asset.get("type") != "vehicle":
                continue

            unit = str(asset.get("name", "")).strip().upper()
            if not unit or unit in seen_units:
                continue

            seen_units.add(unit)

            existing = (
                self.db.query(Vehicle)
                .filter(
                    Vehicle.tenant_id == tenant_id, Vehicle.unit_number == unit
                )
                .first()
            )

            if not existing:
                self.db.merge(
                    Vehicle(
                        vehicle_id=unit,
                        tenant_id=tenant_id,
                        unit_number=unit,
                        vin=asset.get("vin", "UNKNOWN"),
                        make=asset.get("make", "Freightliner").upper(),
                        model=asset.get("model", "Unknown Model").upper(),
                        model_year=(
                            int(asset.get("year", 2022))
                            if str(asset.get("year")).isdigit()
                            else 2022
                        ),
                        odometer_miles=0.0,
                        status="ACTIVE",
                    )
                )
                count += 1

        self.db.commit()
        return count

    def _process_telematics(
        self,
        stats_data: List[Dict[str, Any]],
        vehicle_map: Dict[str, str],
        tenant_id: str,
    ) -> int:
        dtc_count = 0
        current_time = datetime.now(timezone.utc)

        for stat in stats_data:
            samsara_id = str(stat.get("id"))
            unit_number = vehicle_map.get(samsara_id)
            if not unit_number:
                continue

            odo_data = stat.get("obdOdometerMeters")
            odo_meters = None
            if isinstance(odo_data, list) and len(odo_data) > 0:
                odo_meters = odo_data[0].get("value")
            elif isinstance(odo_data, dict):
                odo_meters = odo_data.get("value")

            if odo_meters:
                miles = float(odo_meters) * 0.000621371
                vehicle = (
                    self.db.query(Vehicle)
                    .filter(Vehicle.vehicle_id == unit_number)
                    .first()
                )
                if vehicle:
                    vehicle.odometer_miles = round(miles, 1)
                    vehicle.odometer_updated_at = current_time

            fault_data = stat.get("faultCodes")
            active_faults = []
            if isinstance(fault_data, list) and len(fault_data) > 0:
                j1939 = fault_data[0].get("value", {}).get("j1939", [])
                active_faults = [f for f in j1939 if f.get("isActive")]
            elif isinstance(fault_data, dict):
                j1939 = fault_data.get("j1939", [])
                active_faults = [f for f in j1939 if f.get("isActive")]

            for f in active_faults:
                spn_val = int(f.get("spn", 0))
                fmi_val = int(f.get("fmi", 0))

                existing_dtc = (
                    self.db.query(DiagnosticEvent)
                    .filter(
                        DiagnosticEvent.vehicle_id == unit_number,
                        DiagnosticEvent.spn == spn_val,
                        DiagnosticEvent.is_active == True,
                    )
                    .first()
                )

                if not existing_dtc:
                    self.db.add(
                        DiagnosticEvent(
                            tenant_id=tenant_id,
                            vehicle_id=unit_number,
                            source_system="SAMSARA",
                            source_record_id=f"DTC-{unit_number}-{spn_val}-{fmi_val}-{int(current_time.timestamp())}",
                            occurred_at=current_time,
                            spn=spn_val,
                            fmi=fmi_val,
                            description=f.get(
                                "description", "Samsara J1939 Fault"
                            ),
                            subsystem="ENGINE",
                            severity=(
                                "CRITICAL" if fmi_val in [0, 1, 5] else "HIGH"
                            ),
                            is_active=True,
                        )
                    )
                    dtc_count += 1

        self.db.commit()
        return dtc_count

    def _stage_dvir_for_continuous_learning(
        self, record_id: str, text: str, parsed: Any
    ) -> None:
        """Appends the unreviewed inspection sample to dvir_learning.sqlite3

        so human annotators can review it to trigger subsequent model
        retraining cycles.
        """
        try:
            db_path = db_path()
            normalized = " ".join(text.lower().strip().split())
            text_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            sample_id = f"SMP-LIVE-{record_id[:12]}"
            now_iso = datetime.now(timezone.utc).isoformat()

            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute(
                """
                INSERT OR IGNORE INTO dvir_samples (
                    sample_id, source_record_id, raw_text, normalized_text, text_hash,
                    parser_subsystem, parser_severity, parser_urgency, parser_has_defect,
                    created_at, reviewed_subsystem, reviewed_urgency, reviewer, reviewed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL)
            """,
                (
                    sample_id,
                    record_id,
                    text,
                    normalized,
                    text_hash,
                    parsed.subsystem,
                    parsed.severity,
                    parsed.urgency,
                    1 if parsed.has_defect else 0,
                    now_iso,
                ),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            print(f"Warning: Failed to stage DVIR for continuous training: {exc}")

    def _process_dvirs(
        self, dvirs_data: List[Dict[str, Any]], tenant_id: str
    ) -> int:
        count = 0
        current_time = datetime.now(timezone.utc)

        # Refresh active ML payload if not previously loaded
        if self.active_ml_payload is None:
            try:
                self.active_ml_payload = load_active_model()
            except Exception:
                pass

        for dvir in dvirs_data:
            v_data = dvir.get("vehicle") or dvir.get("trailer") or {}
            unit = str(v_data.get("name", "")).strip().upper()
            dvir_id = str(dvir.get("id", ""))

            mechanic_notes = dvir.get("mechanicNotes", "")
            defect_comments = " ".join(
                [d.get("comment", "") for d in dvir.get("defects", [])]
            )
            combined_notes = f"{mechanic_notes} {defect_comments}".strip()

            if not unit or not combined_notes:
                continue

            existing_dvir = (
                self.db.query(Inspection)
                .filter(Inspection.source_record_id == dvir_id)
                .first()
            )

            if not existing_dvir:
                # 1. Hybrid Parse: PyTorch GPU Model decision + Regex domain tags
                parsed = self.nlp.parse_hybrid(
                    combined_notes, self.active_ml_payload
                )

                # 2. Safety policy: Hold required if urgency is CRITICAL/HIGH or severity >= 0.70
                safety_hold_required = (
                    parsed.urgency in ("CRITICAL", "HIGH")
                    or parsed.severity >= 0.70
                )

                # 3. Insert into the operational database
                self.db.add(
                    Inspection(
                        tenant_id=tenant_id,
                        vehicle_id=unit,
                        source_system="SAMSARA",
                        source_record_id=dvir_id,
                        occurred_at=current_time,
                        inspection_type="DVIR",
                        defect_code=parsed.subsystem,
                        description=combined_notes,
                        severity=parsed.severity,
                        is_resolved=False,
                        safety_hold_required=safety_hold_required,
                    )
                )

                # 4. Stage raw sample into dvir_learning.sqlite3 for continuous learning
                self._stage_dvir_for_continuous_learning(
                    dvir_id, combined_notes, parsed
                )
                count += 1

        self.db.commit()
        return count