"""
GLTF model and Octane material importer for Houdini.

The importer creates a flattened Houdini GLTF SOP, rebuilds the GLTF
materials as Octane Standard Surface builders, and assigns them with the
``gltf_material_name`` primitive attribute produced by Houdini's GLTF SOP.
"""

from __future__ import print_function

import base64
import hashlib
import json
import os
import re
import struct
import tempfile

try:
    from urllib.parse import unquote
except ImportError:
    from urllib import unquote

try:
    import hou
except ImportError:
    hou = None

import openToolsMaterialUtils


GLTF_EXTENSIONS = set([".gltf", ".glb"])
DEFAULT_DISPLACEMENT_HEIGHT = 0.001


def _log(message):
    print("[GLTF] {0}".format(message))


def sanitize_node_name(name):
    value = re.sub(r"[^A-Za-z0-9_]+", "_", str(name or "gltf_model")).strip("_")
    if not value:
        value = "gltf_model"
    if value[0].isdigit():
        value = "_" + value
    return value


def model_name_from_path(gltf_path):
    """Use the asset folder name for generic files named scene.gltf/.glb."""
    normalized_path = os.path.abspath(os.path.normpath(gltf_path))
    file_stem = os.path.splitext(os.path.basename(normalized_path))[0]
    if file_stem.lower() == "scene":
        parent_name = os.path.basename(os.path.dirname(normalized_path))
        if parent_name:
            return parent_name
    return file_stem


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
        "Could not create {0} in {1}: {2}".format(type_names, parent.path(), last_error)
    )


def _set_first_parm(node, names, value):
    for name in names:
        parm = node.parm(name)
        if parm is not None:
            parm.set(value)
            return True
    _log("Missing parameter {0} on {1}".format("/".join(names), node.path()))
    return False


def _read_glb(path):
    with open(path, "rb") as handle:
        data = handle.read()
    if len(data) < 12:
        raise ValueError("GLB header is incomplete.")
    magic, version, declared_length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF" or version != 2:
        raise ValueError("Only GLB version 2 is supported.")
    if declared_length > len(data):
        raise ValueError("GLB file is truncated.")

    json_chunk = None
    binary_chunk = None
    offset = 12
    while offset + 8 <= declared_length:
        chunk_length, chunk_type = struct.unpack_from("<II", data, offset)
        offset += 8
        chunk = data[offset:offset + chunk_length]
        offset += chunk_length
        if chunk_type == 0x4E4F534A:
            json_chunk = chunk
        elif chunk_type == 0x004E4942 and binary_chunk is None:
            binary_chunk = chunk
    if json_chunk is None:
        raise ValueError("GLB does not contain a JSON chunk.")
    document = json.loads(json_chunk.rstrip(b"\x00 \t\r\n").decode("utf-8"))
    return document, binary_chunk


def load_gltf_document(path):
    """Return ``(document, GLB binary chunk)`` for a .gltf or .glb file."""
    path = os.path.abspath(os.path.normpath(path))
    extension = os.path.splitext(path)[1].lower()
    if extension == ".glb":
        return _read_glb(path)
    if extension != ".gltf":
        raise ValueError("Expected a .gltf or .glb file: {0}".format(path))
    with open(path, "r") as handle:
        return json.load(handle), None


def _decode_data_uri(uri):
    header, separator, payload = uri.partition(",")
    if not separator:
        raise ValueError("Invalid data URI.")
    if ";base64" in header.lower():
        return base64.b64decode(payload)
    return unquote(payload).encode("utf-8")


def _buffer_bytes(document, gltf_path, buffer_index, glb_binary):
    buffers = document.get("buffers") or []
    if buffer_index < 0 or buffer_index >= len(buffers):
        return None
    buffer_record = buffers[buffer_index]
    uri = buffer_record.get("uri")
    if uri is None and buffer_index == 0:
        return glb_binary
    if not uri:
        return None
    if uri.startswith("data:"):
        return _decode_data_uri(uri)
    path = os.path.normpath(os.path.join(os.path.dirname(gltf_path), unquote(uri)))
    with open(path, "rb") as handle:
        return handle.read()


def _embedded_extension(image):
    mime_type = str(image.get("mimeType") or "").lower()
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/ktx2": ".ktx2",
    }.get(mime_type, ".bin")


def _embedded_cache_directory(gltf_path):
    stem = sanitize_node_name(os.path.splitext(os.path.basename(gltf_path))[0])
    preferred = os.path.join(os.path.dirname(gltf_path), "{0}_octane_textures".format(stem))
    try:
        if not os.path.isdir(preferred):
            os.makedirs(preferred)
        return preferred
    except Exception:
        fallback = os.path.join(tempfile.gettempdir(), "OctaneGltfImporter", stem)
        if not os.path.isdir(fallback):
            os.makedirs(fallback)
        return fallback


