"""
Shared Octane material creation helpers for Houdini.

The public entry point is createOctaneMaterial(). It accepts a normalized
material description but also tolerates the simple texture dictionaries used by
the existing importers.
"""

from __future__ import print_function

import os
import re

try:
    import hou
except ImportError:
    hou = None


OCTANE_IMAGE_NODE_TYPE = "octane::NT_TEX_IMAGE"
OCTANE_ALPHA_IMAGE_NODE_TYPE = "octane::NT_TEX_ALPHAIMAGE"
OCTANE_FLOAT_IMAGE_NODE_TYPE = "octane::NT_TEX_FLOATIMAGE"
OCTANE_TRANSFORM_2D_NODE_TYPE = "octane::NT_TRANSFORM_2D"
OCTANE_TRANSFORM_3D_NODE_TYPE = "octane::NT_TRANSFORM_3D"
OCTANE_TEXTURE_DISPLACEMENT_NODE_TYPE = "octane::NT_DISPLACEMENT"
OCTANE_VERTEX_DISPLACEMENT_NODE_TYPE = "octane::NT_VERTEX_DISPLACEMENT"
OCTANE_TRIPLANAR_TEXTURE_NODE_TYPE = "octane::NT_TEX_TRIPLANAR"
OCTANE_RGB_TEXTURE_NODE_TYPE = "octane::NT_TEX_RGB"
OCTANE_MULTIPLY_TEXTURE_NODE_TYPE = "octane::NT_TEX_MULTIPLY"

COLOR_SPACE_SRGB = "NAMED_COLOR_SPACE_SRGB"
COLOR_SPACE_NON_COLOR = "NAMED_COLOR_SPACE_OTHER"

PROJECTION_MODE_UV = "uv"
PROJECTION_MODE_MESH_UV = "mesh_uv"
PROJECTION_MODE_LINEAR = "linear"
PROJECTION_MODE_BOX = "box"
PROJECTION_MODE_CYLINDRICAL = "cylindrical"
PROJECTION_MODE_SPHERICAL = "spherical"
PROJECTION_MODE_PERSPECTIVE = "perspective"
PROJECTION_MODE_TRIPLANAR = "triplanar"

DISPLACEMENT_MODE_TEXTURE = "texture_displacement"
DISPLACEMENT_MODE_VERTEX = "vertex_displacement"
DISPLACEMENT_MODE_BUMP = "bump"

TEXTURE_TYPE_IMAGE = "image"
TEXTURE_TYPE_ALPHA = "alpha"
TEXTURE_TYPE_GREYSCALE = "greyscale"
TEXTURE_TYPE_GRAYSCALE = "grayscale"

DEFAULT_DISPLACEMENT_HEIGHT = 0.001
DEFAULT_DISPLACEMENT_MIDLEVEL = 0.5

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
    "backlight",
])

