"""
Cargo USD material importer for Houdini + Octane.

Import this module from a Houdini shelf tool and call main(kwargs), or source
this file and call run(). It scans Cargo material .usd files, shows a selectable list, then
rebuilds the selected materials as Octane material builder subnets.
"""

from __future__ import print_function

import os
import re

try:
    import hou
except ImportError:
    hou = None

import openToolsUtils
import openToolsMaterialUtils


CARGO_ROOT = None
CARGO_TOOL_SETTINGS_NAME = "CargoImporter"

PREFERRED_TEXTURE_VARIANTS = ["png2k", "jpg2k", "png4k", "jpg4k", "png1k", "jpg1k"]
DEFAULT_DISPLACEMENT_HEIGHT = 0.001
DEFAULT_DISPLACEMENT_CREATE_DISCONNECTED = False
MODEL_COLUMN_SPACING = 5.0
NETWORK_INSERT_GAP = 2.0
MATERIAL_USD_EXTENSIONS = set([".usd", ".usda", ".usdc"])
OCTANE_IMAGE_NODE_TYPE = "octane::NT_TEX_IMAGE"
OCTANE_FLOAT_IMAGE_NODE_TYPE = "octane::NT_TEX_FLOATIMAGE"
OCTANE_TRANSFORM_2D_NODE_TYPE = "octane::NT_TRANSFORM_2D"
OCTANE_TRANSFORM_3D_NODE_TYPE = "octane::NT_TRANSFORM_3D"
OCTANE_TEXTURE_DISPLACEMENT_NODE_TYPE = "octane::NT_DISPLACEMENT"
OCTANE_VERTEX_DISPLACEMENT_NODE_TYPE = "octane::NT_VERTEX_DISPLACEMENT"
OCTANE_TRIPLANAR_TEXTURE_NODE_TYPE = "octane::NT_TEX_TRIPLANAR"
PROJECTION_MODE_UV = "uv"
PROJECTION_MODE_LINEAR = "linear"
PROJECTION_MODE_BOX = "box"
PROJECTION_MODE_CYLINDRICAL = "cylindrical"
PROJECTION_MODE_SPHERICAL = "spherical"
PROJECTION_MODE_PERSPECTIVE = "perspective"
PROJECTION_MODE_TRIPLANAR = "triplanar"
HEIGHT_MODE_TEXTURE_DISPLACEMENT = "texture_displacement"
HEIGHT_MODE_VERTEX_DISPLACEMENT = "vertex_displacement"
HEIGHT_MODE_BUMP = "bump"
HEIGHT_TEXTURE_CHANNELS = set(["displacement", "height", "bump"])
PROJECTION_NODE_TYPES = {
    PROJECTION_MODE_LINEAR: ("octane::NT_PROJ_LINEAR", "xyz_to_uvw_projection"),
    PROJECTION_MODE_BOX: ("octane::NT_PROJ_BOX", "box_projection"),
    PROJECTION_MODE_CYLINDRICAL: ("octane::NT_PROJ_CYLINDRICAL", "cylindrical_projection"),
    PROJECTION_MODE_SPHERICAL: ("octane::NT_PROJ_SPHERICAL", "spherical_projection"),
    PROJECTION_MODE_PERSPECTIVE: ("octane::NT_PROJ_PERSPECTIVE", "perspective_projection"),
}


def select_directory(title):
    if hou is None:
        raise RuntimeError("This script must run inside Houdini.")

    selected = hou.ui.selectFile(
        title=title,
        file_type=hou.fileType.Directory,
        chooser_mode=hou.fileChooserMode.Read,
    )
    if not selected:
        return None
    return hou.expandString(selected)


def cargo_root_from_settings():
    root_directory = openToolsUtils.getToolSetting(CARGO_TOOL_SETTINGS_NAME, "root_directory")
    if root_directory:
        return hou.expandString(root_directory) if hou is not None else root_directory
    return None


def save_cargo_root(root_directory):
    openToolsUtils.setToolSetting(CARGO_TOOL_SETTINGS_NAME, "root_directory", root_directory)


def choose_cargo_root_directory():
    root_directory = cargo_root_from_settings()

    if not root_directory:
        root_directory = select_directory("Select Cargo root directory")
        if not root_directory:
            return None
        save_cargo_root(root_directory)

    choice = hou.ui.displayMessage(
        "Where do you want to load Cargo assets from?",
        buttons=("Cargo root", "Project directory", "Change Cargo root", "Cancel"),
        default_choice=0,
        close_choice=3,
        title="Cargo Importer",
    )
    if choice == 0:
        return root_directory
    if choice == 1:
        return select_directory("Select Cargo project directory")
    if choice == 2:
        root_directory = select_directory("Select Cargo root directory")
        if root_directory:
            save_cargo_root(root_directory)
        return root_directory
    return None


SRGB_CHANNELS = set([
    "basecolor",
    "base_color",
    "diffuse",
    "albedo",
    "specularcolor",
    "specular_color",
    "specularedgecolor",
    "emission",
    "emissioncolor",
    "emission_color",
    "emissive",
    "subsurface_color",
    "transmissioncolor",
    "transmission_color",
])
FLOAT_TEXTURE_CHANNELS = set([
    "anisotropy_angle",
    "bump",
    "displacement",
    "height",
    "metalness",
    "metallic",
    "opacity",
    "roughness",
    "scattering_weight",
    "sheen",
    "sheen_opacity",
    "specular",
    "specular_level",
    "specular_rotation",
    "specular_roughness",
    "subsurface",
    "transmission",
])
COLOR_SPACE = "NAMED_COLOR_SPACE_SRGB"
NON_COLOR_SPACE = "NAMED_COLOR_SPACE_OTHER"

