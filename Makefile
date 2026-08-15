# SPDX-License-Identifier: GPL-3.0-or-later

PREFIX      ?= /usr
DESTDIR     ?=
APPID        = io.github.fpauker.LmuRpmLeds
# The distribution interpreter, not whatever PATH resolves to: the GUI needs
# PyGObject and pycairo, which a pyenv/conda/linuxbrew python3 does not have.
PYTHON      ?= /usr/bin/python3

BINDIR       = $(DESTDIR)$(PREFIX)/bin
LIBDIR       = $(DESTDIR)$(PREFIX)/share/lmu-rpm-leds
UNITDIR      = $(DESTDIR)$(PREFIX)/lib/systemd/user
UDEVDIR      = $(DESTDIR)$(PREFIX)/lib/udev/rules.d
APPSDIR      = $(DESTDIR)$(PREFIX)/share/applications
METAINFODIR  = $(DESTDIR)$(PREFIX)/share/metainfo
ICONDIR      = $(DESTDIR)$(PREFIX)/share/icons/hicolor/scalable/apps

MODULES      = lmu_rpm_leds.py lmu_led_config.py moza.py config.py \
               service.py ledview.py
# Shipped for troubleshooting. verify_protocol.py stays out of the package on
# purpose: it compares against boxflat's own encoder and therefore needs boxflat
# installed as a Flatpak, which no user should have to do to run the daemon.
# (It needs no PyYAML of its own — the Flatpak bundles it, and the script puts
# that site-packages directory on sys.path.)
TOOLS        = find_rpm.py led_test.py led_map.py
DEVTOOLS     = verify_protocol.py

.PHONY: all install uninstall check clean

all:
	@echo "Nothing to build — run 'sudo make install'."

check:
	@for f in $(MODULES) $(TOOLS) $(DEVTOOLS); do python3 -m py_compile $$f || exit 1; done
	@echo "All modules compile."

# Paths are substituted rather than hardcoded, so PREFIX=/usr/local produces a
# unit and launchers that actually point at the installed files.
# The sed delimiter is | and not #: make treats # as the start of a comment
# even inside a variable assignment, which silently truncates the expression.
SUBST = sed -e 's|@LIBDIR@|$(PREFIX)/share/lmu-rpm-leds|g' \
            -e 's|@BINDIR@|$(PREFIX)/bin|g' \
            -e 's|@PYTHON@|$(PYTHON)|g'

install:
	install -d $(LIBDIR) $(BINDIR) $(UNITDIR) $(UDEVDIR) $(APPSDIR) $(METAINFODIR) $(ICONDIR)
	install -m 0644 $(MODULES) $(TOOLS) $(LIBDIR)/
	$(SUBST) data/lmu-rpm-leds.in         > $(BINDIR)/lmu-rpm-leds
	$(SUBST) data/lmu-rpm-leds-daemon.in  > $(BINDIR)/lmu-rpm-leds-daemon
	chmod 0755 $(BINDIR)/lmu-rpm-leds $(BINDIR)/lmu-rpm-leds-daemon
	$(SUBST) data/lmu-rpm-leds.service.in > $(UNITDIR)/lmu-rpm-leds.service
	chmod 0644 $(UNITDIR)/lmu-rpm-leds.service
	install -m 0644 data/70-moza-rpm-leds.rules $(UDEVDIR)/
	install -m 0644 data/$(APPID).desktop $(APPSDIR)/
	install -m 0644 data/$(APPID).metainfo.xml $(METAINFODIR)/
	install -m 0644 data/$(APPID).svg $(ICONDIR)/

uninstall:
	rm -rf $(LIBDIR)
	rm -f $(BINDIR)/lmu-rpm-leds $(BINDIR)/lmu-rpm-leds-daemon
	rm -f $(UNITDIR)/lmu-rpm-leds.service
	rm -f $(UDEVDIR)/70-moza-rpm-leds.rules
	rm -f $(APPSDIR)/$(APPID).desktop
	rm -f $(METAINFODIR)/$(APPID).metainfo.xml
	rm -f $(ICONDIR)/$(APPID).svg

clean:
	rm -rf __pycache__ *.pyc
