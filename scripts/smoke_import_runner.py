#!/usr/bin/env python3
"""Smoke test: ImportWorker jobs through SyncRunner (queueing, streaming,
terminal states, cooperative cancel). Headless — QCoreApplication only.

Run: python3 scripts/smoke_import_runner.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtCore import QCoreApplication, QTimer  # noqa: E402

from rsync_app.importer import ImportWorker  # noqa: E402
from rsync_app.runner import SyncRunner  # noqa: E402
from rsync_app import hash_index  # noqa: E402

CHECKS = [0]


def ok(cond, label):
    assert cond, f"FAILED: {label}"
    CHECKS[0] += 1
    print(f"  ok  {label}")


def write(path, content: bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)


def wait_terminal(app, runner, job_id, timeout_ms=15000):
    states = []
    runner.jobStateChanged.connect(
        lambda jid, s: (states.append(s), app.quit())
        if jid == job_id and s in ("done", "failed", "cancelled") else None
    )
    snap = {j["id"]: j["state"] for j in runner.jobs()}
    if snap.get(job_id) in ("done", "failed", "cancelled"):
        return snap[job_id]
    QTimer.singleShot(timeout_ms, app.quit)
    app.exec()
    return states[-1] if states else None


def main():
    app = QCoreApplication([])
    tmp = tempfile.mkdtemp(prefix="smoke_import_runner_")
    archive = os.path.join(tmp, "archive")
    dest = os.path.join(archive, "2026", "2026.01 - Test")
    dump = os.path.join(tmp, "dump")
    write(os.path.join(archive, "2020", "01 - Old", "keep.jpg"), b"KEEP")
    os.makedirs(dest)
    write(os.path.join(dump, "one.jpg"), b"ONE")
    write(os.path.join(dump, "dup.jpg"), b"KEEP")
    cfg = {"dump_path": dump, "dest_path": dest, "archive_root": archive}

    # Point the worker's index at the tempdir, not the real AppData file.
    index_file = os.path.join(tmp, "media_index.db")
    hash_index.index_path = lambda: index_file

    runner = SyncRunner()
    # The controller's mount guard is irrelevant for worker jobs, but set one
    # anyway to prove _pump skips it for kind="python".
    runner.set_pre_start_check(lambda dev, argv: "process jobs blocked")

    print("== dry run job streams and completes ==")
    chunks = []
    runner.jobOutputAppended.connect(lambda jid, t: chunks.append(t))
    worker = ImportWorker(cfg, "dry_run")
    job_id = runner.enqueue_worker("Import — test (dry run)", -1, worker)
    state = wait_terminal(app, runner, job_id)
    ok(state == "done", f"dry-run job reached done (got {state})")
    text = "".join(chunks)
    ok("would delete" in text and "move  one.jpg" in text,
       "log streamed through jobOutputAppended")
    snap = [j for j in runner.jobs() if j["id"] == job_id][0]
    ok(snap["argv"] == [] and snap["exitCode"] == 0,
       "snapshot shape: empty argv, exit 0")
    ok(runner.jobLog(job_id) == text, "jobLog matches streamed chunks")
    ok(os.path.exists(os.path.join(dump, "dup.jpg")),
       "dry run left the dump untouched")

    print("== real run job ==")
    worker = ImportWorker(cfg, "import")
    job_id = runner.enqueue_worker("Import — test", -1, worker)
    state = wait_terminal(app, runner, job_id)
    ok(state == "done", f"import job reached done (got {state})")
    ok(os.path.exists(os.path.join(dest, "one.jpg"))
       and not os.path.exists(os.path.join(dump, "dup.jpg")),
       "worker actually imported (moved + deleted dup)")

    print("== cancelled job ==")
    write(os.path.join(dump, "again.jpg"), b"AGAIN")
    worker = ImportWorker(cfg, "import")
    worker.request_cancel()  # cancelled before it can do anything
    job_id = runner.enqueue_worker("Import — cancelled", -1, worker)
    state = wait_terminal(app, runner, job_id)
    ok(state == "cancelled", f"pre-cancelled job ends cancelled (got {state})")
    ok(os.path.exists(os.path.join(dump, "again.jpg")),
       "cancelled run left the dump file alone")

    print("== same queue key is sequential ==")
    w1 = ImportWorker(cfg, "dry_run")
    w2 = ImportWorker(cfg, "dry_run")
    id1 = runner.enqueue_worker("first", -7, w1)
    id2 = runner.enqueue_worker("second", -7, w2)
    snap = {j["id"]: j["state"] for j in runner.jobs()}
    ok(snap[id2] == "pending", "second job waits while first runs")
    s1 = wait_terminal(app, runner, id1)
    s2 = wait_terminal(app, runner, id2)
    ok(s1 == "done" and s2 == "done", "both sequential jobs finish")

    runner.shutdown()
    import shutil
    shutil.rmtree(tmp)
    print(f"\nsmoke_import_runner: all {CHECKS[0]} checks passed")


if __name__ == "__main__":
    main()
