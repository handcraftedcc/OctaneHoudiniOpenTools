"""
Octane displacement manager for Houdini.

Import this module from a Houdini shelf tool and call main(kwargs), or source
the script and call run(). It finds Octane texture and vertex displacement nodes,
shows them in a table, and lets you disconnect or reconnect them from their
parent Octane Standard Surface material.
"""

from __future__ import print_function

try:
    import hou
except ImportError:
    hou = None

try:
    from PySide6 import QtCore, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtWidgets


TEXTURE_DISPLACEMENT_TYPE = "octane::NT_DISPLACEMENT"
VERTEX_DISPLACEMENT_TYPE = "octane::NT_VERTEX_DISPLACEMENT"
TARGET_NODE_DATA_KEY = "nodeinfo_displacement_target_node"
TARGET_INPUT_DATA_KEY = "nodeinfo_displacement_target_input"
TARGET_OUTPUT_DATA_KEY = "nodeinfo_displacement_source_output"
RESOLUTION_VALUES = list(range(8, 17))


def is_octane_displacement_node(node):
    if node is None:
        return False
    type_name = node.type().name()
    return type_name in (TEXTURE_DISPLACEMENT_TYPE, VERTEX_DISPLACEMENT_TYPE)


def displacement_type_label(node):
    type_name = node.type().name()
    if type_name == VERTEX_DISPLACEMENT_TYPE:
        return "Vertex displacement"
    if type_name == TEXTURE_DISPLACEMENT_TYPE:
        return "Texture displacement"
    return type_name


def safe_parm_value(node, parm_name, default=""):
    parm = node.parm(parm_name)
    if parm is None:
        return default
    try:
        return parm.eval()
    except Exception:
        return default


def set_parm_if_exists(node, parm_name, value):
    parm = node.parm(parm_name)
    if parm is None:
        return False
    parm.set(value)
    return True


def resolution_label(value):
    try:
        encoded = int(value)
    except Exception:
        return ""
    pixels = 2 ** encoded
    return "{0} ({1}x{1})".format(encoded, pixels)


def resolution_value_from_label(label):
    try:
        return int(str(label).split()[0])
    except Exception:
        return None


def exec_menu(menu, global_position):
    exec_method = getattr(menu, "exec", None) or getattr(menu, "exec_", None)
    return exec_method(global_position)


def input_index_by_name(node, input_name):
    try:
        for index, name in enumerate(node.inputNames()):
            if name == input_name:
                return index
    except Exception:
        pass
    return None


def find_parent_material_node(displacement_node):
    node = displacement_node.parent()
    while node is not None:
        for child in node.children():
            type_name = child.type().name().lower()
            child_name = child.name().lower()
            if "standard" in type_name and "surface" in type_name:
                return child
            if "standard" in child_name and "surface" in child_name:
                return child
        node = node.parent()
    return None


def material_display_name(displacement_node):
    parent = displacement_node.parent()
    if parent is not None:
        return parent.name()
    return displacement_node.name()


def connected_target(displacement_node):
    for connection in displacement_node.outputConnections():
        target = connection.outputNode()
        if target is None:
            continue
        try:
            input_name = target.inputNames()[connection.inputIndex()]
        except Exception:
            input_name = ""
        if input_name == "displacement":
            return target, connection.inputIndex(), connection.outputIndex()
    target, input_index, output_index = fallback_target(displacement_node)
    if target is not None and input_index is not None and target.input(input_index) == displacement_node:
        return target, input_index, output_index
    return None, None, None


def store_target(displacement_node, target_node, input_index, output_index):
    if target_node is None or input_index is None:
        return
    displacement_node.setUserData(TARGET_NODE_DATA_KEY, target_node.path())
    displacement_node.setUserData(TARGET_INPUT_DATA_KEY, str(input_index))
    displacement_node.setUserData(TARGET_OUTPUT_DATA_KEY, str(output_index or 0))


def stored_target(displacement_node):
    target_path = displacement_node.userData(TARGET_NODE_DATA_KEY)
    input_index = displacement_node.userData(TARGET_INPUT_DATA_KEY)
    output_index = displacement_node.userData(TARGET_OUTPUT_DATA_KEY)
    if not target_path or input_index is None:
        return None, None, None
    target = hou.node(target_path)
    if target is None:
        return None, None, None
    try:
        input_index = int(input_index)
    except Exception:
        input_index = None
    try:
        output_index = int(output_index or 0)
    except Exception:
        output_index = 0
    return target, input_index, output_index


def fallback_target(displacement_node):
    material_node = find_parent_material_node(displacement_node)
    if material_node is None:
        return None, None, None
    displacement_input_index = input_index_by_name(material_node, "displacement")
    if displacement_input_index is None:
        return None, None, None
    return material_node, displacement_input_index, 0


def is_displacement_enabled(displacement_node):
    target, _input_index, _output_index = connected_target(displacement_node)
    return target is not None


def disable_displacement(displacement_node):
    target, input_index, output_index = connected_target(displacement_node)
    if target is None:
        return False
    store_target(displacement_node, target, input_index, output_index)
    target.setInput(input_index, None)
    return True


