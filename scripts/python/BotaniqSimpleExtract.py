"""Small, intentionally selective extractor for Botaniq's stock materials.

It does not try to serialize a Blender node graph. It follows only the inputs
which describe the useful surface textures of Botaniq's public material groups.
"""
import os

import bpy


FAMILIES = {"bq_Vegetation", "bq_Bark", "bq_Grass", "bq_Basic", "bq_Rock"}

# The names have varied a little between Botaniq releases.  Keeping this table
# explicit is preferable to treating every image reachable from a shader as a
# material texture (which pulls in snow, moss, masks and macro layers).
INPUTS = {
    "base_color": ("base color", "basecolor", "color", "diffuse", "diffuse texture"),
    "normal": ("normal color", "normal", "normal map"),
    "alpha": ("alpha", "opacity", "mask"),
    "height": ("height", "bump", "displacement"),
    "backlight": ("backlight", "subsurface color", "subsurface"),
    "roughness": ("roughness", "roughness texture", "specular roughness"),
}
PARAMETERS = {
    "base_color": ("base color", "basecolor", "color"),
    "roughness": ("roughness", "specular roughness"),
    "metallic": ("metallic", "metalness"),
    "specular": ("specular", "specular ior level"),
}
IGNORE_IMAGE_WORDS = ("snow", "moss", "macro")
STEM_WORDS = ("stem", "branch", "twig", "trunk", "stalk", "cane")


def _normal_name(name):
    return " ".join(name.lower().replace("_", " ").replace("-", " ").split())


def _group(material):
    if not material or not material.use_nodes or not material.node_tree:
        return None, "principled"
    groups = [n for n in material.node_tree.nodes if n.type == "GROUP" and n.node_tree
              and n.node_tree.name.split(".")[0] in FAMILIES]
    if not groups:
        return None, "principled"
    if len(groups) > 1:
        # A material with two top-level Botaniq surface groups is not a shape
        # handled by the simple importer.  Use the first one, but make it loud.
        groups.sort(key=lambda n: n.name)
    return groups[0], groups[0].node_tree.name.split(".")[0]


def _role(family, material_name):
    if family == "bq_Bark":
        return "bark"
    if family == "bq_Grass":
        return "grass"
    if family == "bq_Rock":
        return "rock"
    if family == "bq_Basic" or family == "principled":
        return "basic"
    name = _normal_name(material_name)
    # Flowers and weeds use the vegetation group and need the leaf treatment.
    return "stem" if any(word in name.split() for word in STEM_WORDS) else "leaf"


def _socket(node, aliases):
    """Return a group input by a deliberately exact, normalized name."""
    wanted = set(aliases)
    for socket in node.inputs:
        if _normal_name(socket.name) in wanted:
            return socket
    return None


def _inside_socket(group_node, socket):
    """Find the corresponding socket on the active output of a node group."""
    outputs = [n for n in group_node.node_tree.nodes if n.type == "GROUP_OUTPUT" and n.is_active_output]
    if not outputs:
        return None
    for candidate in outputs[0].inputs:
        if candidate.identifier == socket.identifier:
            return candidate
    return None


def _outer_socket(group_node, socket):
    for candidate in group_node.inputs:
        if candidate.identifier == socket.identifier:
            return candidate
    return None


def _walk(socket, groups=(), seen=None):
    """Return image nodes upstream of *socket*, expanding groups only as needed."""
    seen = set() if seen is None else seen
    found = []
    if socket is None:
        return found
    for link in socket.links:
        node = link.from_node
        key = (node.as_pointer(), link.from_socket.identifier, tuple(n.as_pointer() for n in groups))
        if key in seen:
            continue
        seen.add(key)
        if node.type == "TEX_IMAGE" and node.image:
            found.append(node)
        elif node.type == "GROUP":
            inside = _inside_socket(node, link.from_socket)
            found.extend(_walk(inside, groups + (node,), seen))
        elif node.type == "GROUP_INPUT" and groups:
            outer = _outer_socket(groups[-1], link.from_socket)
            found.extend(_walk(outer, groups[:-1], seen))
        else:
            for input_socket in node.inputs:
                if not input_socket.is_unavailable:
                    found.extend(_walk(input_socket, groups, seen))
    return found


def _uvs(socket, groups=(), seen=None):
    """Collect explicitly selected UV map names feeding an image vector input."""
    seen = set() if seen is None else seen
    values = set()
    if socket is None:
        return values
    for link in socket.links:
        node = link.from_node
        key = (node.as_pointer(), link.from_socket.identifier, tuple(n.as_pointer() for n in groups))
        if key in seen:
            continue
        seen.add(key)
        if node.type == "UVMAP":
            values.add(node.uv_map or "UVMap")
        elif node.type == "TEX_COORD" and link.from_socket.name == "UV":
            values.add("UVMap")
        elif node.type == "GROUP":
            inside = _inside_socket(node, link.from_socket)
            values.update(_uvs(inside, groups + (node,), seen))
        elif node.type == "GROUP_INPUT" and groups:
            values.update(_uvs(_outer_socket(groups[-1], link.from_socket), groups[:-1], seen))
        else:
            for input_socket in node.inputs:
                if not input_socket.is_unavailable:
                    values.update(_uvs(input_socket, groups, seen))
    return values


def _unique(nodes):
    result = []
    seen = set()
    for node in nodes:
        if node.image and node.image.as_pointer() not in seen:
            seen.add(node.image.as_pointer())
            result.append(node)
    return result


def _useful(nodes):
    return [n for n in _unique(nodes)
            if not any(word in os.path.basename(n.image.filepath).lower() for word in IGNORE_IMAGE_WORDS)]


