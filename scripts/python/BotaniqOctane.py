"""Minimal native-Octane material builder for the Botaniq importer.

``create_material(matnet, material, folder)`` intentionally accepts the small,
portable material records produced by the Botaniq extraction path.  It builds a
native Octane Standard Surface subnet and keeps the few artistic controls on the
containing material network.
"""
from __future__ import print_function

import os
import re

try:
    import hou
except ImportError:
    hou = None

import openToolsMaterialUtils
import BotaniqPaths


FOLIAGE_ROLES = set(["leaf", "grass"])
SUPPORTED_ROLES = FOLIAGE_ROLES | set(["bark", "stem", "rock", "basic"])
CHANNEL_ALIASES = {"base_color": "basecolor", "diffuse": "basecolor",
                   "albedo": "basecolor", "alpha": "opacity",
                   "transparency": "opacity", "bump": "height",
                   "subsurface_color": "backlight"}


def _safe_name(value):
    value = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "material")).strip("_")
    if not value:
        return "material"
    return "_" + value if value[0].isdigit() else value


def _warning(material, message):
    material.setdefault("warnings", []).append(message)


def _absolute_path(folder, path):
    path = str(path or "")
    if not path:
        return ""
    return BotaniqPaths.resolve_path(folder, path)


def _textures(material, folder):
    result = {}
    for channel, value in (material.get("textures") or {}).items():
        channel = CHANNEL_ALIASES.get(str(channel).lower(), str(channel).lower())
        if channel not in ("basecolor", "normal", "opacity", "height", "backlight", "roughness"):
            continue
        record = dict(value) if isinstance(value, dict) else {"path": value}
        path = _absolute_path(folder, record.get("path") or record.get("file"))
        if not path:
            _warning(material, "Ignored empty {0} texture path.".format(channel))
            continue
        record["path"] = path
        record["channel"] = channel
        record["node_name"] = _safe_name("{0}_{1}".format(material.get("name"), channel))
        # Exporters commonly label data textures simply as ``data``.  These
        # channels need a concrete Octane node type, not a luminance-derived RGB
        # image chosen by the shared helper.
        if channel == "opacity":
            record["type"] = "alpha"
        elif channel in ("height", "roughness"):
            record["type"] = "greyscale"
        result[channel] = record
    return result


def _first_parm(node, names):
    for name in names:
        parm = node.parm(name)
        if parm is not None:
            return parm
    return None


def _connect(target, source, names):
    connected = openToolsMaterialUtils.connectToFirstNamedInput(target, source, names)
    if connected is None:
        raise RuntimeError("{0} has none of the required inputs: {1}".format(target.path(), ", ".join(names)))


def _corrector(subnet, source, name):
    node = openToolsMaterialUtils.createNode(subnet, ["octane::NT_TEX_COLORCORRECTION"], name)
    _connect(node, source, ["texture"])
    return node


def _diffuse_bump(subnet, api_result, base_record):
    """Make an independent data-space float image for the last-resort bump."""
    node = openToolsMaterialUtils.createNode(
        subnet, [openToolsMaterialUtils.OCTANE_FLOAT_IMAGE_NODE_TYPE], "diffuse_bump")
    if not openToolsMaterialUtils.setFirstExistingParm(node, ["A_FILENAME", "filename", "file"], base_record["path"]):
        raise RuntimeError("Could not set the diffuse bump image path.")
    if not openToolsMaterialUtils.setFirstExistingParm(node, ["colorSpace", "colorspace"], openToolsMaterialUtils.COLOR_SPACE_NON_COLOR):
        raise RuntimeError("Could not set the diffuse bump image color space.")
    transform = api_result.get("transform_2d")
    if transform is not None:
        _connect(node, transform, ["transform"])
    return node


