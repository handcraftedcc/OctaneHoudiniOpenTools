"""
PlantFactory FBX material/texture importer for Houdini.

Part 1 only:
- Parse a PlantFactory asset folder.
- Find regular and billboard FBX files.
- Import each FBX into a temporary File SOP and read unique shop_materialpath values.
- Group texture files into texture sets.
- Match FBX material names to texture sets by progressively stripping underscore sections.
- Log material registry, old-to-new material mapping, unmapped materials, and unused textures.
- Optionally create Houdini import/material-assignment SOP networks.

Import this module from a Houdini shelf tool and call main(kwargs), or place it
somewhere on sys.path and call run().
"""

from __future__ import print_function

import os
import re

try:
    import hou
except ImportError:
    hou = None

import openToolsMaterialUtils


ENABLE_LOG = True
CREATE_HOUDINI_NODES = True
LOG_FILENAME = "plantfactory_material_registry.log"
SIDE_SUFFIXES = set(["front", "back", "left", "right"])
OCTANE_IMAGE_NODE_TYPE = "octane::NT_TEX_IMAGE"
SRGB_CHANNELS = set(["basecolor", "backlight"])
NON_COLOR_SPACE = "NAMED_COLOR_SPACE_OTHER"
COLOR_SPACE = "NAMED_COLOR_SPACE_SRGB"
NODE_COLUMN_SPACING = 5
NODE_ROW_SPACING = 1
NODE_APPEND_PADDING = 4
LEAF_MATERIAL_KEYWORDS = set(["leaf", "leaves", "need", "needle", "needles", "frond", "blade"])

