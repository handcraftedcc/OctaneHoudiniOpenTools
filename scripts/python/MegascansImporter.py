"""
Megascans library browser and Octane importer for Houdini.

The importer scans Bridge-style library folders, presents preview-backed
Material and Asset tabs, resolves requested texture/LOD quality from files that
actually exist, and creates context-sensitive SOP/OBJ/material networks.
"""

from __future__ import print_function

import json
import os
import re

try:
    import hou
except ImportError:
    hou = None

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:
    try:
        from PySide2 import QtCore, QtGui, QtWidgets
    except ImportError:
        QtCore = QtGui = QtWidgets = None

import openToolsMaterialUtils
import openToolsUtils


TOOL_SETTINGS_NAME = "MegascansImporter"
MATERIAL_TYPES = set(["surface", "atlas", "brush"])
ASSET_TYPES = set(["3d", "3dplant"])
TEXTURE_RESOLUTIONS = ["1k", "2k", "4k", "8k", "16k"]
LOD_LEVELS = ["high", "lod0", "lod1", "lod2", "lod3", "lod4", "lod5", "lod6"]
IMAGE_EXTENSIONS = set([".bmp", ".exr", ".jpeg", ".jpg", ".png", ".tga", ".tif", ".tiff"])
IGNORED_SCAN_DIRECTORIES = set(["support", "temp"])
IGNORED_TEXTURE_DIRECTORIES = set(["previews"])
QUALITY_FALLBACK_CLOSEST = "closest"
QUALITY_FALLBACK_SKIP = "skip"
VARIANT_LOAD_SAME_OBJECT = "same_object"
VARIANT_LOAD_SEPARATE_OBJECTS = "separate_objects"
OBJECT_ROW_SPACING = 0.5
OBJECT_VARIANT_COLUMN_SPACING = 2.0

CHANNEL_ALIASES = {
    "albedo": "base_color",
    "basecolor": "base_color",
    "base_color": "base_color",
    "diffuse": "base_color",
    "ao": "ao",
    "ambientocclusion": "ao",
    "ambient_occlusion": "ao",
    "normal": "normal",
    "normalbump": "normal",
    "roughness": "roughness",
    "rough": "roughness",
    "gloss": "roughness",
    "glossiness": "roughness",
    "specular": "specular",
    "metalness": "metallic",
    "metallic": "metallic",
    "opacity": "opacity",
    "alpha": "opacity",
    "displacement": "displacement",
    "height": "displacement",
    "bump": "bump",
    "translucency": "backlight",
    "subsurface": "subsurface",
    "subsurfacecolor": "subsurface_color",
    "subsurface_color": "subsurface_color",
    "transmission": "transmission",
    "emissive": "emission_color",
    "emission": "emission_color",
}


def _log(message):
    print("[Megascans] {0}".format(message))


def select_library_directory(parent=None, start_directory=None):
    """Choose a library directory, keeping the picker above its importer dialog."""
    if hou is None:
        raise RuntimeError("This script must run inside Houdini.")

    # ``hou.ui.selectFile`` is a top-level Houdini dialog.  When invoked from
    # the modal browser it is not a child of that browser, so Qt leaves it
    # visible but prevents it from receiving clicks.  Use a Qt child dialog
    # whenever the browser is available.
    if QtWidgets is not None:
        initial_directory = start_directory or ""
        selected = QtWidgets.QFileDialog.getExistingDirectory(
            parent or hou.qt.mainWindow(),
            "Select Megascans library directory",
            initial_directory,
        )
    else:
        selected = hou.ui.selectFile(
            title="Select Megascans library directory",
            file_type=hou.fileType.Directory,
            chooser_mode=hou.fileChooserMode.Read,
            start_directory=start_directory or "",
        )
    if not selected:
        return None
    return os.path.normpath(hou.expandString(selected))


def library_root_from_settings():
    root = openToolsUtils.getToolSetting(TOOL_SETTINGS_NAME, "root_directory")
    if not root:
        return None
    if hou is not None:
        root = hou.expandString(root)
    return os.path.normpath(root)


def save_library_root(root_directory):
    openToolsUtils.setToolSetting(
        TOOL_SETTINGS_NAME,
        "root_directory",
        os.path.normpath(root_directory),
    )


def ensure_library_root(root_directory=None):
    root_directory = root_directory or library_root_from_settings()
    if root_directory and os.path.isdir(root_directory):
        return root_directory

    if root_directory:
        _log("Saved library directory is unavailable: {0}".format(root_directory))
    root_directory = select_library_directory()
    if not root_directory:
        return None
    save_library_root(root_directory)
    return root_directory


def _read_json(path):
    try:
        with open(path, "r") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except Exception as error:
        _log("Could not read metadata {0}: {1}".format(path, error))
        return {}


def _asset_type(metadata, asset_folder):
    categories = metadata.get("categories") or []
    for category in categories:
        key = str(category).lower()
        if key in MATERIAL_TYPES or key in ASSET_TYPES:
            return key

    parts = [part.lower() for part in os.path.normpath(asset_folder).split(os.sep)]
    for part in reversed(parts):
        if part in MATERIAL_TYPES or part in ASSET_TYPES:
            return part
    return None


