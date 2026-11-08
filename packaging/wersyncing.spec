Name:           wersyncing
Version:        0.12.0
Release:        1%{?dist}
Summary:        Managed rsync connections with a PySide6/QML UI

# Renamed we-rsynk-ing -> wersynking (0.2.0) -> wersyncing (0.4.0);
# let dnf swap the old packages.
Obsoletes:      we-rsynk-ing <= 0.1.0
Provides:       we-rsynk-ing = %{version}-%{release}
Obsoletes:      wersynking <= 0.3.1
Provides:       wersynking = %{version}-%{release}

# PolyForm Noncommercial is a valid SPDX id but NOT a Fedora-allowed license;
# this package is distributed via GitHub Releases only, never Fedora/COPR.
License:        PolyForm-Noncommercial-1.0.0
URL:            https://github.com/jmoraur/wersyncing
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  desktop-file-utils

# External tools invoked at runtime
Requires:       rsync
Requires:       util-linux
Requires:       hicolor-icon-theme
Requires:       python3-pyside6
Recommends:     systemd

%description
WeRsyncing is a desktop app for managing recurring rsync jobs between
local folders, removable drives and SSH remotes, with live device
detection, a system tray indicator and desktop notifications.

License: PolyForm Noncommercial 1.0.0 (source available, noncommercial
use only).

%prep
%autosetup -n %{name}-%{version}

%generate_buildrequires
# -R: skip runtime deps at build time (no need for PySide6 in the buildroot
# to build a pure-Python wheel). Runtime Requires still get auto-generated
# from the installed dist-info metadata.
%pyproject_buildrequires -R

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files -l rsync_app

install -Dpm0644 packaging/wersyncing.desktop \
    %{buildroot}%{_datadir}/applications/wersyncing.desktop
install -Dpm0644 packaging/icons/rsync-app.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/rsync-app.svg

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/wersyncing.desktop

%files -f %{pyproject_files}
%license LICENSE
%doc README.md
%{_bindir}/wersyncing
%{_datadir}/applications/wersyncing.desktop
%{_datadir}/icons/hicolor/scalable/apps/rsync-app.svg

%changelog
* Tue Oct 06 2026 Jan Moraru <jan@moraru.ch> - 0.11.0-1
- Bundles: select jobs (click, Ctrl+click, Shift+click), right-click ->
  Bundle...; bundles can hold bundles, each has its own sync button.
  Take out of bundle, Unbundle, Rename
- Groups are gone: the list is destination -> source -> bundles -> jobs
  (or source -> destination), top rows ordered with up/down. Existing
  data is converted once, with a backup (rsync.db.pre-bundles.bak); every
  job and its command stay the same

* Tue Oct 06 2026 Jan Moraru <jan@moraru.ch> - 0.10.3-1
- View switch reads "Group by: Destinations | Sources"

* Tue Oct 06 2026 Jan Moraru <jan@moraru.ch> - 0.10.2-1
- Sources view: source -> destination device -> jobs, like the Groups
  view; the destination row has its own sync button

* Tue Oct 06 2026 Jan Moraru <jan@moraru.ch> - 0.10.1-1
- The job popup lists the warnings still to confirm, not just how many

* Tue Oct 06 2026 Jan Moraru <jan@moraru.ch> - 0.10.0-1
- A job is plain rsync: from (device + folder) -> to (device + folder)
  + options; groups only group jobs. Devices: this computer, drives,
  servers. The old push/pull switch is gone (it let a single-job run go
  the wrong way). Existing data is converted once, with a backup
  (rsync.db.pre-jobs.bak); every job's command stays the same
- New layout: jobs and activity side by side, sync buttons on the left,
  jobs folded to one line, run list + full-height log
- Confirmations are pop-up windows: one per job and one per warning,
  with Cancel / No / Dry run / Yes / Yes to all

* Tue Oct 06 2026 Jan Moraru <jan@moraru.ch> - 0.9.0-1
- Groups, devices, sources and the Imports section fold open/closed
  (arrow or click on the name); the folded state is remembered
- Destination groups can be moved up/down (hover buttons); the order
  is saved

* Tue Oct 06 2026 Jan Moraru <jan@moraru.ch> - 0.8.1-1
- Connection list no longer shows a doubled slash when a subpath was
  saved with leading/trailing slashes (the rsync command was already
  correct)

* Tue Oct 06 2026 Jan Moraru <jan@moraru.ch> - 0.8.0-1
- Connections can now run in either direction: "device -> laptop" (pull)
  copies from a drive or server into a laptop folder with the same
  options, command preview and pre-flight checks as a normal sync

* Sun Aug 09 2026 Jan Moraru <jan@moraru.ch> - 0.7.0-1
- New "Remove duplicates in archive" action: keeps one copy of each
  set of identical archive files (preferring named event folders,
  shallow paths and clean filenames), deletes the extra copies, and
  repairs the kept file's date when a re-copy had clobbered it

* Sun Aug 09 2026 Jan Moraru <jan@moraru.ch> - 0.6.1-1
- Imports ignore hidden files and folders: Android's trash renames
  deleted media to .trashed-* in place, and importing those would
  resurrect deleted photos into the archive

* Sun Aug 09 2026 Jan Moraru <jan@moraru.ch> - 0.6.0-1
- Import jobs gain a copy mode for importing straight off a mounted
  phone: the source is never modified — new files are copied (verified)
  to the destination, duplicates are reported and skipped, and single
  unreadable files no longer abort the run

* Sun Aug 09 2026 Jan Moraru <jan@moraru.ch> - 0.5.0-1
- New media-import job type: dedup a phone dump folder against the photo
  archive (size + blake2b-256), delete exact duplicates from the dump,
  move new media into a chosen destination folder; dry-run mode and an
  archive-internal duplicate report included

* Tue Jul 21 2026 Jan Moraru <jan@moraru.ch> - 0.4.0-1
- Rename package wersynking -> wersyncing (app is now "WeRsyncing");
  command, desktop entry and two-color wordmark updated to match

* Tue Jul 21 2026 Jan Moraru <jan@moraru.ch> - 0.3.1-1
- Fix the light/dark/system theme switch doing nothing under system Qt:
  the KDE platform theme ignores color-scheme requests, so the app now
  sets its palette itself and cascades it to all controls

* Mon Jul 20 2026 Jan Moraru <jan@moraru.ch> - 0.3.0-2
- Disable Qt's QML disk cache at startup: rpm-normalized mtimes made
  same-day releases serve the previous version's cached UI

* Mon Jul 20 2026 Jan Moraru <jan@moraru.ch> - 0.3.0-1
- Plain-language ownership & permissions (Like in source / Like in
  destination / Custom with owner, group and permission fields); fixes
  the self-cancelling force-dest-values rsync flags
- SSH reach settings (--rsh) move to the device; reachability probe
  honors a custom port
- Skip list with preset chips and an add-folder picker
- Connection form: pinned command preview, live validation messages in
  plain language, resolved destination path under the subpath field

* Mon Jul 20 2026 Jan Moraru <jan@moraru.ch> - 0.2.0-1
- Rename package we-rsynk-ing -> wersynking (app is now "WeRSynking")
- In-app two-color wordmark
- Explicit light/dark/system theme mode

* Mon Jul 20 2026 Jan Moraru <jan@moraru.ch> - 0.1.0-1
- Initial package