USD_TO_OCTANE_INPUTS = {
    "base": ["base"],
    "base_color": ["baseColor"],
    "coat": ["coating"],
    "coat_anisotropy": ["coatingAnisotropy"],
    "coat_color": ["coatingColor"],
    "coat_ior": ["coatingIor"],
    "coat_rotation": ["coatingRotation"],
    "coat_roughness": ["coatingRoughness"],
    "diffuse_roughness": ["diffuseRoughness"],
    "emission": ["emissionWeight"],
    "emission_color": ["emissionColor"],
    "metalness": ["metallic"],
    "opacity": ["opacity"],
    "sheen": ["sheen"],
    "sheen_color": ["sheenColor"],
    "sheen_roughness": ["sheenRoughness"],
    "specular": ["specular"],
    "specular_anisotropy": ["anisotropyTexture"],
    "specular_color": ["specularColor"],
    "specular_ior": ["ior"],
    "specular_rotation": ["rotation"],
    "specular_roughness": ["roughness"],
    "subsurface": ["subsurface"],
    "subsurface_anisotropy": ["subsurfaceAnisotropy"],
    "subsurface_color": ["subsurfaceColor"],
    "subsurface_radius": ["radius"],
    "subsurface_scale": ["scale"],
    "thin_film_ior": ["filmIor"],
    "thin_film_thickness": ["filmwidth"],
    "thin_walled": ["thinWall"],
    "transmission": ["transmission"],
    "transmission_color": ["transmissionColor"],
    "transmission_depth": ["transmissionDepth"],
    "transmission_dispersion": ["dispersion_coefficient_B"],
    "transmission_extra_roughness": ["roughnessExtra"],
    "transmission_scatter": ["scattering"],
    "transmission_scatter_anisotropy": ["scatteringAnisotropy"],
}

TEXTURE_CHANNEL_INPUTS = {
    "base_color": ["baseColor"],
    "basecolor": ["baseColor"],
    "diffuse": ["baseColor"],
    "normal": ["normal"],
    "specular_roughness": ["roughness"],
    "roughness": ["roughness"],
    "metalness": ["metallic"],
    "metallic": ["metallic"],
    "opacity": ["opacity"],
    "emission_color": ["emissionColor"],
    "emissive": ["emissionColor"],
    "bump": ["bump"],
    "specular": ["specular"],
    "specular_color": ["specularColor"],
    "specular_rotation": ["rotation"],
    "specularedgecolor": ["specularColor"],
    "sheen": ["sheen"],
    "sheen_opacity": ["sheen"],
    "scattering_weight": ["subsurface"],
    "subsurface": ["subsurface"],
    "subsurface_color": ["subsurfaceColor"],
    "transmission": ["transmission"],
    "anisotropy_angle": ["rotation"],
}

TEXTURE_NAME_ALIASES = {
    "basecolor": "base_color",
    "base_color": "base_color",
    "color": "base_color",
    "diffuse": "base_color",
    "height": "displacement",
    "displacement": "displacement",
    "normal": "normal",
    "nrm": "normal",
    "roughness": "specular_roughness",
    "rough": "specular_roughness",
    "metallic": "metalness",
    "metalness": "metalness",
    "opacity": "opacity",
    "alpha": "opacity",
    "emission": "emission_color",
    "emissioncolor": "emission_color",
    "emission_color": "emission_color",
    "emissive": "emission_color",
    "specularcolor": "specular_color",
    "specularedgecolor": "specular_color",
    "specularlevel": "specular",
    "specular_level": "specular",
    "sheenopacity": "sheen_opacity",
    "sheen_opacity": "sheen_opacity",
    "scatteringweight": "scattering_weight",
    "scattering_weight": "scattering_weight",
    "anisotropyangle": "anisotropy_angle",
    "anisotropy_angle": "anisotropy_angle",
}


