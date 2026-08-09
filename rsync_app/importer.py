"""The media-import engine: dump folder → archive destination.

Per run, every file in the dump folder is either
  - deleted, when it is byte-identical (size + blake2b-256) to a file
    already under the archive root or already at the destination, or
  - moved into the destination folder (flattened, never overwriting —
    name collisions with different content get " (2)" suffixes), or
  - left in place and reported, when it isn't a media file.

In copy mode (cfg["copy_mode"]) the source tree is never modified: new
media files are copied to the destination instead of moved, duplicates are
reported and skipped instead of deleted, and empty source folders are left
alone. Built for reading a phone straight over an MTP FUSE mount, so a
single unreadable file skips that file (exit 3) rather than aborting the
run, and the source's hashes are cached in the index between runs.

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
                 "dest_name", "verify_path", "verify_digest", "renamed",
                 "note")

    def __init__(self, kind, src, rel, size, mtime_ns, **kw):
        self.kind = kind            # move | delete_dup | delete_at_dest
                                    # | skip_not_media | skip_error
                                    # copy mode: copy | skip_dup
                                    # | skip_at_dest | skip_twin
        self.src = src
        self.rel = rel              # dump-relative, for log lines
        self.size = size
        self.mtime_ns = mtime_ns
        self.digest = kw.get("digest")
        self.dest_name = kw.get("dest_name")
        self.verify_path = kw.get("verify_path")    # the surviving copy
        self.verify_digest = kw.get("verify_digest")
        self.renamed = kw.get("renamed", False)
        self.note = kw.get("note")                  # skip_error detail


def run_import(cfg: dict, conn, dry_run: bool,
               progress=print, cancelled=_never) -> int:
    """cfg carries dump_path / dest_path / archive_root (absolute) and an
    optional truthy copy_mode."""
    dump = os.path.realpath(cfg["dump_path"])
    dest = os.path.realpath(cfg["dest_path"])
    archive = os.path.realpath(cfg["archive_root"])
    copy = bool(cfg.get("copy_mode"))

    errors = [i for i in check_import_job(
        {"dump_path": dump, "dest_path": dest, "archive_root": archive,
         "copy_mode": copy})
        if i["severity"] == "error"]
    if errors:
        for issue in errors:
            progress(f"error: {issue['message']}")
        progress("Nothing was changed.")
        return EXIT_PREFLIGHT

    progress(f"Scanning archive: {archive}")
    n = hash_index.refresh(conn, archive, progress, cancelled)
    progress(f"Archive index ready: {n:,} files.")
    if copy and not cancelled():
        # Cache source hashes between runs too: on an MTP mount every
        # already-imported file size-collides with its archive copy, and
        # without the cache each run would re-read it all over USB.
        progress(f"Scanning source folder: {dump}")
        hash_index.refresh(conn, dump, progress, cancelled)
    if cancelled():
        return EXIT_OK

    plans = _plan(dump, dest, archive, conn, progress, cancelled, copy)
    if dry_run:
        for p in plans:
            progress(_line(p, dry_run=True))
        _summary(plans, 0, progress, dry_run=True, copy=copy)
        return EXIT_OK

    unsafe = 0
    for p in plans:
        if cancelled():
            progress("Cancelled — remaining files were not copied."
                     if copy else
                     "Cancelled — remaining files were left in the dump.")
            break
        unsafe += _execute(p, conn, archive, dest, progress)
    removed_dirs = 0 if copy else _prune_empty_dirs(dump)
    _summary(plans, removed_dirs, progress, dry_run=False, copy=copy)
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


# --- archive dedupe ---------------------------------------------------------

# Keep-rules for "Remove duplicates in archive": lower key wins. Named event
# folders beat dump/no-topic folders, shallow paths beat nested ones, clean
# filenames beat " (2)"/"Copy" names.
_MISC_EVENT = re.compile(r"^(?:\d{4}\.)?00 - ")
_COPYISH = re.compile(r" \(\d+\)| - Copy|_copy")


def _keep_key(relpath: str) -> tuple:
    parts = relpath.split(os.sep)
    name = parts[-1]
    penalty = 0
    if len(parts) >= 2 and _MISC_EVENT.match(parts[1]):
        penalty += 100
    penalty += 10 * max(0, len(parts) - 3)   # deeper than year/event/file
    return (penalty, os.path.dirname(relpath),
            _COPYISH.search(name) is not None, len(name), name)


def run_dedupe(cfg: dict, conn, progress=print, cancelled=_never) -> int:
    archive = os.path.realpath(cfg["archive_root"])
    if not os.path.isdir(archive):
        progress(f"error: The archive folder can't be found: {archive}")
        return EXIT_PREFLIGHT
    progress(f"Scanning archive: {archive}")
    n = hash_index.refresh(conn, archive, progress, cancelled)
    progress(f"Archive index ready: {n:,} files.")
    groups = hash_index.dup_groups(conn, archive, progress, cancelled)
    if cancelled():
        # Deletes only ever run off a complete scan — a partial scan could
        # form incomplete groups and pick the wrong keeper.
        progress("Cancelled — no files were deleted.")
        return EXIT_OK
    deleted = freed = repaired = skipped = 0
    for group in sorted(groups, key=lambda g: -g[0]["size"]):
        if cancelled():
            progress("Cancelled — remaining duplicate groups were left"
                     " alone.")
            break
        d, f, r, s = _dedupe_group(conn, archive, group, progress)
        deleted += d
        freed += f
        repaired += r
        skipped += s
    progress("")
    if not groups:
        progress("=== No duplicates found in the archive ===")
        return EXIT_OK
    progress("=== Archive clean-up finished ===")
    progress("duplicate groups:".ljust(24) + f"{len(groups)}")
    progress("extra copies deleted:".ljust(24) + f"{deleted}"
             f" ({_fmt_bytes(freed)} freed)")
    progress("keeper times repaired:".ljust(24) + f"{repaired}")
    if skipped:
        progress("groups left alone:".ljust(24) + f"{skipped}"
                 " (changed during the run — check the log and re-run)")
    return EXIT_SKIPPED_UNSAFE if skipped else EXIT_OK


def _dedupe_group(conn, archive: str, group: list[dict],
                  progress) -> tuple[int, int, int, int]:
    """Delete every member of one content-identical group except the keeper.
    Returns (deleted, bytes_freed, mtimes_repaired, groups_skipped)."""
    for row in group:
        try:
            st = os.stat(os.path.join(archive, row["relpath"]))
        except OSError:
            st = None
        if st is None or (st.st_size, st.st_mtime_ns) != (row["size"],
                                                          row["mtime_ns"]):
            progress(f"skip  {row['relpath']} — changed on disk since the"
                     " scan, group left alone")
            return (0, 0, 0, 1)
    group = sorted(group, key=lambda r: _keep_key(r["relpath"]))
    keeper, victims = group[0], group[1:]
    keeper_path = os.path.join(archive, keeper["relpath"])
    digest = keeper["hash"]
    try:
        live = hash_index.hash_file(keeper_path)
    except OSError:
        live = None
    if live != digest:                     # live re-hash, never the index
        progress(f"skip  {keeper['relpath']} — the copy to keep no longer"
                 " matches, group left alone")
        return (0, 0, 0, 1)
    progress(f"keep  {keeper['relpath']}")
    deleted = freed = skipped = 0
    for row in victims:
        full = os.path.join(archive, row["relpath"])
        try:
            st = os.stat(full)
            if (st.st_size, st.st_mtime_ns) != (row["size"],
                                                row["mtime_ns"]):
                raise OSError("changed on disk")
            os.remove(full)
        except OSError as e:
            progress(f"skip  {row['relpath']} — {e}, left alone")
            skipped = 1
            continue
        hash_index.remove_file(conn, archive, row["relpath"])
        progress(f"del   {row['relpath']} == kept {keeper['relpath']}")
        deleted += 1
        freed += row["size"]
    repaired = 0
    earliest = min(r["mtime_ns"] for r in group)
    if keeper["mtime_ns"] > earliest:
        # A re-copy clobbered the keeper's mtime; the earliest copy has the
        # original time.
        try:
            os.utime(keeper_path, ns=(earliest, earliest))
            hash_index.upsert_file(conn, archive, keeper["relpath"],
                                   keeper["size"], earliest, digest)
            progress(f"mtime {keeper['relpath']} — repaired to the earliest"
                     " copy's time")
            repaired = 1
        except OSError as e:
            progress(f"note  {keeper['relpath']} — mtime repair failed ({e})")
    return (deleted, freed, repaired, skipped)


# --- planning ---------------------------------------------------------------

def _plan(dump, dest, archive, conn, progress, cancelled,
          copy=False) -> list[_Plan]:
    files, unreadable, hidden = _scan_dump(dump)
    label = "Source" if copy else "Dump"
    progress(f"{label} folder: {len(files):,} files to check.")
    if hidden:
        progress(f"Ignored {hidden:,} hidden files/folders (names starting"
                 " with '.' — trashed or pending media, caches).")
    try:
        dest_names = set(os.listdir(dest))
    except OSError:
        dest_names = set()
    claimed: dict[str, _Plan] = {}   # dest name -> move/copy plan that took it
    plans: list[_Plan] = [
        _Plan("skip_error", full, rel, 0, 0, note=note)
        for full, rel, note in unreadable
    ]

    def src_digest(src, rel, size, mtime_ns):
        """Hash a dump file; in copy mode through the index cache, so an
        unchanged source file is only ever read over USB once."""
        if not copy:
            return hash_index.hash_file(src, cancelled)
        row = hash_index.get_row(conn, dump, rel) or {
            "relpath": rel, "size": size, "mtime_ns": mtime_ns, "hash": None}
        return hash_index.ensure_hash(conn, dump, row, cancelled)

    for src, rel, size, mtime_ns in files:
        if cancelled():
            break
        digest = None

        # 1. Already in the archive? Content wins over everything.
        match = None
        try:
            for row in hash_index.by_size(conn, archive, size):
                if digest is None:
                    digest = src_digest(src, rel, size, mtime_ns)
                    if digest is None:
                        break
                row_digest = hash_index.ensure_hash(
                    conn, archive, row, cancelled)
                if row_digest is not None and row_digest == digest:
                    match = row
                    break
        except OSError as e:
            plans.append(_Plan("skip_error", src, rel, size, mtime_ns,
                               note=str(e)))
            continue
        if digest is None and cancelled():
            break
        if match is not None:
            plans.append(_Plan(
                "skip_dup" if copy else "delete_dup",
                src, rel, size, mtime_ns, digest=digest,
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
        #    already there / already being brought over by this run).
        name = os.path.basename(src)
        plan = None
        error = None
        while name in dest_names or name in claimed:
            other_path = os.path.join(dest, name)
            twin = name in claimed
            if twin:
                # Another dump file is already taking this name; it is
                # still at its dump location, so hash it there.
                other = claimed[name]
                other_size, other_digest = other.size, other.digest
                if other_size == size and other_digest is None:
                    try:
                        other.digest = src_digest(
                            other.src, other.rel, other.size, other.mtime_ns)
                    except OSError:
                        other.digest = None
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
                    try:
                        digest = src_digest(src, rel, size, mtime_ns)
                    except OSError as e:
                        error = str(e)
                        break
                if digest is not None and digest == other_digest:
                    if copy:
                        # Nothing to do — the content is (or will be) at
                        # other_path; the source file stays put.
                        plan = _Plan(
                            "skip_twin" if twin else "skip_at_dest",
                            src, rel, size, mtime_ns, digest=digest,
                            dest_name=other.rel if twin else name,
                        )
                    else:
                        # Identical content will already be at other_path by
                        # the time this delete runs (plans execute in order).
                        plan = _Plan(
                            "delete_at_dest", src, rel, size, mtime_ns,
                            digest=digest, dest_name=name,
                            verify_path=other_path, verify_digest=digest,
                        )
                    break
            name = _next_name(name, dest_names, claimed)
        if error is not None:
            plans.append(_Plan("skip_error", src, rel, size, mtime_ns,
                               note=error))
            continue
        if plan is None:
            plan = _Plan("copy" if copy else "move",
                         src, rel, size, mtime_ns, digest=digest,
                         dest_name=name,
                         renamed=(name != os.path.basename(src)))
            claimed[name] = plan
        plans.append(plan)
    return plans


def _scan_dump(dump: str) -> tuple[list[tuple], list[tuple], int]:
    """Regular files under dump, plus a list of entries that could not be
    statted (flaky MTP reads must cost one file, not the run) and a count
    of ignored hidden entries. Hidden files and folders (leading dot) are
    never imported: on Android they are trashed/pending media and
    thumbnail caches — resurrecting deleted photos would be a bug."""
    files, unreadable = [], []
    hidden = 0
    for dirpath, dirnames, filenames in os.walk(dump):
        hidden += sum(1 for d in dirnames if d.startswith("."))
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fname in filenames:
            if fname.startswith("."):
                hidden += 1
                continue
            if fname.endswith(".part"):   # our own copy temps
                continue
            full = os.path.join(dirpath, fname)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            try:
                st = os.stat(full)
            except OSError as e:
                unreadable.append(
                    (full, os.path.relpath(full, dump), str(e)))
                continue
            files.append((full, os.path.relpath(full, dump),
                          st.st_size, st.st_mtime_ns))
    files.sort(key=lambda f: f[1])
    return files, unreadable, hidden


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
    if p.kind in ("skip_not_media", "skip_dup", "skip_at_dest", "skip_twin"):
        progress(_line(p, dry_run=False))     # nothing touches the source
        return 0
    if p.kind == "skip_error":
        progress(_line(p, dry_run=False))
        return 1
    try:
        st = os.stat(p.src)
    except OSError:
        progress(f"skip  {p.rel} — disappeared during the run")
        return 1
    # Copies check size only: MTP mtimes are coarse and unstable, and the
    # copy itself verifies content. Moves/deletes keep the strict guard.
    changed = (st.st_size != p.size if p.kind == "copy" else
               (st.st_size, st.st_mtime_ns) != (p.size, p.mtime_ns))
    if changed:
        progress(f"skip  {p.rel} — changed during the run, skipped")
        return 1

    if p.kind in ("delete_dup", "delete_at_dest"):
        if not _surviving_copy_ok(p):
            progress(f"skip  {p.rel} — the archived copy no longer matches,"
                     " left in the dump for safety")
            return 1
        os.remove(p.src)
        progress(_line(p, dry_run=False))
        return 0

    dst = os.path.join(dest, p.dest_name)
    if os.path.exists(dst):
        progress(f"skip  {p.rel} — a new file appeared at the destination"
                 " with this name, skipped")
        return 1
    if p.kind == "copy":
        try:
            digest = _copy_verify(p.src, dst, p.size)
        except OSError as e:
            progress(f"skip  {p.rel} — could not copy: {e}")
            return 1
    else:  # move
        digest = p.digest
        try:
            os.rename(p.src, dst)
        except OSError as e:
            if e.errno != errno.EXDEV:
                raise
            digest = _copy_verify(p.src, dst, p.size)
            os.remove(p.src)
    if _is_within(dst, archive):
        st = os.stat(dst)
        hash_index.upsert_file(conn, archive, os.path.relpath(dst, archive),
                               st.st_size, st.st_mtime_ns, digest)
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


def _copy_verify(src: str, dst: str, expected_size: int) -> bytes:
    """Copy src to dst via a .part temp, verifying content, and return the
    verified digest. One read of the source (it may sit on a slow MTP
    mount): the copy loop feeds the digest, then the landed temp is
    re-hashed locally and must match. Never leaves a half-written file
    under the final name, and cleans up its temp on any failure."""
    part = dst + ".part"
    try:
        src_digest = hash_index.copy_hash(src, part)
        st = os.stat(part)
        if st.st_size != expected_size:
            raise OSError(f"short read copying {src}"
                          f" ({st.st_size} of {expected_size} bytes)")
        if hash_index.hash_file(part) != src_digest:
            raise OSError(f"copy verification failed for {src}")
        try:
            shutil.copystat(src, part)   # keep timestamps when the source
        except OSError:                  # filesystem can serve them
            pass
        os.rename(part, dst)
    except BaseException:
        try:
            os.remove(part)
        except OSError:
            pass
        raise
    return src_digest


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
    if p.kind == "skip_dup":
        return (f"dup   {p.rel} == {p.dest_name} — already in the archive,"
                " left on source")
    if p.kind == "skip_at_dest":
        return (f"dup   {p.rel} — already at the destination,"
                " left on source")
    if p.kind == "skip_twin":
        return (f"dup   {p.rel} — identical to {p.dest_name} in this"
                " import, left on source")
    if p.kind == "skip_not_media":
        return f"skip  {p.rel} — not a media file, left where it is"
    if p.kind == "skip_error":
        return f"skip  {p.rel} — could not be read ({p.note})"
    verb = "copy" if p.kind == "copy" else "move"
    suffix = f" (renamed, {p.dest_name} was taken)" if p.renamed else ""
    return f"{verb}  {p.rel} → {p.dest_name}{suffix}"


def _summary(plans: list, removed_dirs: int, progress, dry_run: bool,
             copy: bool = False) -> None:
    brought = [p for p in plans if p.kind in ("move", "copy")]
    dups = sum(1 for p in plans if p.kind in ("delete_dup", "skip_dup"))
    at_dest = sum(1 for p in plans
                  if p.kind in ("delete_at_dest", "skip_at_dest",
                                "skip_twin"))
    skipped = sum(1 for p in plans if p.kind == "skip_not_media")
    errors = sum(1 for p in plans if p.kind == "skip_error")
    renamed = sum(1 for p in brought if p.renamed)
    title = "What would happen" if dry_run else "Import finished"
    progress("")
    progress(f"=== {title} ===")
    verb = "copied" if copy else "moved"
    progress(f"{verb} to destination:".ljust(24) + f"{len(brought)}"
             f" ({_fmt_bytes(sum(p.size for p in brought))})")
    dup_verb = "skipped" if copy else "deleted"
    progress(f"duplicates {dup_verb}:".ljust(24) + f"{dups + at_dest}"
             f" ({dups} in archive, {at_dest} at destination)")
    progress(f"renamed on collision:   {renamed}")
    progress(f"not media, left alone:  {skipped}")
    if errors:
        progress(f"could not be read:      {errors}")
    if not dry_run and not copy:
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
    """Runs one import (or dup report / archive clean-up) off the GUI thread.
    The runner treats it like a process: outputReady streams log lines,
    exit_code lands before finished() fires."""

    outputReady = Signal(str)

    def __init__(self, cfg: dict, mode: str, parent=None):
        super().__init__(parent)
        self._cfg = dict(cfg)
        self._mode = mode            # "import" | "dry_run" | "dup_report"
                                     # | "dedupe"
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
            elif self._mode == "dedupe":
                self.exit_code = run_dedupe(
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