def enable_displacement(displacement_node):
    target, input_index, output_index = stored_target(displacement_node)
    if target is None or input_index is None:
        target, input_index, output_index = fallback_target(displacement_node)
    if target is None or input_index is None:
        return False

    current_input = target.input(input_index)
    if current_input is not None and current_input != displacement_node:
        return False

    target.setInput(input_index, displacement_node, output_index or 0)
    store_target(displacement_node, target, input_index, output_index)
    return True


def displacement_resolution(displacement_node):
    if displacement_node.type().name() != TEXTURE_DISPLACEMENT_TYPE:
        return ""
    value = safe_parm_value(displacement_node, "levelOfDetail", "")
    return resolution_label(value) if value != "" else ""


def displacement_quality(displacement_node):
    if displacement_node.type().name() != TEXTURE_DISPLACEMENT_TYPE:
        return "N/A"

    parm = displacement_node.parm("quality")
    if parm is None:
        return ""
    try:
        value = parm.eval()
        labels = parm.menuLabels()
        if labels and 0 <= int(value) < len(labels):
            return labels[int(value)]
        return str(value)
    except Exception:
        return ""


def displacement_height(displacement_node):
    return safe_parm_value(displacement_node, "amount", "")


def texture_source(displacement_node):
    index = input_index_by_name(displacement_node, "texture")
    if index is None:
        return None
    return displacement_node.input(index)


def copy_displacement_settings(source_node, target_node):
    for parm_name in ("amount", "black_level"):
        value = safe_parm_value(source_node, parm_name, None)
        if value is not None:
            set_parm_if_exists(target_node, parm_name, value)
    texture = texture_source(source_node)
    texture_index = input_index_by_name(target_node, "texture")
    if texture is not None and texture_index is not None:
        target_node.setInput(texture_index, texture)


def convert_displacement_node(displacement_node, target_type):
    if displacement_node.type().name() == target_type:
        return displacement_node

    target, input_index, output_index = connected_target(displacement_node)
    was_enabled = target is not None
    if target is None:
        target, input_index, output_index = stored_target(displacement_node)
    if target is None:
        target, input_index, output_index = fallback_target(displacement_node)

    parent = displacement_node.parent()
    old_position = displacement_node.position()
    old_name = displacement_node.name()
    new_node = parent.createNode(target_type, old_name + "_converted")
    new_node.setPosition(old_position)
    copy_displacement_settings(displacement_node, new_node)
    store_target(new_node, target, input_index, output_index)

    if was_enabled and target is not None and input_index is not None:
        target.setInput(input_index, new_node, output_index or 0)

    try:
        displacement_node.destroy()
    except Exception:
        pass

    try:
        new_node.setName(old_name, unique_name=True)
    except Exception:
        pass
    return new_node


def collect_displacement_nodes_from(root_node):
    found = []
    if root_node is None:
        return found
    if is_octane_displacement_node(root_node):
        found.append(root_node)

    try:
        children = root_node.allSubChildren(recurse_in_locked_nodes=True)
    except TypeError:
        children = root_node.allSubChildren()
    except Exception:
        children = []

    for child in children:
        if is_octane_displacement_node(child):
            found.append(child)
    return found


def collect_displacement_nodes(root_nodes=None):
    if hou is None:
        raise RuntimeError("This script must run inside Houdini.")

    if root_nodes is None:
        selected = list(hou.selectedNodes())
        roots = selected if selected else [hou.node("/mat"), hou.node("/obj")]
    elif isinstance(root_nodes, (list, tuple)):
        roots = list(root_nodes)
    else:
        roots = [root_nodes]

    found = []
    seen = set()
    for root in roots:
        for node in collect_displacement_nodes_from(root):
            if node.path() not in seen:
                found.append(node)
                seen.add(node.path())
    found.sort(key=lambda item: item.path().lower())
    return found


