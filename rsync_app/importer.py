"""The media-import engine: dump folder → archive destination.

Per run, every file in the dump folder is either
  - deleted, when it is byte-identical (size + blake2b-256) to a file
    already under the archive root or already at the destination, or
  - moved into the destination folder (flattened, never overwriting —
    name collisions with different content get " (2)" suffixes), or
  - left in place and reported, when it isn't a media file.

The engine half of this module is pure Python (testable headless): it takes
an open media_index connection plus progress(str)/cancelled() callables and
returns an exit code. ImportWorker is the thin QThread wrapper the runner
drives; it owns its own index connection because sqlite3 connections are
thread-bound.

Deletion is gated three times: content identity at plan time, a re-stat of
the dump file before acting (files changed mid-run are skipped), and a
re-verification of the surviving copy immediately before each delete (a
stale index row downgrades the delete to a skip instead of destroying the
only copy).
"""

import errno
import os
import re
import shutil
import threading

from PySide6.QtCore import QThread, Signal

from rsync_app import hash_index
from rsync_app.preflight import check_import_job

MEDIA_EXTS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".heif",
    ".tif", ".tiff", ".dng", ".raw", ".cr2", ".nef", ".arw", ".mpo",
    ".mp4", ".mov", ".m4v", ".avi", ".mpg", ".mpeg", ".vob", ".mts",
    ".m2ts", ".3gp", ".mkv", ".webm", ".wmv",
    ".m4a", ".mp3", ".wav", ".aac", ".ogg", ".opus",
})

# Exit codes: 0 clean, 2 preflight error, 3 finished but some files were
# skipped as unsafe/changed (nothing lost, re-run after checking the log).
EXIT_OK = 0
EXIT_PREFLIGHT = 2
EXIT_SKIPPED_UNSAFE = 3


def _never() -> bool:
    return False


class _Plan:
    """One planned action for one dump file."""
    __slots__ = ("kind", "src", "rel", "size", "mtime_ns", "digest",
                 "dest_name", "verify_path", "verify_digest", "renamed")

    def __init__(self, kind, src, rel, size, mtime_ns, **kw):
        self.kind = kind            # move | delete_dup | delete_at_dest
                                    # | skip_not_media
        self.src = src
        self.rel = rel              # dump-relative, for log lines
        self.size = size
        self.mtime_ns = mtime_ns
        self.digest = kw.get("digest")
        self.dest_name = kw.get("dest_name")
        self.verify_path = kw.get("verify_path")    # the surviving copy
        self.verify_digest = kw.get("verify_digest")
        self.renamed = kw.get("renamed", False)


def run_import(cfg: dict, conn, dry_run: bool,
               progress=print, cancelled=_never) -> int:
    """cfg carries dump_path / dest_path / archive_root (absolute)."""
    dump = os.path.realpath(cfg["dump_path"])
    dest = os.path.realpath(cfg["dest_path"])
    archive = os.path.realpath(cfg["archive_root"])

    errors = [i for i in check_import_job(
        {"dump_path": dump, "dest_path": dest, "archive_root": archive})
        if i["severity"] == "error"]
    if errors:
        for issue in errors:
            progress(f"error: {issue['message']}")
        progress("Nothing was changed.")
        return EXIT_PREFLIGHT

    progress(f"Scanning archive: {archive}")
    n = hash_index.refresh(conn, archive, progress, cancelled)
    progress(f"Archive index ready: {n:,} files.")
    if cancelled():
        return EXIT_OK

    plans = _plan(dump, dest, archive, conn, progress, cancelled)
    if dry_run:
        for p in plans:
            progress(_line(p, dry_run=True))
        _summary(plans, 0, progress, dry_run=True)
        return EXIT_OK

    unsafe = 0
    for p in plans:
        if cancelled():
            progress("Cancelled — remaining files were left in the dump.")
            break
        unsafe += _execute(p, conn, archive, dest, progress)
    removed_dirs = _prune_empty_dirs(dump)
    _summary(plans, removed_dirs, progress, dry_run=False)
    return EXIT_SKIPPED_UNSAFE if unsafe else EXIT_OK


def run_dup_report(cfg: dict, conn, progress=print, cancelled=_never) -> int:
    archive = os.path.realpath(cfg["archive_root"])
    if not os.path.isdir(archive):
        progress(f"error: The archive folder can't be found: {archive}")
        return EXIT_PREFLIGHT
    progress(f"Scanning archive: {archive}")
    n = hash_index.refresh(conn, archive, progress, cancelled)
    progress(f"Archive index ready: {n:,} files.")
    groups = hash_index.dup_groups(conn, archive, progress, cancelled)
    if cancelled():
        progress("Cancelled — the report below covers what was compared"
                 " so far.")
    wasted = 0
    for group in sorted(groups, key=lambda g: -g[0]["size"]):
        size = group[0]["size"]
        wasted += size * (len(group) - 1)
        progress("")
        progress(f"{len(group)} identical files ({_fmt_bytes(size)} each):")
        for row in sorted(group, key=lambda r: r["relpath"]):
            progress(f"  {row['relpath']}")
    progress("")
    if groups:
        progress(f"=== {len(groups)} duplicate groups,"
                 f" {_fmt_bytes(wasted)} taken up by extra copies ===")
        progress("Nothing was changed — review and clean up by hand.")
    else:
        progress("=== No duplicates found in the archive ===")
    return EXIT_OK


