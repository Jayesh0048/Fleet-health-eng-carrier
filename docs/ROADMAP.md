# Predictive Fleet Health & Maintenance — delivery roadmap

## Scope

Build the first TMS capability only: a trustworthy fleet-health inbox that consumes inspections/DVIRs, breakdown records, maintenance schedules, and odometer/telematics data; produces explainable alerts; and links those alerts to auditable work orders. This initial service is rule-based. Do not claim it predicts failures statistically until a model has been trained and time-based validated against labelled failure outcomes.

## Phase 0 — Repository and data audit (2–5 working days; estimate)

- Inventory existing Vehicle, DVIR/Inspection, Breakdown, Maintenance, WorkOrder, User/Tenant and audit entities.
- Identify source-of-truth systems, IDs, timezone conventions, mileage units, status transitions, permissions, and retention obligations.
- Export a representative, de-identified sample; profile missing IDs, duplicate events, time ordering, defect-code quality, mileage freshness, and repair linkage.
- Map existing tables instead of creating duplicates. Document the jurisdiction and vehicle classes; have safety/compliance staff approve critical defect policy.

Deliverable: field mapping, event lineage diagram, data-quality report, and signed alert/hold matrix.

## Phase 1 — Data foundation (about 1–2 weeks; estimate)

- Normalize vehicle identity across inspections, breakdowns, repair orders, and telemetry.
- Make all timestamps timezone-aware and store canonical UTC instants; preserve original source timestamps/timezone when needed.
- Capture inspection result, defect code/category, free-text description, severity, safety relevance, resolved status, who resolved it, when, and evidence.
- Capture repair/maintenance type, performed timestamp, odometer/engine hours, parts/labor, outcome, next-due rule, downtime/cost and source record ID.
- Add idempotent ingestion using `(tenant_id, source_system, source_record_id)` and audit ingestion errors.
- Track odometer value plus timestamp/source; never treat missing mileage as zero.

Deliverable: documented contracts, backfill, and data-quality dashboard.

## Phase 2 — Explainable rules MVP (about 1–2 weeks; estimate)

- Approved rule set: unresolved approved safety hold; overdue maintenance by date or odometer; recurring defect within rolling 90 days; recent breakdown follow-up; stale/missing source data.
- Rule results provide stable rule code, severity, priority score, reasons, evidence IDs, recommendation, created/last-seen timestamps, and deduplication key.
- Priority scores are triage ranks, never failure probabilities.
- Deduplicate repeated evaluations and do not auto-clear a safety hold when a later model/rule result disappears.
- Add a fleet-health alert list and vehicle timeline in Maintenance, with links from Breakdowns and Vehicles.

Deliverable: alert inbox and auditable vehicle-health endpoint.

## Phase 3 — Work-order workflow (about 1–2 weeks; estimate)

- Create a draft/open work order from an alert; link the original inspection/faults and repair history.
- Workflow: OPEN → APPROVED → SCHEDULED → IN_REPAIR → COMPLETED. Cancel only through permitted transitions.
- Require actor, status-change history, repair notes, completion evidence, and verified odometer/parts as appropriate.
- Distinguish “work order completed” from “safety defect cleared.” Only the approved inspection/repair clearance process may clear a safety restriction.
- Integrate existing role permissions; require authorization for severity overrides and safety clearance.

Deliverable: end-to-end work-order flow and audit trail.

## Phase 4 — Preferred service-center routing (about 1 week; estimate)

- Capture locations, operating hours, supported makes/repair capabilities, service type, preferred status, appointments/availability, towing support, estimated cost and external IDs.
- Filter by capability and safety needs first, then rank by preference, availability, distance and cost.
- If availability is unknown, say so; do not invent it. Dispatcher/maintenance approval before booking unless a tested policy explicitly permits automation.

Deliverable: explainable service-center recommendations; no fake live booking claims.

## Phase 5 — Shadow pilot (2–4 weeks; estimate)

- Run alongside the current workflow without automated dispatch blocks except currently approved safety policy controls.
- Review alerts with maintenance supervisors weekly; capture true/false positives, missed events, actions, repair outcomes and alert fatigue.
- Test tenant isolation, authorization, duplicate imports, delayed/out-of-order source events, outages, retries, data freshness, and manual overrides.
- Agree acceptance thresholds with operations before rollout.

Suggested provisional acceptance criteria (set final targets with stakeholders):
- 100% of approved critical-hold test scenarios generate the expected alert.
- No safety hold can be cleared by prediction score alone.
- No duplicate active alert for the same tenant/vehicle/rule key after repeated evaluation.
- 100% of alert resolution and work-order transitions have actor/timestamp audit records.
- At least 95% vehicle-ID match rate in the pilot data, and a measured source timestamp/mileage freshness distribution.
- Every alert has a reason and links back to the source records.

These are test targets, not claims about current production performance.

## Phase 6 — Predictive model (only after sufficient labelled history)

- Define target precisely: e.g. unplanned failure of a specified subsystem in next 7 or 30 days, and define exclusions/censoring and repair windows.
- Feature examples: age, miles/engine hours since service, rolling breakdown counts, repeat defect codes, repair recency, fault-code history, duty/route context only if legitimately available, and seasonality.
- Avoid post-outcome fields (repair outcome, final breakdown code, later odometer readings, status updates) in forecast-time features.
- Split by time, not random rows; reserve a later holdout and evaluate per vehicle/subsystem.
- Measure precision/recall at actionable alert volumes, PR-AUC, calibration, lead time, false alerts per 100 vehicles/month, missed critical events and decision utility; compare against rules and simple baselines.
- Run in shadow mode, monitor data/model drift, version model/policy, and retain human review.

Do not set an arbitrary “need N records” threshold as a substitute for label quality and time-based validation. The ML engineer should estimate sample requirements from event prevalence and the required precision/recall confidence.

## Architecture target

- Existing TMS frontend: Maintenance / Breakdowns / Vehicles views.
- Existing TMS backend/API: authenticated, tenant-scoped routes; use its existing DB models and migrations.
- Ingestion adapters: DVIR, breakdown, maintenance, odometer/telematics sources.
- Rule engine: deterministic rules and approved defect policy.
- Alert/work-order services: idempotent persistence and auditable state changes.
- Background jobs: ingestion, reevaluation, due-maintenance scans and retries. Use a queue/scheduler already supported by the TMS.
- ML inference service later: versioned and shadowed; it recommends but does not clear safety restrictions.

## Security and production requirements

- Replace the starter's development `X-Tenant-ID` header with identity/tenant derived from validated authentication claims.
- Apply role-based authorization on alert resolution, work-order state transitions, safety hold overrides and repair clearance.
- Use Alembic (or the application's established migration system); never rely on `create_all()` in production.
- Add input limits, schema validation, rate limits, request IDs, structured logs, metrics, backups, secret management and a tested recovery plan.
- Use least privilege for integrations; keep API keys out of source code and logs.
- Validate regulatory policy with qualified safety/compliance staff for the actual operating jurisdiction and vehicle types.

## Starter code limitations

The included FastAPI app is a standalone reference slice using synthetic/demo data and prefixed `fh_*` tables. It is not automatically integrated with the live TMS. The demo tenant header is unsafe for production. Before use with real data, integrate the existing authentication, tenants, vehicle/inspection/work-order models, schema migrations, frontend, and approved safety policy.
