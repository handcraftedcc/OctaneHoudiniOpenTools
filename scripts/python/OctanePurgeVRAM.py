"""
Octane VRAM purge for Houdini.

Shows the "Purge VRAM" dialog and frees GPU/unified memory held by the
OctaneRender for Houdini plugin. Memory usage is reported before and after
the operation.

Modes:
    reload : full IPR scene reload (stop -> full clean -> reload)
    close  : close the Octane Qt5 IPR pane, releasing the IPR engine
    caches : clear Houdini's own texture, viewport, and geometry caches
    report : only report current memory usage

The full reload uses the plugin's internal HOctane_IPR_reLoad(bool) entry
point, which is the same code path as the IPR "Full scene reload" button.
If that symbol is unavailable, the public Octane_IPR_reload command is used
instead, which performs a partial reload.

Run from the shelf tool or the Houdini Python Shell (main thread):

    import OctanePurgeVRAM
    OctanePurgeVRAM.main({})
    OctanePurgeVRAM.purge("reload")
"""

from __future__ import print_function

import ctypes
import glob
import os
import re
import subprocess
import time

try:
    import hou
except ImportError:
    hou = None

try:
    from PySide6 import QtCore, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtWidgets


TOOL_NAME = "OctanePurgeVRAM"
IPR_PANE_NAME = "OctaneQt5IPRPane"
FULL_RELOAD_SYMBOL = "_Z18HOctane_IPR_reLoadb"  # HOctane_IPR_reLoad(bool)

MODE_RELOAD = "reload"
MODE_CLOSE = "close"
MODE_CACHES = "caches"
MODE_REPORT = "report"

MODES = (
    (MODE_RELOAD, "Full IPR reload",
     "Stop rendering, release the IPR scene, and reload it. The IPR stays open."),
    (MODE_CLOSE, "Close IPR",
     "Close the Octane Qt5 IPR pane and release the IPR render engine."),
    (MODE_CACHES, "Houdini caches only",
     "Clear Houdini's texture, viewport, geometry, SOP/OBJ, and COP caches."),
    (MODE_REPORT, "Report only",
     "Print current GPU and Houdini memory usage without changing anything."),
)

HOUDINI_CACHE_COMMANDS = (
    "texcache -c",
    "glcache -c",
    "geocache -c",
    "sopcache -c",
    "objcache -c",
    "copcache -c",
)

MEMORY_SETTLE_SECONDS = 1.0


def _hscript(command):
    """Run an HScript command and return (stdout, stderr) as strings."""
    if hou is None:
        return "", "Houdini is unavailable."
    try:
        result = hou.hscript(command)
    except Exception as exc:
        return "", str(exc)
    if isinstance(result, tuple):
        stdout = result[0] or ""
        stderr = result[1] if len(result) > 1 else ""
        return stdout, stderr or ""
    return result or "", ""


def _runCommand(arguments):
    """Run an external command and return its stdout, or an empty string."""
    try:
        completed = subprocess.run(
            arguments,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
            timeout=10,
        )
    except Exception:
        return ""
    return completed.stdout or ""


def _statValue(statistics, key):
    match = re.search(r'"%s"\s*=\s*(\d+)' % re.escape(key), statistics)
    return int(match.group(1)) if match else None


def gpuMemory():
    """Return GPU memory statistics for Apple Silicon, or None if unavailable.

    The returned dictionary contains:
        inUse       : bytes currently in use by the GPU
        allocated   : bytes allocated by the GPU driver
        utilization : GPU utilization percentage
    """
    output = _runCommand(["ioreg", "-r", "-c", "AGXAccelerator", "-d", "1"])
    if not output:
        output = _runCommand(["ioreg", "-r", "-c", "IOAccelerator", "-d", "1"])
    match = re.search(r'"PerformanceStatistics"\s*=\s*\{([^}]*)\}', output)
    if not match:
        return None
    statistics = match.group(1)
    return {
        "inUse": _statValue(statistics, "In use system memory"),
        "allocated": _statValue(statistics, "Alloc system memory"),
        "utilization": _statValue(statistics, "Device Utilization %"),
    }


def processMemory():
    """Return the resident set size of this Houdini process in bytes."""
    output = _runCommand(["ps", "-o", "rss=", "-p", str(os.getpid())]).strip()
    try:
        return int(output) * 1024
    except (TypeError, ValueError):
        return None


def memorySnapshot():
    return {"gpu": gpuMemory(), "rss": processMemory()}


def formatBytes(value):
    if value is None:
        return "n/a"
    return "%.2f GiB" % (value / (1024.0 ** 3))


def formatChange(before, after):
    if before is None or after is None:
        return "n/a"
    change = after - before
    sign = "+" if change > 0 else "-" if change < 0 else ""
    return "%s%.2f GiB" % (sign, abs(change) / (1024.0 ** 3))