def _preview_path(asset_folder, metadata):
    preview_folder = os.path.join(asset_folder, "previews")
    candidates = []
    if os.path.isdir(preview_folder):
        for filename in os.listdir(preview_folder):
            path = os.path.join(preview_folder, filename)
            if os.path.isfile(path) and os.path.splitext(filename)[1].lower() in IMAGE_EXTENSIONS:
                lower = filename.lower()
                score = 0
                if "_sp." in lower or "sidepanel" in lower:
                    score += 100
                if "retina" in lower:
                    score += 30
                if "preview" in lower:
                    score += 20
                if "thumb" in lower:
                    score += 10
                candidates.append((score, path))

    if candidates:
        candidates.sort(key=lambda item: (-item[0], item[1].lower()))
        return candidates[0][1]

    asset_id = str(metadata.get("id") or "")
    direct_candidates = [
        os.path.join(asset_folder, asset_id + "_Preview.png"),
        os.path.join(asset_folder, asset_id + "_preview.png"),
    ]
    for path in direct_candidates:
        if os.path.isfile(path):
            return path
    return None


def scan_library(root_directory):
    """Return normalized records for Bridge-style Megascans asset folders."""
    records = []
    root_directory = os.path.normpath(root_directory)
    seen_folders = set()

    for current_folder, directories, files in os.walk(root_directory):
        directories[:] = [
            directory
            for directory in directories
            if directory.lower() not in IGNORED_SCAN_DIRECTORIES
        ]
        json_files = [
            filename
            for filename in files
            if filename.lower().endswith(".json")
            and filename.lower() not in ("assetsdata.json", "manifest.json")
        ]
        if not json_files:
            continue

        metadata_path = os.path.join(current_folder, sorted(json_files)[0])
        metadata = _read_json(metadata_path)
        asset_type = _asset_type(metadata, current_folder)
        if asset_type not in MATERIAL_TYPES and asset_type not in ASSET_TYPES:
            continue

        normalized_folder = os.path.normcase(os.path.normpath(current_folder))
        if normalized_folder in seen_folders:
            continue
        seen_folders.add(normalized_folder)

        asset_id = str(metadata.get("id") or os.path.splitext(json_files[0])[0])
        name = str(metadata.get("name") or os.path.basename(current_folder))
        records.append({
            "id": asset_id,
            "name": name,
            "asset_type": asset_type,
            "kind": "material" if asset_type in MATERIAL_TYPES else "asset",
            "folder": current_folder,
            "metadata_path": metadata_path,
            "preview": _preview_path(current_folder, metadata),
            "categories": list(metadata.get("categories") or []),
        })

        # An asset folder never contains another asset folder in Bridge libraries.
        directories[:] = []

    records.sort(key=lambda record: (record["kind"], record["name"].lower(), record["id"].lower()))
    return records


def _walk_asset_files(asset_folder, extensions=None, ignored_directories=None):
    ignored_directories = set(value.lower() for value in (ignored_directories or []))
    for current_folder, directories, files in os.walk(asset_folder):
        directories[:] = [
            directory
            for directory in directories
            if directory.lower() not in ignored_directories
        ]
        for filename in files:
            path = os.path.join(current_folder, filename)
            if extensions is None or os.path.splitext(filename)[1].lower() in extensions:
                yield path


def _resolution_from_path(path):
    normalized = path.replace("\\", "/")
    match = re.search(r"(?i)(?:^|[/_])(1|2|4|8|16)k(?:[/_]|$)", normalized)
    return match.group(1).lower() + "k" if match else None


