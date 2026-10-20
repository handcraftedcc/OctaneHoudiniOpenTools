"""Compose USD assets and rebuild supported UsdShade surfaces for Octane."""

from __future__ import print_function

import hashlib
import io
import os
import tempfile
import zipfile

try:
    import hou
except ImportError:
    hou = None

import CargoImporter as cargo
import openToolsMaterialUtils as materials
import UsdMaterialX

USD_EXTENSIONS = (".usd", ".usda", ".usdc", ".usdz")


def usd_modules():
    try:
        from pxr import Ar, Sdf, Usd, UsdGeom, UsdShade
    except ImportError:
        raise RuntimeError("USD import requires Houdini's OpenUSD Python modules (pxr).")
    return Ar, Sdf, Usd, UsdGeom, UsdShade


def traverse(stage):
    Usd = usd_modules()[2]
    return stage.Traverse(Usd.TraverseInstanceProxies())


def open_stage(path, additional_layers=None):
    """Use a private session layer; never author into a source asset."""
    _Ar, Sdf, Usd, _Geom, _Shade = usd_modules()
    path = os.path.abspath(path)
    root = Sdf.Layer.FindOrOpen(path)
    if root is None:
        raise ValueError("Cannot open USD file: " + path)
    if additional_layers:
        composed = Sdf.Layer.CreateAnonymous("octane_import.usda")
        # Additional layers are explicit overrides at their original prim paths.
        composed.subLayerPaths = [os.path.abspath(p) for p in additional_layers] + [path]
        root = composed
    session = Sdf.Layer.CreateAnonymous("octane_variants.usda")
    stage = Usd.Stage.Open(root, session, Usd.Stage.LoadAll)
    if stage is None:
        raise ValueError("Cannot compose USD file: " + path)
    stage.SetEditTarget(session)
    return stage


def variant_records(stage):
    records = []
    for prim in traverse(stage):
        if prim.IsInstanceProxy():
            continue
        for name in prim.GetVariantSets().GetNames():
            variant = prim.GetVariantSet(name)
            records.append({"path": str(prim.GetPath()), "set": name,
                            "selection": variant.GetVariantSelection(),
                            "choices": variant.GetVariantNames()})
    return records


def apply_variants(stage, selections):
    """Selections are {prim_path: {variant_set: variant_name}}."""
    for path, sets in (selections or {}).items():
        prim = stage.GetPrimAtPath(path)
        if not prim:
            raise ValueError("Variant prim does not exist: " + path)
        for name, value in sets.items():
            variant = prim.GetVariantSet(name)
            if value not in variant.GetVariantNames():
                raise ValueError("Unknown variant {0} on {1}:{2}".format(value, path, name))
            if not variant.SetVariantSelection(value):
                raise ValueError("Cannot select variant on " + path)


def choose_variants(stage):
    if not variant_records(stage):
        return {}
    choice = hou.ui.displayMessage(
        "This asset has variants. Use its authored selections or choose them before importing?",
        buttons=("Authored defaults", "Choose variants", "Cancel"),
        default_choice=0, close_choice=2, title="USD Variants")
    if choice == 2:
        return None
    selections = {}
    if choice == 0:
        return selections
    seen = set()
    # Re-traverse after each selection: variants can introduce new variant sets.
    while True:
        pending = [r for r in variant_records(stage) if (r["path"], r["set"]) not in seen]
        if not pending:
            return selections
        record = pending[0]
        seen.add((record["path"], record["set"]))
        choices = record["choices"]
        if not choices:
            continue
        selected = hou.ui.selectFromList(
            choices, default_choices=((choices.index(record["selection"]),)
                                      if record["selection"] in choices else ()),
            message="{0}\nVariant set: {1}".format(record["path"], record["set"]),
            title="USD Variants", exclusive=True, clear_on_cancel=True)
        if not selected:
            return None
        value = choices[selected[0]]
        update = {record["path"]: {record["set"]: value}}
        apply_variants(stage, update)
        selections.setdefault(record["path"], {})[record["set"]] = value


