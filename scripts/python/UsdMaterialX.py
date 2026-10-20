"""Copy composed UsdShade graphs to native Houdini MaterialX VOPs."""

import re

import openToolsMaterialUtils as materials


USD_TYPES = {
    "float": "float", "double": "float", "half": "float", "int": "integer",
    "bool": "boolean", "string": "string", "token": "string", "asset": "filename",
    "color3f": "color3", "color4f": "color4", "float2": "vector2",
    "float3": "vector3", "float4": "vector4", "normal3f": "vector3",
    "vector3f": "vector3", "point3f": "vector3", "matrix3d": "matrix33",
    "matrix4d": "matrix44",
}
PREVIEW_NAMES = {"diffuseColor": "base_color", "emissiveColor": "emission_color",
                 "metallic": "metalness", "roughness": "specular_roughness",
                 "ior": "specular_IOR", "clearcoat": "coat", "clearcoatRoughness": "coat_roughness",
                 "specularColor": "specular_color", "opacity": "opacity", "normal": "normal"}


def kind(attribute):
    return USD_TYPES.get(str(attribute.GetTypeName()), str(attribute.GetTypeName()))


def read_graph(surface, material, source, time, resolve_asset, warnings):
    """Keep nodes, output names, input types, and connections, without evaluating operations."""
    from pxr import UsdShade
    nodes, visiting = {}, set()

    def signal(attribute, trail=None):
        key = str(attribute.GetPath())
        trail = set(trail or ())
        if key in trail:
            warnings.append("Cyclic shader connection: " + key)
            return None
        trail.add(key)
        connections = attribute.GetConnections()
        if connections:
            if len(connections) != 1:
                warnings.append("Multiple sources for " + key)
                return None
            upstream = attribute.GetPrim().GetStage().GetAttributeAtPath(connections[0])
            if not upstream:
                warnings.append("Missing shader source: " + str(connections[0]))
                return None
            return signal(upstream, trail)
        if attribute.GetName().startswith("inputs:"):
            value = attribute.Get(time)
            if value is None:
                return None
            if kind(attribute) == "filename":
                try:
                    value = resolve_asset(value, attribute, source, time)
                except ValueError as error:
                    warnings.append(str(error))
                    # Preserve an unresolved authoring-layer path for manual repair.
                    from pxr import Sdf
                    stack = attribute.GetPropertyStack(time)
                    value = Sdf.ComputeAssetPathRelativeToLayer(stack[0].layer, value.path) if stack else value.path
            elif not isinstance(value, (str, int, float, bool)):
                value = tuple(value)
            return {"value": value, "type": kind(attribute)}
        shader = UsdShade.Shader(attribute.GetPrim())
        if not shader:
            warnings.append("Output has no shader source: " + key)
            return None
        path = str(shader.GetPath())
        if path in visiting:
            warnings.append("Cyclic shader connection: " + key)
            return None
        if path not in nodes:
            visiting.add(path)
            spec = {"path": path, "name": shader.GetPrim().GetName(),
                    "id": str(shader.GetIdAttr().Get() or ""), "inputs": {},
                    "outputs": {str(o.GetBaseName()): kind(o.GetAttr()) for o in shader.GetOutputs()}}
            if not spec["id"].startswith(("ND_", "UsdUVTexture", "UsdPrimvarReader_")):
                warnings.append("Unsupported shader node {0} at {1}".format(spec["id"] or "unknown", path))
            for inp in shader.GetInputs():
                val = signal(inp.GetAttr())
                if val is not None:
                    spec["inputs"][str(inp.GetBaseName())] = val
                color = inp.GetAttr().GetColorSpace()
                if color:
                    spec.setdefault("colorspaces", {})[str(inp.GetBaseName())] = str(color)
            visiting.remove(path)
            nodes[path] = spec
        return {"node": path, "output": str(attribute.GetBaseName()), "type": kind(attribute)}

    terminals = {}
    preview = str(surface.GetIdAttr().Get()) == "UsdPreviewSurface"
    for inp in surface.GetInputs():
        name = str(inp.GetBaseName())
        if preview:
            name = PREVIEW_NAMES.get(name)
            if name is None:
                continue
        val = signal(inp.GetAttr())
        if val is not None:
            terminals[name] = val
    if preview:
        terminals["base"] = {"value": 1.0, "type": "float"}
        if "emission_color" in terminals:
            terminals["emission"] = {"value": 1.0, "type": "float"}
        workflow = surface.GetInput("useSpecularWorkflow")
        if not workflow or not workflow.Get(time):
            terminals.pop("specular_color", None)
    displacement = None
    for context in ("mtlx", ""):
        shader, output, _kind = material.ComputeDisplacementSource(context)
        if shader:
            attr = shader.GetOutput(output).GetAttr()
            if attr:
                displacement = signal(attr)
            break
    return {"nodes": list(nodes.values()), "surface_id": str(surface.GetIdAttr().Get()),
            "surface": terminals, "displacement": displacement}