def sanitize_node_name(name):
    cleaned = re.sub(r"[^0-9A-Za-z_]+", "_", str(name).strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        cleaned = "cargo_material"
    if cleaned[0].isdigit():
        cleaned = "_" + cleaned
    return cleaned


def uniquify_node_name(parent, name):
    base_name = sanitize_node_name(name)
    if parent.node(base_name) is None:
        return base_name
    index = 1
    while parent.node("{0}_{1}".format(base_name, index)) is not None:
        index += 1
    return "{0}_{1}".format(base_name, index)


def normalize_key(value):
    return re.sub(r"[^0-9a-z]+", "_", str(value).lower()).strip("_")


def scan_material_usds(root_folder):
    records = []
    root_folder = os.path.normpath(root_folder)
    for current_folder, _dirs, files in os.walk(root_folder):
        if "{0}materials{0}".format(os.sep).lower() not in current_folder.lower() + os.sep:
            continue
        for filename in files:
            ext = os.path.splitext(filename)[1].lower()
            if ext not in MATERIAL_USD_EXTENSIONS:
                continue
            path = os.path.join(current_folder, filename)
            name = os.path.splitext(filename)[0]
            records.append({
                "name": name,
                "path": path,
                "kit": kit_name_from_path(root_folder, path),
                "version": version_from_path(root_folder, path),
            })
    records.sort(key=lambda item: (item["kit"].lower(), item["name"].lower()))
    return records


def scan_model_usds(root_folder):
    records = []
    root_folder = os.path.normpath(root_folder)
    for current_folder, _dirs, files in os.walk(root_folder):
        if "geo.usd" not in files:
            continue
        rel_parts = os.path.relpath(current_folder, root_folder).split(os.sep)
        if "Models" not in rel_parts:
            continue
        model_name = os.path.basename(current_folder)
        records.append({
            "name": model_name,
            "path": os.path.join(current_folder, "geo.usd"),
            "mtl_path": os.path.join(current_folder, "mtl.usd"),
            "kit": kit_name_from_path(root_folder, os.path.join(current_folder, "geo.usd")),
            "version": version_from_path(root_folder, os.path.join(current_folder, "geo.usd")),
            "folder": current_folder,
        })
    records.sort(key=lambda item: (item["kit"].lower(), item["name"].lower()))
    return records


def kit_name_from_path(root_folder, path):
    rel_parts = os.path.relpath(path, root_folder).split(os.sep)
    return rel_parts[0] if rel_parts else ""


def version_from_path(root_folder, path):
    rel_parts = os.path.relpath(path, root_folder).split(os.sep)
    return rel_parts[1] if len(rel_parts) > 1 else ""


def read_text(path):
    with open(path, "r") as handle:
        return handle.read()


def parse_default_texture_variant(usd_text):
    match = re.search(r'string\s+texture_variant\s*=\s*"([^"]+)"', usd_text)
    return match.group(1) if match else None


def parse_asset_info(usd_text):
    info = {}
    for key in ("kitDisplayName", "kitId", "kitVersion", "renderer", "subtype"):
        match = re.search(r'string\s+{0}\s*=\s*"([^"]*)"'.format(re.escape(key)), usd_text)
        if match:
            info[key] = match.group(1)
    return info


def parse_material_inputs(usd_text):
    inputs = {}
    input_re = re.compile(r'^\s*(float|bool|color3f)\s+inputs:([A-Za-z0-9_]+)\s*=\s*([^\n]+)$', re.MULTILINE)
    for value_type, name, raw_value in input_re.findall(usd_text):
        raw_value = raw_value.strip()
        if value_type == "color3f":
            values = re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", raw_value)
            if len(values) >= 3:
                inputs[name] = tuple(float(value) for value in values[:3])
        elif value_type == "bool":
            inputs[name] = 1 if raw_value.startswith("1") or raw_value.lower().startswith("true") else 0
        else:
            match = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", raw_value)
            if match:
                inputs[name] = float(match.group(0))
    return inputs


def resolve_asset_path(material_usd_path, asset_path):
    return os.path.normpath(os.path.join(os.path.dirname(material_usd_path), asset_path.replace("/", os.sep)))


def parse_variant_texture_assets(usd_text, material_usd_path):
    variants = {}
    current_variant = None
    current_image = None

    variant_re = re.compile(r'^\s*"([^"]+)"\s*\{\s*$')
    image_re = re.compile(r'^\s*over\s+"image_([^"]+)"')
    file_re = re.compile(r"asset\s+inputs:file\s*=\s*@([^@]+)@")

    for line in usd_text.splitlines():
        variant_match = variant_re.match(line)
        if variant_match:
            current_variant = variant_match.group(1)
            variants.setdefault(current_variant, {})
            current_image = None
            continue

        image_match = image_re.match(line)
        if image_match and current_variant:
            current_image = image_match.group(1)
            continue

        file_match = file_re.search(line)
        if file_match and current_variant and current_image:
            channel = canonical_texture_channel(current_image)
            variants.setdefault(current_variant, {})[channel] = resolve_asset_path(material_usd_path, file_match.group(1))

    return variants


def canonical_texture_channel(raw_channel):
    key = normalize_key(raw_channel)
    if key.startswith("image_"):
        key = key[6:]
    return TEXTURE_NAME_ALIASES.get(key, key)


def choose_texture_variant(variant_textures, preferred_variants):
    for variant in preferred_variants:
        textures = variant_textures.get(variant)
        if textures:
            existing = dict((channel, path) for channel, path in textures.items() if os.path.exists(path))
            if existing:
                return variant, existing

    best_variant = None
    best_textures = {}
    for variant, textures in variant_textures.items():
        existing = dict((channel, path) for channel, path in textures.items() if os.path.exists(path))
        if len(existing) > len(best_textures):
            best_variant = variant
            best_textures = existing
    return best_variant, best_textures


def parse_normal_scale(usd_text):
    normalmap_re = re.compile(r'def\s+Shader\s+"normalmap_[^"]+"\s*\{(.*?)\n\s*\}', re.DOTALL)
    for block in normalmap_re.findall(usd_text):
        match = re.search(r"float\s+inputs:scale\s*=\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)", block)
        if match:
            return float(match.group(1))
    return None


def material_record_from_usd(record, preferred_variants):
    usd_text = read_text(record["path"])
    variant_order = list(preferred_variants or [])
    default_variant = parse_default_texture_variant(usd_text)
    if default_variant and default_variant not in variant_order:
        variant_order.append(default_variant)

    variant_textures = parse_variant_texture_assets(usd_text, record["path"])
    chosen_variant, textures = choose_texture_variant(variant_textures, variant_order)

    material = dict(record)
    material["inputs"] = parse_material_inputs(usd_text)
    material["textures"] = textures
    material["texture_settings"] = {}
    normal_scale = parse_normal_scale(usd_text)
    if normal_scale is not None:
        material["texture_settings"]["normal"] = {"power": normal_scale}
    material["texture_variant"] = chosen_variant
    material["asset_info"] = parse_asset_info(usd_text)
    return material


def parse_model_material_references(model_record):
    mtl_path = model_record.get("mtl_path")
    if not mtl_path or not os.path.exists(mtl_path):
        return []

    usd_text = read_text(mtl_path)
    references = []
    seen = set()
    ref_re = re.compile(r"prepend\s+references\s*=\s*@([^@]+\.usd)@<([^>]+)>")
    for rel_ref, prim_path in ref_re.findall(usd_text):
        material_name = prim_path.replace("\\", "/").split("/")[-1]
        material_usd_path = resolve_asset_path(mtl_path, rel_ref)
        key = (material_name, material_usd_path)
        if key in seen:
            continue
        seen.add(key)
        references.append({
            "name": material_name,
            "path": material_usd_path,
            "kit": model_record["kit"],
            "version": model_record["version"],
            "usdmaterialpath": "/{0}/mtl/{1}".format(model_record["name"], material_name),
        })
    references.sort(key=lambda item: item["name"].lower())
    return references


def get_active_network():
    if hou is None:
        raise RuntimeError("This script must run inside Houdini.")
    editor = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
    if editor is not None:
        return editor.pwd()
    return hou.node("/mat")


def is_material_network(node):
    if node is None:
        return False
    try:
        category = node.childTypeCategory()
        if category == hou.vopNodeTypeCategory():
            return True
        type_name = node.type().name().lower()
        return type_name in ("matnet", "materialnet") or node.path() == "/mat"
    except Exception:
        return False


def is_obj_network(node):
    return node is not None and node.childTypeCategory() == hou.objNodeTypeCategory()


def is_sop_network(node):
    return node is not None and node.childTypeCategory() == hou.sopNodeTypeCategory()


def find_target_material_context():
    active = get_active_network()
    node = active
    while node is not None:
        if is_material_network(node):
            return node
        node = node.parent()
    mat = hou.node("/mat")
    if mat is None:
        mat = hou.node("/").createNode("mat")
    return mat


def find_target_model_context():
    active = get_active_network()
    if is_sop_network(active) or is_obj_network(active):
        return active
    node = active
    while node is not None:
        if is_sop_network(node) or is_obj_network(node):
            return node
        node = node.parent()
    return hou.node("/obj")


def node_network_size(node):
    try:
        size = node.size()
        return float(size.x()), float(size.y())
    except Exception:
        return 1.0, 0.5


def next_right_position(parent, fallback_y=0):
    children = list(parent.children())
    if not children:
        return hou.Vector2(0, fallback_y)

    right_edges = []
    bottom_edges = []
    top_edges = []
    for child in children:
        position = child.position()
        width, height = node_network_size(child)
        right_edges.append(position.x() + width)
        bottom_edges.append(position.y())
        top_edges.append(position.y() + height)

    vertical_center = (min(bottom_edges) + max(top_edges)) * 0.5
    return hou.Vector2(
        max(right_edges) + NETWORK_INSERT_GAP,
        vertical_center if top_edges else fallback_y,
    )


def create_node(parent, type_names, node_name):
    if isinstance(type_names, str):
        type_names = [type_names]
    last_error = None
    for type_name in type_names:
        try:
            return parent.createNode(type_name, uniquify_node_name(parent, node_name))
        except Exception as error:
            last_error = error
    raise RuntimeError("Could not create {0} in {1}: {2}".format(type_names, parent.path(), last_error))


def set_first_existing_parm(node, names, value):
    for name in names:
        parm = node.parm(name)
        if parm is not None:
            parm.set(value)
            return name
        tuple_parms = [node.parm("{0}{1}".format(name, suffix)) for suffix in ("r", "g", "b")]
        if all(tuple_parms):
            for parm_item in tuple_parms:
                parm_item.set(value)
            return name
    return None


def set_color_or_float_parm(node, names, value):
    for name in names:
        if isinstance(value, tuple):
            parm_tuple = node.parmTuple(name)
            if parm_tuple is not None and len(parm_tuple) == len(value):
                parm_tuple.set(value)
                return name
            tuple_parms = [node.parm("{0}{1}".format(name, suffix)) for suffix in ("r", "g", "b")]
            if all(tuple_parms):
                for parm_item, component in zip(tuple_parms, value):
                    parm_item.set(component)
                return name
            continue

        parm = node.parm(name)
        if parm is not None:
            parm.set(value)
            return name
    return None


def find_standard_surface_node(subnet):
    for child in subnet.children():
        type_name = child.type().name().lower()
        node_name = child.name().lower()
        if "standard" in type_name and "surface" in type_name:
            return child
        if "standard" in node_name and "surface" in node_name:
            return child
    return None


def input_index_by_normalized_name(node):
    try:
        names = list(node.inputNames())
    except Exception:
        return {}
    return dict((normalize_key(name), index) for index, name in enumerate(names))


def connect_to_first_named_input(target_node, source_node, input_names):
    input_map = input_index_by_normalized_name(target_node)
    for input_name in input_names:
        key = normalize_key(input_name)
        if key in input_map:
            target_node.setInput(input_map[key], source_node)
            return input_name
    return None


def connect_to_all_named_inputs(target_node, source_node, input_names):
    input_map = input_index_by_normalized_name(target_node)
    connected = []
    for input_name in input_names:
        key = normalize_key(input_name)
        if key in input_map:
            target_node.setInput(input_map[key], source_node)
            connected.append(input_name)
    return connected


def create_octane_material_builder(parent, material_name):
    try:
        import octane_material_builder
        before_nodes = set(parent.children())
        subnet = octane_material_builder.createMaskedOctaneSubnet(name=material_name, target_node=parent)
        if subnet is None:
            created = [node for node in parent.children() if node not in before_nodes]
            subnet = created[0] if created else None
        if subnet is None:
            raise RuntimeError("Octane material builder did not create a node.")
        subnet.setName(material_name, unique_name=True)
        return subnet
    except Exception:
        return create_node(parent, ["subnet"], material_name)


def texture_node_type_for_channel(channel):
    if normalize_key(channel) in FLOAT_TEXTURE_CHANNELS:
        return OCTANE_FLOAT_IMAGE_NODE_TYPE
    return OCTANE_IMAGE_NODE_TYPE


def color_space_for_channel(channel):
    if normalize_key(channel) in SRGB_CHANNELS:
        return COLOR_SPACE
    return NON_COLOR_SPACE


def create_transform_2d_node(subnet):
    transform_node = create_node(subnet, [OCTANE_TRANSFORM_2D_NODE_TYPE], "texture_transform_2d")
    transform_node.setPosition(hou.Vector2(-2, -4))
    return transform_node


def create_transform_3d_node(subnet, node_name):
    transform_node = create_node(subnet, [OCTANE_TRANSFORM_3D_NODE_TYPE], node_name)
    transform_node.setPosition(hou.Vector2(-4, -4))
    return transform_node


def connect_texture_transform(image_node, transform_node):
    if transform_node is not None:
        connect_to_first_named_input(image_node, transform_node, ["transform"])


def create_projection_setup(subnet, projection_mode):
    if projection_mode == PROJECTION_MODE_UV:
        return None, None

    transform_node = create_transform_3d_node(subnet, "projection_transform_3d")
    node_info = PROJECTION_NODE_TYPES.get(projection_mode)
    if node_info is None:
        return transform_node, None

    node_type, node_name = node_info
    projection_node = create_node(subnet, [node_type], node_name)
    projection_node.setPosition(hou.Vector2(-2, -6))
    connect_to_first_named_input(
        projection_node,
        transform_node,
        ["transform", "translation", "rotation", "scale", "rotationOrder", "matrix"],
    )
    return transform_node, projection_node


def connect_texture_projection(image_node, projection_node):
    if projection_node is not None:
        connect_to_first_named_input(image_node, projection_node, ["projection"])


def create_triplanar_texture_node(subnet, image_node, transform_3d_node, channel, index):
    triplanar_node = create_node(subnet, [OCTANE_TRIPLANAR_TEXTURE_NODE_TYPE], "{0}_triplanar".format(sanitize_node_name(channel)))
    triplanar_node.setPosition(image_node.position() + hou.Vector2(0, -2))
    connect_to_all_named_inputs(
        triplanar_node,
        image_node,
        [
            "texture",
            "texturePosX",
            "textureNegX",
            "texturePosY",
            "textureNegY",
            "texturePosZ",
            "textureNegZ",
            "textureX",
            "textureY",
            "textureZ",
        ],
    )
    if transform_3d_node is not None:
        connect_to_first_named_input(
            triplanar_node,
            transform_3d_node,
            ["transform", "translation", "rotation", "scale", "rotationOrder", "matrix"],
        )
    return triplanar_node


def height_texture_channel(material):
    for channel in ("displacement", "height", "bump"):
        if channel in material.get("textures", {}):
            return channel
    return None


def is_height_channel(channel):
    return normalize_key(channel) in HEIGHT_TEXTURE_CHANNELS


def displacement_settings_dict(height_settings):
    if isinstance(height_settings, dict):
        return {
            "mode": height_settings.get("mode", HEIGHT_MODE_TEXTURE_DISPLACEMENT),
            "height": height_settings.get("height", DEFAULT_DISPLACEMENT_HEIGHT),
            "create_disconnected": bool(height_settings.get("create_disconnected", DEFAULT_DISPLACEMENT_CREATE_DISCONNECTED)),
        }
    return {
        "mode": height_settings or HEIGHT_MODE_TEXTURE_DISPLACEMENT,
        "height": DEFAULT_DISPLACEMENT_HEIGHT,
        "create_disconnected": DEFAULT_DISPLACEMENT_CREATE_DISCONNECTED,
    }


def create_displacement_node(subnet, image_node, height_settings):
    settings = displacement_settings_dict(height_settings)
    height_mode = settings["mode"]
    if height_mode == HEIGHT_MODE_VERTEX_DISPLACEMENT:
        node_type = OCTANE_VERTEX_DISPLACEMENT_NODE_TYPE
        node_name = "vertex_displacement"
    else:
        node_type = OCTANE_TEXTURE_DISPLACEMENT_NODE_TYPE
        node_name = "texture_displacement"

    displacement_node = create_node(subnet, [node_type], node_name)
    displacement_node.setPosition(image_node.position() + hou.Vector2(0, -2))
    connect_to_first_named_input(displacement_node, image_node, ["texture"])
    set_first_existing_parm(displacement_node, ["amount"], settings["height"])
    set_first_existing_parm(displacement_node, ["black_level"], 0.5)
    return displacement_node


def connect_height_texture(standard_surface, subnet, image_node, height_settings):
    settings = displacement_settings_dict(height_settings)
    height_mode = settings["mode"]
    if standard_surface is None:
        return
    if height_mode == HEIGHT_MODE_BUMP:
        if not settings["create_disconnected"]:
            connect_to_first_named_input(standard_surface, image_node, ["bump"])
        return

    displacement_node = create_displacement_node(subnet, image_node, settings)
    if not settings["create_disconnected"]:
        connect_to_first_named_input(standard_surface, displacement_node, ["displacement"])


def material_has_emission_enabled(material):
    emission = material.get("inputs", {}).get("emission", 0)
    try:
        return float(emission) > 0
    except Exception:
        return False


def connect_emission_color_fallback(standard_surface, material, image_nodes):
    if standard_surface is None or not material_has_emission_enabled(material):
        return
    if "emission_color" in image_nodes or "emissive" in image_nodes:
        return
    basecolor_node = image_nodes.get("base_color") or image_nodes.get("basecolor")
    if basecolor_node is not None:
        connect_to_first_named_input(standard_surface, basecolor_node, ["emissionColor"])


def create_texture_nodes(subnet, standard_surface, material, height_mode, projection_mode):
    image_nodes = {}
    transform_2d_node = None
    transform_3d_node = None
    projection_node = None
    if material["textures"]:
        if projection_mode == PROJECTION_MODE_TRIPLANAR:
            transform_3d_node = create_transform_3d_node(subnet, "triplanar_transform_3d")
        else:
            transform_2d_node = create_transform_2d_node(subnet)
            transform_3d_node, projection_node = create_projection_setup(subnet, projection_mode)

    for index, channel in enumerate(sorted(material["textures"].keys())):
        texture_path = material["textures"][channel]
        image_node = create_node(subnet, [texture_node_type_for_channel(channel)], sanitize_node_name(channel))
        image_node.setPosition(hou.Vector2(index * 2, -2))
        set_first_existing_parm(image_node, ["A_FILENAME", "filename", "file"], texture_path)
        set_first_existing_parm(image_node, ["colorSpace", "colorspace"], color_space_for_channel(channel))
        texture_settings = material.get("texture_settings", {}).get(channel, {})
        if "power" in texture_settings:
            set_first_existing_parm(image_node, ["power", "A_POWER"], texture_settings["power"])
        connect_texture_transform(image_node, transform_2d_node)
        connect_texture_projection(image_node, projection_node)

        output_node = image_node
        if projection_mode == PROJECTION_MODE_TRIPLANAR:
            output_node = create_triplanar_texture_node(subnet, image_node, transform_3d_node, channel, index)

        if is_height_channel(channel):
            connect_height_texture(standard_surface, subnet, output_node, height_mode)
        else:
            input_names = TEXTURE_CHANNEL_INPUTS.get(channel, [channel])
            if standard_surface is not None:
                connect_to_first_named_input(standard_surface, output_node, input_names)
        image_nodes[channel] = output_node
    connect_emission_color_fallback(standard_surface, material, image_nodes)
    return image_nodes


def apply_material_inputs(standard_surface, material):
    if standard_surface is None:
        return
    for usd_name, value in material.get("inputs", {}).items():
        key = normalize_key(usd_name)
        parm_names = USD_TO_OCTANE_INPUTS.get(key)
        if parm_names:
            set_color_or_float_parm(standard_surface, parm_names, value)


def set_preview_texture(subnet, material):
    basecolor = material["textures"].get("base_color") or material["textures"].get("basecolor")
    if not basecolor:
        return
    set_first_existing_parm(subnet, ["ogl_use_tex1"], 1)
    set_first_existing_parm(subnet, ["ogl_tex1"], basecolor)


def create_octane_material(parent, material, height_mode, projection_mode):
    result = openToolsMaterialUtils.createOctaneMaterial(
        parent,
        name=material["name"],
        textures=material.get("textures", {}),
        parameters=material.get("inputs", {}),
        projection={"mode": projection_mode},
        displacement=height_mode,
        options={
            "material_spec": material,
            "texture_settings": material.get("texture_settings", {}),
            "allow_unknown_parameters": False,
        },
    )
    return result["subnet"]


def set_material_assignment(material_node, slot_index, usd_material_path, material_path):
    parm_index = slot_index + 1
    set_first_existing_parm(material_node, ["group{0}".format(parm_index), "group{0}".format(slot_index)], '@usdmaterialpath={0}'.format(usd_material_path))
    set_first_existing_parm(
        material_node,
        ["shop_materialpath{0}".format(parm_index), "shop_materialpath{0}".format(slot_index)],
        material_path,
    )


def create_usd_import_column(parent, model_record, material_refs, height_mode, projection_mode, preferred_texture_variants, position):
    node_name = sanitize_node_name(model_record["name"])
    matnet = create_node(parent, ["matnet", "materialnet"], "MATNET_{0}".format(node_name))
    import_node = create_node(parent, ["usdimport"], "IMPORT_{0}".format(node_name))
    material_node = create_node(parent, ["material"], "MATERIAL_{0}".format(node_name))
    out_node = create_node(parent, ["null"], "OUT_{0}".format(node_name))

    # Position only this newly-created column. Existing network items must
    # never be moved by a parent-wide layout operation.
    matnet.setPosition(hou.Vector2(position.x(), position.y() + 2.0))
    import_node.setPosition(position)
    material_node.setPosition(hou.Vector2(position.x(), position.y() - 1.5))
    out_node.setPosition(hou.Vector2(position.x(), position.y() - 3.0))

    set_first_existing_parm(import_node, ["filepath1", "filepath", "file"], model_record["path"])
    set_first_existing_parm(import_node, ["input_unpack"], 1)
    set_first_existing_parm(import_node, ["unpack_geomtype"], 1)

    material_node.setInput(0, import_node)
    out_node.setInput(0, material_node)
    out_node.setDisplayFlag(True)
    out_node.setRenderFlag(True)

    material_paths = {}
    for material_ref in material_refs:
        if not os.path.exists(material_ref["path"]):
            print("Missing material USD for {0}: {1}".format(material_ref["name"], material_ref["path"]))
            continue
        material = material_record_from_usd(material_ref, preferred_texture_variants)
        mat_node = create_octane_material(matnet, material, height_mode, projection_mode)
        material_paths[material_ref["name"]] = mat_node.path()

    set_first_existing_parm(material_node, ["num_materials"], len(material_refs))
    for index, material_ref in enumerate(material_refs):
        material_path = material_paths.get(material_ref["name"])
        if material_path is None:
            continue
        material_node_ref = hou.node(material_path)
        if material_node_ref is not None:
            material_path = material_node.relativePathTo(material_node_ref)
        set_material_assignment(material_node, index, material_ref["usdmaterialpath"], material_path)

    matnet.layoutChildren()
    return {
        "matnet": matnet,
        "import": import_node,
        "material": material_node,
        "out": out_node,
    }


def create_geo_container(obj_network, name, position):
    geo_node = create_node(obj_network, ["geo"], name)
    geo_node.setPosition(position)
    for child in list(geo_node.children()):
        child.destroy()
    return geo_node


def create_model_import(model_record, target_context, asset_index, height_mode, projection_mode, preferred_texture_variants, start_position):
    material_refs = parse_model_material_references(model_record)
    if not material_refs:
        print("No material references found for {0}".format(model_record["name"]))

    if is_obj_network(target_context):
        geo_position = hou.Vector2(
            start_position.x() + (asset_index * MODEL_COLUMN_SPACING),
            start_position.y(),
        )
        geo = create_geo_container(target_context, sanitize_node_name(model_record["name"]), geo_position)
        return create_usd_import_column(
            geo,
            model_record,
            material_refs,
            height_mode,
            projection_mode,
            preferred_texture_variants,
            hou.Vector2(0, 0),
        )

    column_position = hou.Vector2(
        start_position.x() + (asset_index * MODEL_COLUMN_SPACING),
        start_position.y(),
    )
    return create_usd_import_column(
        target_context,
        model_record,
        material_refs,
        height_mode,
        projection_mode,
        preferred_texture_variants,
        column_position,
    )


def tree_path_component(value):
    return str(value or "Unknown").replace("/", "_").replace("\\", "_")


def record_tree_path(record):
    return "{0}/{1}/{2}".format(
        tree_path_component(record.get("kit")),
        tree_path_component(record.get("version")),
        tree_path_component(record.get("name")),
    )


def choose_records_from_tree(records, message, title):
    path_to_record = {}
    choices = []
    for record in records:
        path = record_tree_path(record)
        path_to_record[path] = record
        choices.append(path)

    selected_paths = hou.ui.selectFromTree(
        choices,
        message=message,
        title=title,
        clear_on_cancel=True,
        width=700,
        height=600,
        allow_branch_selection=True,
        allow_compound_selection=True,
    )
    selected_records = []
    seen_paths = set()
    for path in selected_paths:
        if path in path_to_record:
            if path not in seen_paths:
                selected_records.append(path_to_record[path])
                seen_paths.add(path)
            continue
        prefix = path.rstrip("/") + "/"
        for choice in choices:
            if choice.startswith(prefix) and choice not in seen_paths:
                selected_records.append(path_to_record[choice])
                seen_paths.add(choice)
    return selected_records


def choose_materials(records):
    return choose_records_from_tree(
        records,
        message="Select Cargo USD materials to create as Octane materials",
        title="Cargo Materials",
    )


def choose_models(records):
    return choose_records_from_tree(
        records,
        message="Select Cargo USD models to import",
        title="Cargo Models",
    )


def choose_import_mode():
    choice = hou.ui.displayMessage(
        "What do you want to load?",
        buttons=("Materials", "Models"),
        default_choice=0,
        close_choice=0,
        title="Cargo Importer",
    )
    return "models" if choice == 1 else "materials"


def choose_height_mode():
    try:
        return choose_height_mode_dialog()
    except Exception:
        pass

    choice = hou.ui.displayMessage(
        "How should height maps be connected?",
        buttons=("Texture displacement", "Vertex displacement", "Bump"),
        default_choice=0,
        close_choice=0,
        title="Cargo Height Maps",
    )
    if choice == 1:
        mode = HEIGHT_MODE_VERTEX_DISPLACEMENT
    elif choice == 2:
        mode = HEIGHT_MODE_BUMP
    else:
        mode = HEIGHT_MODE_TEXTURE_DISPLACEMENT

    height = DEFAULT_DISPLACEMENT_HEIGHT
    if mode != HEIGHT_MODE_BUMP:
        try:
            height_string = hou.ui.readInput(
                "Displacement height",
                buttons=("OK",),
                initial_contents=str(DEFAULT_DISPLACEMENT_HEIGHT),
                title="Cargo Height Maps",
            )[1]
            height = float(height_string)
        except Exception:
            height = DEFAULT_DISPLACEMENT_HEIGHT

    disconnected_choice = hou.ui.displayMessage(
        "Create height/displacement nodes disconnected?",
        buttons=("Connect", "Create disconnected"),
        default_choice=0 if not DEFAULT_DISPLACEMENT_CREATE_DISCONNECTED else 1,
        close_choice=0,
        title="Cargo Height Maps",
    )
    return {
        "mode": mode,
        "height": height,
        "create_disconnected": disconnected_choice == 1,
    }


def choose_height_mode_dialog():
    try:
        from PySide6 import QtCore, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtWidgets

    parent = None
    if hou is not None and hasattr(hou, "qt"):
        parent = hou.qt.mainWindow()

    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle("Cargo Height Maps")
    dialog.setModal(True)

    mode_combo = QtWidgets.QComboBox(dialog)
    mode_combo.addItem("Texture displacement", HEIGHT_MODE_TEXTURE_DISPLACEMENT)
    mode_combo.addItem("Vertex displacement", HEIGHT_MODE_VERTEX_DISPLACEMENT)
    mode_combo.addItem("Bump", HEIGHT_MODE_BUMP)

    height_spin = QtWidgets.QDoubleSpinBox(dialog)
    height_spin.setDecimals(6)
    height_spin.setRange(-1000000.0, 1000000.0)
    height_spin.setSingleStep(0.001)
    height_spin.setValue(float(DEFAULT_DISPLACEMENT_HEIGHT))

    disconnected_check = QtWidgets.QCheckBox("Create disconnected", dialog)
    disconnected_check.setChecked(bool(DEFAULT_DISPLACEMENT_CREATE_DISCONNECTED))

    form = QtWidgets.QFormLayout()
    form.addRow("Height map mode", mode_combo)
    form.addRow("Displacement height", height_spin)
    form.addRow("", disconnected_check)

    buttons = QtWidgets.QDialogButtonBox(
        QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
        QtCore.Qt.Horizontal,
        dialog,
    )
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)

    layout = QtWidgets.QVBoxLayout(dialog)
    layout.addWidget(QtWidgets.QLabel("Choose how Cargo height maps should be created.", dialog))
    layout.addLayout(form)
    layout.addWidget(buttons)

    def update_height_enabled(index):
        height_spin.setEnabled(mode_combo.itemData(index) != HEIGHT_MODE_BUMP)

    mode_combo.currentIndexChanged.connect(update_height_enabled)
    update_height_enabled(mode_combo.currentIndex())

    exec_method = getattr(dialog, "exec", None) or getattr(dialog, "exec_", None)
    if exec_method() != QtWidgets.QDialog.Accepted:
        return {
            "mode": HEIGHT_MODE_TEXTURE_DISPLACEMENT,
            "height": DEFAULT_DISPLACEMENT_HEIGHT,
            "create_disconnected": DEFAULT_DISPLACEMENT_CREATE_DISCONNECTED,
        }

    return {
        "mode": mode_combo.currentData(),
        "height": float(height_spin.value()),
        "create_disconnected": disconnected_check.isChecked(),
    }