def cache_directory(source):
    folder = os.path.join(os.path.dirname(source), os.path.splitext(os.path.basename(source))[0] + "_octane")
    try:
        os.makedirs(folder, exist_ok=True)
        # Check writability even when the directory already exists.
        with tempfile.TemporaryFile(dir=folder):
            pass
    except OSError:
        folder = os.path.join(tempfile.gettempdir(), "octane_usd", hashlib.sha256(source.encode("utf-8")).hexdigest()[:16])
        os.makedirs(folder, exist_ok=True)
    return folder


def local_asset(value, attribute, source, time):
    """Resolve against the authoring layer, including assets inside USDZ."""
    Ar, Sdf, _Usd, _Geom, _Shade = usd_modules()
    resolved = str(value.resolvedPath or "")
    if not resolved:
        for spec in attribute.GetPropertyStack(time):
            anchored = Sdf.ComputeAssetPathRelativeToLayer(spec.layer, value.path)
            resolved = str(Ar.GetResolver().Resolve(anchored))
            if resolved:
                break
    if not resolved:
        raise ValueError("Unresolved texture: " + value.path)
    if Ar.IsPackageRelativePath(resolved):
        outer, inner = Ar.SplitPackageRelativePathOuter(resolved)
        try:
            with zipfile.ZipFile(outer) as package:
                while Ar.IsPackageRelativePath(inner):
                    nested, inner = Ar.SplitPackageRelativePathOuter(inner)
                    data = package.read(nested)
                    package.close()
                    package = zipfile.ZipFile(io.BytesIO(data))
                data = package.read(inner)
                package.close()
        except (OSError, KeyError, zipfile.BadZipFile) as error:
            raise ValueError("Cannot read packaged texture {0}: {1}".format(resolved, error))
        extension = os.path.splitext(inner)[1]
        filename = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:20] + extension
        target = os.path.join(cache_directory(source), filename)
        with open(target, "wb") as handle:
            handle.write(data)
        return target
    return resolved


def material_record(material, source, time):
    Shade = usd_modules()[4]
    record = {"name": material.GetPrim().GetName(), "usdmaterialpath": str(material.GetPath()),
              "inputs": {}, "textures": {}, "warnings": []}
    warnings = record["warnings"]
    surface = None
    for context in ("mtlx", "", "mdl", "arnold", "ri"):
        shader = material.ComputeSurfaceSource(context)[0]
        if shader:
            shader_id = str(shader.GetIdAttr().Get() or "")
            if shader_id == "UsdPreviewSurface" or shader_id.startswith("ND_standard_surface_"):
                surface = shader
                break
    if not surface:
        warnings.append("No supported surface output at " + str(material.GetPath()))
        return record
    record["graph"] = UsdMaterialX.read_graph(surface, material, source, time, local_asset, warnings)
    record["inputs"] = {cargo.normalize_key(name): signal["value"]
                        for name, signal in record["graph"]["surface"].items() if "value" in signal}
    return record


def parse_materials(stage, source, time=None, bound_only=False):
    _Ar, _Sdf, Usd, Geom, Shade = usd_modules()
    time = Usd.TimeCode.Default() if time is None else Usd.TimeCode(time)
    found = {}
    if bound_only:
        for prim in traverse(stage):
            if prim.IsA(Geom.Gprim) or prim.IsA(Geom.Subset):
                for purpose in (Shade.Tokens.full, Shade.Tokens.allPurpose):
                    material = Shade.MaterialBindingAPI(prim).ComputeBoundMaterial(purpose)[0]
                    if material:
                        found[str(material.GetPath())] = material
    else:
        for prim in traverse(stage):
            if prim.IsA(Shade.Material):
                found[str(prim.GetPath())] = Shade.Material(prim)
    return [material_record(found[path], source, time) for path in sorted(found)]


