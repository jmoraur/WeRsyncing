Name:           wersyncing
Version:        0.6.0
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
