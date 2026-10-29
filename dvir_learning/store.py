from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .settings import SUBSYSTEM_LABELS, URGENCY_LABELS, db_path as configured_db_path


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def get_db_path(path: str | Path | None = None) -> Path:
    return Path(path).expanduser().resolve() if path else configured_db_path()


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    db_file = get_db_path(path)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_file), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS dvir_samples (
            sample_id TEXT PRIMARY KEY,
            source_record_id TEXT UNIQUE,
            raw_text TEXT NOT NULL,
            normalized_text TEXT NOT NULL,
            text_hash TEXT NOT NULL,
            parser_subsystem TEXT,
            parser_affected_subsystems TEXT NOT NULL DEFAULT '[]',
            parser_severity REAL,
            parser_urgency TEXT,
            parser_has_defect INTEGER,
            parser_symptoms TEXT NOT NULL DEFAULT '[]',
            parser_version TEXT,
            created_at TEXT NOT NULL,
            reviewed_subsystem TEXT,
            reviewed_urgency TEXT,
            reviewer TEXT,
            reviewed_at TEXT,
            review_notes TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_dvir_reviewed
            ON dvir_samples(reviewed_subsystem, reviewed_urgency, reviewed_at);
        CREATE INDEX IF NOT EXISTS idx_dvir_created ON dvir_samples(created_at);
        CREATE TABLE IF NOT EXISTS pipeline_state (
            state_key TEXT PRIMARY KEY,
            state_value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.commit()


def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT state_value FROM pipeline_state WHERE state_key=?", (key,)).fetchone()
    return str(row[0]) if row else None


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO pipeline_state(state_key,state_value,updated_at) VALUES(?,?,?) "
        "ON CONFLICT(state_key) DO UPDATE SET state_value=excluded.state_value, updated_at=excluded.updated_at",
        (key, value, utc_now_iso()),
    )
    conn.commit()


def ensure_collection_started(conn: sqlite3.Connection, now: datetime | None = None) -> str:
    existing = get_state(conn, "collection_started_at")
    if existing:
        return existing
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    set_state(conn, "collection_started_at", current)
    return current


def _value(parsed: Any, key: str, default: Any = None) -> Any:
    if isinstance(parsed, dict):
        return parsed.get(key, default)
    return getattr(parsed, key, default)


def record_parse(
    raw_text: str,
    parsed: Any,
    source_record_id: str | None = None,
    parser_version: str | None = None,
    path: str | Path | None = None,
) -> str:
    """Store a parser observation. Predicted labels are explicitly NOT review labels."""
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise ValueError("raw_text must be a non-empty string")
    if len(raw_text) > 10000:
        raise ValueError("raw_text exceeds the 10,000 character collection limit")
    conn = connect(path)
    try:
        ensure_collection_started(conn)
        sample_id = str(uuid.uuid4())
        normalized = normalize_text(raw_text)
        text_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        subsystem = str(_value(parsed, "subsystem", "NONE") or "NONE").upper()
        affected = _value(parsed, "affected_subsystems", []) or []
        urgency = str(_value(parsed, "urgency", "NORMAL") or "NORMAL").upper()
        severity = _value(parsed, "severity", 0.0)
        has_defect = bool(_value(parsed, "has_defect", False))
        symptoms = _value(parsed, "detected_symptoms", []) or []
        if source_record_id is not None:
            source_record_id = str(source_record_id).strip()
            if not source_record_id:
                source_record_id = None
        try:
            conn.execute(
                """INSERT INTO dvir_samples(
                    sample_id,source_record_id,raw_text,normalized_text,text_hash,
                    parser_subsystem,parser_affected_subsystems,parser_severity,
                    parser_urgency,parser_has_defect,parser_symptoms,parser_version,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    sample_id, source_record_id, raw_text, normalized, text_hash,
                    subsystem, json.dumps(list(affected), ensure_ascii=False),
                    float(severity) if severity is not None else None, urgency,
                    int(has_defect), json.dumps(list(symptoms), ensure_ascii=False),
                    parser_version or parsed.__class__.__name__, utc_now_iso(),
                ),
            )
            conn.commit()
            return sample_id
        except sqlite3.IntegrityError:
            # A known source event was already captured; return the existing sample.
            if source_record_id:
                row = conn.execute("SELECT sample_id FROM dvir_samples WHERE source_record_id=?", (source_record_id,)).fetchone()
                if row:
                    return str(row[0])
            raise
    finally:
        conn.close()


def export_review_queue(output_csv: str | Path, path: str | Path | None = None, limit: int = 10000) -> int:
    output = Path(output_csv).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        rows = conn.execute(
            """SELECT sample_id,raw_text,parser_subsystem,parser_urgency,parser_severity,
                      parser_has_defect,parser_affected_subsystems,parser_symptoms,created_at
               FROM dvir_samples WHERE reviewed_subsystem IS NULL OR reviewed_urgency IS NULL
               ORDER BY created_at LIMIT ?""", (max(1, min(int(limit), 100000)),)
        ).fetchall()
        fields = [
            "sample_id", "raw_text", "parser_subsystem", "parser_urgency", "parser_severity",
            "parser_has_defect", "parser_affected_subsystems", "parser_symptoms", "created_at",
            "reviewed_subsystem", "reviewed_urgency", "reviewer", "review_notes",
        ]
        with output.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                item = dict(row)
                item.update(reviewed_subsystem="", reviewed_urgency="", reviewer="", review_notes="")
                writer.writerow(item)
        return len(rows)
    finally:
        conn.close()


def import_reviews(input_csv: str | Path, path: str | Path | None = None) -> int:
    source = Path(input_csv).expanduser().resolve()
    updated = 0
    conn = connect(path)
    try:
        with source.open("r", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            required = {"sample_id", "reviewed_subsystem", "reviewed_urgency", "reviewer"}
            if not required.issubset(set(reader.fieldnames or [])):
                raise ValueError(f"CSV must include columns: {', '.join(sorted(required))}")
            for row in reader:
                sample_id = (row.get("sample_id") or "").strip()
                subsystem = (row.get("reviewed_subsystem") or "").strip().upper()
                urgency = (row.get("reviewed_urgency") or "").strip().upper()
                reviewer = (row.get("reviewer") or "").strip()
                if not sample_id or not subsystem or not urgency or not reviewer:
                    continue
                if subsystem not in SUBSYSTEM_LABELS:
                    raise ValueError(f"Invalid subsystem label {subsystem!r} for sample {sample_id}")
                if urgency not in URGENCY_LABELS:
                    raise ValueError(f"Invalid urgency label {urgency!r} for sample {sample_id}")
                cursor = conn.execute(
                    """UPDATE dvir_samples SET reviewed_subsystem=?, reviewed_urgency=?, reviewer=?,
                       reviewed_at=?, review_notes=? WHERE sample_id=?""",
                    (subsystem, urgency, reviewer, utc_now_iso(), (row.get("review_notes") or "").strip(), sample_id),
                )
                if cursor.rowcount:
                    updated += 1
        conn.commit()
        return updated
    finally:
        conn.close()


def min_reviewed_samples() -> int:
    source = Path(__file__).parent / "settings.json"
    if source.exists():
        with source.open("r", encoding="utf-8") as f:
            settings = json.load(f)
            return int(settings.get("min_reviewed_samples", 100))
    return 100


def min_samples_per_class() -> int:
    source = Path(__file__).parent / "settings.json"
    if source.exists():
        with source.open("r", encoding="utf-8") as f:
            settings = json.load(f)
            return int(settings.get("min_samples_per_class", 10))
    return 10


def min_macro_1() -> float:
    source = Path(__file__).parent / "settings.json"
    if source.exists():
        with source.open("r", encoding="utf-8") as f:
            settings = json.load(f)
            return float(settings.get("min_macro_f1", 0.7))
    return 0.7


def macro_f1_threshold() -> float:
    source = Path(__file__).parent / "settings.json"
    if source.exists():
        with source.open("r", encoding="utf-8") as f:
            settings = json.load(f)
            return float(settings.get("macro_f1_threshold", 0.7))
    return 0.7


def reviewed_counts(path: str | Path | None = None) -> dict[str, Any]:
    conn = connect(path)
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM dvir_samples WHERE reviewed_subsystem IS NOT NULL AND reviewed_urgency IS NOT NULL AND reviewer IS NOT NULL AND reviewer <> ''"
        ).fetchone()[0]
        unique = conn.execute(
            """SELECT COUNT(DISTINCT normalized_text) FROM dvir_samples
               WHERE reviewed_subsystem IS NOT NULL AND reviewed_urgency IS NOT NULL AND reviewer IS NOT NULL AND reviewer <> ''"""
        ).fetchone()[0]
        return {"reviewed_records": int(total), "unique_reviewed_notes": int(unique)}
    finally:
        conn.close()