class DisplacementManagerDialog(QtWidgets.QDialog):
    def __init__(self, displacement_nodes, parent=None):
        super(DisplacementManagerDialog, self).__init__(parent)
        self.setWindowTitle("Octane Displacement Manager")
        self.resize(1000, 520)
        self.displacement_nodes = list(displacement_nodes)

        self.table = QtWidgets.QTableWidget(self)
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["Enabled", "Material", "Type", "Height", "Quality", "Resolution", "Node"]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)

        self.refresh_button = QtWidgets.QPushButton("Refresh", self)
        self.close_button = QtWidgets.QPushButton("Close", self)

        button_layout = QtWidgets.QHBoxLayout()
        button_layout.addStretch(1)
        button_layout.addWidget(self.refresh_button)
        button_layout.addWidget(self.close_button)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addLayout(button_layout)

        self.refresh_button.clicked.connect(self.refresh)
        self.close_button.clicked.connect(self.accept)
        self.table.customContextMenuRequested.connect(self.show_context_menu)
        self.populate()

    def disconnect_item_changed(self):
        try:
            self.table.itemChanged.disconnect(self.on_item_changed)
        except Exception:
            pass

    def populate(self):
        self.disconnect_item_changed()
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.displacement_nodes))
        for row, node in enumerate(self.displacement_nodes):
            enabled_item = QtWidgets.QTableWidgetItem("")
            enabled_item.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            enabled_item.setCheckState(QtCore.Qt.Checked if is_displacement_enabled(node) else QtCore.Qt.Unchecked)
            self.table.setItem(row, 0, enabled_item)

            values = [
                material_display_name(node),
                displacement_type_label(node),
                str(displacement_height(node)),
                str(displacement_quality(node)),
                str(displacement_resolution(node)),
                node.path(),
            ]
            for column, value in enumerate(values, start=1):
                item = QtWidgets.QTableWidgetItem(value)
                item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
                self.table.setItem(row, column, item)

        self.table.resizeColumnsToContents()
        self.table.blockSignals(False)
        self.table.itemChanged.connect(self.on_item_changed)

    def refresh(self):
        self.displacement_nodes = collect_displacement_nodes()
        self.populate()

    def selected_rows(self):
        rows = sorted(set(index.row() for index in self.table.selectionModel().selectedRows()))
        if not rows and self.table.currentRow() >= 0:
            rows = [self.table.currentRow()]
        return rows

    def selected_nodes(self):
        return [self.displacement_nodes[row] for row in self.selected_rows()]

    def show_context_menu(self, position):
        if not self.selected_rows():
            return
        menu = QtWidgets.QMenu(self)
        enable_action = menu.addAction("Enable")
        disable_action = menu.addAction("Disable")
        menu.addSeparator()
        height_action = menu.addAction("Change height...")
        resolution_action = menu.addAction("Change resolution...")
        menu.addSeparator()
        vertex_action = menu.addAction("Change to vertex")
        texture_action = menu.addAction("Change to texture")
        action = exec_menu(menu, self.table.viewport().mapToGlobal(position))
        if action == enable_action:
            self.apply_enable_selected(True)
        elif action == disable_action:
            self.apply_enable_selected(False)
        elif action == height_action:
            self.change_height_selected()
        elif action == resolution_action:
            self.change_resolution_selected()
        elif action == vertex_action:
            self.convert_selected(VERTEX_DISPLACEMENT_TYPE)
        elif action == texture_action:
            self.convert_selected(TEXTURE_DISPLACEMENT_TYPE)

    def apply_enable_selected(self, enabled):
        failures = []
        for node in self.selected_nodes():
            changed = enable_displacement(node) if enabled else disable_displacement(node)
            if not changed:
                failures.append(node.path())
        self.populate()
        if failures:
            hou.ui.displayMessage("Could not update:\n{0}".format("\n".join(failures)))

    def change_height_selected(self):
        nodes = self.selected_nodes()
        if not nodes:
            return
        current = str(displacement_height(nodes[0]))
        value, accepted = QtWidgets.QInputDialog.getDouble(self, "Change Height", "Height", float(current or 0.0), -1000000.0, 1000000.0, 6)
        if not accepted:
            return
        for node in nodes:
            set_parm_if_exists(node, "amount", value)
        self.populate()

    def change_resolution_selected(self):
        labels = [resolution_label(value) for value in RESOLUTION_VALUES]
        label, accepted = QtWidgets.QInputDialog.getItem(self, "Change Resolution", "Resolution", labels, 2, False)
        if not accepted:
            return
        value = resolution_value_from_label(label)
        if value is None:
            return
        for node in self.selected_nodes():
            if node.type().name() == TEXTURE_DISPLACEMENT_TYPE:
                set_parm_if_exists(node, "levelOfDetail", value)
        self.populate()

    def convert_selected(self, target_type):
        new_nodes = []
        for node in self.selected_nodes():
            new_nodes.append(convert_displacement_node(node, target_type))
        self.displacement_nodes = collect_displacement_nodes()
        self.populate()

    def on_item_changed(self, item):
        if item.column() != 0:
            return
        node = self.displacement_nodes[item.row()]
        if item.checkState() == QtCore.Qt.Checked:
            changed = enable_displacement(node)
        else:
            changed = disable_displacement(node)
        if not changed:
            self.table.blockSignals(True)
            item.setCheckState(QtCore.Qt.Checked if is_displacement_enabled(node) else QtCore.Qt.Unchecked)
            self.table.blockSignals(False)
            hou.ui.displayMessage("Could not change displacement connection for:\n{0}".format(node.path()))


def run(root_nodes=None):
    if hou is None:
        raise RuntimeError("This script must run inside Houdini.")
    nodes = collect_displacement_nodes(root_nodes=root_nodes)
    if not nodes:
        hou.ui.displayMessage("No Octane displacement nodes found.")
        return None
    dialog = DisplacementManagerDialog(nodes, parent=hou.qt.mainWindow())
    dialog.show()
    return dialog


def main(kwargs=None):
    kwargs = kwargs or {}
    return run(root_nodes=kwargs.get("nodes") or kwargs.get("node"))


def should_auto_run():
    if hou is None:
        return __name__ == "__main__"
    if __name__ in ("__main__", "__builtin__", "builtins"):
        return True
    return False


if should_auto_run():
    run()
