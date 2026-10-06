"""Pure helpers for building an rsync argv from a binding row.

No I/O. The caller resolves each side's live mountpoint (local) or
network target (remote) and the subpath; this module composes flags
and paths.

OPTIONS is the single source of truth for every rsync toggle the app
knows about: DB column name, exact long-form flag spelling, UI
description, default, and whether it belongs to the baseline group.
The DB schema, the controller's draft defaults and the QML option
lists all derive from this table — a flag is spelled HERE and nowhere
else. (The --hardlinks production bug happened because the spelling
lived in several places; keep it that way.)
"""

# Baseline group: on by default, "recommended" in the UI.
# Extra group: off by default.
OPTIONS = [
    {"key": "opt_archive", "flag": "--archive", "baseline": True,
     "description": "Recurse and preserve symlinks, permissions, "
                    "timestamps, owner and group"},
    {"key": "opt_verbose", "flag": "--verbose", "baseline": True,
     "description": "Print each file as it is transferred"},
    {"key": "opt_human_readable", "flag": "--human-readable", "baseline": True,
     "description": "Show sizes like 1.2M instead of raw bytes"},
    {"key": "opt_hardlinks", "flag": "--hard-links", "baseline": True,
     "description": "Preserve hard links as hard links"},
    {"key": "opt_partial", "flag": "--partial", "baseline": True,
     "description": "Keep partially transferred files so a run can resume"},
    {"key": "opt_progress", "flag": "--info=progress2", "baseline": True,
     "description": "Show overall transfer progress"},
    {"key": "opt_compress", "flag": "--compress", "baseline": False,
     "description": "Compress file data during transfer "
                    "(more CPU, less network)"},
    {"key": "opt_dry_run", "flag": "--dry-run", "baseline": False,
     "description": "Trial run — report what would change, transfer nothing"},
    {"key": "opt_delete", "flag": "--delete", "baseline": False,
     "description": "Delete files on the destination that no longer exist "
                    "in the source"},
    {"key": "opt_update", "flag": "--update", "baseline": False,
     "description": "Skip files that are newer on the destination"},
    {"key": "opt_existing", "flag": "--existing", "baseline": False,
     "description": "Only update files already on the destination; "
                    "don't create new ones"},
    {"key": "opt_checksum", "flag": "--checksum", "baseline": False,
     "description": "Compare by checksum instead of size and time "
                    "(slower, thorough)"},
    {"key": "opt_inplace", "flag": "--inplace", "baseline": False,
     "description": "Write updates directly into files instead of "
                    "a temp copy"},
]

# default = baseline membership; kept as an explicit key so a future
# baseline-but-default-off option doesn't force a schema rethink.
for _opt in OPTIONS:
    _opt["default"] = _opt["baseline"]


def _effective(binding_row: dict, overrides: dict | None, key: str):
    if overrides and key in overrides:
        return overrides[key]
    return binding_row.get(key)


def _resolve_path(side: dict, path_mode: str) -> str:
    base = side["base"].rstrip("/")
    subpath = (side.get("subpath") or "").strip("/")
    path = f"{base}/{subpath}" if subpath else base
    if path_mode == "contents":
        path += "/"
    return path


def build_rsync_argv(
    binding_row: dict,
    source: dict,
    dest: dict,
    runtime_overrides: dict | None = None,
) -> list[str]:
    argv = ["rsync"]

    for opt in OPTIONS:
        if _effective(binding_row, runtime_overrides, opt["key"]):
            argv.append(opt["flag"])

    chown_mode = _effective(binding_row, runtime_overrides, "chown_mode")
    if chown_mode == "dest":
        # "Like in destination": files end up as if created fresh on the
        # receiving side — owned by the receiving account, default perms,
        # executables kept runnable. The man page's own recipe (--perms).
        # Stored chown/chmod values are ignored in this mode.
        argv += ["--no-owner", "--no-group", "--no-perms", "--chmod=ugo=rwX"]
    elif chown_mode == "custom":
        # Force exact values. --chown's mapping replaces source ownership,
        # so owner/group/perms must stay ON — adding --no-owner/--no-group
        # here would cancel the --chown, and --no-perms would mask --chmod
        # through the receiver's umask. (Forcing a *different* owner still
        # needs a root receiving side; rsync silently keeps the receiving
        # account otherwise.)
        chown_value = _effective(binding_row, runtime_overrides, "chown_value")
        chmod_value = _effective(binding_row, runtime_overrides, "chmod_value")
        if chown_value:
            argv.append(f"--chown={chown_value}")
        if chmod_value:
            argv.append(f"--chmod={chmod_value}")

    excludes = _effective(binding_row, runtime_overrides, "excludes")
    if excludes:
        for line in excludes.splitlines():
            pattern = line.strip()
            if pattern:
                argv.append(f"--exclude={pattern}")

    # rsh describes how to reach the *device*, so it rides on the dest
    # context, not the binding — there is deliberately no per-run override.
    rsh = dest.get("rsh")
    if rsh:
        argv.append(f"--rsh={rsh}")

    path_mode = _effective(binding_row, runtime_overrides, "path_mode")
    laptop = _resolve_path(source, path_mode)
    device = _resolve_path(dest, path_mode)
    # "pull" copies device → laptop: same flags, the two paths swap. The
    # binding keeps naming them source/dest (laptop folder / device side).
    if binding_row.get("direction") == "pull":
        argv += [device, laptop]
    else:
        argv += [laptop, device]

    return argv
