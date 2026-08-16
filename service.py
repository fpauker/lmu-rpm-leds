# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Florian Pauker
"""systemd --user integration for the GTK app: status, control, journal.

Everything goes over the session bus (org.freedesktop.systemd1), nothing is
polled. Only Gio/GLib from python3-gobject — no extra dependencies.
"""

from gi.repository import Gio, GLib, GObject

from i18n import _

UNIT = "lmu-rpm-leds.service"

BUS_NAME = "org.freedesktop.systemd1"
MGR_PATH = "/org/freedesktop/systemd1"
MGR_IFACE = "org.freedesktop.systemd1.Manager"
UNIT_IFACE = "org.freedesktop.systemd1.Unit"
SERVICE_IFACE = "org.freedesktop.systemd1.Service"


class ServiceController(GObject.Object):
    """Watches and controls a systemd --user unit.

    Signals:
      changed(active_state, sub_state, unit_file_state)  - state changed
      job-done(result)                                   - start/stop/restart done
      error(message)                                     - call failed
    """

    __gsignals__ = {
        "changed":  (GObject.SignalFlags.RUN_FIRST, None, (str, str, str)),
        "job-done": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "error":    (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, unit_name):
        super().__init__()
        self.unit_name = unit_name
        self._last = None          # last reported state, to swallow duplicates
        self._unit = None
        self._attaching = False
        self._file_state = None    # invalidated by UnitFilesChanged

        self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._mgr = Gio.DBusProxy.new_sync(
            self._bus, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None,
            BUS_NAME, MGR_PATH, MGR_IFACE, None)

        # Without Subscribe() you are relying on some other client on the bus
        # being subscribed, which is when systemd broadcasts. Calling it
        # ourselves is the only guarantee.
        try:
            self._mgr.call_sync("Subscribe", None, Gio.DBusCallFlags.NONE, -1, None)
        except GLib.Error:
            pass  # already subscribed is not an error

        self._mgr.connect("g-signal", self._on_manager_signal)
        self._attach_unit()

    # ---------- unit proxy ----------

    def _attach_unit(self):
        """LoadUnit rather than GetUnit: loads the unit if it is not loaded.

        Guarded against re-entry. LoadUnit makes systemd load the unit, and
        loading it makes systemd broadcast UnitNew — for an inactive unit,
        which systemd unloads again straight away, that is a feedback loop:
        every attach triggers the signal that triggers the next attach. Each
        turn costs several synchronous D-Bus round trips inside a signal
        handler, which starved the GTK main loop so thoroughly that the window
        never got a size and stayed invisible.
        """
        if self._attaching:
            return
        self._attaching = True
        try:
            self._load_unit()
        finally:
            self._attaching = False
        self._file_state = None    # a fresh proxy means a fresh reading

    def _load_unit(self):
        try:
            path = self._mgr.call_sync(
                "LoadUnit", GLib.Variant("(s)", (self.unit_name,)),
                Gio.DBusCallFlags.NONE, -1, None).unpack()[0]
        except GLib.Error as e:
            self.emit("error", _("Unit could not be loaded: {error}").format(error=e.message))
            return
        self._unit = Gio.DBusProxy.new_sync(
            self._bus, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None,
            BUS_NAME, path, UNIT_IFACE, None)
        # ActiveState/SubState are emits-change, so the proxy cache stays fresh
        self._unit.connect("g-properties-changed", lambda *_: self._emit_state())
        self._emit_state()

    # ---------- status ----------

    def _prop(self, iface, name, default=""):
        """Read fresh — for properties without emits-change, e.g. UnitFileState."""
        try:
            v = self._bus.call_sync(
                BUS_NAME, self._unit.get_object_path(),
                "org.freedesktop.DBus.Properties", "Get",
                GLib.Variant("(ss)", (iface, name)),
                GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, -1, None)
            return v.unpack()[0]
        except (GLib.Error, AttributeError):
            return default

    def _cached(self, name, default=""):
        if self._unit is None:
            return default
        v = self._unit.get_cached_property(name)
        return v.unpack() if v is not None else default

    @property
    def active_state(self):
        return self._cached("ActiveState", "unknown")

    @property
    def sub_state(self):
        return self._cached("SubState", "")

    @property
    def unit_file_state(self):
        """NO emits-change on this one, so it has to be read explicitly.

        Cached between UnitFilesChanged signals: it used to be fetched
        synchronously on every state change, which put a blocking round trip
        into every signal handler.
        """
        if self._file_state is None:
            self._file_state = self._prop(UNIT_IFACE, "UnitFileState", "unknown")
        return self._file_state

    @property
    def is_enabled(self):
        return self.unit_file_state in ("enabled", "enabled-runtime", "linked", "static")

    def failure_detail(self):
        """For the error display: why did the unit fail?"""
        if self.active_state != "failed":
            return ""
        result = self._prop(SERVICE_IFACE, "Result", "")
        code = self._prop(SERVICE_IFACE, "ExecMainStatus", 0)
        return f"{result} (Exit {code})" if result else ""

    def _emit_state(self):
        state = (self.active_state, self.sub_state, self.unit_file_state)
        if state == self._last:
            return          # systemd fires several times per transition
        self._last = state
        self.emit("changed", *state)

    # ---------- control ----------

    def _call(self, method, params):
        def done(proxy, res):
            try:
                proxy.call_finish(res)
            except GLib.Error as e:
                self.emit("error", e.message)
        self._mgr.call(method, params, Gio.DBusCallFlags.NONE, -1, None, done)

    def start(self):
        self._call("StartUnit", GLib.Variant("(ss)", (self.unit_name, "replace")))

    def stop(self):
        self._call("StopUnit", GLib.Variant("(ss)", (self.unit_name, "replace")))

    def restart(self):
        self._call("RestartUnit", GLib.Variant("(ss)", (self.unit_name, "replace")))

    def set_enabled(self, enabled):
        if enabled:
            # (files, runtime, force) -> (carries_install_info, changes)
            self._call("EnableUnitFiles",
                       GLib.Variant("(asbb)", ([self.unit_name], False, True)))
        else:
            # (files, runtime) -> (changes)   ... different signature to Enable!
            self._call("DisableUnitFiles",
                       GLib.Variant("(asb)", ([self.unit_name], False)))

    def daemon_reload(self):
        """Only needed when the unit FILE changed."""
        self._call("Reload", None)

    # ---------- signals from the manager ----------

    def _on_manager_signal(self, proxy, sender, signal, params):
        if signal == "JobRemoved":
            _id, _path, unit, result = params.unpack()
            if unit == self.unit_name:
                self._emit_state()
                self.emit("job-done", result)
        elif signal == "UnitFilesChanged":
            # Enable/Disable does NOT announce itself via PropertiesChanged
            self._file_state = None
            self._emit_state()
        # UnitNew and UnitRemoved are deliberately ignored. systemd unloads an
        # inactive unit as soon as nothing references it and loads it again on
        # the next query, so for a stopped service those two signals arrive in
        # a steady stream. Re-attaching on them called LoadUnit, which made
        # systemd emit UnitNew, which re-attached again — a feedback loop of
        # synchronous D-Bus round trips inside a signal handler that starved
        # the GTK main loop until the window never even got a size.
        #
        # The proxy does not need re-attaching: the unit's object path is
        # derived from its name and stays valid across unload and reload, and
        # every state change we care about arrives as PropertiesChanged,
        # JobRemoved or UnitFilesChanged.


class JournalTail(GObject.Object):
    """journalctl --user -u UNIT -f, line by line, non-blocking.

    Signals: line(text)
    """

    __gsignals__ = {"line": (GObject.SignalFlags.RUN_FIRST, None, (str,))}

    def __init__(self, unit_name, backlog=100):
        super().__init__()
        self._proc = Gio.Subprocess.new(
            ["journalctl", "--user", "-u", unit_name,
             "-n", str(backlog), "-f", "--no-pager", "-o", "short-precise"],
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
        self._stream = Gio.DataInputStream.new(self._proc.get_stdout_pipe())
        self._cancel = Gio.Cancellable()
        self._read()

    def _read(self):
        self._stream.read_line_async(GLib.PRIORITY_DEFAULT, self._cancel, self._on_line)

    def _on_line(self, stream, res):
        try:
            raw, _ = stream.read_line_finish(res)
        except GLib.Error:
            return
        if raw is None:
            return
        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        self.emit("line", text)
        self._read()

    def stop(self):
        """MUST be called when the window closes."""
        self._cancel.cancel()
        if self._proc is not None:
            self._proc.force_exit()
            self._proc = None


def describe(active_state, sub_state=""):
    """Wording for the status line."""
    words = {
        "active": _("running"),
        "inactive": _("stopped"),
        "failed": _("failed"),
        "activating": _("starting …"),
        "deactivating": _("stopping …"),
        "unknown": _("unknown"),
    }
    text = words.get(active_state, active_state)
    if active_state == "failed" and sub_state:
        text = f"{text} ({sub_state})"
    return text


def read_journal(unit_name, lines=100):
    """Fetch the last lines once, without -f."""
    try:
        ok, out, err = Gio.Subprocess.new(
            ["journalctl", "--user", "-u", unit_name, "-n", str(lines),
             "--no-pager", "-o", "short-precise"],
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE
        ).communicate_utf8(None, None)
        return out or ""
    except GLib.Error as e:
        return _("Journal could not be read: {error}").format(error=e.message)
