# Minimal integration patch

In `app/services/risk_engine.py`, locate the function that receives a new driver inspection note and persists the DVIR record. Call the capture function only once, after the main DVIR row is committed and refreshed:

```python
from app.services.dvir_learning_capture import capture_dvir_parse

# after main DB commit + refresh
capture_dvir_parse(
    raw_text=notes,
    parsed=parsed,
    source_record_id=f"dvir:{log_entry.id}",
)
```

Never call capture during repeated alert evaluation, e.g. in `build_candidates()`, because old inspection rows could be captured many times.

The capture function supports a dictionary or dataclass parser result, but existing code using `parsed["has_defect"]` must be made consistent with dataclass attributes such as `parsed.has_defect` before changing parser implementations. Keep the current deterministic/rule-based parser operational during the collection period.

For the new ML model, use `suggest_from_active_model(notes)` only as an independent suggestion for evaluation. Do not substitute it into approved safety policy. Capture errors are logged and do not roll back the primary inspection transaction; monitor logs and periodically compare expected DVIR counts to captured learning samples.