def node_signature(spec):
    """MaterialX nodedef IDs encode Houdini's operation and overload signature."""
    node_id = spec["id"]
    if not node_id.startswith("ND_"):
        raise ValueError("No MaterialX nodedef for " + node_id)
    parts = node_id[3:].split("_")
    # Categories can contain underscores; the overload starts with a type name.
    types = {"float", "integer", "boolean", "color3", "color4", "vector2", "vector3",
             "vector4", "matrix33", "matrix44", "string", "surfaceshader", "displacementshader"}
    boundary = next((i for i, part in enumerate(parts) if part in types or re.match(r"^(float|color[34]|vector[234])[A-Z]", part)), len(parts))
    category = "_".join(parts[:boundary])
    signature = "".join(parts[boundary:]) or "default"
    return category, signature


def set_value(node, name, signal):
    signature = node.parm("signature").evalAsString() if node.parm("signature") else ""
    names = [name + "_" + signature, name + "_" + signal["type"], name]
    value = signal["value"]
    if signal["type"] == "filename":
        value = str(value).replace("\\", "/")
    if isinstance(value, (tuple, list)):
        for candidate in names:
            parm = node.parmTuple(candidate)
            if parm is not None and len(parm) == len(value):
                parm.set(value)
                return
    else:
        for candidate in names:
            parm = node.parm(candidate)
            if parm is not None:
                parm.set(value)
                return
    raise ValueError("Missing parameter {0} ({1}) at {2}".format(name, signal["type"], node.path()))


def build_graph(subnet, graph, warnings):
    nodes = {}
    # Nodes are stored in dependency order. Build all before wiring so shared
    # outputs and multi-output shaders keep their source topology.
    for spec in graph["nodes"]:
        try:
            if spec["id"] == "UsdUVTexture":
                node = build_uv_texture(subnet, spec, nodes)
                nodes[spec["path"]] = node
                continue
            if spec["id"].startswith("UsdPrimvarReader_"):
                suffix = spec["id"].split("_")[-1]
                category, signature = "geompropvalue", USD_TYPES.get(suffix, suffix)
            else:
                category, signature = node_signature(spec)
            node = subnet.createNode("mtlx" + category, materials.sanitizeNodeName(spec["name"]))
            if node.parm("signature"):
                node.parm("signature").set(signature)
            nodes[spec["path"]] = node
            if len(spec["outputs"]) == 1 and len(node.outputNames()) == 1:
                original_output = next(iter(spec["outputs"]))
                nodes[(spec["path"], original_output)] = (node, node.outputNames()[0])
            if spec["id"].startswith("UsdPrimvarReader_"):
                for output in spec["outputs"]:
                    nodes[(spec["path"], output)] = (node, "out")
            node.setUserData("usd_shader_path", spec["path"])
            for name, val in spec["inputs"].items():
                if "value" in val:
                    try:
                        set_value(node, "geomprop" if name == "varname" and category == "geompropvalue" else name, val)
                    except Exception as error:
                        warnings.append("{0}.{1}: {2}".format(spec["path"], name, error))
            for name, color in spec.get("colorspaces", {}).items():
                parm = node.parm(name + "colorspace")
                if parm:
                    parm.set("Raw" if color in ("raw", "data") else color)
        except Exception as error:
            warnings.append("{0}: {1}".format(spec["path"], error))
    for spec in graph["nodes"]:
        node = nodes.get(spec["path"])
        if node is None:
            continue
        for name, val in spec["inputs"].items():
            if "node" not in val:
                continue
            try:
                upstream, output = endpoint(nodes, val)
                target_name = "texcoord" if spec["id"] == "UsdUVTexture" and name == "st" else name
                if spec["id"] == "UsdUVTexture" and name != "st":
                    raise ValueError("Connected UsdUVTexture " + name + " is unsupported")
                if spec["id"].startswith("UsdPrimvarReader_") and name == "varname":
                    target_name = "geomprop"
                node.setNamedInput(target_name, upstream, output)
            except Exception as error:
                warnings.append("Cannot connect {0}.{1}: {2}".format(spec["path"], name, error))
    return nodes


