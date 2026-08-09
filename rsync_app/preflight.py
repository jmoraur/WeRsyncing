"""Pure pre-flight checks for a binding about to run.

No I/O on Qt objects, no DB, no probes — the caller resolves the source
filesystem path, the destination context (mountpoint or network target),
and a single `available` bool (mounted for local, reachable for remote),
then this module returns a list of issues.

Issue shape: `{"severity": "error"|"warning", "code": str, "message": str}`.

Errors block the run unconditionally. Warnings block until the user
acknowledges each one in the dialog ("I understand").
"""
import os


def check_binding(row: dict, source: dict, dest: dict) -> list[dict]:
    """Inspect a binding draft + its resolved sides and return issues.

    `row` is the binding dict (path_mode, chown_*, opt_*, excludes,
    dest_subpath). `source` is `{"path": <abs source path>}`. `dest` is
    `{"kind": "local"|"remote", "base": <mountpoint|network_target>,
    "subpath": <dest_subpath>, "available": <bool>}`.
    """
    issues: list[dict] = []
    _check_source(source, issues)
    _check_dest(dest, issues)
    _check_warnings(row, source, dest, issues)
    return issues


def _err(code: str, message: str) -> dict:
    return {"severity": "error", "code": code, "message": message}


def _warn(code: str, message: str) -> dict:
    return {"severity": "warning", "code": code, "message": message}


def _check_source(source: dict, issues: list[dict]) -> None:
    path = (source or {}).get("path") or ""
    if not path:
        issues.append(_err("E_SRC_MISSING", "The source folder is not set."))
        return
    if not os.path.isdir(path):
        issues.append(_err(
            "E_SRC_MISSING",
            f"The source folder can't be found: {path}",
        ))
        return
    if not os.access(path, os.R_OK):
        issues.append(_err(
            "E_SRC_UNREADABLE",
            f"The app isn't allowed to read the source folder: {path}",
        ))


def _check_dest(dest: dict, issues: list[dict]) -> None:
    kind = (dest or {}).get("kind", "")
    available = bool((dest or {}).get("available"))
    if kind == "local":
        if not available:
            issues.append(_err(
                "E_DEST_NOT_MOUNTED",
                "The destination drive is not connected.",
            ))
            return
        resolved = _resolved_dest_path(dest)
        ancestor = _existing_ancestor(resolved)
        if ancestor is None:
            issues.append(_err(
                "E_DEST_PARENT_UNWRITABLE",
                f"There's no folder to create the destination in: {resolved}",
            ))
            return
        if not os.access(ancestor, os.W_OK):
            issues.append(_err(
                "E_DEST_PARENT_UNWRITABLE",
                f"The destination folder can't be written to: {ancestor}",
            ))
        return
    if kind == "remote":
        if not available:
            issues.append(_err(
                "E_REMOTE_UNREACHABLE",
                "The server did not respond. Check that it's online.",
            ))


def _check_warnings(row: dict, source: dict, dest: dict,
                    issues: list[dict]) -> None:
    if row.get("opt_delete"):
        issues.append(_warn(
            "W_DELETE_ENABLED",
            "Files that exist only on the destination will be deleted"
            " (--delete is on).",
        ))

    path_mode = row.get("path_mode") or "contents"
    if path_mode == "folder":
        issues.append(_warn(
            "W_PATH_MODE_FOLDER",
            "The source folder itself will be placed inside the"
            " destination, not just its contents.",
        ))

    src_path = (source or {}).get("path") or ""
    if path_mode == "contents" and src_path:
        src_leaf = os.path.basename(src_path.rstrip("/"))
        dest_sub = (row.get("dest_subpath") or "").strip("/")
        if dest_sub:
            dest_leaf = os.path.basename(dest_sub)
        else:
            dest_leaf = os.path.basename(
                ((dest or {}).get("base") or "").rstrip("/")
            )
        if src_leaf and dest_leaf and src_leaf.lower() != dest_leaf.lower():
            issues.append(_warn(
                "W_BASENAME_MISMATCH",
                f"The source folder is called '{src_leaf}' but the"
                f" destination folder is called '{dest_leaf}' —"
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
    check_binding). `job` carries dump_path / dest_path / archive_root and
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


def _resolved_dest_path(dest: dict) -> str:
    base = (dest.get("base") or "").rstrip("/")
    subpath = (dest.get("subpath") or "").strip("/")
    return f"{base}/{subpath}" if subpath else base


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
