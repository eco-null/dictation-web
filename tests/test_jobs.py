

import time
import unittest

import app.jobs as jobs_module

class Jobs(unittest.TestCase):
    def setUp(self):
        self.manager = jobs_module.JobManager(max_jobs=2)

    def submit_and_wait(self, work):
        job_id = self.manager.submit("t", work)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            job = self.manager.get(job_id)
            if job is not None and job.status in ("done", "failed"):
                return job_id
            time.sleep(0.001)
        self.fail("Job did not finish")

    def test_a_job_runs_and_records_result(self):
        job_id = self.submit_and_wait(lambda emit: {"ok": True})
        job = self.manager.get(job_id)
        self.assertEqual(job.status, "done")
        self.assertEqual(job.result, {"ok": True})

    def test_a_failure_is_recorded(self):
        def boom(emit):
            raise RuntimeError("nope")
        job_id = self.submit_and_wait(boom)
        job = self.manager.get(job_id)
        self.assertEqual(job.status, "failed")
        self.assertIn("nope", job.error)

    def test_only_one_at_a_time(self):
        started = {}

        def work(emit):
            started["go"] = True
            time.sleep(0.2)
            return 1

        job_id = self.manager.submit("t", work)
        started["go"] = False
        with self.assertRaises(jobs_module.BusyError):
            self.manager.submit("t", work)
        time.sleep(0.3)
        second = self.submit_and_wait(lambda emit: 2)
        self.assertEqual(self.manager.get(second).status, "done")

    def test_emit_updates_the_stage(self):
        def work(emit):
            emit("halfway")
            return 1

        job_id = self.submit_and_wait(work)
        self.assertEqual(self.manager.get(job_id).stage, "halfway")

    def test_finished_jobs_over_the_limit_are_pruned(self):
        first = self.submit_and_wait(lambda emit: 1)
        second = self.submit_and_wait(lambda emit: 2)
        third = self.submit_and_wait(lambda emit: 3)
        fourth = self.submit_and_wait(lambda emit: 4)
        self.assertIsNone(self.manager.get(first))
        self.assertIsNone(self.manager.get(second))
        self.assertIsNotNone(self.manager.get(third))
        self.assertIsNotNone(self.manager.get(fourth))

    def test_unknown_id_returns_none(self):
        self.assertIsNone(self.manager.get("does-not-exist"))

    def test_job_goes_queued_then_done(self):
        holder = {}
        job_id = self.submit_and_wait(lambda emit: holder.setdefault("ran", True))
        first = self.manager.get(job_id)
        self.assertIn(first.status, ("queued", "running", "done"))
        self.assertIs(holder.get("ran"), True)
        self.assertEqual(self.manager.get(job_id).status, "done")

    def test_prune_keeps_at_most_max_jobs(self):
        ids = []
        for _ in range(5):
            ids.append(self.submit_and_wait(lambda emit: 1))
        self.submit_and_wait(lambda emit: 0)
        kept = [jid for jid in ids if self.manager.get(jid) is not None]
        self.assertLessEqual(len(kept), self.manager.max_jobs)

    def test_a_failed_job_frees_the_slot(self):
        def boom(emit):
            raise RuntimeError("x")

        first = self.submit_and_wait(boom)
        self.assertEqual(self.manager.get(first).status, "failed")
        second = self.submit_and_wait(lambda emit: 2)
        self.assertEqual(self.manager.get(second).status, "done")

if __name__ == "__main__":
    unittest.main()
