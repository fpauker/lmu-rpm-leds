Name:           lmu-rpm-leds
Version:        1.0.0
Release:        1%{?dist}
Summary:        Drive the rev lights of a MOZA wheel from Le Mans Ultimate

License:        GPL-3.0-or-later
URL:            https://github.com/fpauker/lmu-rpm-leds
Source0:        %{url}/archive/v%{version}/%{name}-%{version}.tar.gz

BuildArch:      noarch

BuildRequires:  make
BuildRequires:  desktop-file-utils
BuildRequires:  appstream
BuildRequires:  systemd-rpm-macros
BuildRequires:  gettext

Requires:       python3
Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita
Requires:       python3-cairo
# /usr/lib/systemd/user is owned by systemd, /usr/lib/udev/rules.d by
# systemd-udev; the package installs into both but owns neither.
Requires:       systemd
Requires:       systemd-udev
Requires:       hicolor-icon-theme

%description
MOZA Pit House, the software that feeds the wheelbase on Windows, has no Linux
version, so the rev lights in the rim stay dark. This package provides a small
daemon that reads Le Mans Ultimate telemetry while the game runs under Proton
and pushes the rev bar to the wheel over its serial port, plus a GTK4
application to shape the curve and manage the service.

%prep
%autosetup

# The modules under %%{_datadir} are imported or started through a launcher in
# %%{_bindir}; none of them is executed directly. Drop the shebang rather than
# let brp-mangle-shebangs rewrite it into a non-executable file that still
# carries one.
sed -i '1{/^#!/d}' lmu_rpm_leds.py lmu_led_config.py

%build
%make_build

%install
%make_install PREFIX=%{_prefix}

# Hands the translation catalogues to %%files, so a new language needs no
# change here beyond its po file.
%find_lang %{name}

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/io.github.fpauker.LmuRpmLeds.desktop
appstreamcli validate --no-net --explain \
    %{buildroot}%{_metainfodir}/io.github.fpauker.LmuRpmLeds.metainfo.xml

# The unit is deliberately NOT enabled by a preset: whether the LEDs are fed is
# the user's decision, and the app has a switch for it. Without a shipped preset
# %%systemd_user_post leaves it disabled.
%post
%systemd_user_post %{name}.service

%preun
%systemd_user_preun %{name}.service

%postun
%systemd_user_postun_with_restart %{name}.service

%files -f %{name}.lang
%license LICENSE
%doc README.md
%{_bindir}/lmu-rpm-leds
%{_bindir}/lmu-rpm-leds-daemon
%{_datadir}/lmu-rpm-leds/
%{_userunitdir}/lmu-rpm-leds.service
%{_udevrulesdir}/70-moza-rpm-leds.rules
%{_datadir}/applications/io.github.fpauker.LmuRpmLeds.desktop
%{_metainfodir}/io.github.fpauker.LmuRpmLeds.metainfo.xml
%{_datadir}/icons/hicolor/scalable/apps/io.github.fpauker.LmuRpmLeds.svg

%changelog
* Sat Aug 15 2026 Florian Pauker <38747890+fpauker@users.noreply.github.com> - 1.0.0-1
- First public release