# --- planning ---------------------------------------------------------------

def _plan(dump, dest, archive, conn, progress, cancelled) -> list[_Plan]:
    files = _scan_dump(dump)
    progress(f"Dump folder: {len(files):,} files to check.")
    try:
        dest_names = set(os.listdir(dest))
    except OSError:
        dest_names = set()
    claimed: dict[str, _Plan] = {}   # dest name -> move plan that took it
    plans: list[_Plan] = []

    for src, rel, size, mtime_ns in files:
        if cancelled():
            break
        digest = None

        # 1. Already in the archive? Content wins over everything.
        match = None
        for row in hash_index.by_size(conn, archive, size):
            if digest is None:
                digest = hash_index.hash_file(src, cancelled)
                if digest is None:
                    break
            row_digest = hash_index.ensure_hash(conn, archive, row, cancelled)
            if row_digest is not None and row_digest == digest:
                match = row
                break
        if digest is None and cancelled():
            break
        if match is not None:
            plans.append(_Plan(
                "delete_dup", src, rel, size, mtime_ns, digest=digest,
                dest_name=match["relpath"],
                verify_path=os.path.join(archive, match["relpath"]),
                verify_digest=digest,
            ))
            continue

        # 2. Only media files get imported.
        if os.path.splitext(src)[1].lower() not in MEDIA_EXTS:
            plans.append(_Plan("skip_not_media", src, rel, size, mtime_ns))
            continue

        # 3. Find a free name at the destination (or discover the file is
        #    already there / already being moved by this run).
        name = os.path.basename(src)
        plan = None
        while name in dest_names or name in claimed:
            other_path = os.path.join(dest, name)
            if name in claimed:
                # Another dump file is already being moved to this name; it
                # is still at its dump location, so hash it there.
                other = claimed[name]
                other_size, other_digest = other.size, other.digest
                if other_size == size and other_digest is None:
                    other.digest = hash_index.hash_file(other.src, cancelled)
                    other_digest = other.digest
            else:
                try:
                    other_size = os.stat(other_path).st_size
                except OSError:
                    other_size = -1
                other_digest = None
                if other_size == size:
                    try:
                        other_digest = hash_index.hash_file(
                            other_path, cancelled)
                    except OSError:
                        other_digest = None
            if other_size == size:
                if digest is None:
                    digest = hash_index.hash_file(src, cancelled)
                if digest is not None and digest == other_digest:
                    # Identical content will already be at other_path by the
                    # time this delete runs (plans execute in this order).
                    plan = _Plan(
                        "delete_at_dest", src, rel, size, mtime_ns,
                        digest=digest, dest_name=name,
                        verify_path=other_path, verify_digest=digest,
                    )
                    break
            name = _next_name(name, dest_names, claimed)
        if plan is None:
            plan = _Plan("move", src, rel, size, mtime_ns, digest=digest,
                         dest_name=name,
                         renamed=(name != os.path.basename(src)))
            claimed[name] = plan
        plans.append(plan)
    return plans


def _scan_dump(dump: str) -> list[tuple]:
    files = []
    for dirpath, _dirnames, filenames in os.walk(dump):
        for fname in filenames:
            full = os.path.join(dirpath, fname)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            st = os.stat(full)
            files.append((full, os.path.relpath(full, dump),
                          st.st_size, st.st_mtime_ns))
    files.sort(key=lambda f: f[1])
    return files


def _next_name(name: str, dest_names: set, claimed: dict) -> str:
    stem, ext = os.path.splitext(name)
    # strip an existing " (N)" suffix so retries count up, not nest
    m = re.fullmatch(r"(.*) \((\d+)\)", stem)
    base, start = (m.group(1), int(m.group(2)) + 1) if m else (stem, 2)
    i = start
    while True:
        candidate = f"{base} ({i}){ext}"
        if candidate not in dest_names and candidate not in claimed:
            return candidate
        i += 1


# --- execution --------------------------------------------------------------

def _execute(p: _Plan, conn, archive: str, dest: str, progress) -> int:
    """Carry out one planned action. Returns 1 when the action had to be
    skipped for safety, else 0."""
    if p.kind == "skip_not_media":
        progress(_line(p, dry_run=False))
        return 0
    try:
        st = os.stat(p.src)
    except OSError:
        progress(f"skip  {p.rel} — disappeared during the run")
        return 1
    if (st.st_size, st.st_mtime_ns) != (p.size, p.mtime_ns):
        progress(f"skip  {p.rel} — changed during the run, left in the dump")
        return 1

    if p.kind in ("delete_dup", "delete_at_dest"):
        if not _surviving_copy_ok(p):
            progress(f"skip  {p.rel} — the archived copy no longer matches,"
                     " left in the dump for safety")
            return 1
        os.remove(p.src)
        progress(_line(p, dry_run=False))
        return 0

    # move
    dst = os.path.join(dest, p.dest_name)
    if os.path.exists(dst):
        progress(f"skip  {p.rel} — a new file appeared at the destination"
                 " with this name, left in the dump")
        return 1
    try:
        os.rename(p.src, dst)
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
        _copy_verify_remove(p.src, dst)
    if _is_within(dst, archive):
        st = os.stat(dst)
        hash_index.upsert_file(conn, archive, os.path.relpath(dst, archive),
                               st.st_size, st.st_mtime_ns, p.digest)
    progress(_line(p, dry_run=False))
    return 0