def select_files(title):
    selected = hou.ui.selectFile(title=title, pattern="*.usd *.usda *.usdc *.usdz",
                                 multiple_select=True, chooser_mode=hou.fileChooserMode.Read)
    # Houdini returns a semicolon-separated string for multiple file selections.
    return [os.path.abspath(hou.expandString(p.strip())) for p in selected.split(";") if p.strip()] if selected else []


def choose_layers(path):
    """Ask about sibling layers only for low-level split entry files."""
    siblings = [p for p in os.listdir(os.path.dirname(path))
                if p.lower().endswith(USD_EXTENSIONS) and p != os.path.basename(path)]
    if os.path.splitext(os.path.basename(path))[0].lower() not in ("geo", "geometry", "mtl", "materials", "payload") or not siblings:
        return []
    choice = hou.ui.displayMessage(
        "You selected a split USD layer: {0}\nSibling files: {1}\n"
        "A model's entry USD normally composes its geometry and materials. "
        "Choose extra layers only if they use matching scene paths.".format(os.path.basename(path), ", ".join(sorted(siblings))),
        buttons=("Selected file only", "Add companion layers", "Cancel"),
        default_choice=0, close_choice=2, title="USD Layers")
    if choice == 2:
        return None
    if choice == 1:
        return select_files("Select companion USD layers (strongest first)") or None
    return []


def create_network(parent, path, records, height_mode, position, model_name):
    name = materials.sanitizeNodeName(model_name)
    matnet = cargo.create_node(parent, ["matnet", "materialnet"], "MATNET_" + name)
    import_node = cargo.create_node(parent, ["usdimport"], "IMPORT_" + name)
    unpack = cargo.create_node(parent, ["unpackusd::2.0", "unpackusd"], "UNPACK_" + name)
    assign = cargo.create_node(parent, ["material"], "MATERIAL_" + name)
    out = cargo.create_node(parent, ["null"], "OUT_" + name)
    for node, dy in ((matnet, 2), (import_node, 0), (unpack, -1.5), (assign, -3), (out, -4.5)):
        node.setPosition(hou.Vector2(position.x(), position.y() + dy))
    cargo.set_first_existing_parm(import_node, ["filepath1", "filepath", "file"], path)
    cargo.set_first_existing_parm(import_node, ["purpose"], "render")
    # One unpack pass can leave skeleton and nested model roots packed. Recurse
    # until polygon geometry exposes per-face material bindings to the Material SOP.
    unpack.setInput(0, import_node)
    cargo.set_first_existing_parm(unpack, ["unpacktraversal"], "std:boundables")
    cargo.set_first_existing_parm(unpack, ["limititerations"], 0)
    cargo.set_first_existing_parm(unpack, ["output"], 1)
    assign.setInput(0, unpack)
    out.setInput(0, assign)
    out.setDisplayFlag(True)
    out.setRenderFlag(True)
    cargo.set_first_existing_parm(assign, ["num_materials"], len(records))
    for index, record in enumerate(records):
        node = create_material(matnet, record, height_mode)
        cargo.set_material_assignment(assign, index, '"' + record["usdmaterialpath"] + '"', assign.relativePathTo(node))
    matnet.layoutChildren()
    return {"matnet": matnet, "import": import_node, "unpack": unpack, "material": assign, "out": out}


def create_material(parent, record, height_mode):
    if record.get("graph"):
        subnet = materials.createOctaneMaterialBuilder(parent, materials.sanitizeNodeName(record["name"]))
        surface = materials.findStandardSurfaceNode(subnet)
        if surface is None:
            subnet.destroy()
            raise RuntimeError("The Octane material builder did not provide an Octane standard surface. Check the Octane installation.")
        nodes = UsdMaterialX.build_graph(subnet, record["graph"], record["warnings"])
        UsdMaterialX.connect_surface(surface, record["graph"], nodes, record["warnings"])
        subnet.layoutChildren()
        return subnet
    raise RuntimeError("USD material has no supported surface graph: " + record["usdmaterialpath"])