def choose_projection_mode():
    choices = [
        ("UV", PROJECTION_MODE_UV),
        ("Triplanar", PROJECTION_MODE_TRIPLANAR),
        ("Box", PROJECTION_MODE_BOX),
        ("XYZ to UVW", PROJECTION_MODE_LINEAR),
        ("Cylindrical", PROJECTION_MODE_CYLINDRICAL),
        ("Spherical", PROJECTION_MODE_SPHERICAL),
        ("Perspective", PROJECTION_MODE_PERSPECTIVE),
    ]
    selected = hou.ui.selectFromList(
        [label for label, _mode in choices],
        message="Select texture projection mode",
        title="Cargo Texture Projection",
        column_header="Projection",
        num_visible_rows=len(choices),
        exclusive=True,
    )
    if not selected:
        return PROJECTION_MODE_UV
    return choices[selected[0]][1]


def print_material_list(records):
    for index, record in enumerate(records):
        print("{0:03d}: {1} | {2} | {3}".format(index, record["kit"], record["version"], record["name"]))


def print_model_list(records):
    for index, record in enumerate(records):
        print("{0:03d}: {1} | {2} | {3}".format(index, record["kit"], record["version"], record["name"]))


def run_material_import(root_folder, preferred_texture_variants, selected_indices=None, height_mode=None, projection_mode=None):
    records = scan_material_usds(root_folder)
    if not records:
        hou.ui.displayMessage("No Cargo material USD files found under:\n{0}".format(root_folder))
        return []

    print_material_list(records)
    if selected_indices is None:
        selected_records = choose_materials(records)
    else:
        selected_records = [records[index] for index in selected_indices]

    if not selected_records:
        print("No materials selected.")
        return []

    if height_mode is None:
        height_mode = choose_height_mode()
    if projection_mode is None:
        projection_mode = choose_projection_mode()

    parent = find_target_material_context()
    created = []
    for record in selected_records:
        material = material_record_from_usd(record, preferred_texture_variants)
        node = create_octane_material(parent, material, height_mode, projection_mode)
        created.append(node)
        print("Created {0} from {1} ({2})".format(node.path(), material["path"], material.get("texture_variant")))

    parent.layoutChildren()
    hou.ui.displayMessage("Created {0} Cargo Octane material(s) in {1}.".format(len(created), parent.path()))
    return created