def _ensure_global_controls(matnet):
    if matnet.parm("global_hue") is not None:
        return
    group = matnet.parmTemplateGroup()
    group.append(hou.FolderParmTemplate("botaniq_global", "Global", [
        hou.FloatParmTemplate("global_hue", "Hue", 1, default_value=(0.0,)),
        hou.FloatParmTemplate("global_saturation", "Saturation", 1, default_value=(1.0,)),
        hou.FloatParmTemplate("global_brightness", "Brightness", 1, default_value=(1.0,)),
        hou.FloatParmTemplate("global_roughness", "Roughness Mult", 1, default_value=(1.0,)),
        hou.FloatParmTemplate("global_specular", "Specular Mult", 1, default_value=(1.0,)),
        hou.FloatParmTemplate("global_subsurface", "Subsurface Mult", 1, default_value=(1.0,)),
    ], folder_type=hou.folderType.Tabs))
    matnet.setParmTemplateGroup(group)


def _control_folder(matnet, material, specs):
    """Add one material tab and return control names keyed by semantic name."""
    base = _safe_name(material.get("name"))
    group = matnet.parmTemplateGroup()
    templates = []
    names = {}
    for key, label, default in specs:
        name = "{0}_{1}".format(base, key)
        suffix = 1
        while matnet.parm(name) is not None:
            name = "{0}_{1}_{2}".format(base, key, suffix)
            suffix += 1
        templates.append(hou.FloatParmTemplate(name, label, 1, default_value=(default,)))
        names[key] = name
    group.append(hou.FolderParmTemplate("{0}_controls".format(base), str(material.get("name") or base), templates,
                                        folder_type=hou.folderType.Tabs))
    matnet.setParmTemplateGroup(group)
    return names


def _expression(node, matnet, control, global_control=None, multiply=False):
    relative = node.relativePathTo(matnet)
    local = 'ch("{0}/{1}")'.format(relative, control)
    if not global_control:
        return local
    global_value = 'ch("{0}/{1}")'.format(relative, global_control)
    return "{0} * {1}".format(local, global_value) if multiply else "{0} + {1}".format(local, global_value)


def _set_expression(node, names, expression):
    parm = _first_parm(node, names)
    if parm is None:
        raise RuntimeError("Missing Octane parameter on {0}: {1}".format(node.path(), ", ".join(names)))
    parm.setExpression(expression, language=hou.exprLanguage.Hscript)


def _promote(matnet, material, surface, base_corrector, subsurface_corrector, strength_kind):
    role = str(material.get("role") or "basic").lower()
    parameters = material.get("parameters") or {}
    specs = [("hue", "Base Hue", 0.0), ("saturation", "Base Saturation", 1.0),
             ("brightness", "Base Brightness", 1.0),
             ("roughness", "Roughness", float(parameters.get("roughness", 0.5))),
             ("specular", "Specular", float(parameters.get("specular", 0.5)))]
    if role in FOLIAGE_ROLES:
        specs += [("subsurface", "Subsurface", 0.6), ("subsurface_hue", "Subsurface Hue", 0.0),
                  ("subsurface_saturation", "Subsurface Saturation", 1.0),
                  ("subsurface_brightness", "Subsurface Brightness", 1.0)]
    if strength_kind:
        specs.append((strength_kind, "{0} Strength".format(strength_kind.title()), 1.0 if strength_kind == "normal" else 0.1))
    controls = _control_folder(matnet, material, specs)
    _set_expression(base_corrector, ["hue"], _expression(base_corrector, matnet, controls["hue"], "global_hue"))
    _set_expression(base_corrector, ["saturation"], _expression(base_corrector, matnet, controls["saturation"], "global_saturation", True))
    _set_expression(base_corrector, ["brightness"], _expression(base_corrector, matnet, controls["brightness"], "global_brightness", True))
    _set_expression(surface, ["roughness"], _expression(surface, matnet, controls["roughness"], "global_roughness", True))
    _set_expression(surface, ["specular"], _expression(surface, matnet, controls["specular"], "global_specular", True))
    if role in FOLIAGE_ROLES:
        _set_expression(surface, ["subsurface"], _expression(surface, matnet, controls["subsurface"], "global_subsurface", True))
        _set_expression(subsurface_corrector, ["hue"], _expression(subsurface_corrector, matnet, controls["subsurface_hue"]))
        _set_expression(subsurface_corrector, ["saturation"], _expression(subsurface_corrector, matnet, controls["subsurface_saturation"]))
        _set_expression(subsurface_corrector, ["brightness"], _expression(subsurface_corrector, matnet, controls["subsurface_brightness"]))
    if strength_kind:
        _set_expression(surface, ["bumpHeight"], _expression(surface, matnet, controls[strength_kind]))