def _write_embedded_image(gltf_path, image_index, image, payload):
    digest = hashlib.sha1(payload).hexdigest()[:12]
    extension = _embedded_extension(image)
    filename = "image_{0:03d}_{1}{2}".format(image_index, digest, extension)
    path = os.path.join(_embedded_cache_directory(gltf_path), filename)
    if not os.path.isfile(path):
        with open(path, "wb") as handle:
            handle.write(payload)
    return path


def resolve_image_path(document, gltf_path, image_index, glb_binary=None):
    images = document.get("images") or []
    if image_index is None or image_index < 0 or image_index >= len(images):
        return None
    image = images[image_index]
    uri = image.get("uri")
    if uri:
        if uri.startswith("data:"):
            return _write_embedded_image(
                gltf_path, image_index, image, _decode_data_uri(uri)
            )
        return os.path.abspath(
            os.path.normpath(os.path.join(os.path.dirname(gltf_path), unquote(uri)))
        )

    buffer_view_index = image.get("bufferView")
    buffer_views = document.get("bufferViews") or []
    if buffer_view_index is None or buffer_view_index >= len(buffer_views):
        return None
    buffer_view = buffer_views[buffer_view_index]
    payload = _buffer_bytes(
        document,
        gltf_path,
        int(buffer_view.get("buffer", 0)),
        glb_binary,
    )
    if payload is None:
        return None
    start = int(buffer_view.get("byteOffset", 0))
    end = start + int(buffer_view.get("byteLength", 0))
    return _write_embedded_image(gltf_path, image_index, image, payload[start:end])


def _texture_source_index(texture):
    basisu = (texture.get("extensions") or {}).get("KHR_texture_basisu") or {}
    return basisu.get("source", texture.get("source"))


def texture_path_from_info(document, gltf_path, texture_info, glb_binary=None):
    if not isinstance(texture_info, dict) or "index" not in texture_info:
        return None
    textures = document.get("textures") or []
    texture_index = int(texture_info["index"])
    if texture_index < 0 or texture_index >= len(textures):
        return None
    source_index = _texture_source_index(textures[texture_index])
    if source_index is None:
        return None
    return resolve_image_path(document, gltf_path, int(source_index), glb_binary)


def _color3(value, default):
    value = value if isinstance(value, (list, tuple)) else default
    return tuple(float(component) for component in list(value)[:3])


def _alpha(value, default=1.0):
    if isinstance(value, (list, tuple)) and len(value) > 3:
        return float(value[3])
    return float(default)


def _add_texture(
    textures, channel, path, texture_type=None, power=None, factor=None
):
    if not path:
        return
    record = {"path": path}
    if texture_type:
        record["texture_type"] = texture_type
    if power is not None:
        record["power"] = float(power)
    if factor is not None:
        record["factor"] = tuple(float(value) for value in factor[:3])
    textures[channel] = record


def _find_displacement_texture(material):
    containers = [material, material.get("extras") or {}]
    for extension_name, extension in (material.get("extensions") or {}).items():
        if "displacement" in extension_name.lower() or "height" in extension_name.lower():
            if isinstance(extension, dict):
                containers.append(extension)
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key, value in container.items():
            normalized = str(key).lower()
            if (
                isinstance(value, dict)
                and "index" in value
                and "texture" in normalized
                and any(word in normalized for word in ("displacement", "height", "bump"))
            ):
                return value
    return None