def run(file_paths=None, import_mode=None, variant_selections=None, additional_layers=None, height_mode=None, time=None):
    if hou is None:
        raise RuntimeError("This script must run inside Houdini.")
    paths = file_paths or select_files("Select USD files to import")
    if isinstance(paths, str):
        paths = [paths]
    if not paths:
        return []
    if import_mode is None:
        choice = hou.ui.displayMessage("What do you want to import?", buttons=("Models and materials", "Materials only", "Cancel"),
                                       default_choice=0, close_choice=2, title="USD Importer")
        if choice == 2:
            return []
        import_mode = "materials" if choice == 1 else "models"
    if import_mode not in ("models", "materials"):
        raise ValueError("import_mode must be models or materials")
    prepared = []
    warnings = []
    for path in paths:
        path = os.path.abspath(hou.expandString(path))
        layers = additional_layers.get(path, []) if isinstance(additional_layers, dict) else additional_layers
        if layers is None:
            layers = choose_layers(path)
            if layers is None:
                return []
        stage = open_stage(path, layers)
        selections = variant_selections
        if selections is None:
            selections = choose_variants(stage)
            if selections is None:
                return []
        else:
            apply_variants(stage, selections)
        sample_time = hou.frame() if time is None else time
        records = parse_materials(stage, path, sample_time, bound_only=import_mode == "models")
        if layers or selections:
            # Flattening bakes composition and selected variants while retaining animation.
            # A unique cache keeps repeated imports from changing earlier node networks.
            descriptor, import_path = tempfile.mkstemp(suffix=".usda", prefix="composed_", dir=cache_directory(path))
            os.close(descriptor)
            if not stage.Flatten().Export(import_path):
                raise RuntimeError("Cannot save composed USD cache: " + import_path)
        else:
            import_path = path
        errors = stage.GetCompositionErrors()
        warnings.extend(str(error) for error in errors)
        prepared.append((path, import_path, records))
    created = []
    target = cargo.find_target_material_context() if import_mode == "materials" else cargo.find_target_model_context()
    start = cargo.next_right_position(target)
    with hou.undos.group("Import USD for Octane"):
        for index, (source, import_path, records) in enumerate(prepared):
            position = hou.Vector2(start.x() + index * cargo.MODEL_COLUMN_SPACING, start.y())
            if import_mode == "materials":
                for record in records:
                    node = create_material(target, record, height_mode)
                    node.setPosition(cargo.next_right_position(target))
                    created.append(node)
                continue
            model_name = os.path.splitext(os.path.basename(source))[0]
            parent = target
            if cargo.is_obj_network(target):
                parent = cargo.create_geo_container(target, model_name, position)
                position = hou.Vector2(0, 0)
            result = create_network(parent, import_path, records, height_mode, position, model_name)
            if time is not None:
                cargo.set_first_existing_parm(result["import"], ["importtime", "frame"], time)
            result["source"] = source
            created.append(result)
    for _source, _import_path, records in prepared:
        for record in records:
            warnings.extend(record["name"] + ": " + w for w in record["warnings"])
    for warning in warnings:
        print("USD Importer: " + warning)
    message = "Imported {0} USD file(s), built {1} Octane material(s).".format(len(prepared), sum(len(rs) for _p, _i, rs in prepared))
    if warnings:
        message += "\n{0} warning(s); see details below and the Python shell.".format(len(warnings))
    hou.ui.displayMessage(message, title="USD Importer", details="\n".join(warnings))
    return created


def main(kwargs=None):
    kwargs = kwargs or {}
    return run(file_paths=kwargs.get("file_paths") or kwargs.get("file_path"),
               import_mode=kwargs.get("import_mode"), variant_selections=kwargs.get("variant_selections"),
               additional_layers=kwargs.get("additional_layers"), height_mode=kwargs.get("height_mode"),
               time=kwargs.get("time"))


if __name__ in ("__main__", "__builtin__", "builtins"):
    run()