def endpoint(nodes, signal):
    return nodes.get((signal["node"], signal["output"]), (nodes[signal["node"]], signal["output"]))


def build_uv_texture(subnet, spec, nodes):
    """Represent USD texture channels with MaterialX image/extract/convert nodes."""
    prefix = materials.sanitizeNodeName(spec["name"])
    image = subnet.createNode("mtlximage", prefix)
    image.parm("signature").set("color4")
    inputs = spec["inputs"]
    for source, target in (("file", "file"), ("fallback", "default")):
        val = inputs.get(source)
        if val and "value" in val:
            set_value(image, target, val)
    color = inputs.get("sourceColorSpace", {}).get("value") or spec.get("colorspaces", {}).get("file")
    if color:
        image.parm("filecolorspace").set("Raw" if color == "raw" else "srgb_texture" if color == "sRGB" else "")
    for source, target in (("wrapS", "uaddressmode"), ("wrapT", "vaddressmode")):
        value = inputs.get(source, {}).get("value")
        if value and value != "useMetadata":
            image.parm(target).set({"repeat": "periodic", "clamp": "clamp", "mirror": "mirror", "black": "constant"}[value])
    output = image
    for source, operation, target in (("scale", "multiply", "in2"), ("bias", "add", "in2")):
        val = inputs.get(source)
        if val and "value" in val:
            node = subnet.createNode("mtlx" + operation, prefix + "_" + source)
            node.parm("signature").set("color4")
            node.setNamedInput("in1", output, "out")
            set_value(node, target, dict(val, type="color4"))
            output = node
    for name in spec["outputs"]:
        if name == "rgb":
            node = subnet.createNode("mtlxconvert", prefix + "_rgb")
            node.parm("signature").set("color4color3")
            node.setNamedInput("in", output, "out")
        elif name in "rgba" and len(name) == 1:
            node = subnet.createNode("mtlxextract", prefix + "_" + name)
            node.parm("signature").set("color4")
            node.parm("index").set("rgba".index(name))
            node.setNamedInput("in", output, "out")
        else:
            raise ValueError("Unknown USD texture output: " + name)
        nodes[(spec["path"], name)] = (node, "out")
    return image


def connect_surface(surface, graph, nodes, warnings):
    for name, signal in graph["surface"].items():
        names = materials.semanticNames(materials.PARAMETER_INPUTS, name)
        if name in ("normal", "coat_normal", "tangent"):
            names = {"normal": ["normal"], "coat_normal": ["coatingNormal", "coatNormal"], "tangent": ["tangent"]}[name]
        try:
            if "value" in signal:
                if not materials.setColorOrFloatParm(surface, names, signal["value"]):
                    # Octane has scalar UI controls for some color-valued USD
                    # inputs (notably opacity). Keep the value on a MaterialX
                    # constant and connect the texture pin instead of dropping it.
                    node = surface.parent().createNode("mtlxconstant", materials.sanitizeNodeName(name + "_value"))
                    node.parm("signature").set(signal["type"])
                    set_value(node, "value", signal)
                    if not materials.connectToFirstNamedInput(surface, node, names):
                        node.destroy()
                        warnings.append("Octane surface has no input for " + name)
            else:
                inputs = materials.inputIndexByNormalizedName(surface)
                pin = next((inputs[materials.normalizeKey(n)] for n in names if materials.normalizeKey(n) in inputs), None)
                if pin is None:
                    warnings.append("Octane surface has no input for " + name)
                else:
                    upstream, output = endpoint(nodes, signal)
                    surface.setInput(pin, upstream, upstream.outputIndex(output))
        except Exception as error:
            warnings.append("Cannot connect Octane surface {0}: {1}".format(name, error))
    signal = graph.get("displacement")
    if signal and "node" in signal:
        try:
            upstream, output = endpoint(nodes, signal)
            surface.setNamedInput("displacement", upstream, output)
        except Exception as error:
            warnings.append("Cannot connect MaterialX displacement: " + str(error))