def material_record(document, gltf_path, material, index, glb_binary=None):
    """Convert one GLTF material to the shared material API format."""
    name = str(material.get("name") or "material_{0}".format(index))
    parameters = {"base": 1.0}
    textures = {}
    warnings = []
    alpha_mode = str(material.get("alphaMode") or "OPAQUE").upper()

    pbr = material.get("pbrMetallicRoughness") or {}
    base_factor = pbr.get("baseColorFactor", [1.0, 1.0, 1.0, 1.0])
    parameters["base_color"] = _color3(base_factor, [1.0, 1.0, 1.0])
    parameters["metallic"] = float(pbr.get("metallicFactor", 1.0))
    parameters["roughness"] = float(pbr.get("roughnessFactor", 1.0))
    base_path = texture_path_from_info(
        document, gltf_path, pbr.get("baseColorTexture"), glb_binary
    )
    _add_texture(
        textures,
        "base_color",
        base_path,
        factor=_color3(base_factor, [1.0, 1.0, 1.0]),
    )
    if alpha_mode != "OPAQUE":
        parameters["opacity"] = _alpha(base_factor)
        _add_texture(
            textures,
            "opacity",
            base_path,
            texture_type="alpha",
            power=_alpha(base_factor),
        )

    packed_path = texture_path_from_info(
        document, gltf_path, pbr.get("metallicRoughnessTexture"), glb_binary
    )
    if packed_path:
        warnings.append(
            "packed metallic/roughness texture is not connected (requires G/B channel extraction): {0}".format(
                packed_path
            )
        )

    normal_info = material.get("normalTexture") or {}
    _add_texture(
        textures,
        "normal",
        texture_path_from_info(document, gltf_path, normal_info, glb_binary),
        power=normal_info.get("scale", 1.0),
    )
    _add_texture(
        textures,
        "ao",
        texture_path_from_info(document, gltf_path, material.get("occlusionTexture"), glb_binary),
        texture_type="greyscale",
    )

    emissive_factor = material.get("emissiveFactor", [0.0, 0.0, 0.0])
    emissive_path = texture_path_from_info(
        document, gltf_path, material.get("emissiveTexture"), glb_binary
    )
    if emissive_path or any(float(value) != 0.0 for value in emissive_factor[:3]):
        parameters["emission"] = 1.0
        parameters["emission_color"] = _color3(emissive_factor, [1.0, 1.0, 1.0])
        _add_texture(
            textures,
            "emission_color",
            emissive_path,
            factor=_color3(emissive_factor, [1.0, 1.0, 1.0]),
        )

    extensions = material.get("extensions") or {}
    specular = extensions.get("KHR_materials_specular") or {}
    if specular:
        parameters["specular"] = float(specular.get("specularFactor", 1.0))
        parameters["specular_color"] = _color3(
            specular.get("specularColorFactor"), [1.0, 1.0, 1.0]
        )
        _add_texture(
            textures,
            "specular",
            texture_path_from_info(document, gltf_path, specular.get("specularTexture"), glb_binary),
            texture_type="alpha",
            power=float(specular.get("specularFactor", 1.0)),
        )
        _add_texture(
            textures,
            "specular_color",
            texture_path_from_info(document, gltf_path, specular.get("specularColorTexture"), glb_binary),
            factor=_color3(
                specular.get("specularColorFactor"), [1.0, 1.0, 1.0]
            ),
        )

    clearcoat = extensions.get("KHR_materials_clearcoat") or {}
    if clearcoat:
        parameters["coat"] = float(clearcoat.get("clearcoatFactor", 0.0))
        parameters["coat_roughness"] = float(clearcoat.get("clearcoatRoughnessFactor", 0.0))
        if clearcoat.get("clearcoatTexture") or clearcoat.get("clearcoatRoughnessTexture"):
            warnings.append("packed clearcoat textures are not connected")

    transmission = extensions.get("KHR_materials_transmission") or {}
    if transmission:
        parameters["transmission"] = float(transmission.get("transmissionFactor", 0.0))
        _add_texture(
            textures,
            "transmission",
            texture_path_from_info(document, gltf_path, transmission.get("transmissionTexture"), glb_binary),
            texture_type="greyscale",
            power=float(transmission.get("transmissionFactor", 0.0)),
        )
    ior = extensions.get("KHR_materials_ior") or {}
    if ior:
        parameters["specular_ior"] = float(ior.get("ior", 1.5))

    emissive_strength = extensions.get("KHR_materials_emissive_strength") or {}
    if emissive_strength:
        parameters["emission"] = float(emissive_strength.get("emissiveStrength", 1.0))

    spec_gloss = extensions.get("KHR_materials_pbrSpecularGlossiness") or {}
    if spec_gloss:
        diffuse_factor = spec_gloss.get("diffuseFactor", [1.0, 1.0, 1.0, 1.0])
        parameters["base_color"] = _color3(diffuse_factor, [1.0, 1.0, 1.0])
        parameters["metallic"] = 0.0
        parameters["roughness"] = 1.0 - float(spec_gloss.get("glossinessFactor", 1.0))
        parameters["specular_color"] = _color3(
            spec_gloss.get("specularFactor"), [1.0, 1.0, 1.0]
        )
        diffuse_path = texture_path_from_info(
            document, gltf_path, spec_gloss.get("diffuseTexture"), glb_binary
        )
        _add_texture(
            textures,
            "base_color",
            diffuse_path,
            factor=_color3(diffuse_factor, [1.0, 1.0, 1.0]),
        )
        if alpha_mode != "OPAQUE":
            parameters["opacity"] = _alpha(diffuse_factor)
            _add_texture(
                textures,
                "opacity",
                diffuse_path,
                texture_type="alpha",
                power=_alpha(diffuse_factor),
            )
        if spec_gloss.get("specularGlossinessTexture"):
            warnings.append("packed specular/glossiness texture is not connected")

    displacement_info = _find_displacement_texture(material)
    _add_texture(
        textures,
        "displacement",
        texture_path_from_info(document, gltf_path, displacement_info, glb_binary),
        texture_type="greyscale",
    )

    for channel, texture in list(textures.items()):
        if not os.path.isfile(texture["path"]):
            warnings.append("missing {0} texture: {1}".format(channel, texture["path"]))

    return {
        "name": name,
        "parameters": parameters,
        "textures": textures,
        "alpha_mode": alpha_mode,
        "alpha_cutoff": float(material.get("alphaCutoff", 0.5)),
        "double_sided": bool(material.get("doubleSided", False)),
        "has_displacement": "displacement" in textures,
        "warnings": warnings,
    }


