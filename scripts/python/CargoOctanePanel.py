"""Modeless control panel for the Octane-native Cargo listener."""

from __future__ import print_function

import os

import hou

try:
    from PySide6 import QtCore, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtWidgets

import CargoOctaneListener


_panel = None


class CargoOctanePanel(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(CargoOctanePanel, self).__init__(parent or hou.qt.mainWindow())
        self.setWindowTitle("Cargo Octane Listener")
        self.setObjectName("CargoOctaneListenerPanel")
        self.setMinimumWidth(520)
        self.setWindowFlag(QtCore.Qt.WindowContextHelpButtonHint, False)

        self._listener = CargoOctaneListener.CargoOctaneListener.get_instance()

        self._status = QtWidgets.QLabel()
        self._copy_to_destination = QtWidgets.QCheckBox("Copy to Destination")
        self._copy_to_destination.setChecked(self._listener.copy_to_destination())
        self._destination = QtWidgets.QLineEdit(self._listener.destination())
        self._destination.setPlaceholderText(CargoOctaneListener.DEFAULT_DESTINATION)
        self._resolved_destination = QtWidgets.QLabel()
        self._resolved_destination.setStyleSheet("color: #999;")
        self._texture_resolution = QtWidgets.QComboBox()
        for resolution in CargoOctaneListener.TEXTURE_RESOLUTIONS:
            self._texture_resolution.addItem(resolution.upper(), resolution)
        current_resolution_index = self._texture_resolution.findData(
            self._listener.texture_resolution()
        )
        if current_resolution_index >= 0:
            self._texture_resolution.setCurrentIndex(current_resolution_index)

        import_settings = self._listener.displacement_settings()
        self._displacement_mode = QtWidgets.QComboBox()
        self._displacement_mode.addItem(
            "Texture displacement",
            CargoOctaneListener.CargoImporter.HEIGHT_MODE_TEXTURE_DISPLACEMENT,
        )
        self._displacement_mode.addItem(
            "Vertex displacement",
            CargoOctaneListener.CargoImporter.HEIGHT_MODE_VERTEX_DISPLACEMENT,
        )
        self._displacement_mode.addItem(
            "Bump",
            CargoOctaneListener.CargoImporter.HEIGHT_MODE_BUMP,
        )
        displacement_index = self._displacement_mode.findData(import_settings["mode"])
        if displacement_index >= 0:
            self._displacement_mode.setCurrentIndex(displacement_index)

        self._displacement_height = QtWidgets.QDoubleSpinBox()
        self._displacement_height.setDecimals(6)
        self._displacement_height.setRange(-1000000.0, 1000000.0)
        self._displacement_height.setSingleStep(0.001)
        self._displacement_height.setValue(float(import_settings["height"]))

        self._displacement_disconnected = QtWidgets.QCheckBox("Create disconnected")
        self._displacement_disconnected.setChecked(
            bool(import_settings["create_disconnected"])
        )

        self._projection_mode = QtWidgets.QComboBox()
        projection_choices = (
            ("UV", CargoOctaneListener.CargoImporter.PROJECTION_MODE_UV),
            ("Triplanar", CargoOctaneListener.CargoImporter.PROJECTION_MODE_TRIPLANAR),
            ("Box", CargoOctaneListener.CargoImporter.PROJECTION_MODE_BOX),
            ("XYZ to UVW", CargoOctaneListener.CargoImporter.PROJECTION_MODE_LINEAR),
            (
                "Cylindrical",
                CargoOctaneListener.CargoImporter.PROJECTION_MODE_CYLINDRICAL,
            ),
            ("Spherical", CargoOctaneListener.CargoImporter.PROJECTION_MODE_SPHERICAL),
            (
                "Perspective",
                CargoOctaneListener.CargoImporter.PROJECTION_MODE_PERSPECTIVE,
            ),
        )
        for label, mode in projection_choices:
            self._projection_mode.addItem(label, mode)
        projection_index = self._projection_mode.findData(
            self._listener.projection_mode()
        )
        if projection_index >= 0:
            self._projection_mode.setCurrentIndex(projection_index)

        self._browse_button = QtWidgets.QPushButton("Browse...")
        self._browse_button.clicked.connect(self._browse_destination)

        self._start_button = QtWidgets.QPushButton("Start Listener")
        self._start_button.clicked.connect(self._start_listener)
        self._stop_button = QtWidgets.QPushButton("Stop Listener")
        self._stop_button.clicked.connect(self._stop_listener)

        destination_row = QtWidgets.QHBoxLayout()
        destination_row.addWidget(self._destination, 1)
        destination_row.addWidget(self._browse_button)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self._start_button)
        buttons.addWidget(self._stop_button)

        form = QtWidgets.QFormLayout()
        form.addRow("", self._copy_to_destination)
        form.addRow("Destination:", destination_row)
        form.addRow("Resolves to:", self._resolved_destination)

        import_settings_group = QtWidgets.QGroupBox("Import Settings")
        import_settings_form = QtWidgets.QFormLayout(import_settings_group)
        import_settings_form.addRow("Texture resolution:", self._texture_resolution)
        import_settings_form.addRow("Height map mode:", self._displacement_mode)
        import_settings_form.addRow("Displacement height:", self._displacement_height)
        import_settings_form.addRow("", self._displacement_disconnected)
        import_settings_form.addRow("Projection (materials only):", self._projection_mode)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self._status)
        layout.addSpacing(8)
        layout.addLayout(form)
        layout.addSpacing(16)
        layout.addWidget(import_settings_group)
        layout.addSpacing(8)
        layout.addLayout(buttons)

        self._destination.textChanged.connect(self._refresh_destination)
        self._destination.editingFinished.connect(self._save_destination)
        self._copy_to_destination.toggled.connect(self._save_copy_to_destination)
        self._texture_resolution.currentIndexChanged.connect(
            self._save_texture_resolution
        )
        self._displacement_mode.currentIndexChanged.connect(self._save_import_settings)
        self._displacement_height.valueChanged.connect(self._save_import_settings)
        self._displacement_disconnected.toggled.connect(self._save_import_settings)
        self._projection_mode.currentIndexChanged.connect(self._save_import_settings)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._refresh_status)
        self._timer.start()

        self._refresh_destination()
        self._update_destination_enabled()
        self._update_displacement_height_enabled()
        self._refresh_status()

    def _refresh_status(self):
        running = self._listener.is_running()
        self._status.setText(
            "<b><font color='#57c96b'>Listener Running</font></b>"
            if running
            else "<b><font color='#e36b6b'>Listener Not Running</font></b>"
        )
        self._start_button.setEnabled(not running)
        self._stop_button.setEnabled(running)

    def _refresh_destination(self):
        try:
            resolved = CargoOctaneListener.resolve_destination(self._destination.text())
        except Exception:
            resolved = ""
        self._resolved_destination.setText(resolved or "(unresolved)")

    def _save_destination(self):
        raw_destination = self._destination.text().strip()
        if not raw_destination:
            raw_destination = CargoOctaneListener.DEFAULT_DESTINATION
            self._destination.setText(raw_destination)
        self._listener.set_destination(raw_destination)
        self._refresh_destination()

    def _save_copy_to_destination(self, enabled):
        self._listener.set_copy_to_destination(enabled)
        self._update_destination_enabled()

    def _update_destination_enabled(self):
        enabled = self._copy_to_destination.isChecked()
        self._destination.setEnabled(enabled)
        self._browse_button.setEnabled(enabled)
        self._resolved_destination.setEnabled(enabled)

    def _browse_destination(self):
        selected = hou.ui.selectFile(
            title="Choose Cargo destination",
            file_type=hou.fileType.Directory,
            chooser_mode=hou.fileChooserMode.Read,
            start_directory=self._destination.text(),
        )
        if selected:
            self._destination.setText(selected)
            self._save_destination()

    def _save_texture_resolution(self, _index=None):
        resolution = self._texture_resolution.currentData()
        self._listener.set_texture_resolution(resolution)

    def _save_import_settings(self, _value=None):
        self._listener.set_import_settings(
            self._displacement_mode.currentData(),
            self._displacement_height.value(),
            self._displacement_disconnected.isChecked(),
            self._projection_mode.currentData(),
        )
        self._update_displacement_height_enabled()

    def _update_displacement_height_enabled(self):
        is_bump = (
            self._displacement_mode.currentData()
            == CargoOctaneListener.CargoImporter.HEIGHT_MODE_BUMP
        )
        self._displacement_height.setEnabled(not is_bump)

    def _start_listener(self):
        self._save_destination()
        self._save_copy_to_destination(self._copy_to_destination.isChecked())
        self._save_texture_resolution()
        self._save_import_settings()
        try:
            self._listener.start_listener(
                self._destination.text(),
                self._texture_resolution.currentData(),
                self._copy_to_destination.isChecked(),
            )
        except Exception as error:
            hou.ui.displayMessage(
                "Could not start the Cargo listener:\n\n{0}".format(error),
                severity=hou.severityType.Error,
                title="Cargo Octane Listener",
            )
        self._refresh_status()

    def _stop_listener(self):
        self._listener.stop_listener()
        self._refresh_status()


def show():
    global _panel
    if _panel is None:
        _panel = CargoOctanePanel()
    _panel.show()
    _panel.raise_()
    _panel.activateWindow()
    return _panel