def _channel_from_texture(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    stem = re.sub(r"(?i)_lod\d+$", "", stem)
    tokens = [token.lower() for token in re.split(r"[_\-\s]+", stem) if token]
    for count in (2, 1):
        if len(tokens) < count:
            continue
        key = "_".join(tokens[-count:])
        if key in CHANNEL_ALIASES:
            return CHANNEL_ALIASES[key]
        compact = "".join(tokens[-count:])
        if compact in CHANNEL_ALIASES:
            return CHANNEL_ALIASES[compact]
    return None


def texture_candidates(record):
    candidates = {}
    for path in _walk_asset_files(
        record["folder"],
        extensions=IMAGE_EXTENSIONS,
        ignored_directories=IGNORED_TEXTURE_DIRECTORIES,
    ):
        resolution = _resolution_from_path(path)
        channel = _channel_from_texture(path)
        if not resolution or not channel:
            continue
        candidates.setdefault(resolution, {}).setdefault(channel, []).append(path)
    return candidates


def _closest_quality(preferred, available, ordered_values, prefer_higher_index=False):
    if preferred in available:
        return preferred
    order = dict((value, index) for index, value in enumerate(ordered_values))
    preferred_index = order.get(preferred, 0)
    return min(
        available,
        key=lambda value: (
            abs(order.get(value, 999) - preferred_index),
            -order.get(value, 999) if prefer_higher_index else order.get(value, 999),
        ),
    )


def resolve_texture_quality(record, preferred, fallback, mesh_quality=None):
    candidates = texture_candidates(record)
    available = [quality for quality in TEXTURE_RESOLUTIONS if candidates.get(quality)]
    if preferred in available:
        chosen = preferred
    elif available and fallback == QUALITY_FALLBACK_CLOSEST:
        # On an equally distant tie, prefer more texture detail, like Cargo's
        # 2K -> 4K -> 1K fallback ordering.
        chosen = _closest_quality(
            preferred,
            available,
            TEXTURE_RESOLUTIONS,
            prefer_higher_index=True,
        )
    else:
        chosen = None

    textures = {}
    if chosen:
        for channel, paths in candidates[chosen].items():
            def path_score(path):
                lower = path.lower().replace("\\", "/")
                extension = os.path.splitext(path)[1].lower()
                score = 0
                if "/thumbs/" not in lower:
                    score += 100
                if channel == "displacement" and extension == ".exr":
                    score += 20
                elif extension in (".jpg", ".jpeg", ".png"):
                    score += 10
                if channel == "roughness" and "_roughness" in lower:
                    score += 30

                path_lod_match = re.search(r"(?i)_lod(\d+)\.[^.]+$", lower)
                requested_lod = (
                    mesh_quality[3:]
                    if mesh_quality and mesh_quality.startswith("lod")
                    else None
                )
                if requested_lod is not None:
                    if path_lod_match and path_lod_match.group(1) == requested_lod:
                        score += 50
                    elif path_lod_match:
                        score -= 30
                elif path_lod_match:
                    score -= 20
                return (-score, len(path), path.lower())

            textures[channel] = sorted(paths, key=path_score)[0]

        # Megascans gloss is inverse roughness; do not silently wire it as roughness.
        if "roughness" in textures:
            source_name = os.path.basename(textures["roughness"]).lower()
            if "_gloss" in source_name or "_glossiness" in source_name:
                del textures["roughness"]

    return chosen, textures, available


def geometry_candidates(record):
    candidates = {}
    for path in _walk_asset_files(record["folder"], extensions=set([".fbx"])):
        lower = path.lower().replace("\\", "/")
        if "/billboard/" in lower or "billboard" in os.path.basename(lower):
            continue
        stem = os.path.splitext(os.path.basename(path))[0]
        lod_match = re.search(r"(?i)(?:^|_)lod(\d+)$", stem)
        if lod_match:
            quality = "lod" + lod_match.group(1)
        elif re.search(r"(?i)(?:^|_)(high|highpoly|original)$", stem):
            quality = "high"
        else:
            continue
        candidates.setdefault(quality, []).append(path)

    for quality in candidates:
        candidates[quality].sort(key=lambda path: path.lower())
    return candidates


def resolve_geometry_quality(record, preferred, fallback):
    candidates = geometry_candidates(record)
    available = [quality for quality in LOD_LEVELS if candidates.get(quality)]
    if preferred in available:
        chosen = preferred
    elif available and fallback == QUALITY_FALLBACK_CLOSEST:
        chosen = _closest_quality(preferred, available, LOD_LEVELS)
    else:
        chosen = None
    return chosen, list(candidates.get(chosen, [])), available


def default_preferences():
    settings = openToolsUtils.getToolSettings(TOOL_SETTINGS_NAME)
    return {
        "texture_resolution": settings.get("texture_resolution", "2k"),
        "lod": settings.get("lod", "lod0"),
        "fallback": settings.get("fallback", QUALITY_FALLBACK_CLOSEST),
        "displacement_mode": settings.get(
            "displacement_mode",
            openToolsMaterialUtils.DISPLACEMENT_MODE_TEXTURE,
        ),
        "displacement_height": float(settings.get("displacement_height", 0.001)),
        "displacement_disconnected": bool(settings.get("displacement_disconnected", False)),
        "projection": settings.get("projection", openToolsMaterialUtils.PROJECTION_MODE_UV),
        "variant_load_mode": settings.get(
            "variant_load_mode",
            VARIANT_LOAD_SAME_OBJECT,
        ),
    }


def save_preferences(preferences):
    settings = openToolsUtils.getToolSettings(TOOL_SETTINGS_NAME)
    settings.update(preferences)
    openToolsUtils.setToolSettings(TOOL_SETTINGS_NAME, settings)


def _dialog_exec(dialog):
    exec_method = getattr(dialog, "exec", None) or getattr(dialog, "exec_", None)
    return exec_method()


def choose_import_preferences(has_assets, parent=None):
    if QtWidgets is None:
        raise RuntimeError("PySide is required for the Megascans importer UI.")

    preferences = default_preferences()
    parent = parent or (hou.qt.mainWindow() if hou is not None and hasattr(hou, "qt") else None)
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle("Megascans Import Preferences")
    dialog.setModal(True)

    texture_combo = QtWidgets.QComboBox(dialog)
    for quality in TEXTURE_RESOLUTIONS:
        texture_combo.addItem(quality.upper(), quality)
    texture_combo.setCurrentIndex(max(0, texture_combo.findData(preferences["texture_resolution"])))

    lod_combo = QtWidgets.QComboBox(dialog)
    for quality in LOD_LEVELS:
        lod_combo.addItem("High poly" if quality == "high" else quality.upper(), quality)
    lod_combo.setCurrentIndex(max(0, lod_combo.findData(preferences["lod"])))
    lod_combo.setEnabled(has_assets)

    variant_combo = QtWidgets.QComboBox(dialog)
    variant_combo.addItem("In same object", VARIANT_LOAD_SAME_OBJECT)
    variant_combo.addItem("As separate objects", VARIANT_LOAD_SEPARATE_OBJECTS)
    variant_combo.setCurrentIndex(
        max(0, variant_combo.findData(preferences["variant_load_mode"]))
    )
    variant_combo.setEnabled(has_assets)

    fallback_combo = QtWidgets.QComboBox(dialog)
    fallback_combo.addItem("Closest available", QUALITY_FALLBACK_CLOSEST)
    fallback_combo.addItem("Don't load asset", QUALITY_FALLBACK_SKIP)
    fallback_combo.setCurrentIndex(max(0, fallback_combo.findData(preferences["fallback"])))

    displacement_combo = QtWidgets.QComboBox(dialog)
    displacement_combo.addItem(
        "Texture displacement",
        openToolsMaterialUtils.DISPLACEMENT_MODE_TEXTURE,
    )
    displacement_combo.addItem(
        "Vertex displacement",
        openToolsMaterialUtils.DISPLACEMENT_MODE_VERTEX,
    )
    displacement_combo.addItem("Bump", openToolsMaterialUtils.DISPLACEMENT_MODE_BUMP)
    displacement_combo.setCurrentIndex(
        max(0, displacement_combo.findData(preferences["displacement_mode"]))
    )

    height_spin = QtWidgets.QDoubleSpinBox(dialog)
    height_spin.setDecimals(6)
    height_spin.setRange(-1000000.0, 1000000.0)
    height_spin.setSingleStep(0.001)
    height_spin.setValue(preferences["displacement_height"])

    disconnected_check = QtWidgets.QCheckBox("Create disconnected", dialog)
    disconnected_check.setChecked(preferences["displacement_disconnected"])

    projection_combo = QtWidgets.QComboBox(dialog)
    projection_choices = [
        ("UV", openToolsMaterialUtils.PROJECTION_MODE_UV),
        ("Triplanar", openToolsMaterialUtils.PROJECTION_MODE_TRIPLANAR),
        ("Box", openToolsMaterialUtils.PROJECTION_MODE_BOX),
        ("XYZ to UVW", openToolsMaterialUtils.PROJECTION_MODE_LINEAR),
        ("Cylindrical", openToolsMaterialUtils.PROJECTION_MODE_CYLINDRICAL),
        ("Spherical", openToolsMaterialUtils.PROJECTION_MODE_SPHERICAL),
        ("Perspective", openToolsMaterialUtils.PROJECTION_MODE_PERSPECTIVE),
    ]
    for label, value in projection_choices:
        projection_combo.addItem(label, value)
    projection_combo.setCurrentIndex(max(0, projection_combo.findData(preferences["projection"])))

    form = QtWidgets.QFormLayout()
    form.addRow("Texture resolution:", texture_combo)
    form.addRow("Geometry quality:", lod_combo)
    form.addRow("Load variants:", variant_combo)
    form.addRow("Missing quality:", fallback_combo)
    form.addRow("Height map mode:", displacement_combo)
    form.addRow("Displacement height:", height_spin)
    form.addRow("", disconnected_check)
    form.addRow("Projection (materials only):", projection_combo)

    buttons = QtWidgets.QDialogButtonBox(
        QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
        parent=dialog,
    )
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)

    layout = QtWidgets.QVBoxLayout(dialog)
    layout.addWidget(QtWidgets.QLabel(
        "Requested quality is checked per asset. Every fallback or skipped asset is printed.",
        dialog,
    ))
    layout.addLayout(form)
    layout.addWidget(buttons)

    def update_height_enabled(_index=None):
        height_spin.setEnabled(
            displacement_combo.currentData() != openToolsMaterialUtils.DISPLACEMENT_MODE_BUMP
        )

    displacement_combo.currentIndexChanged.connect(update_height_enabled)
    update_height_enabled()

    if _dialog_exec(dialog) != QtWidgets.QDialog.Accepted:
        return None

    result = {
        "texture_resolution": texture_combo.currentData(),
        "lod": lod_combo.currentData(),
        "fallback": fallback_combo.currentData(),
        "displacement_mode": displacement_combo.currentData(),
        "displacement_height": float(height_spin.value()),
        "displacement_disconnected": disconnected_check.isChecked(),
        "projection": projection_combo.currentData(),
        "variant_load_mode": variant_combo.currentData(),
    }
    save_preferences(result)
    return result


