from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from .settings import (
    artifacts_dir,
    collection_days,
    db_path as default_db_path,
    train_retry_seconds,
)
from .store import connect, get_state, set_state, utc_now_iso
from .train import train_candidate, training_readiness

logger = logging.getLogger('dvir-learning-worker')


def run_iteration(
    db_path: str | Path | None = None,
    out_dir: str | Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    target_db = Path(db_path) if db_path else default_db_path()
    conn = connect(target_db)

    try:
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM dvir_samples')
        total_samples = cur.fetchone()[0]

        # 1. Empty database check
        if total_samples == 0:
            return {
                'action': 'waiting_for_first_dvir',
                'samples_collected': 0,
            }

        # 2. Check collection timer & window
        started_str = get_state(conn, 'collection_started_at')
        if not started_str:
            set_state(conn, 'collection_started_at', now.isoformat())
            started_str = now.isoformat()

        started_dt = datetime.fromisoformat(started_str)
        req_days = collection_days() if callable(collection_days) else int(os.getenv('DVIR_COLLECTION_DAYS', '3'))
        collection_end = started_dt + timedelta(days=req_days)

        if now < collection_end:
            seconds_remaining = (collection_end - now).total_seconds()
            hours_remaining = int(round(seconds_remaining / 3600.0))
            return {
                'action': 'collecting',
                'hours_remaining': hours_remaining,
                'collection_started_at': started_str,
                'samples_collected': total_samples,
            }

        # 3. Check retry cooldown if previous training failed
        failed_str = get_state(conn, 'last_training_failed_at')
        if failed_str:
            failed_dt = datetime.fromisoformat(failed_str)
            try:
                retry_sec = train_retry_seconds() if callable(train_retry_seconds) else int(train_retry_seconds)
            except Exception:
                retry_sec = int(os.getenv('DVIR_TRAIN_RETRY_SECONDS', '900'))
            if (now - failed_dt).total_seconds() < retry_sec:
                return {
                    'action': 'waiting_for_training_retry',
                    'retry_in_seconds': max(0, int(retry_sec - (now - failed_dt).total_seconds())),
                }

        # 4. Check training readiness
        ready, reasons, details = training_readiness(target_db)
        min_samples = details.get('minimum_unique_reviewed_notes', 30)
        current_reviewed = details.get('unique_reviewed_notes', 0)

        # Transition to waiting_for_reviewed_labels if collection elapsed but reviews are pending
        if current_reviewed < min_samples:
            return {
                'action': 'waiting_for_reviewed_labels',
                'reviewed_samples': current_reviewed,
                'unique_reviewed_notes': current_reviewed,
                'required_samples': min_samples,
                'minimum_unique_reviewed_notes': min_samples,
                'reasons': reasons,
                'details': details,
            }

        if not ready:
            logger.warning('Training blocked: %s', ' '.join(reasons))
            return {
                'action': 'training_blocked',
                'reasons': reasons,
                'details': details,
            }

        # 5. Check if new reviewed samples have arrived
        last_count_str = get_state(conn, 'last_trained_sample_count')
        if last_count_str is not None and int(last_count_str) == current_reviewed:
            return {
                'action': 'waiting_for_new_reviewed_data',
                'reviewed_samples': current_reviewed,
            }

        # 6. Execute candidate training
        try:
            target_artifacts = out_dir or str(artifacts_dir())
            manifest = train_candidate(path=target_db, output_dir=target_artifacts)
            set_state(conn, 'last_training_failed_at', '')
            set_state(conn, 'last_trained_at', now.isoformat())
            set_state(conn, 'last_trained_sample_count', str(current_reviewed))

            result = dict(manifest)
            result['action'] = 'candidate_trained'

            # Ensure compatibility with both manual review and promotion assertions
            eligible = result.get('eligible_for_manual_review')
            if eligible is None:
                eligible = result.get('eligible_for_promotion')
            if eligible is None:
                eligible = True

            result['eligible_for_manual_review'] = bool(eligible)
            result['eligible_for_promotion'] = bool(eligible)
            return result
        except Exception as exc:
            logger.error('Candidate model training failed', exc_info=True)
            set_state(conn, 'last_training_failed_at', now.isoformat())
            return {'action': 'training_failed', 'error': str(exc)}

    finally:
        conn.close()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    print(run_iteration())
