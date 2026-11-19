from contextlib import contextmanager
import threading
import time
import uuid


class BusyError(Exception):
    pass


class Job:
    def __init__(self, kind):
        self.id = uuid.uuid4().hex
        self.kind = kind
        self.status = "queued"
        self.stage = ""
        self.result = None
        self.error = ""
        self.created = time.time()
        self.started = 0.0
        self.finished = 0.0

    def as_dict(self):
        return {
            "id": self.id, "kind": self.kind, "status": self.status,
            "stage": self.stage, "error": self.error, "result": self.result,
            "created": self.created, "started": self.started,
            "finished": self.finished,
        }


class JobManager:
    def __init__(self, max_jobs=100):
        self._lock = threading.Lock()
        self._jobs = {}
        self._busy = None
        self.max_jobs = max_jobs

    def submit(self, kind, work):
        with self._lock:
            if self._busy is not None:
                raise BusyError()
            job = Job(kind)
            self._jobs[job.id] = job
            self._busy = job.id

        def run():
            with self._lock:
                job.status = "running"
                job.started = time.time()
            result, error = None, "Job interrupted."
            try:
                result = work(lambda text: self._stage(job, text))
            except Exception as exc:
                error = str(exc) or type(exc).__name__
                result = None
            else:
                error = ""
            finally:
                with self._lock:
                    job.result = result
                    job.error = error
                    job.finished = time.time()
                    job.status = "failed" if error else "done"
                    self._busy = None
                    self._prune()

        try:
            threading.Thread(target=run, daemon=True).start()
        except RuntimeError:
            with self._lock:
                self._jobs.pop(job.id, None)
                self._busy = None
            raise
        return job.id

    @contextmanager
    def exclusive(self):
        with self._lock:
            if self._busy is not None:
                raise BusyError()
            yield

    def _stage(self, job, text):
        with self._lock:
            job.stage = text

    def get(self, job_id):
        with self._lock:
            return self._jobs.get(job_id)

    def _prune(self):
        finished = [j for j in self._jobs.values() if j.status in ("done", "failed")]
        finished.sort(key=lambda j: j.finished)
        overflow = len(finished) - self.max_jobs
        for job in finished[:overflow]:
            self._jobs.pop(job.id, None)


_manager = JobManager()


def submit(kind, work):
    return _manager.submit(kind, work)


def get(job_id):
    return _manager.get(job_id)


def exclusive():
    return _manager.exclusive()