def sanitize_node_name(name):
    cleaned = re.sub(r"[^0-9A-Za-z_]+", "_", str(name).strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        return "megascans_asset"
    if cleaned[0].isdigit():
        cleaned = "_" + cleaned
    return cleaned


def _unique_node_name(parent, name):
    name = sanitize_node_name(name)
    if parent.node(name) is None:
        return name
    index = 1
    while parent.node("{0}_{1}".format(name, index)) is not None:
        index += 1
    return "{0}_{1}".format(name, index)


def _create_node(parent, type_names, name):
    if isinstance(type_names, str):
        type_names = [type_names]
    last_error = None
    for type_name in type_names:
        try:
            return parent.createNode(type_name, _unique_node_name(parent, name))
        except Exception as error:
            last_error = error
    raise RuntimeError(
        "Could not create node {0} with types {1}: {2}".format(
            name,
            ", ".join(type_names),
            last_error,
        )
    )


def _set_first_parm(node, names, value):
    for name in names:
        parm = node.parm(name)
        if parm is not None:
            parm.set(value)
            return True
    _log("Missing parameter {0} on {1}".format("/".join(names), node.path()))
    return False


def get_active_network():
    editor = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
    if editor is not None:
        return editor.pwd()
    return hou.node("/obj")


def is_material_network(node):
    if node is None:
        return False
    try:
        return (
            node.childTypeCategory() == hou.vopNodeTypeCategory()
            or node.type().name().lower() in ("matnet", "materialnet")
            or node.path() == "/mat"
        )
    except Exception:
        return False


def is_obj_network(node):
    return node is not None and node.childTypeCategory() == hou.objNodeTypeCategory()


def is_sop_network(node):
    return node is not None and node.childTypeCategory() == hou.sopNodeTypeCategory()


def find_target_material_context():
    node = get_active_network()
    while node is not None:
        if is_material_network(node):
            return node
        node = node.parent()
    material_context = hou.node("/mat")
    if material_context is None:
        material_context = hou.node("/").createNode("mat")
    return material_context


def find_target_asset_context():
    node = get_active_network()
    while node is not None:
        if is_sop_network(node) or is_obj_network(node):
            return node
        node = node.parent()
    return hou.node("/obj")


def next_right_position(parent, padding=3.0):
    children = list(parent.children())
    if not children:
        return hou.Vector2(0, 0)
    return hou.Vector2(
        max(child.position().x() for child in children) + padding,
        max(child.position().y() for child in children),
    )


def _displacement_settings(preferences):
    return {
        "mode": preferences["displacement_mode"],
        "height": preferences["displacement_height"],
        "create_disconnected": preferences["displacement_disconnected"],
    }


def create_octane_material(parent, record, textures, preferences, projection=None):
    parameters = {"base": 1.0}
    if "backlight" in textures or "subsurface_color" in textures:
        # Megascans foliage translucency is an RGB scattering-color texture.
        # Enable Octane's thin-wall diffuse-transmission layer separately.
        parameters.update({"subsurface": 0.5, "thin_walled": True})

    result = openToolsMaterialUtils.createOctaneMaterial(
        parent,
        name=record["name"],
        textures=textures,
        # Octane's Standard Surface defaults Base Weight to 0.8. Texture-based
        # assets expect their base color at full strength.
        parameters=parameters,
        projection={"mode": projection or preferences["projection"]},
        displacement=_displacement_settings(preferences),
        options={
            "allow_unknown_parameters": False,
            "material_spec": {
                "name": record["name"],
                "textures": textures,
            },
        },
        logger=_log,
    )
    return result["subnet"]


def _set_material_assignment(material_node, material_path):
    _set_first_parm(material_node, ["num_materials"], 1)
    _set_first_parm(material_node, ["group1", "group0"], "")
    material_ref = hou.node(material_path)
    if material_ref is not None:
        material_path = material_node.relativePathTo(material_ref)
    _set_first_parm(material_node, ["shop_materialpath1", "shop_materialpath0"], material_path)


def _mesh_variant_name(mesh_path, asset_name=None):
    variant_name = sanitize_node_name(os.path.splitext(os.path.basename(mesh_path))[0])
    asset_name = sanitize_node_name(asset_name) if asset_name else ""
    prefix = asset_name + "_"
    if asset_name and variant_name.lower().startswith(prefix.lower()):
        variant_name = variant_name[len(prefix):]
    return variant_name or "variant"


def _create_asset_sop_network(
    parent,
    record,
    mesh_paths,
    textures,
    preferences,
    position,
    layout_created_object=False,
):
    node_name = sanitize_node_name(record["name"])
    matnet = _create_node(parent, ["matnet", "materialnet"], "MATNET_{0}".format(node_name))
    matnet.setPosition(hou.Vector2(position.x(), position.y() + 2.0))
    material = create_octane_material(
        matnet,
        record,
        textures,
        preferences,
        projection=openToolsMaterialUtils.PROJECTION_MODE_UV,
    )

    file_nodes = []
    transform_nodes = []
    material_nodes = []
    variant_outputs = []
    for index, mesh_path in enumerate(mesh_paths):
        variant_name = _mesh_variant_name(
            mesh_path,
            record.get("base_asset_name", record["name"]),
        )
        variant_x = position.x() + (index * 2.5)

        file_node = _create_node(
            parent,
            ["file"],
            "FILE_{0}_{1}".format(node_name, variant_name),
        )
        file_node.setPosition(hou.Vector2(variant_x, position.y()))
        _set_first_parm(file_node, ["file"], mesh_path)
        file_nodes.append(file_node)

        transform_node = _create_node(
            parent,
            ["xform"],
            "TRANSFORM_{0}_{1}".format(node_name, variant_name),
        )
        transform_node.setPosition(hou.Vector2(variant_x, position.y() - 1.2))
        transform_node.setInput(0, file_node)
        _set_first_parm(transform_node, ["scale"], 0.01)
        transform_nodes.append(transform_node)

        material_node = _create_node(
            parent,
            ["material"],
            "MATERIAL_{0}_{1}".format(node_name, variant_name),
        )
        material_node.setPosition(hou.Vector2(variant_x, position.y() - 2.4))
        material_node.setInput(0, transform_node)
        _set_material_assignment(material_node, material.path())
        material_nodes.append(material_node)

        variant_out = _create_node(parent, ["null"], "OUT_{0}".format(variant_name))
        variant_out.setPosition(hou.Vector2(variant_x, position.y() - 3.6))
        variant_out.setInput(0, material_node)
        variant_outputs.append(variant_out)

    merge_node = _create_node(parent, ["merge"], "MERGE_{0}_ALL".format(node_name))
    merge_x = position.x() + (max(0, len(variant_outputs) - 1) * 1.25)
    merge_node.setPosition(hou.Vector2(merge_x, position.y() - 4.8))
    for index, variant_out in enumerate(variant_outputs):
        merge_node.setInput(index, variant_out)

    out_node = _create_node(parent, ["null"], "OUT_{0}_ALL".format(node_name))
    out_node.setPosition(hou.Vector2(merge_x, position.y() - 6.0))
    out_node.setInput(0, merge_node)
    out_node.setDisplayFlag(True)
    out_node.setRenderFlag(True)

    matnet.layoutChildren()
    if layout_created_object:
        # The caller guarantees this is a newly-created, otherwise empty GEO.
        parent.layoutChildren()
    return {
        "matnet": matnet,
        "material": material,
        "files": file_nodes,
        "transforms": transform_nodes,
        "material_nodes": material_nodes,
        "variant_outputs": variant_outputs,
        "merge": merge_node,
        "out": out_node,
    }


def _create_geo_container(parent, record, position):
    geo = _create_node(parent, ["geo"], record["name"])
    geo.setPosition(position)
    for child in list(geo.children()):
        child.destroy()
    return geo


def _format_available(values):
    return ", ".join(value.upper() for value in values) if values else "none"


def resolve_record(record, preferences):
    mesh_quality = None
    mesh_paths = []
    mesh_available = []
    if record["kind"] == "asset":
        mesh_quality, mesh_paths, mesh_available = resolve_geometry_quality(
            record,
            preferences["lod"],
            preferences["fallback"],
        )

    texture_quality, textures, texture_available = resolve_texture_quality(
        record,
        preferences["texture_resolution"],
        preferences["fallback"],
        mesh_quality=mesh_quality,
    )

    skipped = not texture_quality or not textures
    if record["kind"] == "asset":
        skipped = skipped or not mesh_quality or not mesh_paths

    return {
        "record": record,
        "texture_quality": texture_quality,
        "textures": textures,
        "texture_available": texture_available,
        "mesh_quality": mesh_quality,
        "mesh_paths": mesh_paths,
        "mesh_available": mesh_available,
        "skipped": skipped,
    }


def print_quality_report(resolved_records, preferences):
    _log("Quality resolution report")
    _log(
        "Requested textures={0}, geometry={1}, fallback={2}".format(
            preferences["texture_resolution"].upper(),
            preferences["lod"].upper(),
            preferences["fallback"],
        )
    )
    for resolved in resolved_records:
        record = resolved["record"]
        texture_status = resolved["texture_quality"] or "not loaded"
        texture_fallback = texture_status != preferences["texture_resolution"]
        mesh_status = resolved["mesh_quality"] or "not loaded"
        mesh_fallback = record["kind"] == "asset" and mesh_status != preferences["lod"]
        status = "SKIPPED" if resolved["skipped"] else "READY"
        _log(
            "{0}: {1} [{2}] textures {3}{4}; available: {5}".format(
                status,
                record["name"],
                record["asset_type"],
                texture_status.upper(),
                " (fallback)" if texture_fallback else "",
                _format_available(resolved["texture_available"]),
            )
        )
        if record["kind"] == "asset":
            _log(
                "    geometry {0}{1}; available: {2}; meshes: {3}".format(
                    mesh_status.upper(),
                    " (fallback)" if mesh_fallback else "",
                    _format_available(resolved["mesh_available"]),
                    len(resolved["mesh_paths"]),
                )
            )


def import_records(records, preferences):
    if hou is None:
        raise RuntimeError("This script must run inside Houdini.")

    resolved_records = [resolve_record(record, preferences) for record in records]
    print_quality_report(resolved_records, preferences)
    ready = [resolved for resolved in resolved_records if not resolved["skipped"]]
    if not ready:
        return {"created": [], "skipped": len(resolved_records)}

    material_records = [resolved for resolved in ready if resolved["record"]["kind"] == "material"]
    asset_records = [resolved for resolved in ready if resolved["record"]["kind"] == "asset"]
    created = []

    if material_records:
        material_context = find_target_material_context()
        for resolved in material_records:
            node = create_octane_material(
                material_context,
                resolved["record"],
                resolved["textures"],
                preferences,
            )
            created.append(node)
            _log("Created material: {0}".format(node.path()))
        material_context.layoutChildren()

    if asset_records:
        asset_context = find_target_asset_context()
        start_position = next_right_position(asset_context)
        if is_obj_network(asset_context):
            for asset_index, resolved in enumerate(asset_records):
                separate_objects = (
                    preferences.get("variant_load_mode") == VARIANT_LOAD_SEPARATE_OBJECTS
                )
                mesh_groups = (
                    [[mesh_path] for mesh_path in resolved["mesh_paths"]]
                    if separate_objects
                    else [resolved["mesh_paths"]]
                )
                for variant_index, mesh_group in enumerate(mesh_groups):
                    object_record = dict(resolved["record"])
                    if separate_objects:
                        object_record["base_asset_name"] = resolved["record"]["name"]
                        object_record["name"] = "{0}_{1}".format(
                            resolved["record"]["name"],
                            _mesh_variant_name(
                                mesh_group[0],
                                resolved["record"]["name"],
                            ),
                        )
                    position = hou.Vector2(
                        start_position.x()
                        + (
                            variant_index * OBJECT_VARIANT_COLUMN_SPACING
                            if separate_objects
                            else 0.0
                        ),
                        start_position.y() - (asset_index * OBJECT_ROW_SPACING),
                    )
                    geo = _create_geo_container(asset_context, object_record, position)
                    _create_asset_sop_network(
                        geo,
                        object_record,
                        mesh_group,
                        resolved["textures"],
                        preferences,
                        hou.Vector2(0, 0),
                        layout_created_object=True,
                    )
                    created.append(geo)
                    _log("Created asset object: {0}".format(geo.path()))
        else:
            # We are already inside an existing GEO. Always keep all variants
            # in this object, ignore the object-splitting preference, and
            # position only the nodes created by this import.
            column_offset = 0.0
            for resolved in asset_records:
                position = hou.Vector2(
                    start_position.x() + column_offset,
                    start_position.y(),
                )
                result = _create_asset_sop_network(
                    asset_context,
                    resolved["record"],
                    resolved["mesh_paths"],
                    resolved["textures"],
                    preferences,
                    position,
                    layout_created_object=False,
                )
                created.append(result["out"])
                _log("Created asset SOP network: {0}".format(result["out"].path()))
                column_offset += max(6.0, len(resolved["mesh_paths"]) * 2.5 + 2.0)

    return {
        "created": created,
        "skipped": len(resolved_records) - len(ready),
        "resolved": resolved_records,
    }


class MegascansBrowser(QtWidgets.QDialog if QtWidgets is not None else object):
    def __init__(self, root_directory, records, parent=None):
        super(MegascansBrowser, self).__init__(parent)
        self.root_directory = root_directory
        self.records = records
        self.setWindowTitle("Megascans Importer")
        self.resize(1050, 760)

        self._root_label = QtWidgets.QLineEdit(root_directory, self)
        self._root_label.setReadOnly(True)
        self._change_root_button = QtWidgets.QPushButton("Change Library", self)
        self._rescan_button = QtWidgets.QPushButton("Rescan", self)
        self._search = QtWidgets.QLineEdit(self)
        self._search.setPlaceholderText("Filter by name, id, or category...")
        self._tabs = QtWidgets.QTabWidget(self)
        self._material_list = self._create_asset_list()
        self._asset_list = self._create_asset_list()
        self._tabs.addTab(self._material_list, "Materials")
        self._tabs.addTab(self._asset_list, "Assets")
        self._status = QtWidgets.QLabel(self)
        self._import_button = QtWidgets.QPushButton("Import Selected", self)
        self._close_button = QtWidgets.QPushButton("Close", self)

        root_row = QtWidgets.QHBoxLayout()
        root_row.addWidget(self._root_label, 1)
        root_row.addWidget(self._change_root_button)
        root_row.addWidget(self._rescan_button)

        footer = QtWidgets.QHBoxLayout()
        footer.addWidget(self._status, 1)
        footer.addWidget(self._import_button)
        footer.addWidget(self._close_button)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(root_row)
        layout.addWidget(self._search)
        layout.addWidget(self._tabs, 1)
        layout.addLayout(footer)

        self._change_root_button.clicked.connect(self._change_library)
        self._rescan_button.clicked.connect(self._rescan)
        self._search.textChanged.connect(self._apply_filter)
        self._import_button.clicked.connect(self._import_selected)
        self._close_button.clicked.connect(self.reject)
        self._populate()

    def _create_asset_list(self):
        widget = QtWidgets.QListWidget(self)
        widget.setViewMode(QtWidgets.QListView.IconMode)
        widget.setResizeMode(QtWidgets.QListView.Adjust)
        widget.setMovement(QtWidgets.QListView.Static)
        widget.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        widget.setIconSize(QtCore.QSize(180, 120))
        widget.setGridSize(QtCore.QSize(205, 175))
        widget.setWordWrap(True)
        widget.setSpacing(6)
        return widget

    def _populate(self):
        self._material_list.clear()
        self._asset_list.clear()
        for record in self.records:
            label = "{0}\n{1} · {2}".format(
                record["name"],
                record["asset_type"],
                record["id"],
            )
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, record)
            item.setToolTip(
                "{0}\n{1}".format(
                    record["folder"],
                    " / ".join(str(value) for value in record["categories"]),
                )
            )
            if record.get("preview"):
                item.setIcon(QtGui.QIcon(record["preview"]))
            target = self._material_list if record["kind"] == "material" else self._asset_list
            target.addItem(item)
        self._apply_filter()

    def _apply_filter(self, _text=None):
        query = self._search.text().strip().lower()
        visible_counts = [0, 0]
        for list_index, widget in enumerate((self._material_list, self._asset_list)):
            for index in range(widget.count()):
                item = widget.item(index)
                record = item.data(QtCore.Qt.UserRole)
                haystack = " ".join([
                    record["name"],
                    record["id"],
                    record["asset_type"],
                    " ".join(str(value) for value in record["categories"]),
                ]).lower()
                hidden = bool(query and query not in haystack)
                item.setHidden(hidden)
                if not hidden:
                    visible_counts[list_index] += 1
        self._tabs.setTabText(0, "Materials ({0})".format(visible_counts[0]))
        self._tabs.setTabText(1, "Assets ({0})".format(visible_counts[1]))
        self._status.setText("{0} library records".format(len(self.records)))

    def _selected_records(self):
        selected = []
        seen = set()
        for widget in (self._material_list, self._asset_list):
            for item in widget.selectedItems():
                record = item.data(QtCore.Qt.UserRole)
                key = os.path.normcase(record["folder"])
                if key not in seen:
                    selected.append(record)
                    seen.add(key)
        return selected

    def _rescan(self):
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            self.records = scan_library(self.root_directory)
            self._populate()
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

    def _change_library(self):
        root_directory = select_library_directory(self, self.root_directory)
        if not root_directory:
            return
        self.root_directory = root_directory
        save_library_root(root_directory)
        self._root_label.setText(root_directory)
        self._rescan()

    def _import_selected(self):
        records = self._selected_records()
        if not records:
            self._display_message(
                "Select one or more Megascans materials or assets first.",
                title="Megascans Importer",
                icon=QtWidgets.QMessageBox.Warning,
            )
            return

        preferences = choose_import_preferences(
            has_assets=any(record["kind"] == "asset" for record in records),
            parent=self,
        )
        if preferences is None:
            return

        try:
            result = import_records(records, preferences)
        except Exception as error:
            _log("Import failed: {0}".format(error))
            self._display_message(
                "Megascans import failed:\n{0}\n\nSee the Python shell for the quality report.".format(error),
                title="Megascans Importer",
                icon=QtWidgets.QMessageBox.Critical,
            )
            return

        self._display_message(
            "Created {0} item(s); skipped {1}.\n\nSee the Python shell for the full quality report.".format(
                len(result["created"]),
                result["skipped"],
            ),
            title="Megascans Importer",
        )

    def _display_message(self, message, title, icon=None):
        """Show a modal child notification that can receive input."""
        dialog = QtWidgets.QMessageBox(
            icon or QtWidgets.QMessageBox.Information,
            title,
            message,
            QtWidgets.QMessageBox.Ok,
            self,
        )
        _dialog_exec(dialog)


def show(root_directory=None):
    if hou is None:
        raise RuntimeError("This script must run inside Houdini.")
    if QtWidgets is None:
        raise RuntimeError("PySide is required for the Megascans importer UI.")

    root_directory = ensure_library_root(root_directory)
    if not root_directory:
        _log("No Megascans library selected.")
        return None

    records = scan_library(root_directory)
    if not records:
        hou.ui.displayMessage(
            "No Megascans assets were found under:\n{0}".format(root_directory),
            title="Megascans Importer",
        )
        return None

    parent = hou.qt.mainWindow() if hasattr(hou, "qt") else None
    dialog = MegascansBrowser(root_directory, records, parent=parent)
    _dialog_exec(dialog)
    return dialog


def run(root_directory=None):
    return show(root_directory=root_directory)


def main(kwargs=None):
    kwargs = kwargs or {}
    return run(root_directory=kwargs.get("root_directory") or kwargs.get("root_folder"))


def should_auto_run():
    if hou is None:
        return __name__ == "__main__"
    return __name__ in ("__main__", "__builtin__", "builtins")


if should_auto_run():
    run()