def parse_materials(gltf_path):
    document, glb_binary = load_gltf_document(gltf_path)
    records = []
    for index, material in enumerate(document.get("materials") or []):
        records.append(material_record(document, gltf_path, material, index, glb_binary))
    return records


def select_gltf_file():
    if hou is None:
        raise RuntimeError("This script must run inside Houdini.")
    selected = hou.ui.selectFile(
        title="Select GLTF model",
        file_type=hou.fileType.Geometry,
        pattern="*.gltf *.glb",
        chooser_mode=hou.fileChooserMode.Read,
    )
    if not selected:
        return None
    path = os.path.normpath(hou.expandString(selected))
    if os.path.splitext(path)[1].lower() not in GLTF_EXTENSIONS:
        hou.ui.displayMessage("Select a .gltf or .glb file.", title="GLTF Importer")
        return None
    return path


def get_active_network():
    editor = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
    return editor.pwd() if editor is not None else hou.node("/obj")


def is_obj_network(node):
    return node is not None and node.childTypeCategory() == hou.objNodeTypeCategory()


def is_sop_network(node):
    return node is not None and node.childTypeCategory() == hou.sopNodeTypeCategory()


def find_target_context():
    node = get_active_network()
    while node is not None:
        if is_obj_network(node) or is_sop_network(node):
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


def choose_displacement_settings():
    choice = hou.ui.displayMessage(
        "This GLTF declares displacement/height textures. How should they be connected?",
        buttons=("Texture displacement", "Vertex displacement", "Bump", "Cancel"),
        default_choice=0,
        close_choice=3,
        title="GLTF Displacement",
    )
    if choice == 3:
        return None
    modes = [
        openToolsMaterialUtils.DISPLACEMENT_MODE_TEXTURE,
        openToolsMaterialUtils.DISPLACEMENT_MODE_VERTEX,
        openToolsMaterialUtils.DISPLACEMENT_MODE_BUMP,
    ]
    height = DEFAULT_DISPLACEMENT_HEIGHT
    if choice != 2:
        result = hou.ui.readInput(
            "Displacement height",
            buttons=("OK", "Cancel"),
            default_choice=0,
            close_choice=1,
            initial_contents=str(DEFAULT_DISPLACEMENT_HEIGHT),
            title="GLTF Displacement",
        )
        if result[0] == 1:
            return None
        try:
            height = float(result[1])
        except ValueError:
            height = DEFAULT_DISPLACEMENT_HEIGHT
    return {"mode": modes[choice], "height": height, "create_disconnected": False}


def create_octane_material(parent, record, displacement_settings):
    result = openToolsMaterialUtils.createOctaneMaterial(
        parent,
        name=record["name"],
        textures=record["textures"],
        parameters=record["parameters"],
        projection={"mode": openToolsMaterialUtils.PROJECTION_MODE_UV},
        displacement=displacement_settings,
        options={
            "allow_unknown_parameters": False,
            "material_spec": record,
        },
        logger=_log,
    )
    return result["subnet"]


def _assignment_group(material_name):
    escaped = str(material_name).replace("\\", "\\\\").replace('"', '\\"')
    return '@gltf_material_name="{0}"'.format(escaped)


