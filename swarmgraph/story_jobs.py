"""On-demand local storyline jobs. GET reads state; only POST or the CLI starts the agent harness."""
import os
import threading
import time

_JOBS = {}
_LOCK = threading.Lock()


def status(db):
    with _LOCK:
        return dict(_JOBS.get(os.path.abspath(db), {}))


def start(db, runner=None):
    """Deduplicate concurrent clicks per dataset. A runner can be injected by tests."""
    key = os.path.abspath(db)
    with _LOCK:
        if _JOBS.get(key, {}).get("status") == "building":
            return dict(_JOBS[key])
        _JOBS[key] = {"status": "building", "stage": "Preparing evidence", "started": time.time()}
        initial = dict(_JOBS[key])

    def progress(message):
        message = message.lower()
        stage = "Reviewing evidence" if "review" in message else "Writing scenes" if "writ" in message else "Discovering incidents" if "discover" in message or "scout" in message else "Preparing evidence"
        with _LOCK:
            _JOBS[key]["stage"] = stage

    def work():
        try:
            if runner is None:
                from .incident_storylines import run
                result = run(key, force=True, log=progress)
            else:
                result = runner(key, force=True, log=progress)
            with _LOCK:
                _JOBS[key].update(status="ready", stage="Ready", result=result, finished=time.time())
        except Exception:
            # Detailed diagnostics belong to the local process, not the people's page.
            import traceback
            traceback.print_exc()
            with _LOCK:
                _JOBS[key].update(status="error", stage="The storylines could not be created. Try again.", finished=time.time())

    thread = threading.Thread(target=work, name="storyline-harness", daemon=True)
    thread.start()
    # Even a fast injected stage may finish before the POST responds. Return the
    # start acknowledgement; the next GET supplies the complete replayable plan.
    return initial


def page(db, con):
    from . import incident_storylines
    job = status(db)
    if job.get("status") in ("building", "error"):
        return {k: job[k] for k in ("status", "stage")}
    return incident_storylines.page(con)
