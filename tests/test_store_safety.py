"""Tests for the crash-safe notification machinery, retention cleanup,
and source anomaly detection — the state transitions behind the
duplicate-alert and lost-alert fixes."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import store


def _job(job_id: str = "job-1", **overrides) -> dict:
    base = {
        "job_id": job_id,
        "company": "Example",
        "title": "Software Development Engineer",
        "location": "Austin, TX",
        "url": f"https://example.com/jobs/{job_id}",
        "posted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "greenhouse",
    }
    base.update(overrides)
    return base


class PendingNotificationTests(unittest.TestCase):
    def test_pending_sentinel_excludes_job_from_requeue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jobwatch.db"
            with patch.object(store, "DB_PATH", db_path):
                store.sync_jobs([_job()])
                store.mark_jobs_pending_notification(["job-1"])
                # While 'pending', sync_jobs must treat the job as in-flight,
                # not re-queue it (that would duplicate the alert).
                self.assertEqual(store.sync_jobs([_job()]), [])

    def test_reset_returns_pending_jobs_to_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jobwatch.db"
            with patch.object(store, "DB_PATH", db_path):
                store.sync_jobs([_job()])
                store.mark_jobs_pending_notification(["job-1"])
                self.assertEqual(store.reset_pending_notifications(), 1)
                requeued = store.sync_jobs([_job()])
                self.assertEqual([j["job_id"] for j in requeued], ["job-1"])

    def test_mark_notified_overwrites_pending_sentinel(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jobwatch.db"
            with patch.object(store, "DB_PATH", db_path):
                store.sync_jobs([_job()])
                store.mark_jobs_pending_notification(["job-1"])
                self.assertEqual(store.mark_jobs_notified(["job-1"]), 1)
                # Fully notified: neither requeued nor reset-able.
                self.assertEqual(store.sync_jobs([_job()]), [])
                self.assertEqual(store.reset_pending_notifications(), 0)

    def test_scoped_reset_leaves_other_pending_rows_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jobwatch.db"
            with patch.object(store, "DB_PATH", db_path):
                store.sync_jobs([_job("job-1"), _job("job-2")])
                store.mark_jobs_pending_notification(["job-1", "job-2"])
                # Scoped reset: only job-1 goes back to the queue; job-2's
                # in-flight marker (e.g. an overlapping run) is untouched.
                self.assertEqual(store.reset_pending_notifications(["job-1"]), 1)
                requeued = store.sync_jobs([_job("job-1"), _job("job-2")])
                self.assertEqual([j["job_id"] for j in requeued], ["job-1"])

    def test_scoped_reset_with_empty_list_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jobwatch.db"
            with patch.object(store, "DB_PATH", db_path):
                store.sync_jobs([_job()])
                store.mark_jobs_pending_notification(["job-1"])
                self.assertEqual(store.reset_pending_notifications([]), 0)


class RetentionCleanupTests(unittest.TestCase):
    def _backdate(self, db_path: Path, job_id: str, days: int) -> None:
        old = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE seen_jobs SET last_seen = ? WHERE job_id = ?",
                (old, job_id),
            )
            conn.commit()

    def _set_status(self, db_path: Path, job_id: str, status: str) -> None:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE seen_jobs SET status = ? WHERE job_id = ?",
                (status, job_id),
            )
            conn.commit()

    def _job_ids(self, db_path: Path) -> set[str]:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute("SELECT job_id FROM seen_jobs").fetchall()
        return {row[0] for row in rows}

    def test_deletes_only_stale_new_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jobwatch.db"
            with patch.object(store, "DB_PATH", db_path):
                store.sync_jobs([_job("stale-new"), _job("fresh-new"), _job("stale-applied")])
                self._backdate(db_path, "stale-new", days=40)
                self._backdate(db_path, "stale-applied", days=40)
                self._set_status(db_path, "stale-applied", "applied")

                store.cleanup_old_jobs()

            remaining = self._job_ids(db_path)
        # Stale 'new' row is gone; fresh row and applied row survive
        # regardless of age (they carry application tracking).
        self.assertEqual(remaining, {"fresh-new", "stale-applied"})

    def test_fresh_last_seen_protects_old_first_seen(self) -> None:
        # The live-posting invariant: sync_jobs refreshes last_seen on every
        # scrape, so a job first seen months ago but still listed must NOT
        # be deleted (deleting it would re-alert it as brand-new).
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jobwatch.db"
            with patch.object(store, "DB_PATH", db_path):
                store.sync_jobs([_job("long-lived")])
                old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
                with sqlite3.connect(db_path) as conn:
                    conn.execute(
                        "UPDATE seen_jobs SET first_seen = ? WHERE job_id = 'long-lived'",
                        (old,),
                    )
                    conn.commit()
                # Re-scrape refreshes last_seen, then cleanup runs.
                store.sync_jobs([_job("long-lived")])
                store.cleanup_old_jobs()

            self.assertIn("long-lived", self._job_ids(db_path))


class SourceAnomalyTests(unittest.TestCase):
    PREVIOUS = [
        {
            "company": "Example",
            "ats": "greenhouse",
            "lane": "fast",
            "status": "ok",
            "raw_count": 100,
            "matched_count": 5,
            "duration": 0.4,
            "error": None,
        }
    ]

    def _current(self, **overrides) -> list[dict]:
        base = {
            "company": "Example",
            "ats": "greenhouse",
            "lane": "fast",
            "status": "ok",
            "raw_count": 100,
            "matched_count": 5,
            "duration": 0.4,
            "error": None,
        }
        base.update(overrides)
        return [base]

    def test_count_drop_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jobwatch.db"
            with patch.object(store, "DB_PATH", db_path):
                store.record_source_results(self.PREVIOUS, "fast")
                anomalies = store.detect_source_anomalies(self._current(raw_count=15))
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]["anomaly"], "count_drop")

    def test_source_failed_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jobwatch.db"
            with patch.object(store, "DB_PATH", db_path):
                store.record_source_results(self.PREVIOUS, "fast")
                anomalies = store.detect_source_anomalies(
                    self._current(status="error", raw_count=0, error="boom")
                )
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]["anomaly"], "source_failed")

    def test_healthy_run_has_no_anomalies(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jobwatch.db"
            with patch.object(store, "DB_PATH", db_path):
                store.record_source_results(self.PREVIOUS, "fast")
                anomalies = store.detect_source_anomalies(self._current(raw_count=95))
        self.assertEqual(anomalies, [])


if __name__ == "__main__":
    unittest.main()