def run_model_import(root_folder, preferred_texture_variants, selected_indices=None, height_mode=None, projection_mode=None):
    records = scan_model_usds(root_folder)
    if not records:
        hou.ui.displayMessage("No Cargo model geo.usd files found under:\n{0}".format(root_folder))
        return []

    print_model_list(records)
    if selected_indices is None:
        selected_records = choose_models(records)
    else:
        selected_records = [records[index] for index in selected_indices]

    if not selected_records:
        print("No models selected.")
        return []

    if height_mode is None:
        height_mode = choose_height_mode()
    projection_mode = PROJECTION_MODE_UV

    target_context = find_target_model_context()
    start_position = next_right_position(target_context, fallback_y=0)
    created = []
    for index, model_record in enumerate(selected_records):
        created.append(create_model_import(
            model_record,
            target_context,
            index,
            height_mode,
            projection_mode,
            preferred_texture_variants,
            start_position,
        ))

    hou.ui.displayMessage("Imported {0} Cargo model(s) in {1}.".format(len(created), target_context.path()))
    return created


def run(root_folder=CARGO_ROOT, preferred_texture_variants=None, selected_indices=None, height_mode=None, projection_mode=None, import_mode=None):
    if hou is None:
        raise RuntimeError("This script must run inside Houdini.")

    if root_folder is None:
        root_folder = choose_cargo_root_directory()
    if not root_folder:
        print("No Cargo directory selected.")
        return None

    preferred_texture_variants = preferred_texture_variants or PREFERRED_TEXTURE_VARIANTS
    import_mode = import_mode or choose_import_mode()
    if import_mode == "models":
        return run_model_import(
            root_folder,
            preferred_texture_variants,
            selected_indices=selected_indices,
            height_mode=height_mode,
            projection_mode=projection_mode,
        )
    return run_material_import(
        root_folder,
        preferred_texture_variants,
        selected_indices=selected_indices,
        height_mode=height_mode,
        projection_mode=projection_mode,
    )


def main(kwargs=None):
    kwargs = kwargs or {}
    return run(
        root_folder=kwargs.get("root_folder"),
        preferred_texture_variants=kwargs.get("preferred_texture_variants"),
        selected_indices=kwargs.get("selected_indices"),
        height_mode=kwargs.get("height_mode"),
        projection_mode=kwargs.get("projection_mode"),
        import_mode=kwargs.get("import_mode"),
    )


def should_auto_run():
    if hou is None:
        return __name__ == "__main__"
    if __name__ in ("__main__", "__builtin__", "builtins"):
        return True
    return False


if should_auto_run():
    run()
