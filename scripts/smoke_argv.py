"""Assertion matrix for build_rsync_argv — the app's core artifact.

Pure Python, no Qt. Run with: PYTHONPATH=. python scripts/smoke_argv.py
Covers the three ownership modes (x values present/partial/absent),
local/remote destinations, and device-level rsh.
"""
from rsync_app.rsync import build_rsync_argv

SRC = {"kind": "local", "base": "/home/user/Pictures", "subpath": ""}
DEST_LOCAL = {"kind": "local", "base": "/mnt/backup", "subpath": "photos"}
DEST_REMOTE = {"kind": "remote", "base": "root@10.0.0.5:/mnt/user",
               "subpath": "media", "rsh": "ssh -p 2222"}

BASE = {
    "path_mode": "contents",
    "chown_mode": "source",
    "chown_value": None,
    "chmod_value": None,
    "opt_archive": 1,
    "excludes": None,
}

DEST_RECIPE = ["--no-owner", "--no-group", "--no-perms", "--chmod=ugo=rwX"]


def argv(row_extra=None, dest=DEST_LOCAL, overrides=None):
    row = {**BASE, **(row_extra or {})}
    return build_rsync_argv(row, SRC, dest, overrides)


def check(label, condition):
    assert condition, label
    print(f"  ok  {label}")


# --- ownership: source mode -------------------------------------------------
a = argv()
check("source mode emits no ownership flags",
      not [f for f in a if f.startswith(("--chown", "--chmod", "--no-"))])

# --- ownership: dest mode ("like in destination") ---------------------------
a = argv({"chown_mode": "dest"})
check("dest mode emits the exact recipe",
      [f for f in a if f in DEST_RECIPE or f.startswith("--chmod")]
      == DEST_RECIPE)
a = argv({"chown_mode": "dest",
          "chown_value": "nobody:users", "chmod_value": "D775,F664"})
check("dest mode ignores stored chown/chmod values",
      "--chown=nobody:users" not in a and "--chmod=D775,F664" not in a
      and "--chmod=ugo=rwX" in a)

# --- ownership: custom mode -------------------------------------------------
a = argv({"chown_mode": "custom",
          "chown_value": "nobody:users", "chmod_value": "D755,F755"})
check("custom mode emits both flags",
      "--chown=nobody:users" in a and "--chmod=D755,F755" in a)
check("custom mode emits no --no-* trio (the old self-cancelling bug)",
      not [f for f in a if f.startswith("--no-")])
a = argv({"chown_mode": "custom", "chown_value": "nobody:users"})
check("custom with only owner emits only --chown",
      "--chown=nobody:users" in a
      and not [f for f in a if f.startswith("--chmod")])
a = argv({"chown_mode": "custom", "chmod_value": "D755,F644"})
check("custom with only permissions emits only --chmod",
      "--chmod=D755,F644" in a
      and not [f for f in a if f.startswith("--chown")])
a = argv({"chown_mode": "custom"})
check("custom with no values emits nothing",
      not [f for f in a if f.startswith(("--chown", "--chmod", "--no-"))])

# --- per-run override of the mode -------------------------------------------
a = argv(overrides={"chown_mode": "dest"})
check("runtime override switches source → dest recipe",
      "--chmod=ugo=rwX" in a)

# --- rsh: device-level, no binding override ---------------------------------
a = argv(dest=DEST_REMOTE)
check("remote dest ctx rsh is emitted", "--rsh=ssh -p 2222" in a)
a = argv(dest=DEST_LOCAL)
check("local dest emits no --rsh",
      not [f for f in a if f.startswith("--rsh")])
a = argv({"rsh": "ssh -p 9999"}, dest=DEST_LOCAL)
check("a stale binding-level rsh key is inert",
      not [f for f in a if f.startswith("--rsh")])
a = argv({"rsh": "ssh -p 9999"}, dest=DEST_REMOTE,
         overrides={"rsh": "ssh -p 8888"})
check("neither binding nor override rsh beats the device rsh",
      [f for f in a if f.startswith("--rsh")] == ["--rsh=ssh -p 2222"])

# --- paths + excludes sanity (unchanged behavior) ---------------------------
a = argv({"excludes": ".cache/\n\n*.tmp\n"})
check("excludes emit one --exclude per non-empty line",
      [f for f in a if f.startswith("--exclude")]
      == ["--exclude=.cache/", "--exclude=*.tmp"])
check("contents mode adds trailing slashes",
      a[-2].endswith("/") and a[-1] == "/mnt/backup/photos/")
a = argv({"path_mode": "folder"}, dest=DEST_REMOTE)
check("folder mode, remote dest path composed without trailing slash",
      a[-1] == "root@10.0.0.5:/mnt/user/media")

print("smoke_argv: all checks passed")
