from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Manually approve a reviewed DVIR candidate; no automatic promotion is supported.")
    parser.add_argument("--manifest", required=True, help="Path to candidate JSON manifest")
    parser.add_argument("--approved-by", required=True, help="Human approver identity")
    parser.add_argument("--change-ticket", required=True, help="Review/approval ticket identifier")
    parser.add_argument("--active-dir", default="models/dvir_active", help="Directory for the active model")
    args = parser.parse_args()
    manifest_path = Path(args.manifest).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not manifest.get("eligible_for_manual_review"):
        raise SystemExit("Refusing promotion: candidate did not meet the configured validation threshold.")
    candidate = Path(manifest["candidate_model"]).resolve()
    if not candidate.is_file():
        raise SystemExit(f"Candidate model not found: {candidate}")
    out_dir = Path(args.active_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "dvir_text_model.joblib"
    shutil.copy2(candidate, target)
    promotion = {
        "active_model": str(target),
        "candidate_manifest": str(manifest_path),
        "approved_by": args.approved_by,
        "change_ticket": args.change_ticket,
        "eligible_for_manual_review": True,
        "approved_manually": True,
        "safety_policy": "Model outputs are suggestions only and cannot set or clear dispatch holds.",
    }
    (out_dir / "promotion_record.json").write_text(json.dumps(promotion, indent=2), encoding="utf-8")
    print(json.dumps(promotion, indent=2))


if __name__ == "__main__":
    main()