def create_material(matnet, material, folder):
    """Create and return one compact native-Octane material subnet.

    ``material`` is mutated only to append actionable warnings for intentionally
    omitted optional maps and fallback relief wiring.
    """
    if hou is None:
        raise RuntimeError("BotaniqOctane must run inside Houdini.")
    if matnet is None:
        raise ValueError("A target material network is required.")
    if not isinstance(material, dict) or not material.get("name"):
        raise ValueError("Material records require a name.")
    role = str(material.get("role") or "basic").lower()
    if role not in SUPPORTED_ROLES:
        _warning(material, "Unknown role '{0}'; using basic surface behavior.".format(role))
        role = "basic"
    texture_records = _textures(material, folder)
    if "normal" in texture_records and "height" in texture_records:
        del texture_records["height"]
        _warning(material, "Ignored height map because a normal map takes precedence.")
    parameters = dict(material.get("parameters") or {})
    result = openToolsMaterialUtils.createOctaneMaterial(
        matnet, name=material["name"], textures=texture_records, parameters=parameters,
        projection={"mode": openToolsMaterialUtils.PROJECTION_MODE_UV},
        displacement={"mode": openToolsMaterialUtils.DISPLACEMENT_MODE_BUMP},
        options={"layout": False, "surface_defaults": {"thinWall": 1 if role in FOLIAGE_ROLES else 0}},
    )
    subnet, surface, images = result["subnet"], result["standard_surface"], result["texture_nodes"]
    if surface is None:
        raise RuntimeError("Octane material builder did not contain a Standard Surface node.")
    source_base = images.get("basecolor")
    base = source_base
    if base is None:
        _warning(material, "No basecolor texture; using the Standard Surface base color.")
        base = openToolsMaterialUtils.createNode(subnet, [openToolsMaterialUtils.OCTANE_RGB_TEXTURE_NODE_TYPE], "basecolor_constant")
        color = tuple(parameters.get("base_color", (0.5, 0.5, 0.5)))[:3]
        if len(color) != 3 or not openToolsMaterialUtils.setColorOrFloatParm(base, ["A_VALUE", "value", "color"], color):
            raise RuntimeError("Could not set the fallback base color.")
    base_corrector = _corrector(subnet, base, "basecolor_correction")
    _connect(surface, base_corrector, ["baseColor"])
    subsurface_corrector = None
    if role in FOLIAGE_ROLES:
        subsurface_source = images.get("backlight") or base
        subsurface_corrector = _corrector(subnet, subsurface_source, "subsurface_correction")
        _connect(surface, subsurface_corrector, ["subsurfaceColor"])
    strength_kind = None
    # Native Octane's Normal Texture node has no strength parameter; Standard
    # Surface ``bumpHeight`` only affects its bump input.  Only promote a
    # strength control when we have actually connected bump relief.
    if images.get("normal") is None and images.get("height") is not None:
        strength_kind = "bump"
    elif images.get("normal") is None and role not in FOLIAGE_ROLES and source_base is not None:
        strength_kind = "bump"
        _connect(surface, _diffuse_bump(subnet, result, texture_records["basecolor"]), ["bump"])
        _warning(material, "No normal or height map; using low-strength diffuse-to-bump fallback.")
    _ensure_global_controls(matnet)
    _promote(matnet, material, surface, base_corrector, subsurface_corrector, strength_kind)
    subnet.layoutChildren()
    return subnet
