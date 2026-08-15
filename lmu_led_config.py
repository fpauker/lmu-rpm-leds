#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Florian Pauker
"""Configuration app for the LMU RPM LED daemon.

Change the rev-light curve, watch it apply live on the wheel, and control the
systemd user service that feeds it.

Needs the Fedora python (/usr/bin/python3) — that is the one carrying PyGObject.
The daemon itself stays stdlib-only and does not depend on any of this.
"""

import os
import sys
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402
from i18n import _  # noqa: E402
import ledview  # noqa: E402
import lmu_rpm_leds as daemon  # noqa: E402
import moza  # noqa: E402
import service  # noqa: E402

APP_ID = "io.github.fpauker.LmuRpmLeds"

# Keys double as button labels, so they are translated at use time.
PRESETS = {
    "late": {"start": 0.90, "end": 0.99, "blink": 0.995},
    "standard": {"start": 0.85, "end": 0.98, "blink": 0.99},
    "early": {"start": 0.75, "end": 0.96, "blink": 0.985},
    "full range": {"start": 0.40, "end": 0.97, "blink": 0.99},
}

PRESET_LABELS = {
    "late": _("Late"),
    "standard": _("Standard"),
    "early": _("Early"),
    "full range": _("Full range"),
}


class TelemetrySource:
    """Read-only view of the game's telemetry, safe to run beside the daemon.

    Only ever reads from /proc, so it never competes for the serial port.
    """

    def __init__(self):
        self._tele = None
        self._next_scan = 0.0

    def sample(self):
        """(rpm, maxrpm, gear, throttle) or None when nothing is driving."""
        now = time.monotonic()
        if self._tele is not None and not self._tele.alive():
            self._tele.close()
            self._tele = None
        if self._tele is None:
            if now < self._next_scan:
                return None
            self._next_scan = now + 3.0
            found = daemon.find_source()
            if not found:
                return None
            self._tele = daemon.Telemetry(*found)
        try:
            return self._tele.read()
        except OSError:
            self._tele.close()
            self._tele = None
            return None

    def close(self):
        if self._tele:
            self._tele.close()
            self._tele = None


