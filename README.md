# WeRsyncing

A KDE desktop app that replaces hand-typed `rsync` invocations with a managed
library of connections. Define *source folder → destination device* pairs once,
then run them with one click — the app always shows you the literal `rsync`
command it is about to execute.

![Main window](docs/screenshot.png)

## What it does

- **One page.** A tree of your sync connections, groupable by destination or
  by source. No tabs, no wizards.
- **Mount-aware.** Local backup drives are tracked by filesystem UUID and
  polled via `lsblk`; SSH remotes are probed on their SSH port. Unreachable
  destinations are shown as such, and their sync buttons disable themselves.
- **Scoped syncs.** Run a single connection, everything on one device
  (sequential — one writer per disk), or a whole container of devices
  (parallel across devices, sequential within each).
- **Pre-flight checks.** Before anything runs: source exists and is readable,
  destination is actually mounted, `--delete` and other risky options require
  an explicit per-run acknowledgement.
- **The command is the interface.** Every confirmation dialog and output panel
  shows the exact shell-quoted `rsync` argv. Copy it, inspect it, distrust it.
- **Live output.** Streaming per-job output panel, system tray with progress
  tooltip, desktop notifications on completion, sleep inhibition while
  syncing.
- **rsync options as data.** Long-form flags (`--archive`, not `-a`) with
  per-connection defaults and per-run overrides. Structured `--chown`/`--chmod`
  support for NAS-style destinations, excludes, custom `--rsh`.
- **Media imports.** A second job type for photo/video archives: point it at
  a source folder, an archive, and a destination. In *move* mode the source
  is a dump folder that gets cleaned out — new files are moved to the
  destination; files already in the archive (verified byte-identical,
  size + blake2b-256, re-checked right before deletion) are deleted. In
  *copy* mode the source is never touched — made for importing straight off
  a phone on an MTP mount: new files are copied over (verified), duplicates
  are just reported, and single unreadable files don't abort the run.
  Hidden files and folders are never imported (on Android those are
  trashed/pending media and thumbnail caches). Includes a dry-run mode
  and a report-only duplicate finder for the archive itself.

## Install (Fedora)

Download the latest `.rpm` from
[Releases](https://github.com/jmoraur/wersyncing/releases), then:

```bash
sudo dnf install ./wersyncing-*.noarch.rpm
```

Launch **WeRsyncing** from the app menu / KRunner, or run `wersyncing`
in a terminal.

Dependencies (pulled in automatically): `python3-pyside6`, `rsync`.

## Run from source

```bash
git clone https://github.com/jmoraur/wersyncing.git
cd wersyncing
sudo dnf install python3-pyside6 rsync
python -m rsync_app
```

Optional: `bash scripts/install_desktop.sh` adds a launcher entry that runs
the app from the checkout.

## Data locations

- Connection library (SQLite): `~/.local/share/RsyncApp/RsyncApp/rsync.db`
- Window/UI settings: `~/.config/RsyncApp/RsyncApp.conf`
- Media-import content index (rebuildable cache):
  `~/.local/share/RsyncApp/RsyncApp/media_index.db`

Delete any of these to reset (the index just gets rebuilt on the next
import run).

## Scope and non-goals

Built for a single user on Fedora KDE. Local USB disks and rsync-over-SSH
remotes only — no SSHFS/NFS/CIFS mount management, no scheduling daemon, no
sync history database. For sync connections, the app manages and runs
commands and your data is only ever touched by plain `rsync`. The one
exception is the media-import feature: in move mode it moves and deletes
files in your dump folder itself — guarded by content verification, a
pre-delete re-check, and a first-class dry run — while in copy mode it only
ever writes verified copies into the destination and never modifies the
source.

## License

[PolyForm Noncommercial 1.0.0](LICENSE) — free for personal and other
noncommercial use, forks welcome; commercial use is not permitted.
