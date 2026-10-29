from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .settings import artifacts_dir, min_macro_f1, min_reviewed_samples, min_samples_per_class
from .store import connect, normalize_text, utc_now_iso


class InsufficientReviewedData(RuntimeError):
    pass


def _load_reviewed(path: str | Path | None = None) -> list[dict[str, Any]]:
    conn = connect(path)
    try:
        rows = conn.execute(
            """SELECT sample_id,raw_text,normalized_text,reviewed_subsystem,reviewed_urgency,reviewer,reviewed_at
               FROM dvir_samples
               WHERE reviewed_subsystem IS NOT NULL AND reviewed_urgency IS NOT NULL
                 AND reviewer IS NOT NULL AND reviewer <> ''
               ORDER BY reviewed_at"""
        ).fetchall()
        # Collapse exact text duplicates. If reviewers disagree on an identical note,
        # exclude that text until it is reconciled instead of silently choosing a label.
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            item = dict(row)
            groups[item["normalized_text"] or normalize_text(item["raw_text"])].append(item)
        result = []
        for text, group in groups.items():
            label_pairs = {(g["reviewed_subsystem"], g["reviewed_urgency"]) for g in group}
            if len(label_pairs) != 1 or not text:
                continue
            first = group[-1]
            first["normalized_text"] = text
            result.append(first)
        return result
    finally:
        conn.close()


def _class_support(values: list[str]) -> dict[str, int]:
    return dict(Counter(values))


def training_readiness(path: str | Path | None = None) -> tuple[bool, list[str], dict[str, Any]]:
    rows = _load_reviewed(path)
    details: dict[str, Any] = {"unique_reviewed_notes": len(rows)}
    reasons: list[str] = []
    if len(rows) < min_reviewed_samples():
        reasons.append(f"Need at least {min_reviewed_samples()} unique reviewed notes; found {len(rows)}.")
    minimum = min_samples_per_class()
    subsystem_counts = _class_support([r["reviewed_subsystem"] for r in rows])
    urgency_counts = _class_support([r["reviewed_urgency"] for r in rows])
    details["subsystem_class_counts"] = subsystem_counts
    details["urgency_class_counts"] = urgency_counts
    for target, counts in (("subsystem", subsystem_counts), ("urgency", urgency_counts)):
        under = {k: v for k, v in counts.items() if v < minimum}
        if under:
            reasons.append(f"Insufficient support for {target} class(es) (minimum {minimum} each): {under}")
    details["all_reviewed_records"] = int(sum(1 for _ in rows))
    return not reasons, reasons, details

def load_candidate_model(path:str | Path | None = None) -> dict[str, Any]:
    model_path = Path(path or artifacts_dir() / "dvir_candidate.joblib").expanduser().resolve()
    if not model_path.is_absolute():
        raise ValueError(f"Model path must be absolute:")
    if not model_path.is_file():
        raise FileNotFoundError(f"Candidate model not found at {model_path}")
    from joblib import load
    return load(model_path)

def load_active_model(path:str | Path | None = None) -> dict[str, Any]:
    model_path = Path(path or artifacts_dir() / "dvir_active/dvir_text_model.joblib").expanduser().resolve()
    if not model_path.is_absolute():
        raise ValueError(f"Model path must be absolute:")
    if not model_path.is_absolute():
        raise ValueError(f"Model path must be absolute:")

def load_enterprise_model(path:str | Path | None = None) -> dict[str, Any]:
    model_path = Path(path or artifacts_dir() / "dvir_enterprise/dvir_text_model.joblib").expanduser().absolute()
    if not model_path.is_absolute():
        raise ValueError(f"Model path must be absolute:")
    if not model_path.is_absolute():
        raise ValueError(f"Model path must be absolute:")

    
def train_candidate(path: str | Path | None = None, output_dir: str | Path | None = None) -> dict[str, Any]:
    ready, reasons, data_details = training_readiness(path)
    if not ready:
        raise InsufficientReviewedData(" ".join(reasons))

    # Import optional heavyweight ML dependencies only when training is due.
    from joblib import dump
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.pipeline import FeatureUnion
    from sklearn.linear_model import SGDClassifier
    from sklearn.metrics import accuracy_score, f1_score, classification_report
    from sklearn.model_selection import train_test_split
    from sklearn.pipeline import Pipeline

    rows = _load_reviewed(path)
    texts = [r["normalized_text"] for r in rows]
    labels_subsystem = [r["reviewed_subsystem"] for r in rows]
    labels_urgency = [r["reviewed_urgency"] for r in rows]

    models: dict[str, Any] = {}
    metrics: dict[str, Any] = {}
    random_state = 42
    for target, labels in (("subsystem", labels_subsystem), ("urgency", labels_urgency)):
        X_train, X_test, y_train, y_test = train_test_split(
            texts, labels, test_size=0.25, random_state=random_state, stratify=labels
        )
        clf = Pipeline([
            ("features", FeatureUnion([
                ("word", TfidfVectorizer(
                    analyzer="word", ngram_range=(1, 2), min_df=2,
                    max_features=50000, strip_accents="unicode", sublinear_tf=True,
                )),
                ("char", TfidfVectorizer(
                    analyzer="char_wb", ngram_range=(3, 5), min_df=2,
                    max_features=75000, strip_accents="unicode", sublinear_tf=True,
                )),
            ])),
            ("classifier", SGDClassifier(
                loss="log_loss", class_weight="balanced", max_iter=80,
                tol=1e-3, alpha=1e-5, random_state=random_state,
            )),
        ])
        clf.fit(X_train, y_train)
        pred = clf.predict(X_test)
        macro_f1 = float(f1_score(y_test, pred, average="macro", zero_division=0))
        metrics[target] = {
            "accuracy": float(accuracy_score(y_test, pred)),
            "macro_f1": macro_f1,
            "n_train": len(y_train),
            "n_test": len(y_test),
            "class_counts": _class_support(labels),
            "classification_report": classification_report(y_test, pred, output_dict=True, zero_division=0),
        }
        models[target] = clf

    threshold = min_macro_f1()
    scores_pass = all(metrics[target]["macro_f1"] >= threshold for target in ("subsystem", "urgency"))
    out_dir = Path(output_dir).expanduser().resolve() if output_dir else artifacts_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    model_path = out_dir / f"dvir_candidate_{stamp}.joblib"
    manifest_path = out_dir / f"dvir_candidate_{stamp}.json"
    payload = {
        "subsystem_model": models["subsystem"],
        "urgency_model": models["urgency"],
        "model_type": "TFIDF(word+char)+SGDClassifier(log_loss)",
        "trained_at": utc_now_iso(),
        "training_rows": len(rows),
        "feature_policy": "Normalized inspection text; human-reviewed labels only; exact conflicting texts excluded.",
        "safety_policy": "Predictions are review suggestions only; never establish/clear dispatch holds.",
        "metrics": metrics,
    }
    dump(payload, model_path, compress=3)
    manifest = {
        "candidate_model": str(model_path),
        "trained_at": payload["trained_at"],
        "training_rows": len(rows),
        "data_details": data_details,
        "metrics": metrics,
        "minimum_macro_f1_threshold": threshold,
        "eligible_for_manual_review": scores_pass,
        "auto_promoted": False,
        "note": "A held-out score is not evidence of production readiness. Review class-level metrics and perform time-based validation before promotion.",
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {**manifest, "manifest_path": str(manifest_path)}