class Window(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title(_("MOZA RPM LEDs"))
        self.set_default_size(680, 900)

        self.cfg = config.load()
        self.telemetry = TelemetrySource()
        # Held as an attribute on purpose: a DBusProxy with no surviving Python
        # reference gets collected and silently stops delivering signals.
        self.svc = service.ServiceController(service.UNIT)
        self.svc.connect("changed", self._on_service_changed)
        self.svc.connect("error", lambda _c, msg: self._toast(msg))
        self._journal_tail = None
        self._loading = False       # guard against feedback while filling widgets
        self._sim_value = 0.0
        self._sim_wheel = None
        self._testing = False
        self._renew = None
        self._abort_test = None
        self._blink_phase = False
        self._blink_at = 0.0
        self._preview_mask = 0
        self._live_frac = None
        self._last_maxrpm = None

        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        view.add_top_bar(header)

        self.banner = Adw.Banner(revealed=False)
        self.banner.set_button_label(_("Show journal"))
        self.banner.connect("button-clicked", self._on_banner_clicked)

        page = Adw.PreferencesPage()
        page.add(self._group_status())
        page.add(self._group_preview())
        page.add(self._group_curve())
        page.add(self._group_advanced())

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self.banner)
        box.append(page)
        page.set_vexpand(True)
        view.set_content(box)
        self.toasts.set_child(view)
        self.set_content(self.toasts)

        menu = Gio.Menu()
        menu.append(_("Show journal"), "win.journal")
        menu.append(_("Configuration file"), "win.reveal")
        button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        header.pack_end(button)
        self._add_action("journal", lambda *_: self._show_journal())
        self._add_action("reveal", lambda *_: self._reveal_config())

        self._load_into_widgets()
        GLib.timeout_add(50, self._tick_preview)
        self._on_service_changed(self.svc, self.svc.active_state,
                                 self.svc.sub_state, self.svc.unit_file_state)
        self.connect("close-request", self._on_close)

    # ---------------------------------------------------------------- widgets

    def _add_action(self, name, cb):
        act = Gio.SimpleAction.new(name, None)
        act.connect("activate", cb)
        self.add_action(act)

    def _group_status(self):
        group = Adw.PreferencesGroup(title=_("Service"))

        self.row_state = Adw.ActionRow(title=_("Status"), subtitle=_("checking …"))
        self.state_icon = Gtk.Image(icon_name="content-loading-symbolic")
        self.row_state.add_prefix(self.state_icon)
        restart = Gtk.Button(icon_name="view-refresh-symbolic",
                             tooltip_text=_("Restart the service"),
                             valign=Gtk.Align.CENTER)
        restart.add_css_class("flat")
        restart.connect("clicked", lambda *_: self.svc.restart())
        self.row_state.add_suffix(restart)
        group.add(self.row_state)

        self.row_running = Adw.SwitchRow(title=_("Service running"),
                                         subtitle=_("feeds the LEDs in the background"))
        self.row_running.connect("notify::active", self._on_running_toggled)
        group.add(self.row_running)

        self.row_autostart = Adw.SwitchRow(title=_("Start on login"),
                                           subtitle=_("enable the systemd user service"))
        self.row_autostart.connect("notify::active", self._on_autostart_toggled)
        group.add(self.row_autostart)

        self.row_game = Adw.ActionRow(title="Le Mans Ultimate", subtitle="—")
        self.game_icon = Gtk.Image(icon_name="applications-games-symbolic")
        self.row_game.add_prefix(self.game_icon)
        group.add(self.row_game)
        return group

    def _group_preview(self):
        group = Adw.PreferencesGroup(
            title=_("Preview"),
            description=_("Shows live what is lit on the wheel"))

        self.bar = Gtk.DrawingArea(content_height=46)
        self.bar.set_draw_func(self._draw_bar)
        self.bar.set_margin_top(6)
        self.bar.set_margin_bottom(6)
        self.bar.set_margin_start(6)
        self.bar.set_margin_end(6)

        self.lbl_rpm = Gtk.Label(label=_("no telemetry"), xalign=0.5)
        self.lbl_rpm.add_css_class("dim-label")

        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        wrap.append(self.bar)
        wrap.append(self.lbl_rpm)
        row = Adw.PreferencesRow(activatable=False)
        row.set_child(wrap)
        group.add(row)

        self.row_sim = Adw.SwitchRow(
            title=_("Simulation"),
            subtitle=_("pause the service and drive the LEDs with the slider"))
        self.row_sim.connect("notify::active", self._on_sim_toggled)
        group.add(self.row_sim)

        self.sim_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0.0, 105.0, 0.5)
        self.sim_scale.set_draw_value(True)
        self.sim_scale.set_value_pos(Gtk.PositionType.RIGHT)
        self.sim_scale.set_format_value_func(lambda _s, v: f"{v:.0f} %")
        self.sim_scale.set_sensitive(False)
        self.sim_scale.connect("value-changed", self._on_sim_value)
        for mark in (50, 75, 85, 95, 100):
            self.sim_scale.add_mark(mark, Gtk.PositionType.BOTTOM, None)
        sim_row = Adw.PreferencesRow(activatable=False)
        sim_row.set_child(self.sim_scale)
        self.sim_scale.set_margin_start(12)
        self.sim_scale.set_margin_end(12)
        self.sim_scale.set_margin_bottom(6)
        group.add(sim_row)
        return group

    def _group_curve(self):
        group = Adw.PreferencesGroup(
            title=_("Curve"),
            description=_("Fraction of maximum RPM, in percent"))

        self.chart = Gtk.DrawingArea(content_height=170)
        self.chart.set_draw_func(self._draw_chart)
        chart_row = Adw.PreferencesRow(activatable=False)
        chart_row.set_child(self.chart)
        self.chart.set_margin_top(8)
        self.chart.set_margin_bottom(8)
        self.chart.set_margin_start(8)
        self.chart.set_margin_end(8)
        group.add(chart_row)

        self.row_mode = Adw.ComboRow(
            title=_("Fill style"),
            subtitle=_("how the bar grows as the revs rise"),
            model=Gtk.StringList.new([_("Left to right"), _("From both ends")]))
        self.row_mode.connect("notify::selected", self._on_widget_changed)
        group.add(self.row_mode)

        self.row_start = self._spin(_("First LED"),
                                    _("the first LED lights from here"), 30, 100, 1)
        self.row_end = self._spin(_("All LEDs"),
                                  _("the whole bar is lit from here"), 35, 105, 1)
        self.row_blink = self._spin(_("Blink"),
                                    _("shift point, the bar flashes"), 40, 110, 1)
        # Percentages alone give no feel for where a threshold actually sits, so
        # the live limiter is folded into the subtitles as absolute revs.
        self._spin_texts = {
            self.row_start: _("the first LED lights from here"),
            self.row_end: _("the whole bar is lit from here"),
            self.row_blink: _("shift point, the bar flashes"),
        }
        for row in (self.row_start, self.row_end, self.row_blink):
            group.add(row)

        presets = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                          halign=Gtk.Align.CENTER, margin_top=6, margin_bottom=6)
        for name in PRESETS:
            btn = Gtk.Button(label=PRESET_LABELS[name])
            btn.connect("clicked", self._on_preset, name)
            presets.append(btn)
        preset_row = Adw.PreferencesRow(activatable=False)
        preset_row.set_child(presets)
        group.add(preset_row)
        return group

    def _group_advanced(self):
        group = Adw.PreferencesGroup(title=_("More settings"))

        self.row_blink_hz = self._spin(_("Blink rate"), _("flashes per second"),
                                       1, 30, 1, suffix=" Hz")
        group.add(self.row_blink_hz)

        self.row_rate = self._spin(_("Sample rate"), _("telemetry polls per second"),
                                   5, 200, 5, suffix=" Hz")
        group.add(self.row_rate)

        self.row_leds = self._spin(_("LEDs in the rim"), _("number of segments"), 1, 16, 1)
        group.add(self.row_leds)

        self.row_legacy = Adw.SwitchRow(
            title=_("Legacy telemetry command"),
            subtitle=_("only needed if the bar stays dark"))
        self.row_legacy.connect("notify::active", self._on_widget_changed)
        group.add(self.row_legacy)

        test = Adw.ActionRow(title=_("LED test"),
                             subtitle=_("play a sweep on the wheel"))
        btn = Gtk.Button(label=_("Start"), valign=Gtk.Align.CENTER)
        btn.connect("clicked", lambda *_: self._run_test())
        test.add_suffix(btn)
        group.add(test)
        return group

    def _spin(self, title, subtitle, low, high, step, suffix=""):
        adj = Gtk.Adjustment(lower=low, upper=high, step_increment=step,
                             page_increment=step * 5)
        if suffix:
            subtitle = f"{subtitle} ({suffix.strip()})"
        row = Adw.SpinRow(title=title, subtitle=subtitle, adjustment=adj, digits=0)
        row.connect("notify::value", self._on_widget_changed)
        return row

    # ------------------------------------------------------------- config i/o

    def _load_into_widgets(self):
        self._loading = True
        self.row_start.set_value(round(self.cfg["start"] * 100))
        self.row_end.set_value(round(self.cfg["end"] * 100))
        self.row_blink.set_value(round(self.cfg["blink"] * 100))
        self.row_blink_hz.set_value(self.cfg["blink_hz"])
        self.row_rate.set_value(self.cfg["rate"])
        self.row_leds.set_value(self.cfg["leds"])
        self.row_legacy.set_active(self.cfg["legacy"])
        self.row_mode.set_selected(config.MODES.index(self.cfg["mode"]))
        self._loading = False
        self._redraw()

    def _on_widget_changed(self, *_args):
        if self._loading:
            return
        self.cfg.update({
            "start": self.row_start.get_value() / 100,
            "end": self.row_end.get_value() / 100,
            "blink": self.row_blink.get_value() / 100,
            "blink_hz": self.row_blink_hz.get_value(),
            "rate": self.row_rate.get_value(),
            "leds": int(self.row_leds.get_value()),
            "legacy": self.row_legacy.get_active(),
            "mode": config.MODES[self.row_mode.get_selected()],
        })
        self.cfg = config.save(self.cfg)
        self._redraw()

    def _on_preset(self, _btn, name):
        self.cfg.update(PRESETS[name])
        self.cfg = config.save(self.cfg)
        self._load_into_widgets()
        self._toast(_("Curve “{name}” applied").format(name=PRESET_LABELS[name]))

    # ------------------------------------------------------------- simulation

    def _on_sim_toggled(self, row, _param):
        active = row.get_active()
        if active and self._testing:
            # Otherwise both the sweep and the slider would write to the port.
            self._loading = True
            row.set_active(False)
            self._loading = False
            self._toast(_("Wait for the LED test to finish"))
            return
        self.sim_scale.set_sensitive(active)
        if active:
            # Take the wheel from the daemon, then open the port ourselves.
            config.pause(3.0)
            try:
                self._sim_wheel = moza.MozaSerial()
                self._sim_wheel.set_indicator_mode(1)
            except OSError as exc:
                self._sim_wheel = None
                config.unpause()
                self._toast(_("Wheel not reachable: {error}").format(error=exc))
                self._loading = True
                row.set_active(False)
                self._loading = False
                self.sim_scale.set_sensitive(False)
                return
            self._renew = GLib.timeout_add_seconds(1, self._renew_pause)
            self._toast(_("Service paused — the slider drives the LEDs"))
        else:
            self._release_sim()
        self._update_sim_banner()

    def _update_sim_banner(self):
        """Simulation left switched on looks exactly like a broken daemon: the
        LEDs stop following the game. Say so, permanently, while it is active."""
        if self.row_sim.get_active():
            self.banner.set_title(_("Simulation active — the LEDs follow the "
                                    "slider, not the game"))
            self.banner.set_button_label(_("Stop"))
            self.banner.set_revealed(True)
        else:
            self.banner.set_button_label(_("Show journal"))
            self.banner.set_revealed(False)

    def _renew_pause(self):
        """Keep the lease alive while we hold the port."""
        if not self.row_sim.get_active() and not self._testing:
            self._renew = None
            return False
        config.pause(3.0)
        return True

    def _release_sim(self):
        if getattr(self, "_renew", None):
            GLib.source_remove(self._renew)
            self._renew = None
        if self._sim_wheel:
            try:
                self._sim_wheel.set_leds(0)
                self._sim_wheel.close()
            except OSError:
                pass
            self._sim_wheel = None
        if not self._testing:
            config.unpause()

    def _on_sim_value(self, scale):
        self._sim_value = scale.get_value() / 100.0

    # ----------------------------------------------------------------- timers

    def _on_service_changed(self, _ctrl, active_state, sub_state, unit_file_state):
        running = active_state == "active"
        failed = active_state == "failed"

        self.row_state.set_subtitle(service.describe(active_state, sub_state))
        self.state_icon.set_from_icon_name(
            "emblem-ok-symbolic" if running else
            "dialog-error-symbolic" if failed else "media-playback-stop-symbolic")

        # The simulation warning outranks the failure notice: it is the state
        # the user is most likely to have forgotten about.
        if self.row_sim.get_active():
            self._update_sim_banner()
        elif failed:
            detail = self.svc.failure_detail()
            self.banner.set_title(_("The service has failed")
                                  + (f" — {detail}" if detail else ""))
            self.banner.set_button_label(_("Show journal"))
            self.banner.set_revealed(True)
        else:
            self.banner.set_revealed(False)

        # Setting the switches would otherwise bounce straight back as a
        # start/stop request.
        self._loading = True
        self.row_running.set_active(running)
        self.row_autostart.set_active(
            unit_file_state in ("enabled", "enabled-runtime", "linked", "static"))
        self._loading = False

    def _on_running_toggled(self, row, _param):
        if self._loading:
            return
        self.svc.start() if row.get_active() else self.svc.stop()

    def _on_autostart_toggled(self, row, _param):
        if self._loading:
            return
        self.svc.set_enabled(row.get_active())

    def _tick_preview(self):
        cfg = self.cfg
        leds = cfg["leds"]
        sample = None if self.row_sim.get_active() else self.telemetry.sample()

        if self.row_sim.get_active():
            frac = self._sim_value
            self.lbl_rpm.set_label(
                _("Simulation — {percent:.0f} % of maximum RPM").format(
                    percent=frac * 100))
        elif sample:
            rpm, mx, gear, throttle = sample
            frac = rpm / mx if mx else 0.0
            if mx and mx != self._last_maxrpm:
                self._last_maxrpm = mx
                self._update_threshold_texts()
            gear_text = "N" if gear == 0 else ("R" if gear < 0 else str(gear))
            self.lbl_rpm.set_label(
                _("{rpm} of {max} rpm  ·  {percent:.0f} %  ·  gear {gear}  ·  "
                  "throttle {throttle:.0f} %").format(
                      rpm=f"{rpm:,.0f}", max=f"{mx:,.0f}", percent=frac * 100,
                      gear=gear_text, throttle=throttle * 100))
        else:
            frac = None
            self.lbl_rpm.set_label(_("no telemetry — are you in the car?"))

        # candidate_fds() forks pgrep, so it must not run at preview rate.
        now = time.monotonic()
        if sample:
            self.row_game.set_subtitle(_("receiving telemetry"))
            self.game_icon.set_from_icon_name("emblem-ok-symbolic")
            self._game_checked = now
        elif now - getattr(self, "_game_checked", 0.0) > 1.0:
            self._game_checked = now
            if daemon.candidate_fds():
                self.row_game.set_subtitle(_("running, but no car on track"))
                self.game_icon.set_from_icon_name("content-loading-symbolic")
            else:
                self.row_game.set_subtitle(_("not started"))
                self.game_icon.set_from_icon_name("applications-games-symbolic")

        if frac is None:
            mask = 0
        else:
            mask = daemon.mask_for(
                daemon.leds_lit(frac, cfg["start"], cfg["end"], leds),
                leds, cfg["mode"])
            if frac >= cfg["blink"]:
                now = time.monotonic()
                if now - self._blink_at > 0.5 / cfg["blink_hz"]:
                    self._blink_phase = not self._blink_phase
                    self._blink_at = now
                mask = (1 << leds) - 1 if self._blink_phase else 0

        if self._sim_wheel and self.row_sim.get_active():
            try:
                if cfg["legacy"]:
                    self._sim_wheel.set_leds_legacy(mask)
                else:
                    self._sim_wheel.set_leds(mask)
            except OSError:
                self._release_sim()

        self._live_frac = frac
        self._preview_mask = mask
        self.bar.queue_draw()
        self.chart.queue_draw()
        return True

    # ---------------------------------------------------------------- drawing

    def _redraw(self):
        self.bar.queue_draw()
        self.chart.queue_draw()
        self._update_threshold_texts()

    def _update_threshold_texts(self):
        """Restate each threshold in revs of the car currently being driven."""
        mx = self._last_maxrpm
        pairs = ((self.row_start, "start"), (self.row_end, "end"),
                 (self.row_blink, "blink"))
        for row, key in pairs:
            base = self._spin_texts[row]
            if mx:
                row.set_subtitle(_("{base} — {rpm} rpm").format(
                    base=base, rpm=f"{self.cfg[key] * mx:,.0f}"))
            else:
                row.set_subtitle(base)

    def _dark(self):
        return Adw.StyleManager.get_default().get_dark()

    def _draw_bar(self, _area, cr, width, height):
        ledview.draw_bar(cr, width, height, self._preview_mask,
                         self.cfg["leds"], self._dark())

    def _draw_chart(self, _area, cr, width, height):
        ledview.draw_curve(cr, width, height, self.cfg,
                           self._live_frac, self._dark())

    # ------------------------------------------------------------------ misc

    def _on_banner_clicked(self, _banner):
        if self.row_sim.get_active():
            self.row_sim.set_active(False)   # runs _on_sim_toggled, clears banner
        else:
            self._show_journal()

    def _toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=4))

    def _run_test(self):
        """Sweep the bar once. Borrows the wheel from the daemon meanwhile."""
        if self._testing or self.row_sim.get_active():
            return

        config.pause(4.0)
        try:
            wheel = moza.MozaSerial()
            wheel.set_indicator_mode(1)
        except OSError as exc:
            config.unpause()
            self._toast(f"Lenkrad nicht erreichbar: {exc}")
            return

        self._testing = True
        if not self._renew:
            self._renew = GLib.timeout_add_seconds(1, self._renew_pause)

        # The sweep uses the configured fill style, so the test shows what
        # driving will actually look like.
        leds, mode = self.cfg["leds"], self.cfg["mode"]
        steps = [daemon.mask_for(n, leds, mode) for n in range(leds + 1)]
        steps += [daemon.mask_for(n, leds, mode) for n in reversed(range(leds))]
        state = {"i": 0}

        def finish():
            if state.get("done"):
                return
            state["done"] = True
            for key in ("delay", "step"):
                if state.get(key):
                    GLib.source_remove(state[key])
                    state[key] = None
            try:
                wheel.set_leds(0)
                wheel.close()
            except OSError:
                pass
            self._testing = False
            if not self.row_sim.get_active():
                if self._renew:
                    GLib.source_remove(self._renew)
                    self._renew = None
                config.unpause()

        self._abort_test = finish

        def step():
            if state["i"] >= len(steps):
                state["step"] = None
                finish()
                return False
            try:
                wheel.set_leds(steps[state["i"]])
            except OSError:
                state["step"] = None
                finish()
                return False
            state["i"] += 1
            return True

        def begin():
            state["delay"] = None
            state["step"] = GLib.timeout_add(60, step)
            return False

        # Give the daemon one poll interval to notice the pause first.
        state["delay"] = GLib.timeout_add(300, begin)
        self._toast(_("LED test running …"))

    def _show_journal(self):
        """Live journal view — keeps following while the dialog is open."""
        dialog = Adw.AlertDialog(heading=_("Journal"),
                                 body=_("Service messages, live"))
        view = Gtk.TextView(editable=False, monospace=True, top_margin=6,
                            left_margin=6, right_margin=6, bottom_margin=6,
                            wrap_mode=Gtk.WrapMode.WORD_CHAR)
        buf = view.get_buffer()
        scroll = Gtk.ScrolledWindow(min_content_height=340, min_content_width=600)
        scroll.set_child(view)
        dialog.set_extra_child(scroll)
        dialog.add_response("close", _("Close"))

        def append(_tail, line):
            buf.insert(buf.get_end_iter(), line + "\n")
            view.scroll_to_mark(buf.get_insert(), 0.0, True, 0.0, 1.0)
            buf.place_cursor(buf.get_end_iter())

        tail = service.JournalTail(service.UNIT, backlog=80)
        tail.connect("line", append)
        self._journal_tail = tail

        def closed(*_args):
            tail.stop()
            self._journal_tail = None

        dialog.connect("closed", closed)
        dialog.present(self)

    def _reveal_config(self):
        path = config.config_path()
        try:
            Gio.AppInfo.launch_default_for_uri(
                GLib.filename_to_uri(os.path.dirname(path), None), None)
        except GLib.Error:
            self._toast(path)

    def _on_close(self, *_args):
        # Hand the wheel back; the lease would lapse by itself, but not instantly.
        # A sweep still in flight has to be stopped, or it keeps writing to a
        # port nobody owns any more and leaves the bar lit.
        if self._abort_test:
            self._abort_test()
        self._testing = False
        self._release_sim()
        config.unpause()
        if self._journal_tail:
            self._journal_tail.stop()
        self.telemetry.close()
        return False


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_activate(self):
        window = self.props.active_window or Window(application=self)
        window.present()


def main():
    return App().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