def _set_material_assignment(material_node, slot_index, material_name, material_path):
    parm_index = slot_index + 1
    _set_first_parm(
        material_node,
        ["group{0}".format(parm_index), "group{0}".format(slot_index)],
        _assignment_group(material_name),
    )
    material_ref = hou.node(material_path)
    if material_ref is not None:
        material_path = material_node.relativePathTo(material_ref)
    _set_first_parm(
        material_node,
        [
            "shop_materialpath{0}".format(parm_index),
            "shop_materialpath{0}".format(slot_index),
        ],
        material_path,
    )


def _create_geo_container(parent, name, position):
    geo = _create_node(parent, ["geo"], name)
    geo.setPosition(position)
    for child in list(geo.children()):
        child.destroy()
    return geo


def create_gltf_network(parent, gltf_path, records, displacement_settings, position):
    model_name = sanitize_node_name(model_name_from_path(gltf_path))
    matnet = _create_node(parent, ["matnet", "materialnet"], "MATNET_{0}".format(model_name))
    import_node = _create_node(parent, ["gltf"], "IMPORT_{0}".format(model_name))
    material_node = _create_node(parent, ["material"], "MATERIAL_{0}".format(model_name))
    out_node = _create_node(parent, ["null"], "OUT_{0}".format(model_name))

    matnet.setPosition(hou.Vector2(position.x(), position.y() + 2.0))
    import_node.setPosition(position)
    material_node.setPosition(hou.Vector2(position.x(), position.y() - 1.5))
    out_node.setPosition(hou.Vector2(position.x(), position.y() - 3.0))

    _set_first_parm(import_node, ["gltffile"], gltf_path)
    _set_first_parm(import_node, ["importnodegeometryas"], "flattenedgeometry")
    material_node.setInput(0, import_node)
    out_node.setInput(0, material_node)
    out_node.setDisplayFlag(True)
    out_node.setRenderFlag(True)

    material_paths = []
    for record in records:
        material = create_octane_material(matnet, record, displacement_settings)
        material_paths.append(material.path())
        for warning in record.get("warnings", []):
            _log("{0}: {1}".format(record["name"], warning))

    _set_first_parm(material_node, ["num_materials"], len(material_paths))
    for index, (record, material_path) in enumerate(zip(records, material_paths)):
        _set_material_assignment(
            material_node, index, record["name"], material_path
        )

    matnet.layoutChildren()
    return {
        "matnet": matnet,
        "import": import_node,
        "material": material_node,
        "out": out_node,
        "materials": material_paths,
    }


def import_gltf(gltf_path, displacement_settings=None):
    if hou is None:
        raise RuntimeError("This script must run inside Houdini.")
    gltf_path = os.path.abspath(os.path.normpath(hou.expandString(gltf_path)))
    records = parse_materials(gltf_path)
    has_displacement = any(record["has_displacement"] for record in records)
    if has_displacement and displacement_settings is None:
        displacement_settings = choose_displacement_settings()
        if displacement_settings is None:
            return None
    displacement_settings = displacement_settings or {
        "mode": openToolsMaterialUtils.DISPLACEMENT_MODE_TEXTURE,
        "height": DEFAULT_DISPLACEMENT_HEIGHT,
        "create_disconnected": False,
    }

    target = find_target_context()
    start_position = next_right_position(target)
    if is_obj_network(target):
        model_name = model_name_from_path(gltf_path)
        geo = _create_geo_container(target, model_name, start_position)
        result = create_gltf_network(
            geo, gltf_path, records, displacement_settings, hou.Vector2(0, 0)
        )
        geo.layoutChildren()
        result["geo"] = geo
    else:
        result = create_gltf_network(
            target, gltf_path, records, displacement_settings, start_position
        )

    _log(
        "Imported {0} with {1} material(s) in {2}".format(
            gltf_path, len(records), result["out"].parent().path()
        )
    )
    hou.ui.displayMessage(
        "Imported GLTF model with {0} Octane material(s).".format(len(records)),
        title="GLTF Importer",
    )
    return result


def run(gltf_path=None, displacement_settings=None):
    if hou is None:
        raise RuntimeError("This script must run inside Houdini.")
    gltf_path = gltf_path or select_gltf_file()
    if not gltf_path:
        _log("No GLTF file selected.")
        return None
    return import_gltf(gltf_path, displacement_settings=displacement_settings)


def main(kwargs=None):
    kwargs = kwargs or {}
    return run(
        gltf_path=kwargs.get("gltf_path") or kwargs.get("file_path"),
        displacement_settings=kwargs.get("displacement_settings") or kwargs.get("height_mode"),
    )


def should_auto_run():
    if hou is None:
        return __name__ == "__main__"
    return __name__ in ("__main__", "__builtin__", "builtins")


if should_auto_run():
    run()