def memorySummary(snapshot):
    gpu = snapshot.get("gpu")
    if gpu:
        parts = [
            "GPU in use %s" % formatBytes(gpu.get("inUse")),
            "allocated %s" % formatBytes(gpu.get("allocated")),
        ]
        utilization = gpu.get("utilization")
        if utilization is not None:
            parts.append("utilization %d%%" % utilization)
        gpu_text = ", ".join(parts)
    else:
        gpu_text = "GPU memory unavailable"
    return "%s | Houdini RSS %s" % (gpu_text, formatBytes(snapshot.get("rss")))


def qtIPROpen():
    """Return True when the Octane Qt5 IPR window is open."""
    stdout, _ = _hscript("Octane_getIPRQtPointer")
    value = (stdout or "0").strip()
    if not value:
        return False
    try:
        return int(value, 16) != 0
    except ValueError:
        return False


def _pluginDylibPath():
    roots = []
    octane_root = os.environ.get("OCTVERSION")
    if octane_root:
        roots.append(octane_root)
    if hou is not None:
        try:
            roots.extend(hou.houdiniPath())
        except Exception:
            pass
    for root in roots:
        matches = sorted(glob.glob(os.path.join(root, "dso", "Houdini_Octane_*.dylib")))
        if matches:
            return matches[0]
    return None


def _fullReloadFunction():
    """Resolve HOctane_IPR_reLoad(bool) from the loaded Octane plugin."""
    libraries = []
    try:
        libraries.append(ctypes.CDLL(None))
    except OSError:
        pass
    plugin_path = _pluginDylibPath()
    if plugin_path:
        try:
            libraries.append(ctypes.CDLL(plugin_path))
        except OSError:
            pass

    for library in libraries:
        try:
            function = getattr(library, FULL_RELOAD_SYMBOL)
        except AttributeError:
            continue
        function.argtypes = [ctypes.c_bool]
        function.restype = None
        return function
    return None


def reloadIPR():
    """Reload the open IPR, preferring the plugin's full-clean path."""
    if not qtIPROpen():
        _hscript("Octane_IPR_reload")
        return "No Octane Qt5 IPR pane detected; ran the standard Octane_IPR_reload."

    reload_function = _fullReloadFunction()
    if reload_function is None:
        _hscript("Octane_IPR_reload")
        return "Full reload entry point unavailable; ran the standard Octane_IPR_reload."

    try:
        reload_function(True)
    except Exception as exc:
        _hscript("Octane_IPR_reload")
        return "Full reload failed (%s); ran the standard Octane_IPR_reload." % exc
    return "Full IPR reload complete."


def closeIPR():
    """Close the Octane Qt5 IPR pane, falling back to the HScript commands."""
    closed_tabs = []
    if hou is not None:
        try:
            pane_tabs = hou.ui.paneTabs()
        except Exception:
            pane_tabs = []
        for tab in pane_tabs:
            try:
                if tab.type() != hou.paneTabType.PythonPanel:
                    continue
                if tab.activeInterface() != IPR_PANE_NAME:
                    continue
                tab.close()
                closed_tabs.append(tab.name())
            except Exception:
                continue

    if not closed_tabs:
        _hscript("Octane_IPR_pause")
        _hscript("Octane_closeIPRQtWindow")
        return "Sent Octane_IPR_pause and Octane_closeIPRQtWindow (no matching pane tab found)."
    return "Closed IPR pane: %s." % ", ".join(closed_tabs)


def clearHoudiniCaches():
    """Clear Houdini's internal caches and return the commands that succeeded."""
    cleared = []
    for command in HOUDINI_CACHE_COMMANDS:
        _, stderr = _hscript(command)
        if not stderr.strip():
            cleared.append(command.split()[0])
    return cleared


def purge(mode=MODE_RELOAD, clear_caches=False):
    """Run a purge mode, print a before/after report, and return it."""
    mode = (mode or MODE_RELOAD).lower()
    valid_modes = [entry[0] for entry in MODES]
    if mode not in valid_modes:
        raise ValueError(
            "Unknown purge mode %r; expected one of %s."
            % (mode, ", ".join(valid_modes))
        )

    before = memorySnapshot()
    print("Purge VRAM before: %s" % memorySummary(before))

    notes = []
    if mode == MODE_RELOAD:
        notes.append(reloadIPR())
    elif mode == MODE_CLOSE:
        notes.append(closeIPR())
    elif mode == MODE_CACHES:
        clear_caches = True

    if clear_caches:
        cleared = clearHoudiniCaches()
        notes.append(
            "Cleared Houdini caches: %s."
            % (", ".join(cleared) if cleared else "none")
        )

    if mode != MODE_REPORT:
        time.sleep(MEMORY_SETTLE_SECONDS)

    after = memorySnapshot()
    print("Purge VRAM after:  %s" % memorySummary(after))

    report = buildReport(mode, before, after, notes)
    print(report)
    return report


