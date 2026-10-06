"""Pure pre-flight checks for a job about to run.

No I/O on Qt objects, no DB, no probes — the caller resolves both
sides of the job (path + whether the device is there), then this module
returns a list of issues.

Issue shape: `{"severity": "error"|"warning", "code": str, "message": str}`.

Errors block the run unconditionally. Each warning is its own
confirmation popup; its `code` is the "kind" that "Yes to all" covers.
"""
import os


def check_job(row: dict, src: dict, dst: dict) -> list[dict]:
    """Inspect a job draft + its resolved sides and return issues.

    `row` is the job dict (path_mode, chown_*, opt_*, excludes). `src` and
    `dst` are resolved endpoints: {"kind": "laptop"|"drive"|"server",
    "device": <device label>, "path": <local path or user@host:/path>,
    "available": <drive connected / server answering; laptop always>}.
    Both sides are checked the same way: the source must be there to
    read, the destination must be creatable/writable. Server folders are
    never inspected (the app doesn't log in to look).
    """
    issues: list[dict] = []
    if src.get("kind") == "server" and dst.get("kind") == "server":
        issues.append(_err(
            "E_TWO_SERVERS",
            "rsync can't copy from one server to another directly —"
            " one side must be this laptop or a drive.",
        ))
    for side, role in ((src, "from"), (dst, "to")):
        _check_side(side, role, issues)
    _check_warnings(row, src, dst, issues)
    return issues


def _err(code: str, message: str) -> dict:
    return {"severity": "error", "code": code, "message": message}


def _warn(code: str, message: str) -> dict:
    return {"severity": "warning", "code": code, "message": message}


def _check_side(side: dict, role: str, issues: list[dict]) -> None:
    kind = side.get("kind", "")
    name = side.get("device") or "the device"
    path = side.get("path") or ""
    what = "copy from" if role == "from" else "copy to"
    if not side.get("available"):
        issues.append(_err(
            f"E_{role.upper()}_UNAVAILABLE",
            f"{name} is not connected." if kind == "drive"
            else f"{name} did not respond. Check that it's online.",
        ))
        return
    if kind == "server":
        return
    if not path:
        issues.append(_err(f"E_{role.upper()}_MISSING",
                           f"The folder to {what} is not set."))
        return
    if role == "from":
        if not os.path.isdir(path):
            issues.append(_err(
                "E_FROM_MISSING",
                f"The folder to copy from can't be found: {path}"))
        elif not os.access(path, os.R_OK):
            issues.append(_err(
                "E_FROM_UNREADABLE",
                f"The app isn't allowed to read the folder: {path}"))
        return
    ancestor = _existing_ancestor(path)
    if ancestor is None or not os.access(ancestor, os.W_OK):
        issues.append(_err(
            "E_TO_UNWRITABLE",
            f"The folder to copy to can't be written to: {ancestor or path}"))


def _check_warnings(row: dict, src: dict, dst: dict,
                    issues: list[dict]) -> None:
    if row.get("opt_delete"):
        issues.append(_warn(
            "W_DELETE_ENABLED",
            f"Files that exist only on {dst.get('device') or 'the destination'}"
            f" ({dst.get('path') or '?'}) will be deleted (--delete is on).",
        ))

    path_mode = row.get("path_mode") or "contents"
    if path_mode == "folder":
        issues.append(_warn(
            "W_PATH_MODE_FOLDER",
            "The source folder itself will be placed inside the"
            " destination folder, not just its contents.",
        ))

    src_leaf = _leaf(src.get("path"))
    dst_leaf = _leaf(dst.get("path"))
    if path_mode == "contents" and src_leaf and dst_leaf \
            and src_leaf.lower() != dst_leaf.lower():
        issues.append(_warn(
            "W_BASENAME_MISMATCH",
            f"The folder to copy from is called '{src_leaf}' but the"
            f" folder to copy to is called '{dst_leaf}' —"
            " double-check this is the right place.",
        ))

    if row.get("chown_mode") == "custom":
        if not (row.get("chown_value") or "").strip():
            issues.append(_warn(
                "W_DEST_CHOWN_EMPTY",
                "Custom ownership is selected, but no owner is filled in.",
            ))
        if not (row.get("chmod_value") or "").strip():
            issues.append(_warn(
                "W_DEST_CHMOD_EMPTY",
                "Custom ownership is selected, but no permissions are"
                " filled in.",
            ))

    excludes = row.get("excludes") or ""
    for idx, raw in enumerate(excludes.splitlines(), start=1):
        if not raw.strip():
            continue
        if "\r" in raw:
            issues.append(_warn(
                "W_EXCLUDES_CRLF",
                f"Line {idx} of the skip list contains a hidden"
                " line-ending character and won't match anything —"
                " re-type it.",
            ))
            continue
        if raw[0].isspace():
            issues.append(_warn(
                "W_EXCLUDES_WHITESPACE",
                f"Line {idx} of the skip list starts with a space, which"
                " becomes part of the name to skip.",
            ))


