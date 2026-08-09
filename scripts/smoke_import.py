#!/usr/bin/env python3
"""Headless smoke test for the media-import engine (no Qt event loop).

Builds a throwaway dump/archive fixture in a tempdir and checks: dedup
deletion, moves, collision renames, already-at-destination handling,
non-media skips, empty-folder pruning, dry-run inertness, idempotent
re-runs, the tampered-index delete guard, and preflight overlap aborts.
Copy mode gets its own sections: source never modified, duplicates
skipped, no pruning, read-only sources, and per-file read errors costing
one file (exit 3) instead of the run.

Run: python3 scripts/smoke_import.py
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rsync_app import hash_index, importer  # noqa: E402
from rsync_app.preflight import check_import_job  # noqa: E402

CHECKS = [0]


def ok(cond, label):
    assert cond, f"FAILED: {label}"
    CHECKS[0] += 1
    print(f"  ok  {label}")


def write(path, content: bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)


def snapshot(root):
    """relpath -> content for every file under root."""
    out = {}
    for dirpath, _d, filenames in os.walk(root):
        for name in filenames:
            full = os.path.join(dirpath, name)
            with open(full, "rb") as f:
                out[os.path.relpath(full, root)] = f.read()
    return out


def run(cfg, conn, dry_run=False):
    lines = []
    code = importer.run_import(cfg, conn, dry_run, progress=lines.append)
    return code, lines


def main():
    tmp = tempfile.mkdtemp(prefix="smoke_import_")
    archive = os.path.join(tmp, "archive")
    dest = os.path.join(archive, "2026", "2026.08 - New")
    dump = os.path.join(tmp, "dump")

    a_bytes = b"AAAA-old-event-photo" * 50
    write(os.path.join(archive, "2015", "03 - Old Event", "a.jpg"), a_bytes)
    write(os.path.join(archive, "2024", "2024.06 - Trip", "b.mp4"),
          b"BBBB-video" * 99)
    write(os.path.join(archive, "2024", "2024.06 - Trip", "c.heic"),
          b"CCCC-heic" * 7)
    write(os.path.join(archive, "2015", "03 - Old Event", "same_size.jpg"),
          b"G" * 64)
    write(os.path.join(dest, "c2.jpg"), b"DEST-C2-CONTENT")
    write(os.path.join(dest, "b_same.jpg"), b"SAME-CONTENT-AS-DUMP")

    write(os.path.join(dump, "DCIM", "dup_of_a.jpg"), a_bytes)
    write(os.path.join(dump, "DCIM", "new1.JPG"), b"NEW-UNIQUE-PHOTO-1")
    write(os.path.join(dump, "DCIM", "twin.jpg"), b"TWIN-CONTENT")
    write(os.path.join(dump, "sub", "twin.jpg"), b"TWIN-CONTENT")
    write(os.path.join(dump, "sub", "c2.jpg"), b"OTHER-C2-CONTENT!")
    write(os.path.join(dump, "sub", "b_same.jpg"), b"SAME-CONTENT-AS-DUMP")
    write(os.path.join(dump, "thumbs.db"), b"cruft")
    write(os.path.join(dump, "desktop.ini"), b"cruft2")
    os.makedirs(os.path.join(dump, "empty", "nested"))

    cfg = {"dump_path": dump, "dest_path": dest, "archive_root": archive}
    conn = hash_index.connect(os.path.join(tmp, "media_index.db"))

    print("== preflight ==")
    ok(not [i for i in check_import_job(cfg) if i["severity"] == "error"],
       "valid config has no errors")
    bad = check_import_job({**cfg, "dump_path": os.path.join(archive, "d")})
    ok(any(i["code"] == "E_IMP_DUMP_MISSING" for i in bad),
       "missing dump reported")
    inside = os.path.join(archive, "2015")
    bad = check_import_job({**cfg, "dump_path": inside})
    ok(any(i["code"] == "E_IMP_DUMP_OVERLAPS_ARCHIVE" for i in bad),
       "dump inside archive rejected")
    bad = check_import_job({**cfg, "dest_path": dump})
    ok(any(i["code"] == "E_IMP_DUMP_OVERLAPS_DEST" for i in bad),
       "dest inside dump rejected")
    outside = os.path.join(tmp, "outside-dest")
    os.makedirs(outside)
    warn = check_import_job({**cfg, "dest_path": outside})
    ok(any(i["code"] == "W_IMP_DEST_OUTSIDE_ARCHIVE" for i in warn),
       "dest outside archive warns")
    ok(any(i["code"] == "W_IMP_INDEX_EMPTY"
           for i in check_import_job(cfg, index_empty=True)),
       "empty index warns")

    print("== dry run ==")
    before_dump, before_arch = snapshot(dump), snapshot(archive)
    code, lines = run(cfg, conn, dry_run=True)
    ok(code == importer.EXIT_OK, "dry run exit 0")
    ok(snapshot(dump) == before_dump and snapshot(archive) == before_arch,
       "dry run changed nothing")
    text = "\n".join(lines)
    ok("would delete" in text and "move  DCIM/new1.JPG" in text,
       "dry run predicts deletes and moves")

    print("== real run ==")
    code, lines = run(cfg, conn)
    text = "\n".join(lines)
    ok(code == importer.EXIT_OK, "real run exit 0")
    dump_left = snapshot(dump)
    ok(set(dump_left) == {"thumbs.db", "desktop.ini"},
       f"only cruft left in dump (got {sorted(dump_left)})")
    ok(not os.path.isdir(os.path.join(dump, "DCIM"))
       and not os.path.isdir(os.path.join(dump, "empty")),
       "emptied dump subfolders removed")
    d = snapshot(dest)
    ok(d.get("new1.JPG") == b"NEW-UNIQUE-PHOTO-1", "new file moved")
    ok(d.get("twin.jpg") == b"TWIN-CONTENT"
       and sum(1 for k in d if k.startswith("twin")) == 1,
       "identical twin moved once, second copy deleted")
    ok(d.get("c2.jpg") == b"DEST-C2-CONTENT"
       and d.get("c2 (2).jpg") == b"OTHER-C2-CONTENT!",
       "collision renamed, original untouched")
    ok(d.get("b_same.jpg") == b"SAME-CONTENT-AS-DUMP"
       and "already at the destination" in text,
       "already-at-destination dump copy deleted")
    ok("dup_of_a.jpg" not in d
       and not os.path.exists(os.path.join(dump, "DCIM", "dup_of_a.jpg")),
       "archive duplicate deleted from dump, not moved")
    ok(snapshot(os.path.join(archive, "2015")) == {
        "03 - Old Event/a.jpg": a_bytes,
        "03 - Old Event/same_size.jpg": b"G" * 64},
       "archive originals untouched")
    row = conn.execute(
        "SELECT * FROM archive_files WHERE relpath = ?",
        (os.path.join("2026", "2026.08 - New", "new1.JPG"),)).fetchone()
    ok(row is not None and row["size"] == len(b"NEW-UNIQUE-PHOTO-1"),
       "moved file upserted into index (hash stays lazy)")
    row = conn.execute(
        "SELECT * FROM archive_files WHERE relpath = ?",
        (os.path.join("2026", "2026.08 - New", "twin.jpg"),)).fetchone()
    ok(row is not None and row["hash"] is not None,
       "moved file with a known digest keeps it in the index")

    print("== second run is a no-op ==")
    before_dump, before_arch = snapshot(dump), snapshot(archive)
    code, lines = run(cfg, conn)
    text = "\n".join(lines)
    ok(code == importer.EXIT_OK, "re-run exit 0")
    ok("moved to destination:   0" in text
       and "duplicates deleted:     0" in text,
       "re-run moves and deletes nothing")
    ok(snapshot(dump) == before_dump and snapshot(archive) == before_arch,
       "re-run changed no files")

    print("== tampered index must not delete ==")
    dump2 = os.path.join(tmp, "dump2")
    fake = b"F" * 64  # same size as archive same_size.jpg, different bytes
    write(os.path.join(dump2, "fake.jpg"), fake)
    cfg2 = {**cfg, "dump_path": dump2}
    hash_index.refresh(conn, os.path.realpath(archive))
    fake_digest = hash_index.hash_file(os.path.join(dump2, "fake.jpg"))
    n = conn.execute(
        "UPDATE archive_files SET hash = ? WHERE relpath LIKE ?",
        (fake_digest, "%same_size%")).rowcount
    conn.commit()
    ok(n == 1, "index row tampered for the test")
    code, lines = run(cfg2, conn)
    text = "\n".join(lines)
    ok(code == importer.EXIT_SKIPPED_UNSAFE, "tampered run exits 3")
    ok(os.path.exists(os.path.join(dump2, "fake.jpg")),
       "file survives a lying index row")
    ok("no longer matches" in text, "skip reason surfaced")

    print("== overlap config aborts ==")
    before_arch = snapshot(archive)
    code, lines = run({**cfg, "dump_path": os.path.join(archive, "2015")},
                      conn)
    ok(code == importer.EXIT_PREFLIGHT, "overlapping config exit 2")
    ok(snapshot(archive) == before_arch, "abort changed nothing")

    print("== copy mode: dry run ==")
    src = os.path.join(tmp, "phone")
    cdest = os.path.join(archive, "2026", "2026.09 - Copy")
    os.makedirs(cdest)
    write(os.path.join(src, "Immich", "dup_of_a.jpg"), a_bytes)
    write(os.path.join(src, "Immich", "cnew.jpg"), b"COPY-NEW-1")
    write(os.path.join(src, "Immich", "ctwin.jpg"), b"CTWIN-CONTENT")
    write(os.path.join(src, "sub", "ctwin.jpg"), b"CTWIN-CONTENT")
    write(os.path.join(src, "Immich", "col.jpg"), b"COL-SRC-CONTENT!")
    write(os.path.join(cdest, "col.jpg"), b"COL-DEST-CONTENT")
    write(os.path.join(src, "notes.txt"), b"not media")
    os.makedirs(os.path.join(src, "cempty"))
    ccfg = {**cfg, "dump_path": src, "dest_path": cdest, "copy_mode": 1}
    before_src = snapshot(src)
    before_cdest = snapshot(cdest)
    code, lines = run(ccfg, conn, dry_run=True)
    text = "\n".join(lines)
    ok(code == importer.EXIT_OK, "copy dry run exit 0")
    ok(snapshot(src) == before_src and snapshot(cdest) == before_cdest,
       "copy dry run changed nothing")
    ok("copy  Immich/cnew.jpg" in text and "left on source" in text,
       "copy dry run predicts copies and skips")

    print("== copy mode: real run ==")
    code, lines = run(ccfg, conn)
    text = "\n".join(lines)
    ok(code == importer.EXIT_OK, "copy run exit 0")
    ok(snapshot(src) == before_src, "source completely untouched")
    ok(os.path.isdir(os.path.join(src, "cempty")),
       "empty source folder not pruned")
    d = snapshot(cdest)
    ok(d.get("cnew.jpg") == b"COPY-NEW-1", "new file copied")
    ok(d.get("col.jpg") == b"COL-DEST-CONTENT"
       and d.get("col (2).jpg") == b"COL-SRC-CONTENT!",
       "collision copied under a new name, original untouched")
    ok(d.get("ctwin.jpg") == b"CTWIN-CONTENT"
       and sum(1 for k in d if "ctwin" in k) == 1
       and "in this import, left on source" in text,
       "in-dump twin copied once, second reported as twin")
    ok("dup_of_a.jpg" not in d and "already in the archive" in text,
       "archive duplicate skipped, not copied")
    ok(not any(k.endswith(".part") for k in d), "no .part temps left")
    row = conn.execute(
        "SELECT * FROM archive_files WHERE relpath = ?",
        (os.path.join("2026", "2026.09 - Copy", "cnew.jpg"),)).fetchone()
    ok(row is not None and row["hash"] is not None,
       "copied file upserted into index with its verified digest")

    print("== copy mode: re-run is a no-op ==")
    code, lines = run(ccfg, conn)
    text = "\n".join(lines)
    ok(code == importer.EXIT_OK, "copy re-run exit 0")
    ok("copied to destination:  0" in text, "copy re-run copies nothing")
    ok(snapshot(src) == before_src and snapshot(cdest) == d,
       "copy re-run changed no files")

    print("== copy mode: read-only source ==")
    os.chmod(src, 0o555)
    ok(not [i for i in check_import_job(ccfg) if i["severity"] == "error"],
       "read-only source passes copy-mode preflight")
    bad = check_import_job({**ccfg, "copy_mode": 0})
    ok(any(i["code"] == "E_IMP_DUMP_UNREADABLE" for i in bad),
       "read-only dump fails move-mode preflight")
    code, lines = run(ccfg, conn)
    ok(code == importer.EXIT_OK and snapshot(src) == before_src,
       "copy run works on a read-only source")
    os.chmod(src, 0o755)

    print("== copy mode: unreadable file skips, run continues ==")
    locked = os.path.join(src, "Immich", "locked.jpg")
    write(locked, b"L" * 64)   # size-collides with archive same_size.jpg
    write(os.path.join(src, "Immich", "cnew2.jpg"), b"COPY-NEW-2")
    os.chmod(locked, 0o000)
    code, lines = run(ccfg, conn)
    text = "\n".join(lines)
    ok(code == importer.EXIT_SKIPPED_UNSAFE, "unreadable file exits 3")
    ok("could not be read" in text, "unreadable file reported")
    ok(snapshot(cdest).get("cnew2.jpg") == b"COPY-NEW-2",
       "remaining files still copied")
    os.chmod(locked, 0o644)

    print("== copy mode: overlap config aborts ==")
    code, lines = run({**ccfg, "dump_path": os.path.join(archive, "2015")},
                      conn)
    ok(code == importer.EXIT_PREFLIGHT, "copy overlap config exit 2")

    print("== archive duplicate report ==")
    write(os.path.join(archive, "2024", "2024.06 - Trip", "a_copy.jpg"),
          a_bytes)
    before_arch = snapshot(archive)
    lines = []
    code = importer.run_dup_report(cfg, conn, progress=lines.append)
    text = "\n".join(lines)
    ok(code == importer.EXIT_OK, "dup report exit 0")
    ok("2 identical files" in text and "a_copy.jpg" in text
       and "a.jpg" in text, "duplicate group reported")
    ok(snapshot(archive) == before_arch, "report changed nothing")

    conn.close()
    import shutil
    shutil.rmtree(tmp)
    print(f"\nsmoke_import: all {CHECKS[0]} checks passed")


if __name__ == "__main__":
    main()