def buildReport(mode, before, after, notes):
    labels = dict((entry[0], entry[1]) for entry in MODES)
    lines = ["Purge VRAM: %s" % labels.get(mode, mode), ""]
    lines.append("Before: %s" % memorySummary(before))
    lines.append("After:  %s" % memorySummary(after))

    before_gpu = before.get("gpu") or {}
    after_gpu = after.get("gpu") or {}
    if before_gpu.get("inUse") is not None and after_gpu.get("inUse") is not None:
        lines.append(
            "GPU in-use change: %s"
            % formatChange(before_gpu.get("inUse"), after_gpu.get("inUse"))
        )
    if before.get("rss") is not None and after.get("rss") is not None:
        lines.append(
            "Houdini RSS change: %s"
            % formatChange(before.get("rss"), after.get("rss"))
        )

    if notes:
        lines.append("")
        lines.extend(notes)
    return "\n".join(lines)


def _loadSettings():
    try:
        import openToolsUtils
        return openToolsUtils.getToolSettings(TOOL_NAME) or {}
    except Exception:
        return {}


def _saveSettings(settings):
    try:
        import openToolsUtils
        openToolsUtils.setToolSettings(TOOL_NAME, settings)
    except Exception:
        pass


def _setBusy(enabled):
    try:
        if enabled:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        else:
            QtWidgets.QApplication.restoreOverrideCursor()
    except Exception:
        pass


def _execDialog(dialog):
    exec_method = getattr(dialog, "exec", None) or getattr(dialog, "exec_", None)
    return exec_method()


class PurgeVRAMDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(PurgeVRAMDialog, self).__init__(parent)
        self.setWindowTitle("Purge Octane VRAM")
        self.setMinimumWidth(440)
        self._settings = _loadSettings()

        layout = QtWidgets.QVBoxLayout(self)

        summary = QtWidgets.QLabel(memorySummary(memorySnapshot()), self)
        summary.setWordWrap(True)
        layout.addWidget(summary)

        mode_group = QtWidgets.QGroupBox("Action", self)
        mode_layout = QtWidgets.QVBoxLayout(mode_group)
        self._mode_buttons = []
        selected_mode = self._settings.get("mode", MODE_RELOAD)
        for mode, label, description in MODES:
            button = QtWidgets.QRadioButton(label, mode_group)
            button.setToolTip(description)
            button.setChecked(mode == selected_mode)
            mode_layout.addWidget(button)
            self._mode_buttons.append((mode, button))
        if not any(button.isChecked() for _, button in self._mode_buttons):
            self._mode_buttons[0][1].setChecked(True)
        layout.addWidget(mode_group)

        self._clear_caches = QtWidgets.QCheckBox("Also clear Houdini caches", self)
        self._clear_caches.setChecked(bool(self._settings.get("clearCaches", True)))
        self._clear_caches.setToolTip(
            "Clear Houdini's texture, viewport, geometry, SOP/OBJ, and COP caches "
            "after the action."
        )
        layout.addWidget(self._clear_caches)

        button_box = QtWidgets.QDialogButtonBox(self)
        button_box.addButton("Purge", QtWidgets.QDialogButtonBox.AcceptRole)
        button_box.addButton(QtWidgets.QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        for _, button in self._mode_buttons:
            button.toggled.connect(self._updateEnabledState)
        self._updateEnabledState()

    def selectedMode(self):
        for mode, button in self._mode_buttons:
            if button.isChecked():
                return mode
        return MODE_RELOAD

    def clearCaches(self):
        return self._clear_caches.isEnabled() and self._clear_caches.isChecked()

    def saveSettings(self):
        _saveSettings(
            {
                "mode": self.selectedMode(),
                "clearCaches": self._clear_caches.isChecked(),
            }
        )

    def _updateEnabledState(self):
        self._clear_caches.setEnabled(self.selectedMode() in (MODE_RELOAD, MODE_CLOSE))


def main(kwargs=None):
    """Shelf-tool entry point: show the dialog, run the purge, show the report."""
    if hou is None:
        raise RuntimeError("Purge VRAM must run inside Houdini.")

    stdout, stderr = _hscript("Octane_version")
    if stderr.strip() or not stdout.strip():
        hou.ui.displayMessage(
            "OctaneRender for Houdini does not appear to be loaded.",
            severity=hou.severityType.Error,
            title="Purge Octane VRAM",
        )
        return None

    dialog = PurgeVRAMDialog(parent=hou.qt.mainWindow())
    if _execDialog(dialog) != QtWidgets.QDialog.Accepted:
        return None

    mode = dialog.selectedMode()
    clear_caches = dialog.clearCaches()
    dialog.saveSettings()

    _setBusy(True)
    try:
        report = purge(mode, clear_caches=clear_caches)
    finally:
        _setBusy(False)

    hou.ui.displayMessage(
        report,
        buttons=("Close",),
        severity=hou.severityType.Message,
        title="Purge Octane VRAM",
    )
    return report