IMAGE_EXTENSIONS = set([".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".bmp", ".tga"])

CHANNEL_ALIASES = {
    "color": "basecolor",
    "basecolor": "basecolor",
    "albedo": "basecolor",
    "diffuse": "basecolor",
    "normal": "normal",
    "nrm": "normal",
    "opacity": "opacity",
    "alpha": "opacity",
    "transparency": "opacity",
    "specular": "specular",
    "spec": "specular",
    "roughness": "roughness",
    "rough": "roughness",
    "ambientocclusion": "ao",
    "occlusion": "ao",
    "ao": "ao",
    "backlight": "backlight",
    "backlighting": "backlight",
    "metallic": "metallic",
    "metalness": "metallic",
    "height": "height",
    "bump": "height",
    "displacement": "height",
}

CHANNEL_QUALIFIERS = set([
    "a",
    "alpha",
    "opacity",
    "trans",
    "tr",
    "hl",
    "highlight",
    "spec",
    "bl",
    "backlight",
    "rt",
])


class RegistryLogger(object):
    def __init__(self, log_path=None):
        self.log_path = log_path
        self._handle = None
        if log_path:
            self._handle = open(log_path, "w")

    def write(self, message=""):
        message = str(message)
        if self._handle is not None:
            self._handle.write(message + "\n")
        else:
            print(message)

    def close(self):
        if self._handle is not None:
            self._handle.close()
            self._handle = None


def create_logger(root_folder=None):
    if ENABLE_LOG and root_folder:
        log_path = os.path.join(os.path.normpath(root_folder), LOG_FILENAME)
        return RegistryLogger(log_path), log_path
    return RegistryLogger(), None


def sanitize_node_name(name):
    """Return a Houdini node-safe name while preserving readability."""
    cleaned = re.sub(r"[^0-9A-Za-z_]+", "_", name.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        return "unnamed"
    if cleaned[0].isdigit():
        cleaned = "_" + cleaned
    return cleaned


def uniquify_node_name(parent, name):
    clean_name = sanitize_node_name(name)
    if parent.node(clean_name) is None:
        return clean_name

    index = 1
    while parent.node("{0}_{1}".format(clean_name, index)) is not None:
        index += 1
    return "{0}_{1}".format(clean_name, index)


def normalize_key(value):
    return re.sub(r"[^0-9a-z]+", "_", value.lower()).strip("_")


def material_leaf(shop_materialpath):
    material_name = str(shop_materialpath or "").strip()
    if not material_name:
        return ""
    material_name = material_name.replace("\\", "/").split("/")[-1]
    if material_name.startswith("Material::"):
        material_name = material_name.split("::", 1)[1]
    return material_name


def is_billboard_fbx(path):
    return "_billboard" in os.path.splitext(os.path.basename(path))[0].lower()


def find_asset_files(asset_folder):
    fbx_files = []
    texture_files = []

    for root, _dirs, files in os.walk(asset_folder):
        for filename in files:
            path = os.path.join(root, filename)
            ext = os.path.splitext(filename)[1].lower()
            if ext == ".fbx":
                fbx_files.append(path)
            elif ext in IMAGE_EXTENSIONS:
                texture_files.append(path)

    fbx_files.sort(key=lambda p: (is_billboard_fbx(p), os.path.basename(p).lower()))
    texture_files.sort(key=lambda p: os.path.basename(p).lower())
    return fbx_files, texture_files


def classify_texture_channel(stem):
    tokens = stem.split("_")
    for token_count in (2, 1):
        if len(tokens) <= token_count:
            continue
        channel_token = "".join(tokens[-token_count:]).lower()
        channel = CHANNEL_ALIASES.get(channel_token)
        if channel:
            return channel, "_".join(tokens[:-token_count])
    return "unknown", stem


def normalize_texture_set_name(raw_set_name):
    tokens = raw_set_name.split("_")
    while tokens and tokens[-1].lower() in CHANNEL_QUALIFIERS:
        tokens.pop()
    return "_".join(tokens) if tokens else raw_set_name


def make_texture_record(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    channel, raw_set_name = classify_texture_channel(stem)
    set_name = normalize_texture_set_name(raw_set_name)
    return {
        "path": path,
        "stem": stem,
        "stem_key": normalize_key(stem),
        "channel": channel,
        "raw_set_name": raw_set_name,
        "set_name": set_name,
        "set_key": normalize_key(set_name),
    }


def build_texture_records(texture_files):
    return [make_texture_record(path) for path in texture_files]


def build_texture_sets(texture_records, logger=None):
    texture_sets = {}

    for record in texture_records:
        path = record["path"]
        channel = record["channel"]
        raw_set_name = record["raw_set_name"]
        set_name = record["set_name"]
        key = record["set_key"]

        if key not in texture_sets:
            texture_sets[key] = {
                "name": set_name,
                "raw_names": [],
                "textures": {},
                "files": [],
            }

        entry = texture_sets[key]
        if raw_set_name not in entry["raw_names"]:
            entry["raw_names"].append(raw_set_name)
        entry["files"].append(path)

        if channel not in entry["textures"]:
            entry["textures"][channel] = path
        else:
            existing = os.path.basename(entry["textures"][channel])
            incoming = os.path.basename(path)
            entry["textures"][channel + "_duplicate_" + str(len(entry["textures"]))] = path
            message = "Warning: duplicate channel for {0}: {1} and {2}".format(set_name, existing, incoming)
            if logger is not None:
                logger.write(message)
            else:
                RegistryLogger().write(message)

    return texture_sets


def material_match_candidates(material_name):
    aliases = []
    cleaned_name = re.sub(r"\([0-9]+\)", "", material_name)
    cleaned_name = re.sub(r"\s+", " ", cleaned_name).strip()

    for name in (material_name, cleaned_name):
        if name and name not in aliases:
            aliases.append(name)

    cleaned_parts = cleaned_name.split("_")
    if len(cleaned_parts) > 1 and cleaned_parts[-1].lower() in SIDE_SUFFIXES:
        no_side = "_".join(cleaned_parts[:-1])
        if no_side and no_side not in aliases:
            aliases.append(no_side)

    parts = material_name.split("_")
    candidates = []
    for end in range(len(parts), 0, -1):
        candidate = "_".join(parts[:end])
        if candidate and candidate not in candidates:
            candidates.append(candidate)

        candidate_clean = re.sub(r"\([0-9]+\)", "", candidate)
        candidate_clean = re.sub(r"\s+", " ", candidate_clean).strip()
        if candidate_clean and candidate_clean not in candidates:
            candidates.append(candidate_clean)

        candidate_parts = candidate_clean.split("_")
        if len(candidate_parts) > 1 and candidate_parts[-1].lower() in SIDE_SUFFIXES:
            candidate_no_side = "_".join(candidate_parts[:-1])
            if candidate_no_side and candidate_no_side not in candidates:
                candidates.append(candidate_no_side)

    for alias in aliases:
        if alias and alias not in candidates:
            candidates.append(alias)

    return candidates


def score_texture_record(texture_record, candidate_key):
    set_key = texture_record["set_key"]
    stem_key = texture_record["stem_key"]
    if set_key == candidate_key:
        return 100000
    if set_key.endswith("_" + candidate_key):
        return 90000 - len(set_key)
    if ("_" + candidate_key + "_") in ("_" + set_key + "_"):
        return 80000 - len(set_key)
    if candidate_key in set_key:
        return 70000 - len(set_key)
    if ("_" + candidate_key + "_") in ("_" + stem_key + "_"):
        return 60000 - len(stem_key)
    if candidate_key in stem_key:
        return 50000 - len(stem_key)
    return -1


def find_texture_records_for_material(material_name, texture_records):
    candidate_matches = []

    for candidate in material_match_candidates(material_name):
        candidate_key = normalize_key(candidate)
        matches = []

        for texture_record in texture_records:
            score = score_texture_record(texture_record, candidate_key)
            if score >= 0:
                matches.append((score, texture_record))

        if matches:
            matches.sort(key=lambda item: (-item[0], len(item[1]["set_name"]), item[1]["set_name"].lower()))
            records = [item[1] for item in matches]
            channels = set([record["channel"] for record in records])
            duplicate_count = len(records) - len(channels)
            candidate_matches.append({
                "candidate": candidate,
                "candidate_key": candidate_key,
                "records": records,
                "channel_count": len(channels),
                "duplicate_count": duplicate_count,
            })

    if candidate_matches:
        chosen = candidate_matches[0]
        chosen_records = list(chosen["records"])
        chosen_channels = set([record["channel"] for record in chosen_records])

        # Keep the most specific material as the registry material. If a variant
        # only has its own color texture, borrow missing channels from broader
        # parent candidates instead of collapsing the material into the parent.
        if len(chosen_channels) <= 1:
            sibling_records = find_first_numbered_sibling_records(chosen["candidate_key"], texture_records)
            for record in sibling_records:
                if record["channel"] not in chosen_channels:
                    chosen_records.append(record)
                    chosen_channels.add(record["channel"])

            for fallback in candidate_matches[1:]:
                for record in fallback["records"]:
                    if record["channel"] not in chosen_channels:
                        chosen_records.append(record)
                        chosen_channels.add(record["channel"])

        chosen["records"] = chosen_records
        chosen["channel_count"] = len(chosen_channels)
        chosen["duplicate_count"] = len(chosen_records) - len(chosen_channels)
        return chosen

    return None


def find_first_numbered_sibling_records(candidate_key, texture_records):
    first_variant_key = re.sub(r"(^|_)n[2-9][0-9]*(_|$)", r"\1n1\2", candidate_key)
    if first_variant_key == candidate_key:
        return []

    matches = []
    for texture_record in texture_records:
        score = score_texture_record(texture_record, first_variant_key)
        if score >= 0:
            matches.append((score, texture_record))

    matches.sort(key=lambda item: (-item[0], len(item[1]["set_name"]), item[1]["set_name"].lower()))
    return [item[1] for item in matches]


def choose_channel_textures(texture_records):
    textures = {}
    duplicates = {}

    for record in texture_records:
        channel = record["channel"]
        if channel not in textures:
            textures[channel] = record["path"]
        else:
            duplicates.setdefault(channel, []).append(record["path"])

    return textures, duplicates


def create_probe_container(parent):
    node_name = "__plantfactory_material_probe__"
    existing = parent.node(node_name)
    if existing is not None:
        existing.destroy()
    container = parent.createNode("geo", node_name)
    container.setDisplayFlag(False)
    return container


def read_fbx_materials_with_file_sop(fbx_path, probe_container, logger=None):
    if logger is not None:
        logger.write("  Probing FBX materials: {0}".format(fbx_path))

    file_node = probe_container.createNode("file", sanitize_node_name(os.path.basename(fbx_path)))
    file_parm = file_node.parm("file")
    if file_parm is None:
        raise RuntimeError("Created File SOP has no 'file' parameter.")

    file_parm.set(fbx_path)
    file_node.cook(force=True)
    geo = file_node.geometry()
    attrib = geo.findPrimAttrib("shop_materialpath")

    materials = []
    if attrib is not None:
        seen = set()
        for prim in geo.prims():
            name = material_leaf(prim.attribValue(attrib))
            if name and name not in seen:
                seen.add(name)
                materials.append(name)

    file_node.destroy()
    materials.sort(key=lambda value: value.lower())

    if logger is not None:
        logger.write("    Materials found: {0}".format(len(materials)))

    return materials


def read_fbx_materials(fbx_files, keep_probe_nodes=False, logger=None):
    if hou is None:
        raise RuntimeError("This script must run inside Houdini to read FBX shop_materialpath values.")

    parent = hou.node("/obj")
    probe = create_probe_container(parent)
    materials_by_fbx = {}

    try:
        if logger is not None:
            logger.write("")
            logger.write("FBX material probe:")

        for fbx_path in fbx_files:
            materials_by_fbx[fbx_path] = read_fbx_materials_with_file_sop(fbx_path, probe, logger=logger)
    finally:
        if not keep_probe_nodes and probe is not None:
            probe.destroy()
            if logger is not None:
                logger.write("  Removed temporary FBX probe nodes.")

    return materials_by_fbx


def build_material_registry(materials_by_fbx, texture_records):
    registry = {}
    material_mapping = {}
    unmapped_materials = []
    used_texture_paths = set()

    all_materials = []
    for _fbx_path, materials in materials_by_fbx.items():
        for material in materials:
            if material not in all_materials:
                all_materials.append(material)

    for material in sorted(all_materials, key=lambda value: value.lower()):
        match = find_texture_records_for_material(material, texture_records)
        if match is None:
            unmapped_materials.append(material)
            continue

        registry_key = match["candidate_key"]
        new_material = match["candidate"]
        textures, duplicates = choose_channel_textures(match["records"])

        if registry_key not in registry:
            registry[registry_key] = {
                "new_material": new_material,
                "assigned_material_names": [],
                "matched_texture_sets": [],
                "textures": textures,
                "duplicate_textures": duplicates,
            }

        entry = registry[registry_key]
        if material not in entry["assigned_material_names"]:
            entry["assigned_material_names"].append(material)
        material_mapping[material] = entry["new_material"]

        for record in match["records"]:
            used_texture_paths.add(record["path"])
            if record["set_name"] not in entry["matched_texture_sets"]:
                entry["matched_texture_sets"].append(record["set_name"])

    unused_texture_records = []
    for record in texture_records:
        if record["path"] not in used_texture_paths:
            unused_texture_records.append(record)

    registry_list = list(registry.values())
    registry_list.sort(key=lambda entry: entry["new_material"].lower())
    unused_texture_records.sort(key=lambda entry: entry["stem"].lower())
    unmapped_materials.sort(key=lambda value: value.lower())

    return registry_list, material_mapping, unmapped_materials, unused_texture_records


def print_registry_report(asset_folder, fbx_files, materials_by_fbx, registry, material_mapping, unmapped_materials, unused_texture_records, logger):
    logger.write("")
    logger.write("=" * 100)
    logger.write("PlantFactory material registry")
    logger.write("Asset folder: {0}".format(asset_folder))
    logger.write("=" * 100)

    logger.write("")
    logger.write("FBX files:")
    for fbx_path in fbx_files:
        label = "billboard" if is_billboard_fbx(fbx_path) else "model"
        logger.write("  [{0}] {1}".format(label, fbx_path))
        for material in materials_by_fbx.get(fbx_path, []):
            logger.write("      - {0}".format(material))

    logger.write("")
    logger.write("Material registry:")
    if not registry:
        logger.write("  <none>")
    for entry in registry:
        logger.write("  {0}".format(entry["new_material"]))
        logger.write("    AssignedMaterialNames: {0}".format(", ".join(entry["assigned_material_names"])))
        logger.write("    MatchedTextureSets: {0}".format(", ".join(entry["matched_texture_sets"])))
        for channel in sorted(entry["textures"].keys()):
            logger.write("    {0}: {1}".format(channel, entry["textures"][channel]))
        for channel in sorted(entry["duplicate_textures"].keys()):
            for path in entry["duplicate_textures"][channel]:
                logger.write("    duplicate_{0}: {1}".format(channel, path))

    logger.write("")
    logger.write("Material mapping (old -> new):")
    if not material_mapping:
        logger.write("  <none>")
    for old_material in sorted(material_mapping.keys(), key=lambda value: value.lower()):
        logger.write("  {0} -> {1}".format(old_material, material_mapping[old_material]))

    logger.write("")
    logger.write("Unmapped FBX materials:")
    if not unmapped_materials:
        logger.write("  <none>")
    for material in unmapped_materials:
        logger.write("  {0}".format(material))

    logger.write("")
    logger.write("Unused textures:")
    if not unused_texture_records:
        logger.write("  <none>")
    for record in unused_texture_records:
        logger.write("  {0} [{1}]".format(record["path"], record["channel"]))

    logger.write("=" * 100)
    logger.write("")


def get_active_network():
    if hou is None:
        raise RuntimeError("Houdini is required for node creation.")

    network_editor = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
    if network_editor is not None:
        return network_editor.pwd()
    return hou.node("/obj")


def is_obj_network(node):
    return node is not None and node.childTypeCategory() == hou.objNodeTypeCategory()


def is_sop_network(node):
    return node is not None and node.childTypeCategory() == hou.sopNodeTypeCategory()


def create_node(parent, type_names, node_name, logger):
    if isinstance(type_names, str):
        type_names = [type_names]

    last_error = None
    for type_name in type_names:
        try:
            return parent.createNode(type_name, uniquify_node_name(parent, node_name))
        except Exception as error:
            last_error = error

    raise RuntimeError("Could not create node {0} with types {1}: {2}".format(node_name, ", ".join(type_names), last_error))


def next_right_position(parent, fallback_y=0):
    children = list(parent.children())
    if not children:
        return hou.Vector2(0, fallback_y)

    max_x = None
    max_y = None
    for child in children:
        pos = child.position()
        if max_x is None or pos.x() > max_x:
            max_x = pos.x()
        if max_y is None or pos.y() > max_y:
            max_y = pos.y()

    return hou.Vector2(max_x + NODE_APPEND_PADDING, max_y if max_y is not None else fallback_y)


def set_parm_if_exists(node, parm_name, value, logger=None):
    parm = node.parm(parm_name)
    if parm is None:
        if logger is not None:
            logger.write("    Missing parm {0} on {1}".format(parm_name, node.path()))
        return False
    parm.set(value)
    return True


def set_first_existing_parm(node, parm_names, value, logger=None):
    for parm_name in parm_names:
        parm = node.parm(parm_name)
        if parm is not None:
            parm.set(value)
            return parm_name
    if logger is not None:
        logger.write("    Missing parms {0} on {1}".format(", ".join(parm_names), node.path()))
    return None


def asset_base_name(result):
    asset_node_name = result.get("asset_node_name")
    if asset_node_name:
        return sanitize_node_name(asset_node_name)
    return sanitize_node_name(os.path.basename(result["asset_folder"]))


def first_fbx_for_column(result, include_billboard):
    for fbx_path in result["fbx_files"]:
        if is_billboard_fbx(fbx_path) == include_billboard:
            return fbx_path
    return None


def fbx_materials_for_column(result, fbx_path):
    if not fbx_path:
        return []
    return result["materials_by_fbx"].get(fbx_path, [])


def registry_for_materials(registry, material_names):
    material_set = set(material_names)
    entries = []
    for entry in registry:
        assigned = [name for name in entry["assigned_material_names"] if name in material_set]
        if assigned:
            filtered = dict(entry)
            filtered["assigned_material_names"] = assigned
            entries.append(filtered)
    return entries


def create_matnet(parent, name, position, logger):
    matnet = create_node(parent, ["matnet", "materialnet"], name, logger)
    matnet.setPosition(position)
    ensure_global_material_controls(matnet, logger)
    return matnet


def create_octane_material(matnet, material_entry, logger):
    textures = {}
    for channel, texture_path in material_entry.get("textures", {}).items():
        textures[channel] = {
            "path": texture_path,
            "node_name": sanitize_node_name("{0}_{1}".format(material_entry["new_material"], channel)),
        }

    result = openToolsMaterialUtils.createOctaneMaterial(
        matnet,
        name=material_entry["new_material"],
        textures=textures,
        projection={"mode": openToolsMaterialUtils.PROJECTION_MODE_UV},
        displacement={"mode": openToolsMaterialUtils.DISPLACEMENT_MODE_BUMP},
        options={
            "layout": False,
            "surface_defaults": {"thinWall": 1},
        },
        logger=logger,
    )

    subnet = result["subnet"]
    image_nodes = result["texture_nodes"]
    standard_surface = result["standard_surface"]
    set_standard_surface_defaults(standard_surface, material_entry, image_nodes, logger)
    promote_material_controls(matnet, material_entry, standard_surface, image_nodes, logger)
    subnet.layoutChildren()
    return subnet


def set_ogl_preview_texture(subnet, material_entry, logger):
    basecolor_path = material_entry.get("textures", {}).get("basecolor")
    if not basecolor_path:
        return

    use_set = set_parm_if_exists(subnet, "ogl_use_tex1", 1, logger=logger)
    tex_set = set_parm_if_exists(subnet, "ogl_tex1", basecolor_path, logger=logger)
    if use_set and tex_set:
        logger.write("    Set OGL preview texture: {0}".format(basecolor_path))


def create_octane_texture_nodes(subnet, material_entry, logger):
    textures = material_entry.get("textures", {})
    image_nodes = {}
    x_pos = 0

    for channel in sorted(textures.keys()):
        texture_path = textures[channel]
        node_name = sanitize_node_name("{0}_{1}".format(material_entry["new_material"], channel))
        try:
            image_node = create_node(subnet, [OCTANE_IMAGE_NODE_TYPE], node_name, logger)
        except Exception as error:
            logger.write("      WARNING: Could not create Octane image node for {0}: {1}".format(channel, error))
            continue

        image_node.setPosition(hou.Vector2(x_pos, -2))
        x_pos += 2
        set_first_existing_parm(image_node, ["A_FILENAME", "filename", "file"], texture_path, logger=logger)

        color_space = COLOR_SPACE if channel in SRGB_CHANNELS else NON_COLOR_SPACE
        set_first_existing_parm(image_node, ["colorSpace", "colorspace"], color_space, logger=logger)
        logger.write("      Texture node {0}: {1}".format(channel, texture_path))
        image_nodes[channel] = image_node

    connect_octane_texture_nodes(subnet, material_entry, image_nodes, logger)
    return image_nodes


def find_standard_surface_node(subnet):
    for child in subnet.children():
        type_name = child.type().name().lower()
        node_name = child.name().lower()
        if "standard" in type_name and "surface" in type_name:
            return child
        if "standard" in node_name and "surface" in node_name:
            return child
    return None


def input_index_by_normalized_name(target_node):
    input_names = []
    try:
        input_names = list(target_node.inputNames())
    except Exception:
        return {}

    mapping = {}
    for index, input_name in enumerate(input_names):
        mapping[normalize_key(input_name)] = index
    return mapping


def connect_to_first_named_input(target_node, source_node, names):
    input_map = input_index_by_normalized_name(target_node)
    for name in names:
        normalized = normalize_key(name)
        if normalized in input_map:
            target_node.setInput(input_map[normalized], source_node)
            return normalized
    return None


def is_leaf_like_material(material_entry):
    text = " ".join(
        [material_entry.get("new_material", "")]
        + material_entry.get("assigned_material_names", [])
        + material_entry.get("matched_texture_sets", [])
    )
    tokens = set(normalize_key(text).split("_"))
    return bool(tokens.intersection(LEAF_MATERIAL_KEYWORDS))


def set_standard_surface_defaults(standard_surface, material_entry, image_nodes, logger):
    set_first_existing_parm(standard_surface, ["thinWall", "thin_wall", "A_THIN_WALL"], 1, logger=logger)

    if "roughness" not in image_nodes:
        set_first_existing_parm(standard_surface, ["roughness", "A_ROUGHNESS"], 0.7, logger=logger)
        logger.write("      No roughness texture; set roughness parameter to 0.7")

    if is_leaf_like_material(material_entry):
        logger.write("      Leaf-like material detected; enabling subsurface defaults.")
        set_first_existing_parm(standard_surface, ["subsurface", "subSurface", "A_SUBSURFACE"], 0.6, logger=logger)
        basecolor_node = image_nodes.get("basecolor")
        if basecolor_node is not None:
            connected_input = connect_to_first_named_input(
                standard_surface,
                basecolor_node,
                ["subsurfaceColor", "subSurfaceColor", "subsurface_color"],
            )
            if connected_input:
                logger.write("      Connected basecolor to {0}".format(connected_input))
            else:
                logger.write("      WARNING: No subsurfaceColor input found on {0}".format(standard_surface.path()))


def first_existing_parm(node, parm_names):
    if node is None:
        return None
    for parm_name in parm_names:
        parm = node.parm(parm_name)
        if parm is not None:
            return parm
    return None


def parm_default_value(parm, fallback):
    if parm is None:
        return fallback
    try:
        value = parm.eval()
        if isinstance(value, (int, float)):
            return float(value)
    except Exception:
        pass
    return fallback


GLOBAL_MATERIAL_CONTROLS = [
    ("global_roughness_mult", "Roughness Mult", 1.0),
    ("global_specular_mult", "Specular Mult", 1.0),
    ("global_subsurface_mult", "Subsurface Mult", 1.0),
    ("global_basecolor_power_mult", "Basecolor Power Mult", 1.0),
]


def ensure_global_material_controls(matnet, logger):
    existing = [name for name, _label, _default in GLOBAL_MATERIAL_CONTROLS if matnet.parm(name) is not None]
    if len(existing) == len(GLOBAL_MATERIAL_CONTROLS):
        return

    parm_group = matnet.parmTemplateGroup()
    parm_templates = []
    for parm_name, label, default_value in GLOBAL_MATERIAL_CONTROLS:
        if matnet.parm(parm_name) is None:
            parm_templates.append(hou.FloatParmTemplate(parm_name, label, 1, default_value=(default_value,)))

    if not parm_templates:
        return

    folder_template = hou.FolderParmTemplate(
        "global_material_controls",
        "Global",
        parm_templates,
        folder_type=hou.folderType.Tabs,
    )
    parm_group.append(folder_template)
    matnet.setParmTemplateGroup(parm_group)
    logger.write("  Added global material controls to {0}".format(matnet.path()))


def unique_parm_name(node, base_name):
    candidate = sanitize_node_name(base_name)
    if node.parm(candidate) is None:
        return candidate

    index = 1
    while node.parm("{0}_{1}".format(candidate, index)) is not None:
        index += 1
    return "{0}_{1}".format(candidate, index)


def link_target_parm_to_control(matnet, target_parm, control_name, global_multiplier_name, logger):
    target_node = target_parm.node()
    relative_path = target_node.relativePathTo(matnet)
    if global_multiplier_name:
        expression = 'ch("{0}/{1}") * ch("{0}/{2}")'.format(relative_path, control_name, global_multiplier_name)
    else:
        expression = 'ch("{0}/{1}")'.format(relative_path, control_name)
    target_parm.setExpression(expression, language=hou.exprLanguage.Hscript)
    logger.write("      Promoted {0}.{1} -> {2}".format(target_node.path(), target_parm.name(), control_name))


def promote_material_controls(matnet, material_entry, standard_surface, image_nodes, logger):
    if standard_surface is None:
        logger.write("      WARNING: Cannot promote material controls; no standard surface found.")
        return

    material_name = sanitize_node_name(material_entry["new_material"])
    folder_name = unique_parm_name(matnet, "{0}_controls".format(material_name))
    controls = []

    control_specs = [
        ("roughness", "Roughness", standard_surface, ["roughness", "A_ROUGHNESS"], 0.7, "global_roughness_mult"),
        ("specular", "Specular", standard_surface, ["specular", "specularWeight", "specular_weight", "A_SPECULAR", "A_SPECULAR_WEIGHT"], 0.5, "global_specular_mult"),
        ("subsurface", "Subsurface", standard_surface, ["subsurface", "subSurface", "A_SUBSURFACE"], 0.0, "global_subsurface_mult"),
    ]

    basecolor_node = image_nodes.get("basecolor")
    if basecolor_node is not None:
        control_specs.append(
            ("basecolor_power", "Basecolor Power", basecolor_node, ["power", "A_POWER"], 1.0, "global_basecolor_power_mult")
        )

    parm_templates = []
    for suffix, label, target_node, target_names, fallback, global_multiplier_name in control_specs:
        target_parm = first_existing_parm(target_node, target_names)
        if target_parm is None:
            logger.write("      WARNING: Cannot promote {0}; target parm not found.".format(label))
            continue

        control_name = unique_parm_name(matnet, "{0}_{1}".format(material_name, suffix))
        default_value = parm_default_value(target_parm, fallback)
        parm_template = hou.FloatParmTemplate(control_name, label, 1, default_value=(default_value,))
        parm_templates.append(parm_template)
        controls.append((control_name, target_parm, global_multiplier_name))

    if not parm_templates:
        return

    parm_group = matnet.parmTemplateGroup()
    folder_template = hou.FolderParmTemplate(
        folder_name,
        material_entry["new_material"],
        parm_templates,
        folder_type=hou.folderType.Tabs,
    )
    parm_group.append(folder_template)
    matnet.setParmTemplateGroup(parm_group)

    for control_name, target_parm, global_multiplier_name in controls:
        link_target_parm_to_control(matnet, target_parm, control_name, global_multiplier_name, logger)


def connect_octane_texture_nodes(subnet, material_entry, image_nodes, logger):
    standard_surface = find_standard_surface_node(subnet)
    if standard_surface is None:
        logger.write("      WARNING: Could not find standard surface node in {0}".format(subnet.path()))
        return

    channel_inputs = {
        "basecolor": ["baseColor", "base_color", "basecolor", "albedo", "diffuse"],
        "opacity": ["opacity"],
        "normal": ["normal"],
        "roughness": ["roughness"],
        "specular": ["specular"],
        "metallic": ["metallic", "metalness"],
        "height": ["bump", "height", "displacement"],
        "ao": ["ao", "ambientOcclusion", "ambient_occlusion", "occlusion"],
        "backlight": ["subsurfaceColor", "subSurfaceColor", "subsurface_color"],
    }

    set_standard_surface_defaults(standard_surface, material_entry, image_nodes, logger)

    for channel, image_node in image_nodes.items():
        input_names = channel_inputs.get(channel)
        if not input_names:
            logger.write("      No connection mapping for channel: {0}".format(channel))
            continue
        connected_input = connect_to_first_named_input(standard_surface, image_node, input_names)
        if connected_input:
            logger.write("      Connected {0} to {1}".format(channel, connected_input))
        else:
            logger.write("      WARNING: No matching input for {0} on {1}".format(channel, standard_surface.path()))


def create_materials(matnet, registry_entries, logger):
    material_paths = {}
    for entry in registry_entries:
        subnet = create_octane_material(matnet, entry, logger)
        for assigned_name in entry["assigned_material_names"]:
            material_paths[assigned_name] = subnet.path()
    matnet.layoutChildren()
    return material_paths


def set_material_slot(material_node, slot_index, group_expression, material_path, logger):
    # Houdini's material SOP is usually 1-based for multiparm instances.
    parm_index = slot_index + 1
    set_first_existing_parm(material_node, ["group{0}".format(parm_index), "group{0}".format(slot_index)], group_expression, logger=logger)
    set_first_existing_parm(
        material_node,
        ["shop_materialpath{0}".format(parm_index), "shop_materialpath{0}".format(slot_index)],
        material_path,
        logger=logger,
    )


def create_asset_sop_column(parent, result, fbx_path, material_names, column_name, base_position, logger):
    column_name = sanitize_node_name(column_name)
    x_pos = base_position.x()
    file_y = base_position.y()

    logger.write("")
    logger.write("Creating SOP column: {0}".format(column_name))
    logger.write("  Parent: {0}".format(parent.path()))
    logger.write("  FBX: {0}".format(fbx_path))
    logger.write("  Column position: x={0}, file_y={1}".format(x_pos, file_y))

    matnet = create_matnet(parent, "{0}_MATNET".format(column_name), hou.Vector2(x_pos, file_y + NODE_ROW_SPACING), logger)
    file_node = create_node(parent, ["file"], "{0}_GEOIMPORT".format(column_name), logger)
    material_node = create_node(parent, ["material"], "{0}_MATERIALASSIGNMENT".format(column_name), logger)
    name_node = create_node(parent, ["name"], "{0}_NAME".format(column_name), logger)
    out_node = create_node(parent, ["null"], "OUT_{0}".format(column_name), logger)

    file_node.setPosition(hou.Vector2(x_pos, file_y))
    material_node.setPosition(hou.Vector2(x_pos, file_y - NODE_ROW_SPACING))
    name_node.setPosition(hou.Vector2(x_pos, file_y - (NODE_ROW_SPACING * 2)))
    out_node.setPosition(hou.Vector2(x_pos, file_y - (NODE_ROW_SPACING * 3)))

    set_parm_if_exists(file_node, "file", fbx_path, logger=logger)
    set_parm_if_exists(name_node, "name1", column_name, logger=logger)
    material_node.setInput(0, file_node)
    name_node.setInput(0, material_node)
    out_node.setInput(0, name_node)
    out_node.setDisplayFlag(True)
    out_node.setRenderFlag(True)

    registry_entries = registry_for_materials(result["registry"], material_names)
    logger.write("  Material subnets to create: {0}".format(len(registry_entries)))
    material_paths = create_materials(matnet, registry_entries, logger)

    set_parm_if_exists(material_node, "num_materials", len(material_names), logger=logger)
    for index, old_material_name in enumerate(material_names):
        material_path = material_paths.get(old_material_name)
        if material_path is None:
            logger.write("    WARNING: No material path for {0}".format(old_material_name))
            continue
        material_node_ref = hou.node(material_path)
        if material_node_ref is not None:
            material_path = material_node.relativePathTo(material_node_ref)
        group_expression = '@shop_materialpath="{0}"'.format(old_material_name)
        set_material_slot(material_node, index, group_expression, material_path, logger)
        logger.write("    Assign {0} -> {1}".format(old_material_name, material_path))

    return {
        "matnet": matnet,
        "file": file_node,
        "material": material_node,
        "name": name_node,
        "out": out_node,
    }


def create_geo_container(obj_network, name, position, logger):
    geo_node = create_node(obj_network, ["geo"], name, logger)
    geo_node.setPosition(position)
    for child in geo_node.children():
        child.destroy()
    return geo_node


def create_nodes_for_result_obj_mode(obj_network, result, include_billboard, asset_index, start_position, logger):
    base_name = asset_base_name(result)
    columns = []
    main_fbx = first_fbx_for_column(result, include_billboard=False)
    if main_fbx:
        columns.append((base_name, main_fbx, False))

    billboard_fbx = first_fbx_for_column(result, include_billboard=True)
    if include_billboard and billboard_fbx:
        columns.append(("{0}_BILLBOARD".format(base_name), billboard_fbx, True))

    for column_offset, (column_name, fbx_path, _is_billboard) in enumerate(columns):
        obj_position = hou.Vector2(
            start_position.x() + (asset_index * NODE_COLUMN_SPACING),
            start_position.y() - (column_offset * NODE_ROW_SPACING),
        )
        geo = create_geo_container(obj_network, column_name, obj_position, logger)
        material_names = fbx_materials_for_column(result, fbx_path)
        create_asset_sop_column(geo, result, fbx_path, material_names, column_name, hou.Vector2(0, 0), logger)
        logger.write("Created OBJ import: {0}".format(geo.path()))


def create_nodes_for_result_sop_mode(sop_network, result, include_billboard, asset_index, start_position, logger):
    base_name = asset_base_name(result)
    columns_per_asset = 2 if include_billboard else 1
    base_x = start_position.x() + (asset_index * columns_per_asset * NODE_COLUMN_SPACING)
    file_y = start_position.y()

    main_fbx = first_fbx_for_column(result, include_billboard=False)
    if main_fbx:
        create_asset_sop_column(
            sop_network,
            result,
            main_fbx,
            fbx_materials_for_column(result, main_fbx),
            base_name,
            hou.Vector2(base_x, file_y),
            logger,
        )

    billboard_fbx = first_fbx_for_column(result, include_billboard=True)
    if include_billboard and billboard_fbx:
        create_asset_sop_column(
            sop_network,
            result,
            billboard_fbx,
            fbx_materials_for_column(result, billboard_fbx),
            "{0}_BILLBOARD".format(base_name),
            hou.Vector2(base_x + NODE_COLUMN_SPACING, file_y),
            logger,
        )


def create_houdini_nodes(results, include_billboard, logger):
    if not CREATE_HOUDINI_NODES:
        logger.write("")
        logger.write("Houdini node creation disabled.")
        return []

    active_network = get_active_network()
    logger.write("")
    logger.write("=" * 100)
    logger.write("Houdini node creation")
    logger.write("Active network: {0}".format(active_network.path()))

    created = []
    if is_obj_network(active_network):
        start_position = next_right_position(active_network, fallback_y=0)
        logger.write("Network mode: OBJ. Creating one geo object per asset column.")
        logger.write("Append position: x={0}, y={1}".format(start_position.x(), start_position.y()))
        for index, result in enumerate(results):
            create_nodes_for_result_obj_mode(active_network, result, include_billboard, index, start_position, logger)
            created.append(result)
    elif is_sop_network(active_network):
        start_position = next_right_position(active_network, fallback_y=0)
        logger.write("Network mode: SOP. Creating asset columns in the active SOP network.")
        logger.write("Append position: x={0}, y={1}".format(start_position.x(), start_position.y()))
        for index, result in enumerate(results):
            create_nodes_for_result_sop_mode(active_network, result, include_billboard, index, start_position, logger)
            created.append(result)
    else:
        raise RuntimeError("Active network is neither OBJ nor SOP: {0}".format(active_network.path()))

    logger.write("Houdini node creation complete: {0} assets.".format(len(created)))
    logger.write("=" * 100)
    logger.write("")
    return created


def build_registry_for_folder(asset_folder, include_billboard=True, keep_probe_nodes=False, logger=None, asset_node_name=None):
    asset_folder = os.path.normpath(asset_folder)
    owns_logger = logger is None
    if logger is None:
        logger, _log_path = create_logger(asset_folder)

    fbx_files, texture_files = find_asset_files(asset_folder)

    try:
        logger.write("")
        logger.write("Asset discovery: {0}".format(asset_folder))
        logger.write("  FBX files found: {0}".format(len(fbx_files)))
        logger.write("  Texture files found: {0}".format(len(texture_files)))

        if not include_billboard:
            fbx_files = [path for path in fbx_files if not is_billboard_fbx(path)]
            logger.write("  Billboard FBX loading: disabled")
            logger.write("  FBX files after billboard filter: {0}".format(len(fbx_files)))
        else:
            logger.write("  Billboard FBX loading: enabled")

        if not fbx_files:
            raise RuntimeError("No FBX files found in: {0}".format(asset_folder))

        texture_records = build_texture_records(texture_files)
        texture_sets = build_texture_sets(texture_records, logger=logger)
        logger.write("  Texture records built: {0}".format(len(texture_records)))
        logger.write("  Texture set keys built: {0}".format(len(texture_sets)))

        materials_by_fbx = read_fbx_materials(fbx_files, keep_probe_nodes=keep_probe_nodes, logger=logger)
        registry, material_mapping, unmapped_materials, unused_texture_records = build_material_registry(materials_by_fbx, texture_records)
        logger.write("")
        logger.write("Registry build:")
        logger.write("  Registry materials: {0}".format(len(registry)))
        logger.write("  Material mappings: {0}".format(len(material_mapping)))
        logger.write("  Unmapped FBX materials: {0}".format(len(unmapped_materials)))
        logger.write("  Unused textures: {0}".format(len(unused_texture_records)))

        result = {
            "asset_folder": asset_folder,
            "asset_node_name": asset_node_name or sanitize_node_name(os.path.basename(asset_folder)),
            "fbx_files": fbx_files,
            "texture_files": texture_files,
            "texture_records": texture_records,
            "texture_sets": texture_sets,
            "materials_by_fbx": materials_by_fbx,
            "registry": registry,
            "material_mapping": material_mapping,
            "unmapped_materials": unmapped_materials,
            "unused_texture_records": unused_texture_records,
        }

        print_registry_report(asset_folder, fbx_files, materials_by_fbx, registry, material_mapping, unmapped_materials, unused_texture_records, logger)
        return result
    finally:
        if owns_logger:
            logger.close()


def folder_has_fbx(folder):
    for filename in os.listdir(folder):
        if filename.lower().endswith(".fbx") and os.path.isfile(os.path.join(folder, filename)):
            return True
    return False


def find_asset_folders(root_folder):
    root_folder = os.path.normpath(root_folder)
    asset_folders = []

    for current_folder, dirs, _files in os.walk(root_folder):
        dirs[:] = sorted(dirs, key=lambda value: value.lower())
        if folder_has_fbx(current_folder):
            asset_folders.append(current_folder)
            dirs[:] = []

    return asset_folders


def asset_folders_have_billboards(asset_folders):
    for asset_folder in asset_folders:
        fbx_files, _texture_files = find_asset_files(asset_folder)
        for fbx_path in fbx_files:
            if is_billboard_fbx(fbx_path):
                return True
    return False


def build_asset_node_name_map(root_folder, asset_folders):
    leaf_counts = {}
    for asset_folder in asset_folders:
        leaf = sanitize_node_name(os.path.basename(asset_folder))
        leaf_counts[leaf] = leaf_counts.get(leaf, 0) + 1

    name_map = {}
    used_names = set()
    for asset_folder in asset_folders:
        leaf = sanitize_node_name(os.path.basename(asset_folder))
        if leaf_counts.get(leaf, 0) == 1:
            candidate = leaf
        else:
            rel_path = os.path.relpath(asset_folder, root_folder)
            candidate = sanitize_node_name(rel_path)

        unique = candidate
        index = 1
        while unique in used_names:
            unique = "{0}_{1}".format(candidate, index)
            index += 1

        used_names.add(unique)
        name_map[asset_folder] = unique

    return name_map


def ask_include_billboards(asset_folders, logger):
    if not asset_folders_have_billboards(asset_folders):
        logger.write("Billboard FBX files detected: no")
        return False

    logger.write("Billboard FBX files detected: yes")
    if hou is None:
        logger.write("Houdini UI unavailable; importing billboards by default.")
        return True

    choice = hou.ui.displayMessage(
        "Billboard FBX files were found. Import billboard versions too?",
        buttons=("Import Billboards", "Skip Billboards"),
        default_choice=0,
        close_choice=1,
    )
    include_billboard = choice == 0
    logger.write("Billboard import enabled: {0}".format(include_billboard))
    return include_billboard


def build_registries_for_root(root_folder, include_billboard=None, keep_probe_nodes=False, logger=None):
    root_folder = os.path.normpath(root_folder)
    owns_logger = logger is None
    log_path = None
    if logger is None:
        logger, log_path = create_logger(root_folder)

    asset_folders = find_asset_folders(root_folder)
    if include_billboard is None:
        include_billboard = ask_include_billboards(asset_folders, logger)
    asset_node_names = build_asset_node_name_map(root_folder, asset_folders)

    results = []
    failures = []
    node_failures = []

    logger.write("")
    logger.write("=" * 100)
    logger.write("PlantFactory batch material registry")
    logger.write("Root folder: {0}".format(root_folder))
    if log_path:
        logger.write("Log file: {0}".format(log_path))
    logger.write("Asset folders found: {0}".format(len(asset_folders)))
    logger.write("Include billboards: {0}".format(include_billboard))
    logger.write("Asset node names:")
    for asset_folder in asset_folders:
        logger.write("  {0} -> {1}".format(asset_folder, asset_node_names.get(asset_folder)))
    logger.write("=" * 100)

    if not asset_folders:
        logger.write("No asset folders with direct FBX files were found under the selected root.")
        result = {
            "root_folder": root_folder,
            "asset_folders": [],
            "results": [],
            "failures": [],
            "node_failures": [],
            "log_path": log_path,
        }
        if owns_logger:
            logger.close()
        return result

    try:
        for asset_folder in asset_folders:
            try:
                result = build_registry_for_folder(
                    asset_folder,
                    include_billboard=include_billboard,
                    keep_probe_nodes=keep_probe_nodes,
                    logger=logger,
                    asset_node_name=asset_node_names.get(asset_folder),
                )
                results.append(result)
            except Exception as error:
                failures.append((asset_folder, error))
                logger.write("")
                logger.write("FAILED: {0}".format(asset_folder))
                logger.write("  {0}".format(error))

        if results:
            try:
                create_houdini_nodes(results, include_billboard, logger)
            except Exception as error:
                node_failures.append(error)
                logger.write("")
                logger.write("NODE CREATION FAILED:")
                logger.write("  {0}".format(error))

        logger.write("")
        logger.write("=" * 100)
        logger.write("Batch summary")
        logger.write("Processed: {0}".format(len(results)))
        logger.write("Failed: {0}".format(len(failures)))
        logger.write("Node creation failures: {0}".format(len(node_failures)))
        if failures:
            logger.write("")
            logger.write("Failures:")
            for asset_folder, error in failures:
                logger.write("  {0}: {1}".format(asset_folder, error))
        if node_failures:
            logger.write("")
            logger.write("Node creation failures:")
            for error in node_failures:
                logger.write("  {0}".format(error))
        logger.write("=" * 100)
        logger.write("")

        return {
            "root_folder": root_folder,
            "asset_folders": asset_folders,
            "results": results,
            "failures": failures,
            "node_failures": node_failures,
            "log_path": log_path,
        }
    finally:
        if owns_logger:
            logger.close()


def select_root_folder():
    if hou is None:
        raise RuntimeError("No root folder was supplied and Houdini UI is unavailable.")

    selected = hou.ui.selectFile(
        title="Select PlantFactory root folder",
        file_type=hou.fileType.Directory,
        chooser_mode=hou.fileChooserMode.Read,
    )
    if not selected:
        return None
    return hou.expandString(selected)


def run(root_folder=None, include_billboard=None, keep_probe_nodes=False):
    if root_folder is None:
        root_folder = select_root_folder()
    if not root_folder:
        RegistryLogger().write("No root folder selected.")
        return None
    return build_registries_for_root(root_folder, include_billboard=include_billboard, keep_probe_nodes=keep_probe_nodes)


def main(kwargs=None):
    kwargs = kwargs or {}
    return run(
        root_folder=kwargs.get("root_folder"),
        include_billboard=kwargs.get("include_billboard"),
        keep_probe_nodes=kwargs.get("keep_probe_nodes", False),
    )


def should_auto_run():
    if hou is None:
        return __name__ == "__main__"

    if __name__ in ("__main__", "__builtin__", "builtins"):
        return True
    return False


if should_auto_run():
    # Shelf-tool run path: choose a root folder and build registries for every
    # descendant folder containing direct FBX files.
    run()
