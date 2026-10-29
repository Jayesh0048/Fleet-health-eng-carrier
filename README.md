# Fleet Health & Maintenance Agent — reference starter

This is a **standalone reference MVP**, not a direct patch to `etruckingsystem.com`. The actual TMS source code and database schema have not yet been inspected. The starter demonstrates a safe implementation direction using FastAPI + SQLAlchemy and synthetic records.

## Included

- Explainable, deterministic fleet-health rules:
  - unresolved inspection defect explicitly marked by an approved policy as requiring a safety hold;
  - recurring defect within 90 days;
  - recent breakdown follow-up;
  - overdue preventive maintenance by date or odometer;
  - missing/stale odometer data.
- Deduplicated alerts with priority rank, evidence and recommendations.
- Vehicle-health endpoint.
- Work-order creation and guarded status transitions.
- Audit events for alert resolution and work-order changes.
- Unit tests for core risk rules.
- Detailed phase roadmap and production integration checklist in `docs/ROADMAP.md`.

## Important safety and integration notes

- `priority_score` is a triage score, **not** a probability of failure.
- Do not let an LLM set safety-critical flags directly. The `safety_hold_required` field must be controlled by a reviewed defect mapping and authorized workflow.
- Completing a work order does not automatically clear a safety hold.
- The `X-Tenant-ID` header is provided solely to make local testing easy. Replace it with a tenant ID derived from the authenticated principal before any deployment. As provided, this service is not production-secure.
- SQLite and `Base.metadata.create_all()` are development defaults only. Use the TMS's existing DB and migration framework for integration.
- Do not run `scripts/seed_demo.py` against a production database; it creates explicitly synthetic data.

## Run locally

Requires Python 3.11+ recommended.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/seed_demo.py
uvicorn app.main:app --reload --port 8035
```

Open `http://127.0.0.1:8035/docs`.

Evaluate the synthetic vehicle:

```bash
curl -X POST http://127.0.0.1:8035/api/v1/fleet-health/vehicles/DEMO-TRK-104/evaluate \
  -H 'X-Tenant-ID: demo'
```

List alerts:

```bash
curl 'http://127.0.0.1:8035/api/v1/fleet-health/alerts?status=OPEN' \
  -H 'X-Tenant-ID: demo'
```

Create a work order (use a real `alert_id` returned by the evaluation):

```bash
curl -X POST http://127.0.0.1:8035/api/v1/maintenance/work-orders \
  -H 'Content-Type: application/json' -H 'X-Tenant-ID: demo' \
  -d '{"vehicle_id":"DEMO-TRK-104","alert_id":1,"title":"Inspect recurring brake report","description":"Inspect the reported brake issue and document repair findings.","priority":"CRITICAL","requested_by":"demo-maintenance-manager"}'
```

The IDs in the command are examples; use IDs actually returned by your run.

## Test

```bash
pytest -q
```

## Before integrating into the real TMS

1. Share a sanitized repository ZIP or relevant backend/frontend files (exclude `.env`, credentials, tokens, private keys and customer data).
2. Map the actual Vehicle, DVIR, Breakdown, Maintenance, WorkOrder, Tenant and User models; reuse them where possible rather than duplicating business tables.
3. Confirm backend/frontend frameworks, authentication, roles, database engine and migration approach.
4. Approve the critical defect-to-dispatch-hold matrix with the person responsible for fleet safety/compliance.
5. Map actual source APIs for DVIR, repair history, odometer/telematics and preferred service centers.
6. Integrate the service behind existing authentication, tenant isolation, audit logging and frontend navigation.
7. Run the pilot in shadow mode and agree measured acceptance criteria before enabling production actions.

See `docs/ROADMAP.md` for milestones, data definitions, testing and future ML requirements.
# Fleet-health-eng-carrier