def _choose(nodes, channel, warnings, material_name, alpha_nodes=()):
    nodes = _useful(nodes)
    if not nodes:
        return None
    if channel == "base_color" and "areca" in material_name.lower() and len(nodes) > 1:
        # Areca mixes a tip image over its atlas.  Match the *actual alpha
        # image* by filename, rather than relying on a diffuse/leaf name score.
        # The alpha atlas is also the map whose cutout layout the compact shader
        # will use.
        alpha_names = {os.path.basename(n.image.filepath).lower() for n in _unique(alpha_nodes)}
        matching = [n for n in nodes if os.path.basename(n.image.filepath).lower() in alpha_names]
        if matching:
            return matching[0]
        warnings.append("Areca base color has no candidate matching its alpha atlas")
    if len(nodes) > 1:
        names = ", ".join(n.image.name for n in nodes)
        warnings.append("Ambiguous %s textures for %s: %s; using %s" %
                        (channel, material_name, names, nodes[0].image.name))
    return nodes[0]


def _image_type(channel, image):
    if channel == "opacity":
        return "alpha"
    if channel in {"normal", "height", "roughness"}:
        return "data"
    return "color" if image.colorspace_settings.name == "sRGB" else "data"


def _has_transparency(image, warnings):
    """Inspect decoded alpha; PNG palette data cannot be judged by channels."""
    # These formats cannot carry an alpha channel.  This avoids scanning large
    # JPEGs while preserving the important PNG/TGA cases.
    if image.file_format in {"JPEG", "BMP"}:
        return False
    try:
        image.reload()
        pixels = image.pixels
        for index in range(3, len(pixels), 4):
            if pixels[index] < 0.99999:
                return True
    except Exception as exc:
        warnings.append("Could not inspect alpha for %s: %s" % (image.name, exc))
        # Do not invent opacity if Blender cannot decode the asset, but never
        # silently drop a potentially meaningful cutout.
        return False
    return False


def _entry(node, channel, extractor):
    return {"path": extractor.image(node.image), "type": _image_type(channel, node.image)}


def _set_primary_uvs(extractor, nodes):
    if not hasattr(extractor, "primary_uvs"):
        return []
    uvs = set()
    for node in nodes:
        uvs.update(_uvs(node.inputs.get("Vector")))
    # Exporters commonly seed this set with the mesh's active UVs.  Only add
    # concrete maps discovered on selected textures; never overwrite it.
    extractor.primary_uvs.update(uvs)
    return sorted(uvs)


def extract_material(material, extractor):
    """Extract Botaniq's useful primary texture inputs into a compact record.

    ``extractor.image(bpy_image)`` owns copying and returns the desired relative
    path.  ``extractor.primary_uvs`` is optional and, when supplied, is updated
    with actual UV maps used by selected images.
    """
    group, family = _group(material)
    warnings = []
    result = {"name": material.name, "family": family, "role": _role(family, material.name),
              "textures": {}, "parameters": {}, "warnings": warnings}
    if group is None:
        warnings.append("No recognized Botaniq material group")
        return result

    if len([n for n in material.node_tree.nodes if n.type == "GROUP" and n.node_tree and
            n.node_tree.name.split(".")[0] in FAMILIES]) > 1:
        warnings.append("Multiple Botaniq groups; extracted %s" % group.name)

    candidates = {}
    for channel, aliases in INPUTS.items():
        socket = _socket(group, aliases)
        candidates[channel] = _walk(socket)
    selected = {}
    # Resolve alpha first so Areca's color atlas can be selected by an actual
    # filename match, not by the order of a blend graph.
    selected["alpha"] = _choose(candidates["alpha"], "alpha", warnings, material.name)
    for channel in INPUTS:
        if channel != "alpha":
            selected[channel] = _choose(candidates[channel], channel, warnings, material.name,
                                        candidates["alpha"])

    if family == "bq_Grass":
        # Grass deliberately has its two useful images behind the group inputs.
        # Read those actual image nodes, while rejecting the macro/noise layers.
        inside = list(group.node_tree.nodes)
        diffuse = [n for n in inside if n.type == "TEX_IMAGE" and n.image and
                   "grass" in n.image.name.lower() and "diffuse" in n.image.name.lower()]
        normal = [n for n in inside if n.type == "TEX_IMAGE" and n.image and
                  "grass" in n.image.name.lower() and "normal" in n.image.name.lower()]
        if diffuse:
            selected["base_color"] = _choose(diffuse, "base_color", warnings, material.name)
        if normal:
            selected["normal"] = _choose(normal, "normal", warnings, material.name)

    for channel in ("base_color", "normal", "roughness", "backlight"):
        node = selected.get(channel)
        if node:
            key = "basecolor" if channel == "base_color" else channel
            result["textures"][key] = _entry(node, key, extractor)

    alpha = selected.get("alpha")
    if alpha and _has_transparency(alpha.image, warnings):
        result["textures"]["opacity"] = _entry(alpha, "opacity", extractor)
    # A normal is authoritative in this compact representation.  In particular,
    # do not retain Botaniq's diffuse-derived bump alongside it.
    if not selected.get("normal") and selected.get("height"):
        result["textures"]["height"] = _entry(selected["height"], "height", extractor)

    for key, aliases in PARAMETERS.items():
        socket = _socket(group, aliases)
        if socket and not socket.is_linked:
            value = socket.default_value
            try:
                value = list(value)
            except TypeError:
                pass
            result["parameters"][key] = value

    result["primary_uvs"] = _set_primary_uvs(extractor, [n for n in selected.values() if n])
    return result