def _surviving_copy_ok(p: _Plan) -> bool:
    """The copy that justifies deleting the dump file must still be intact.

    Always a live re-hash, never a stat shortcut: a stale or tampered index
    row can carry a wrong cached hash behind an unchanged stat, and this
    check is the last line before an irreversible delete.
    """
    try:
        digest = hash_index.hash_file(p.verify_path)
    except OSError:
        return False
    return digest == p.verify_digest


def _copy_verify_remove(src: str, dst: str) -> None:
    """Cross-filesystem move: copy to .part, verify content, swap in, then
    remove the source. Never leaves a half-written file under the final name."""
    part = dst + ".part"
    src_digest = hash_index.hash_file(src)
    shutil.copyfile(src, part)
    if hash_index.hash_file(part) != src_digest:
        os.remove(part)
        raise OSError(f"copy verification failed for {src}")
    shutil.copystat(src, part)
    os.rename(part, dst)
    os.remove(src)


def _prune_empty_dirs(dump: str) -> int:
    removed = 0
    for dirpath, dirnames, filenames in os.walk(dump, topdown=False):
        if dirpath == dump:
            continue
        if not dirnames and not filenames:
            try:
                os.rmdir(dirpath)
                removed += 1
            except OSError:
                pass
        else:
            # os.walk snapshots dirnames before children were removed;
            # re-check the live directory.
            try:
                if not os.listdir(dirpath):
                    os.rmdir(dirpath)
                    removed += 1
            except OSError:
                pass
    return removed


# --- reporting --------------------------------------------------------------

def _line(p: _Plan, dry_run: bool) -> str:
    would = "would " if dry_run else ""
    if p.kind == "delete_dup":
        return (f"dup   {p.rel} == {p.dest_name} — {would}delete from dump")
    if p.kind == "delete_at_dest":
        return (f"dup   {p.rel} — already at the destination —"
                f" {would}delete from dump")
    if p.kind == "skip_not_media":
        return f"skip  {p.rel} — not a media file, left in the dump"
    suffix = f" (renamed, {p.dest_name} was taken)" if p.renamed else ""
    return f"move  {p.rel} → {p.dest_name}{suffix}"


def _summary(plans: list, removed_dirs: int, progress, dry_run: bool) -> None:
    moved = [p for p in plans if p.kind == "move"]
    dups = sum(1 for p in plans if p.kind == "delete_dup")
    at_dest = sum(1 for p in plans if p.kind == "delete_at_dest")
    skipped = sum(1 for p in plans if p.kind == "skip_not_media")
    renamed = sum(1 for p in moved if p.renamed)
    title = "What would happen" if dry_run else "Import finished"
    progress("")
    progress(f"=== {title} ===")
    progress(f"moved to destination:   {len(moved)}"
             f" ({_fmt_bytes(sum(p.size for p in moved))})")
    progress(f"duplicates deleted:     {dups + at_dest}"
             f" ({dups} in archive, {at_dest} at destination)")
    progress(f"renamed on collision:   {renamed}")
    progress(f"not media, left alone:  {skipped}")
    if not dry_run:
        progress(f"empty folders removed:  {removed_dirs}")


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _is_within(inner: str, outer: str) -> bool:
    try:
        return os.path.commonpath(
            [os.path.realpath(inner), os.path.realpath(outer)]
        ) == os.path.realpath(outer)
    except ValueError:
        return False


# --- Qt worker --------------------------------------------------------------

class ImportWorker(QThread):
    """Runs one import (or dup report) off the GUI thread. The runner treats
    it like a process: outputReady streams log lines, exit_code lands before
    finished() fires."""

    outputReady = Signal(str)

    def __init__(self, cfg: dict, mode: str, parent=None):
        super().__init__(parent)
        self._cfg = dict(cfg)
        self._mode = mode            # "import" | "dry_run" | "dup_report"
        self._cancel = threading.Event()
        self.exit_code = -1

    def request_cancel(self) -> None:
        self._cancel.set()

    @property
    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def run(self) -> None:
        conn = None
        try:
            conn = hash_index.connect()
            emit = self.outputReady.emit
            cancelled = self._cancel.is_set
            if self._mode == "dup_report":
                self.exit_code = run_dup_report(
                    self._cfg, conn, emit, cancelled)
            else:
                self.exit_code = run_import(
                    self._cfg, conn, self._mode == "dry_run",
                    emit, cancelled)
        except Exception as e:  # surfaced in the panel, job turns failed
            self.outputReady.emit(f"error: {e.__class__.__name__}: {e}")
            self.exit_code = 1
        finally:
            if conn is not None:
                conn.close()