def check_import_job(job: dict, index_empty: bool = False) -> list[dict]:
    """Inspect an import-job draft and return issues (same shape as
    check_job). `job` carries dump_path / dest_path / archive_root and
    an optional truthy copy_mode; `index_empty` is True when the archive
    has no index rows yet (first run).

    All three folders must already exist — the import moves and deletes
    files (or writes copies of them), so pointing it at a typo'd path must
    fail loudly, not create folders. The dump must not overlap the archive:
    the index would then contain the dump's own files and every one of them
    would count as an already-archived duplicate. In copy mode the source
    only needs to be readable (an MTP-mounted phone often isn't writable,
    and nothing on it is ever changed).
    """
    issues: list[dict] = []
    dump = (job.get("dump_path") or "").strip()
    dest = (job.get("dest_path") or "").strip()
    archive = (job.get("archive_root") or "").strip()
    copy = bool(job.get("copy_mode"))

    if not dump or not os.path.isdir(dump):
        issues.append(_err(
            "E_IMP_DUMP_MISSING",
            f"The source folder can't be found: {dump or '(not set)'}"
            " — is the phone connected, unlocked and mounted?"
            if copy else
            f"The dump folder can't be found: {dump or '(not set)'}",
        ))
    elif copy:
        # os.access is unreliable on FUSE mounts; probe with a real read.
        try:
            os.listdir(dump)
        except OSError:
            issues.append(_err(
                "E_IMP_DUMP_UNREADABLE",
                f"The source folder can't be read: {dump}",
            ))
    elif not (os.access(dump, os.R_OK) and os.access(dump, os.W_OK)):
        issues.append(_err(
            "E_IMP_DUMP_UNREADABLE",
            f"The app needs to read and change the dump folder: {dump}",
        ))

    if not dest or not os.path.isdir(dest):
        issues.append(_err(
            "E_IMP_DEST_MISSING",
            f"The destination folder can't be found: {dest or '(not set)'}"
            " — create it first, it is not created automatically.",
        ))
    elif not os.access(dest, os.W_OK):
        issues.append(_err(
            "E_IMP_DEST_UNWRITABLE",
            f"The destination folder can't be written to: {dest}",
        ))

    if not archive or not os.path.isdir(archive):
        issues.append(_err(
            "E_IMP_ARCHIVE_MISSING",
            f"The archive folder can't be found: {archive or '(not set)'}",
        ))

    if dump and archive and _overlaps(dump, archive):
        issues.append(_err(
            "E_IMP_DUMP_OVERLAPS_ARCHIVE",
            "The source folder and the archive folder overlap — every file"
            " would match itself and nothing would ever import."
            if copy else
            "The dump folder and the archive folder overlap — every dump"
            " file would match itself and be deleted.",
        ))
    if dump and dest and _overlaps(dump, dest):
        issues.append(_err(
            "E_IMP_DUMP_OVERLAPS_DEST",
            "The source folder and the destination folder overlap."
            if copy else
            "The dump folder and the destination folder overlap.",
        ))

    if (dest and archive and os.path.isdir(dest) and os.path.isdir(archive)
            and not _is_within(dest, archive)):
        issues.append(_warn(
            "W_IMP_DEST_OUTSIDE_ARCHIVE",
            "The destination is outside the archive folder, so copies made"
            " there won't count as archived — renamed duplicates can be"
            " copied again on later runs."
            if copy else
            "The destination is outside the archive folder, so files moved"
            " there won't count as archived on later runs.",
        ))

    if index_empty and archive and os.path.isdir(archive):
        issues.append(_warn(
            "W_IMP_INDEX_EMPTY",
            "The archive hasn't been scanned yet — the first run reads"
            " every archive file that matches a dump file's size, which"
            " can take a while.",
        ))
    return issues


def _overlaps(a: str, b: str) -> bool:
    return _is_within(a, b) or _is_within(b, a)


def _is_within(inner: str, outer: str) -> bool:
    inner = os.path.realpath(inner)
    outer = os.path.realpath(outer)
    try:
        return os.path.commonpath([inner, outer]) == outer
    except ValueError:
        return False


def _leaf(path: str | None) -> str:
    """Last folder name of a local path or user@host:/path."""
    path = (path or "").split(":", 1)[-1] if ":" in (path or "") else path
    return os.path.basename((path or "").rstrip("/"))


def _existing_ancestor(path: str) -> str | None:
    if not path:
        return None
    cur = path
    while cur and not os.path.exists(cur):
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent
    return cur or None
