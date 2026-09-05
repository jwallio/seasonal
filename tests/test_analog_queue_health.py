"""Offline behavioral tests for runner starvation versus normal concurrency wait."""

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_analog_queue import inspect_queue, stalled_jobs


NOW = datetime(2026, 9, 5, 1, tzinfo=timezone.utc)


def job(**overrides):
    return dict(dict(id=1, status="queued", runner_id=0,
                     labels=["self-hosted", "wn2-analogwx"],
                     created_at="2026-09-05T00:40:00Z"), **overrides)


class QueueTests(unittest.TestCase):
    def test_stalled_unassigned_job(self):
        self.assertEqual(stalled_jobs([job()], NOW)[0][1], 20)

    def test_actual_build_is_not_queue_failure(self):
        self.assertFalse(stalled_jobs([job(status="in_progress", runner_id=21)], NOW))
        self.assertFalse(stalled_jobs([job(runner_id=21)], NOW))

    def test_new_job_after_long_concurrency_wait(self):
        self.assertFalse(stalled_jobs([job(created_at="2026-09-05T00:59:00Z")], NOW))

    def test_exact_threshold_and_future_time(self):
        self.assertTrue(stalled_jobs([job(created_at="2026-09-05T00:45:00Z")], NOW))
        self.assertFalse(stalled_jobs([job(created_at="2026-09-05T01:01:00Z")], NOW))

    def test_completed_canceled_and_other_runner_ignored(self):
        self.assertFalse(stalled_jobs([job(status="completed"), job(labels=["other"])], NOW))

    def test_missing_timestamp_is_not_silent_success(self):
        with self.assertRaises(ValueError):
            stalled_jobs([job(created_at=None)], NOW)
        self.assertTrue(stalled_jobs([job(created_at=None, started_at="2026-09-05T00:40:00Z")], NOW))

    def test_run_without_jobs_is_concurrency_wait(self):
        def fetch(path):
            return {"jobs": [], "total_count": 0} if "/jobs?" in path else {
                "workflow_runs": [{"id": 3}], "total_count": 1}
        self.assertEqual(inspect_queue("owner/repo", fetch, NOW), ([], 1))

    def test_queue_limit_is_not_silent_success(self):
        with self.assertRaises(RuntimeError):
            inspect_queue("owner/repo", lambda path: {"total_count": 21}, NOW)

    def test_hosted_workflow_has_read_only_permissions(self):
        text = (Path(__file__).resolve().parents[1] / ".github/workflows/seasonal-analog-health.yml").read_text()
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertIn("actions: read", text)
        self.assertNotIn(": write", text)


if __name__ == "__main__":
    unittest.main()