FLOAT_TEXTURE_CHANNELS = set([
    "anisotropy_angle",
    "ao",
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

HEIGHT_TEXTURE_CHANNELS = set(["displacement", "height", "bump"])

PROJECTION_NODE_TYPES = {
    PROJECTION_MODE_MESH_UV: ("octane::NT_PROJ_UVW", "mesh_uv_projection"),
    PROJECTION_MODE_LINEAR: ("octane::NT_PROJ_LINEAR", "xyz_to_uvw_projection"),
    PROJECTION_MODE_BOX: ("octane::NT_PROJ_BOX", "box_projection"),
    PROJECTION_MODE_CYLINDRICAL: ("octane::NT_PROJ_CYLINDRICAL", "cylindrical_projection"),
    PROJECTION_MODE_SPHERICAL: ("octane::NT_PROJ_SPHERICAL", "spherical_projection"),
    PROJECTION_MODE_PERSPECTIVE: ("octane::NT_PROJ_PERSPECTIVE", "perspective_projection"),
    PROJECTION_MODE_TRIPLANAR: ("octane::NT_PROJ_TRIPLANAR", "triplanar_projection"),
}

STANDARD_SURFACE_INPUTS = {
    "base_color": ["baseColor"],
    "basecolor": ["baseColor"],
    "diffuse": ["baseColor"],
    "normal": ["normal"],
    "specular_roughness": ["roughness"],
    "roughness": ["roughness"],
    "metalness": ["metallic"],
    "metallic": ["metallic", "metalness"],
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
    "ao": ["ao", "ambientOcclusion", "ambient_occlusion", "occlusion"],
    "backlight": ["subsurfaceColor", "subSurfaceColor", "subsurface_color"],
}

PARAMETER_INPUTS = {
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
    "metallic": ["metallic"],
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
    "roughness": ["roughness"],
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


def _log(logger, message):
    if logger is None:
        return
    try:
        logger.write(message)
    except Exception:
        pass


def sanitizeNodeName(name):
    value = re.sub(r"[^A-Za-z0-9_]+", "_", str(name or "material")).strip("_")
    if not value:
        value = "material"
    if value[0].isdigit():
        value = "_" + value
    return value


def uniquifyNodeName(parent, name):
    base = sanitizeNodeName(name)
    if parent.node(base) is None:
        return base

    index = 1
    while parent.node("{0}_{1}".format(base, index)) is not None:
        index += 1
    return "{0}_{1}".format(base, index)


def normalizeKey(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def createNode(parent, type_names, node_name, logger=None):
    last_error = None
    for type_name in type_names:
        try:
            return parent.createNode(type_name, uniquifyNodeName(parent, node_name))
        except Exception as error:
            last_error = error
    raise RuntimeError("Could not create node {0} with types {1}: {2}".format(node_name, type_names, last_error))


def setFirstExistingParm(node, parm_names, value):
    if node is None:
        return False
    for parm_name in parm_names:
        parm = node.parm(parm_name)
        if parm is not None:
            parm.set(value)
            return True
    return False


def setColorOrFloatParm(node, parm_names, value):
    if node is None:
        return False
    for parm_name in parm_names:
        if isinstance(value, tuple):
            try:
                parm_tuple = node.parmTuple(parm_name)
            except Exception:
                parm_tuple = None
            if parm_tuple is not None and len(parm_tuple) == len(value):
                parm_tuple.set(value)
                return True

            tuple_parms = [node.parm("{0}{1}".format(parm_name, suffix)) for suffix in ("r", "g", "b")]
            if all(tuple_parms):
                for parm_item, component in zip(tuple_parms, value):
                    parm_item.set(component)
                return True
            continue

        parm = node.parm(parm_name)
        if parm is None:
            continue
        try:
            parm.set(value)
            return True
        except Exception:
            pass
    return False


def findStandardSurfaceNode(subnet):
    for child in subnet.children():
        type_name = child.type().name().lower()
        node_name = child.name().lower()
        if "standard" in type_name and "surface" in type_name:
            return child
        if "standard" in node_name and "surface" in node_name:
            return child
    return None


def inputIndexByNormalizedName(node):
    try:
        input_names = list(node.inputNames())
    except Exception:
        return {}
    mapping = {}
    for index, input_name in enumerate(input_names):
        mapping[normalizeKey(input_name)] = index
    return mapping


def connectToFirstNamedInput(target_node, source_node, input_names):
    if target_node is None or source_node is None:
        return None
    input_map = inputIndexByNormalizedName(target_node)
    for input_name in input_names:
        key = normalizeKey(input_name)
        if key in input_map:
            target_node.setInput(input_map[key], source_node)
            return input_name
    return None


def connectToAllNamedInputs(target_node, source_node, input_names):
    connected = []
    if target_node is None or source_node is None:
        return connected
    input_map = inputIndexByNormalizedName(target_node)
    for input_name in input_names:
        key = normalizeKey(input_name)
        if key in input_map:
            target_node.setInput(input_map[key], source_node)
            connected.append(input_name)
    return connected


def semanticNames(mapping, name, fallback=None):
    normalized_name = normalizeKey(name)
    for key, names in mapping.items():
        if normalizeKey(key) == normalized_name:
            return names
    return fallback if fallback is not None else [name]


def semanticSetContains(values, name):
    normalized_name = normalizeKey(name)
    return any(normalizeKey(value) == normalized_name for value in values)


def createOctaneMaterialBuilder(parent, material_name, logger=None):
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
        _log(logger, "    Created Octane material builder: {0}".format(subnet.path()))
        return subnet
    except Exception as error:
        _log(logger, "    WARNING: Octane material builder failed for {0}: {1}".format(material_name, error))
        return createNode(parent, ["subnet"], material_name, logger=logger)


def textureNodeType(texture):
    texture_type = normalizeKey(texture.get("texture_type") or texture.get("type"))
    channel = normalizeKey(texture.get("channel"))
    if texture_type in (TEXTURE_TYPE_ALPHA, "alphaimage"):
        return OCTANE_ALPHA_IMAGE_NODE_TYPE
    if texture_type in (TEXTURE_TYPE_GREYSCALE, TEXTURE_TYPE_GRAYSCALE, "float", "floatimage"):
        return OCTANE_FLOAT_IMAGE_NODE_TYPE
    if semanticSetContains(FLOAT_TEXTURE_CHANNELS, channel):
        return OCTANE_FLOAT_IMAGE_NODE_TYPE
    return OCTANE_IMAGE_NODE_TYPE


def colorSpaceForTexture(texture):
    explicit = texture.get("color_space") or texture.get("colorspace")
    if explicit:
        return explicit
    if semanticSetContains(SRGB_CHANNELS, texture.get("channel")):
        return COLOR_SPACE_SRGB
    return COLOR_SPACE_NON_COLOR


def normalizeTextures(textures, texture_settings=None):
    texture_settings = texture_settings or {}
    records = []
    if isinstance(textures, dict):
        iterable = []
        for channel, value in textures.items():
            if isinstance(value, dict):
                texture = dict(value)
                texture.setdefault("channel", channel)
            else:
                texture = {"channel": channel, "path": value}
            iterable.append(texture)
    else:
        iterable = list(textures or [])

    for texture in iterable:
        texture = dict(texture)
        channel = texture.get("channel") or texture.get("name")
        texture["channel"] = channel
        if "path" not in texture and "file" in texture:
            texture["path"] = texture.get("file")
        settings = texture_settings.get(channel, {})
        if isinstance(settings, dict):
            merged = dict(settings)
            merged.update(texture)
            texture = merged
        records.append(texture)
    return records


def createTransform2DNode(subnet):
    node = createNode(subnet, [OCTANE_TRANSFORM_2D_NODE_TYPE], "texture_transform_2d")
    node.setPosition(hou.Vector2(-2, -4))
    return node


def createTransform3DNode(subnet, node_name):
    node = createNode(subnet, [OCTANE_TRANSFORM_3D_NODE_TYPE], node_name)
    node.setPosition(hou.Vector2(-4, -4))
    return node


def applyProjectionSettings(projection_node, projection_settings):
    if projection_node is None:
        return
    if "coordinate_space" in projection_settings:
        setFirstExistingParm(projection_node, ["positionType", "coordinateSpace", "coordinate_space", "space"], projection_settings["coordinate_space"])
    if "use_rest_attributes" in projection_settings:
        setFirstExistingParm(projection_node, ["useRestAttributes", "use_rest_attributes"], projection_settings["use_rest_attributes"])


def createProjectionSetup(subnet, projection_settings, transform_node=None):
    mode = projection_settings.get("mode", PROJECTION_MODE_UV)
    if mode in (None, PROJECTION_MODE_UV):
        return None, None

    if transform_node is None:
        transform_node = createTransform3DNode(subnet, "projection_transform_3d")
    node_info = PROJECTION_NODE_TYPES.get(mode)
    if node_info is None:
        return transform_node, None

    node_type, node_name = node_info
    projection_node = createNode(subnet, [node_type], node_name)
    projection_node.setPosition(hou.Vector2(-2, -6))
    connectToFirstNamedInput(
        projection_node,
        transform_node,
        ["transform", "translation", "rotation", "scale", "rotationOrder", "matrix"],
    )
    applyProjectionSettings(projection_node, projection_settings)
    return transform_node, projection_node


def createTriplanarTextureNode(subnet, image_node, transform_3d_node, channel, projection_settings=None):
    projection_settings = projection_settings or {}
    node = createNode(subnet, [OCTANE_TRIPLANAR_TEXTURE_NODE_TYPE], "{0}_triplanar".format(sanitizeNodeName(channel)))
    node.setPosition(image_node.position() + hou.Vector2(0, -2))
    connectToAllNamedInputs(
        node,
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
    connectToFirstNamedInput(node, transform_3d_node, ["transform", "translation", "rotation", "scale", "rotationOrder", "matrix"])
    applyProjectionSettings(node, projection_settings)
    return node


def createTextureFactorNode(subnet, texture_node, factor, channel):
    """Multiply a texture by a scalar or RGB factor and return the output node."""
    if isinstance(factor, (int, float)):
        factor = (float(factor),) * 3
    elif isinstance(factor, (list, tuple)):
        factor = tuple(float(value) for value in factor[:3])
    else:
        return texture_node
    if len(factor) != 3 or factor == (1.0, 1.0, 1.0):
        return texture_node

    rgb_node = createNode(
        subnet,
        [OCTANE_RGB_TEXTURE_NODE_TYPE],
        "{0}_factor".format(sanitizeNodeName(channel)),
    )
    setColorOrFloatParm(rgb_node, ["A_VALUE", "value", "color"], factor)
    rgb_node.setPosition(texture_node.position() + hou.Vector2(-1, -2))

    multiply_node = createNode(
        subnet,
        [OCTANE_MULTIPLY_TEXTURE_NODE_TYPE],
        "{0}_multiply".format(sanitizeNodeName(channel)),
    )
    multiply_node.setPosition(texture_node.position() + hou.Vector2(1, -2))
    first = connectToFirstNamedInput(
        multiply_node, texture_node, ["texture1", "input1", "texture"]
    )
    second = connectToFirstNamedInput(
        multiply_node, rgb_node, ["texture2", "input2", "factor"]
    )
    try:
        if first is None:
            multiply_node.setInput(0, texture_node)
        if second is None:
            multiply_node.setInput(1, rgb_node)
    except Exception:
        pass
    return multiply_node


def normalizeProjectionSettings(projection):
    if isinstance(projection, dict):
        settings = dict(projection)
    else:
        settings = {"mode": projection or PROJECTION_MODE_UV}
    settings.setdefault("mode", PROJECTION_MODE_UV)
    return settings


def normalizeDisplacementSettings(displacement):
    if isinstance(displacement, dict):
        settings = dict(displacement)
    else:
        settings = {"mode": displacement or DISPLACEMENT_MODE_TEXTURE}
    settings.setdefault("mode", DISPLACEMENT_MODE_TEXTURE)
    settings.setdefault("height", DEFAULT_DISPLACEMENT_HEIGHT)
    settings.setdefault("midlevel", DEFAULT_DISPLACEMENT_MIDLEVEL)
    settings.setdefault("create_disconnected", False)
    return settings


def createDisplacementNode(subnet, texture_node, displacement_settings):
    settings = normalizeDisplacementSettings(displacement_settings)
    if settings["mode"] == DISPLACEMENT_MODE_VERTEX:
        node_type = OCTANE_VERTEX_DISPLACEMENT_NODE_TYPE
        node_name = "vertex_displacement"
    else:
        node_type = OCTANE_TEXTURE_DISPLACEMENT_NODE_TYPE
        node_name = "texture_displacement"

    displacement_node = createNode(subnet, [node_type], node_name)
    displacement_node.setPosition(texture_node.position() + hou.Vector2(0, -2))
    connectToFirstNamedInput(displacement_node, texture_node, ["texture"])
    setFirstExistingParm(displacement_node, ["amount"], settings["height"])
    setFirstExistingParm(displacement_node, ["black_level", "midLevel", "midlevel"], settings["midlevel"])
    if "displace_rest_position" in settings:
        setFirstExistingParm(displacement_node, ["displaceRestPosition", "displace_rest_position"], settings["displace_rest_position"])
    return displacement_node


def isHeightChannel(channel):
    return semanticSetContains(HEIGHT_TEXTURE_CHANNELS, channel)


def connectHeightTexture(standard_surface, subnet, texture_node, displacement_settings, created_nodes):
    settings = normalizeDisplacementSettings(displacement_settings)
    if standard_surface is None:
        return
    if settings["mode"] == DISPLACEMENT_MODE_BUMP:
        if not settings["create_disconnected"]:
            connectToFirstNamedInput(standard_surface, texture_node, ["bump"])
        return

    displacement_node = createDisplacementNode(subnet, texture_node, settings)
    created_nodes.setdefault("displacement_nodes", []).append(displacement_node)
    if not settings["create_disconnected"]:
        connectToFirstNamedInput(standard_surface, displacement_node, ["displacement"])


def applyMaterialParameters(standard_surface, parameters, allow_unknown_parameters=True):
    if standard_surface is None:
        return
    for name, value in (parameters or {}).items():
        parm_names = semanticNames(PARAMETER_INPUTS, name, fallback=None)
        if parm_names is None:
            if not allow_unknown_parameters:
                continue
            parm_names = [name]
        setColorOrFloatParm(standard_surface, parm_names, value)


def setPreviewTexture(subnet, textures):
    texture_map = {}
    if isinstance(textures, dict):
        texture_map = textures
    basecolor = texture_map.get("base_color") or texture_map.get("basecolor")
    if isinstance(basecolor, dict):
        basecolor = basecolor.get("path") or basecolor.get("file")
    if not basecolor:
        return
    setFirstExistingParm(subnet, ["ogl_use_tex1"], 1)
    setFirstExistingParm(subnet, ["ogl_tex1"], basecolor)


def materialHasEmissionEnabled(material_spec):
    emission = (material_spec.get("parameters") or material_spec.get("inputs") or {}).get("emission", 0)
    try:
        return float(emission) > 0
    except Exception:
        return False


def connectEmissionColorFallback(standard_surface, material_spec, texture_nodes):
    if standard_surface is None or not materialHasEmissionEnabled(material_spec):
        return
    if "emission_color" in texture_nodes or "emissive" in texture_nodes:
        return
    basecolor_node = texture_nodes.get("base_color") or texture_nodes.get("basecolor")
    if basecolor_node is not None:
        connectToFirstNamedInput(standard_surface, basecolor_node, ["emissionColor"])


def createTextureNodes(subnet, standard_surface, material_spec, projection_settings, displacement_settings, logger=None):
    textures = normalizeTextures(material_spec.get("textures", {}), material_spec.get("texture_settings"))
    texture_nodes = {}
    created_nodes = {"displacement_nodes": []}
    transform_2d_node = None
    transform_3d_node = None
    projection_node = None
    projection_mode = projection_settings.get("mode", PROJECTION_MODE_UV)

    if textures:
        if projection_mode == PROJECTION_MODE_TRIPLANAR:
            transform_3d_node = createTransform3DNode(subnet, "triplanar_transform_3d")
            transform_3d_node, projection_node = createProjectionSetup(subnet, projection_settings, transform_node=transform_3d_node)
        else:
            transform_2d_node = createTransform2DNode(subnet)
            transform_3d_node, projection_node = createProjectionSetup(subnet, projection_settings)

    created_nodes["transform_2d"] = transform_2d_node
    created_nodes["transform_3d"] = transform_3d_node
    created_nodes["projection_node"] = projection_node

    for index, texture in enumerate(sorted(textures, key=lambda item: str(item.get("channel", "")).lower())):
        channel = texture.get("channel")
        texture_path = texture.get("path")
        if not channel or not texture_path:
            continue

        node_name = texture.get("node_name") or sanitizeNodeName(texture.get("name") or channel)
        image_node = createNode(subnet, [textureNodeType(texture)], node_name, logger=logger)
        image_node.setPosition(hou.Vector2(index * 2, -2))
        setFirstExistingParm(image_node, ["A_FILENAME", "filename", "file"], texture_path)
        setFirstExistingParm(image_node, ["colorSpace", "colorspace"], colorSpaceForTexture(texture))
        if "power" in texture:
            setFirstExistingParm(image_node, ["power", "A_POWER"], texture["power"])
        for parm_name, value in texture.get("parameters", {}).items():
            setFirstExistingParm(image_node, [parm_name], value)

        connectToFirstNamedInput(image_node, transform_2d_node, ["transform"])
        connectToFirstNamedInput(image_node, projection_node, ["projection"])

        output_node = image_node
        if projection_mode == PROJECTION_MODE_TRIPLANAR:
            output_node = createTriplanarTextureNode(subnet, image_node, transform_3d_node, channel, projection_settings)

        if "factor" in texture:
            output_node = createTextureFactorNode(
                subnet, output_node, texture["factor"], channel
            )

        if isHeightChannel(channel):
            connectHeightTexture(standard_surface, subnet, output_node, displacement_settings, created_nodes)
        else:
            input_names = texture.get("inputs") or semanticNames(STANDARD_SURFACE_INPUTS, channel, fallback=[channel])
            connected_input = connectToFirstNamedInput(standard_surface, output_node, input_names)
            if connected_input:
                _log(logger, "      Connected {0} to {1}".format(channel, connected_input))
            else:
                _log(logger, "      No connection mapping for channel: {0}".format(channel))

        texture_nodes[channel] = output_node
        _log(logger, "      Texture node {0}: {1}".format(channel, texture_path))

    connectEmissionColorFallback(standard_surface, material_spec, texture_nodes)
    created_nodes["texture_nodes"] = texture_nodes
    return created_nodes


def createOctaneMaterial(targetNetwork, name=None, textures=None, parameters=None, projection=None, displacement=None, options=None, logger=None):
    if hou is None:
        raise RuntimeError("This script must run inside Houdini.")

    options = options or {}
    material_spec = dict(options.get("material_spec") or {})
    material_name = name or material_spec.get("name") or material_spec.get("new_material") or "octane_material"
    material_spec["name"] = material_name
    material_spec["textures"] = textures if textures is not None else material_spec.get("textures", {})
    material_spec["parameters"] = parameters if parameters is not None else material_spec.get("parameters", material_spec.get("inputs", {}))
    material_spec["texture_settings"] = options.get("texture_settings", material_spec.get("texture_settings", {}))

    projection_settings = normalizeProjectionSettings(projection if projection is not None else options.get("projection", PROJECTION_MODE_UV))
    displacement_settings = normalizeDisplacementSettings(displacement if displacement is not None else options.get("displacement"))

    material_node_name = sanitizeNodeName(material_name)
    subnet = createOctaneMaterialBuilder(targetNetwork, material_node_name, logger=logger)
    setPreviewTexture(subnet, material_spec["textures"])
    standard_surface = findStandardSurfaceNode(subnet)

    for parm_name, value in options.get("surface_defaults", {}).items():
        setFirstExistingParm(standard_surface, [parm_name], value)
    applyMaterialParameters(
        standard_surface,
        material_spec["parameters"],
        allow_unknown_parameters=options.get("allow_unknown_parameters", True),
    )

    created_nodes = createTextureNodes(
        subnet,
        standard_surface,
        material_spec,
        projection_settings,
        displacement_settings,
        logger=logger,
    )

    if options.get("layout", True):
        subnet.layoutChildren()

    return {
        "subnet": subnet,
        "material": subnet,
        "standard_surface": standard_surface,
        "texture_nodes": created_nodes.get("texture_nodes", {}),
        "transform_2d": created_nodes.get("transform_2d"),
        "transform_3d": created_nodes.get("transform_3d"),
        "projection_node": created_nodes.get("projection_node"),
        "displacement_nodes": created_nodes.get("displacement_nodes", []),
    }


def create_octane_material(*args, **kwargs):
    return createOctaneMaterial(*args, **kwargs)
