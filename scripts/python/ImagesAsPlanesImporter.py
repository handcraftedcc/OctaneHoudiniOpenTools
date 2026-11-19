"""Import images as aspect-correct planes with native Octane materials."""

import os

try:
    import hou
except ImportError:
    hou = None

import openToolsMaterialUtils as materials
import openToolsUtils


DEFAULT_OPTIONS = {"shared_matnet": True, "use_existing_matnet": False,
                   "material_mode": "individual"}
COLOR_CONTROLS = (("hue", "Hue", 0.0), ("saturation", "Saturation", 1.0),
                  ("brightness", "Brightness", 1.0), ("gamma", "Gamma", 1.0),
                  ("contrast", "Contrast", 0.0), ("gain", "Gain", 1.0),
                  ("exposure", "Exposure", 0.0))
OPACITY_RAYS = (("camera", "Camera", "texture"), ("shadow", "Shadow", "texture1"),
                ("diffuse", "Diffuse", "texture2"), ("reflection", "Reflection", "texture3"),
                ("refraction", "Refraction", "texture4"), ("ao", "AO", "texture5"),
                ("photon", "Photon", "texture6"))


def normalize_paths(paths):
    """Keep selection order (the tile order), discarding duplicate files."""
    if isinstance(paths, str):
        paths = paths.split(";")
    result, seen = [], set()
    for path in paths or []:
        path = str(path).strip()
        if not path:
            continue
        path = os.path.abspath(os.path.normpath(hou.expandString(path) if hou else path))
        key = os.path.normcase(path)
        if key not in seen:
            seen.add(key)
            result.append(path.replace("\\", "/"))
    return result


def normalize_options(options=None):
    options = dict(options or {})
    # Migrate saved options from the initial importer implementation.
    for old, new in (("shared_subnet", "shared_matnet"), ("use_existing_subnet", "use_existing_matnet")):
        if old in options:
            options.setdefault(new, options.pop(old))
    result = dict(DEFAULT_OPTIONS)
    result.update(options)
    if result["material_mode"] not in ("individual", "shared"):
        raise ValueError("Material mode must be 'individual' or 'shared'.")
    result["use_existing_matnet"] = bool(result["shared_matnet"] and result["use_existing_matnet"])
    return result


def find_target_context():
    editor = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
    node = editor.pwd() if editor else hou.node("/obj")
    while node is not None:
        if node.childTypeCategory() in (hou.objNodeTypeCategory(), hou.sopNodeTypeCategory()):
            return node
        node = node.parent()
    return hou.node("/obj")


def _position(parent):
    children = parent.children()
    return hou.Vector2(max(n.position().x() for n in children) + 3,
                       max(n.position().y() for n in children)) if children else hou.Vector2(0, 0)


def _node(parent, type_name, name):
    name = materials.uniquifyNodeName(parent, name)
    node = materials.createNode(parent, [type_name], name)
    # Octane's OnCreated hook replaces the requested name with its type label.
    node.setName(name, unique_name=True)
    return node


def _set(node, name, value):
    parm = node.parm(name)
    if parm is None:
        raise RuntimeError("{0} requires parameter '{1}'. Check your Octane version.".format(node.path(), name))
    parm.set(value)
    return parm


def _connect(target, source, name):
    if materials.connectToFirstNamedInput(target, source, [name]) is None:
        raise RuntimeError("Missing input '{0}' on {1}".format(name, target.path()))


def _channel(node, matnet, name):
    return 'ch("{0}/{1}")'.format(node.relativePathTo(matnet), name)


def _link(node, parm, matnet, control):
    _set(node, parm, 0).setExpression(_channel(node, matnet, control), hou.exprLanguage.Hscript)


def _controls(matnet):
    group = matnet.parmTemplateGroup()
    color = hou.FloatParmTemplate("basecolor", "Solid Basecolor", 3, default_value=(0, 0, 0),
                                 naming_scheme=hou.parmNamingScheme.RGBA, look=hou.parmLook.ColorSquare)
    color.setConditional(hou.parmCondType.DisableWhen, "{ use_basecolor_texture == 1 }")
    mask_controls = [
        hou.ToggleParmTemplate("use_rgb_as_alpha", "Use RGB as Alpha", default_value=False),
        hou.ToggleParmTemplate("invert_image_alpha", "Invert Opacity Mask", default_value=False),
    ]
    for template in mask_controls:
        template.setConditional(hou.parmCondType.DisableWhen, "{ image_alpha_to_opacity == 0 }")
    ray_controls = []
    for name, label, _ in OPACITY_RAYS:
        template = hou.FloatParmTemplate("opacity_ray_" + name, label + " Ray", 1,
            default_value=(1,), min=0, max=1, min_is_strict=True, max_is_strict=True)
        template.setHelp("Multiply opacity for this ray type. Set Shadow Ray to 0 to disable decal shadows.")
        ray_controls.append(template)
    ray_folder = hou.FolderParmTemplate("opacity_ray_switch", "Ray Switch Opacity",
        ray_controls, folder_type=hou.folderType.Collapsible)
    opacity_controls = mask_controls + [ray_folder]
    templates = [hou.FloatParmTemplate("emission_amount", "Emission Amount", 1, default_value=(0,), min=0),
                 hou.ToggleParmTemplate("use_basecolor_texture", "Basecolor Texture", default_value=True),
                 color,
                 hou.ToggleParmTemplate("image_alpha_to_opacity", "Image Alpha to Opacity", default_value=True)]
    templates += opacity_controls
    templates.append(hou.ToggleParmTemplate("color_adjustment", "Color Adjustment", default_value=False))
    for name, label, default in COLOR_CONTROLS:
        template = hou.FloatParmTemplate(name, label, 1, default_value=(default,))
        template.setConditional(hou.parmCondType.DisableWhen, "{ color_adjustment == 0 }")
        templates.append(template)
    invert = hou.ToggleParmTemplate("invert", "Invert", default_value=False)
    invert.setConditional(hou.parmCondType.DisableWhen, "{ color_adjustment == 0 }")
    templates.append(invert)
    templates = [template for template in templates if group.find(template.name()) is None]
    changed = False
    # Add new opacity controls alongside the toggle when reusing an older matnet.
    if group.find("image_alpha_to_opacity") is not None:
        previous = "image_alpha_to_opacity"
        for template in opacity_controls:
            if group.find(template.name()) is None:
                group.insertAfter(previous, template)
                changed = True
            previous = template.name()
        templates = [template for template in templates if group.find(template.name()) is None]
    if templates:
        folder_name = "images_as_planes"
        while group.find(folder_name) is not None:
            folder_name += "_controls"
        group.append(hou.FolderParmTemplate(folder_name, "Images as Planes", templates))
        changed = True
    if changed:
        matnet.setParmTemplateGroup(group)


def _switch(subnet, name, off, on, matnet, control):
    node = _node(subnet, "octane::NT_SWITCH_TEXTURE", name)
    _set(node, "A_OPTION_COUNT", 2)
    _connect(node, off, "Input_1")
    _connect(node, on, "Input_2")
    node.parm("index").setExpression("1 + " + _channel(node, matnet, control), hou.exprLanguage.Hscript)
    return node


def _tiles(subnet, paths, name, image_format=None):
    node = _node(subnet, "octane::NT_TEX_IMAGE_TILES", name)
    # File-list mode is supplied by recent Octane Houdini plugins. Fail clearly
    # on older versions instead of silently producing an empty material.
    _set(node, "mode", "1")
    _set(node, "filelist", len(paths))
    _set(node, "A_GRID_SIZEmin", len(paths))
    _set(node, "A_GRID_SIZEmax", 1)
    for index, path in enumerate(paths):
        _set(node, "imageFile{0}".format(index), path)
    if image_format is not None:
        _set(node, "A_IMAGE_COLOR_FORMAT", image_format)
    return node


def _viewport(subnet, paths):
    """Replace Octane's fixed diffuse UI with Houdini's native multiparm."""
    group = subnet.parmTemplateGroup()
    # The builder uses a fixed diffuseMap folder plus an external use toggle.
    for name in ("ogl_numtex", "diffuseMap", "ogl_use_tex1"):
        if group.find(name) is not None:
            group.remove(name)
    # Accommodate builders that place the fixed texture parms directly in a tab.
    for template in list(group.entriesWithoutFolders()):
        if template.name().startswith(("ogl_tex", "ogl_use_tex")):
            group.remove(template.name())
    group.append(hou.properties.parmTemplate("material", "ogl_numtex"))
    subnet.setParmTemplateGroup(group)
    _set(subnet, "ogl_numtex", len(paths))
    for index, path in enumerate(paths):
        suffix = str(index + 1)
        _set(subnet, "ogl_tex" + suffix, path)
        # Blank defaults select uv2, uv3, ... for subsequent layers.
        _set(subnet, "ogl_texuvset" + suffix, "uv")
        _set(subnet, "ogl_use_tex" + suffix, 1)
        subnet.parmTuple("ogl_tex_uvtranslate" + suffix).set((index, 0))
        # Avoid repeating every layer across the other images' UV tiles.
        _set(subnet, "ogl_tex_wrap" + suffix, "decal")
        _set(subnet, "ogl_tex_vwrap" + suffix, "decal")


def create_material(matnet, paths, name, shared=False):
    result = materials.createOctaneMaterial(matnet, name=name, textures={},
        parameters={"base": 1.0, "roughness": 0.5, "thin_walled": 1}, options={"layout": False})
    subnet, surface = result["subnet"], result["standard_surface"]
    if surface is None:
        raise RuntimeError("The Octane material builder did not create a Standard Surface material.")
    if shared:
        source = _tiles(subnet, paths, "images")
        alpha = _tiles(subnet, paths, "image_alpha", image_format=3)  # Alpha image
        rgb_mask = _tiles(subnet, paths, "rgb_mask", image_format=2)  # Greyscale
    else:
        source = _node(subnet, materials.OCTANE_IMAGE_NODE_TYPE, "image")
        alpha = _node(subnet, materials.OCTANE_ALPHA_IMAGE_NODE_TYPE, "image_alpha")
        rgb_mask = _node(subnet, materials.OCTANE_FLOAT_IMAGE_NODE_TYPE, "rgb_mask")
        for node in (source, alpha, rgb_mask):
            _set(node, "A_FILENAME", paths[0])
    _set(source, "colorSpace", materials.COLOR_SPACE_SRGB)
    for mask in (alpha, rgb_mask):
        _set(mask, "colorSpace", materials.COLOR_SPACE_NON_COLOR)
        _set(mask, "gamma", 1)
        _link(mask, "invert", matnet, "invert_image_alpha")
    correction = _node(subnet, "octane::NT_TEX_COLORCORRECTION", "color_adjustment")
    _connect(correction, source, "texture")
    for control in [item[0] for item in COLOR_CONTROLS] + ["invert"]:
        _link(correction, control, matnet, control)
    adjusted = _switch(subnet, "enable_color_adjustment", source, correction, matnet, "color_adjustment")
    solid = _node(subnet, materials.OCTANE_RGB_TEXTURE_NODE_TYPE, "solid_basecolor")
    for component in "rgb":
        _link(solid, "A_VALUE" + component, matnet, "basecolor" + component)
    base = _switch(subnet, "enable_basecolor_texture", solid, adjusted, matnet, "use_basecolor_texture")
    _connect(surface, base, "baseColor")
    _connect(surface, adjusted, "emissionColor")
    _link(surface, "emissionWeight", matnet, "emission_amount")
    opaque = _node(subnet, materials.OCTANE_RGB_TEXTURE_NODE_TYPE, "opaque")
    opaque.parmTuple("A_VALUE").set((1, 1, 1))
    mask = _switch(subnet, "opacity_mask_source", alpha, rgb_mask, matnet, "use_rgb_as_alpha")
    opacity = _switch(subnet, "enable_image_alpha", opaque, mask, matnet, "image_alpha_to_opacity")
    ray_switch = _node(subnet, "octane::NT_TEX_RAY_SWITCH", "opacity_ray_switch")
    for name, _, parm in OPACITY_RAYS:
        _link(ray_switch, parm, matnet, "opacity_ray_" + name)
    opacity_multiply = _node(subnet, materials.OCTANE_MULTIPLY_TEXTURE_NODE_TYPE, "opacity_multiply")
    _connect(opacity_multiply, opacity, "texture1")
    _connect(opacity_multiply, ray_switch, "texture2")
    _connect(surface, opacity_multiply, "opacity")
    _viewport(subnet, paths)
    subnet.layoutChildren()
    return subnet


def create_plane(parent, record, material, tile):
    """Use editable native SOPs; saved networks do not depend on this module."""
    name = materials.sanitizeNodeName(record["name"])
    grid = _node(parent, "grid", "PLANE_" + name)
    grid.setParms({"rows": 2, "cols": 2, "orient": 0,
                   "sizex": record["size"][0], "sizey": record["size"][1]})
    uv = _node(parent, "attribwrangle", "UV_" + name)
    uv.setInput(0, grid)
    uv.parm("class").set("vertex")
    uv.parm("snippet").set('vector p = relbbox(0, @P);\nv@uv = set(p.x + %d, p.y, 0);' % tile)
    name_node = _node(parent, "name", "NAME_" + name)
    name_node.setInput(0, uv)
    name_node.setParms({"attribname": "name", "numnames": 1, "name1": record["name"]})
    attributes = _node(parent, "attribcreate", "IMAGE_PATH_" + name)
    attributes.setInput(0, name_node)
    attributes.setParms({"name1": "image_path", "class1": 1, "type1": 3, "string1": record["path"]})
    assignment = _node(parent, "material", "MATERIAL_" + name)
    assignment.setInput(0, attributes)
    _set(assignment, "shop_materialpath1", assignment.relativePathTo(material))
    out = _node(parent, "null", "OUT_" + name)
    out.setInput(0, assignment)
    return {"grid": grid, "uv": uv, "name": name_node, "attributes": attributes, "assignment": assignment,
            "out": out, "material": material, "image": record["path"]}


def _labs_layout(parent, merge):
    """Offer an editable layout when Labs is installed; keep imports centered."""
    types = hou.sopNodeTypeCategory().nodeTypes()
    type_name = next((name for name in ("labs::align_and_distribute::2.0",
                                      "labs::align_and_distribute") if name in types), None)
    if type_name is None:
        return None
    layout = _node(parent, type_name, "LAYOUT_images")
    layout.setInput(0, merge)
    for name, value in {"layout": 0, "axis": 0, "spacing": 0.25}.items():
        materials.setFirstExistingParm(layout, [name], value)
    for name in ("alignment", "justifyx", "justifyy", "justifyz"):
        parm = layout.parm(name)
        if parm is not None:
            template = parm.parmTemplate()
            for token, label in zip(template.menuItems(), template.menuLabels()):
                if "center" in label.lower():
                    parm.set(token)
                    break
    layout.bypass(True)
    layout.setComment("Enable this node to lay out the centered image planes.")
    return layout


def existing_matnets(target):
    return [node for node in target.children() if node.type().name() in ("matnet", "materialnet")]


def _existing_matnet(target, options):
    candidates = existing_matnets(target)
    reference = options.get("existing_matnet")
    if reference:
        node = target.node(reference) if isinstance(reference, str) else reference
        if node not in candidates:
            raise ValueError("Choose a matnet in the current SOP network.")
        return node
    selected = [node for node in candidates if node.isSelected()]
    if len(selected) == 1:
        return selected[0]
    if len(candidates) == 1:
        return candidates[0]
    raise ValueError("Select an existing matnet in the current SOP network, or choose one in the import options.")


def import_images(paths, options=None, target=None):
    """Return one result per matnet. SOP chains stay in the current network."""
    if hou is None:
        raise RuntimeError("Images as Planes must run inside Houdini.")
    options = normalize_options(options)
    paths = normalize_paths(paths)
    if not paths:
        return []
    records = []
    for path in paths:
        if not os.path.isfile(path):
            raise ValueError("Image does not exist: " + path)
        width, height = hou.imageResolution(path)
        if width <= 0 or height <= 0:
            raise ValueError("Could not read image dimensions: " + path)
        scale = float(max(width, height))
        records.append({"path": path, "name": os.path.splitext(os.path.basename(path))[0],
                        "size": (width / scale, height / scale)})
    target = target if target is not None else find_target_context()
    is_obj = target.childTypeCategory() == hou.objNodeTypeCategory()
    if not is_obj and target.childTypeCategory() != hou.sopNodeTypeCategory():
        raise ValueError("Images as Planes requires an OBJ or SOP network.")
    batches = [records] if options["shared_matnet"] else [[record] for record in records]
    reused_matnet = _existing_matnet(target, options) if not is_obj and options["use_existing_matnet"] else None
    original_materials = set(reused_matnet.children()) if reused_matnet is not None else set()
    original_templates = reused_matnet.parmTemplateGroup() if reused_matnet is not None else None
    results, created = [], []
    previous_display = target.displayNode() if not is_obj else None
    previous_render = target.renderNode() if not is_obj else None
    with hou.undos.group("Import Images as Planes"):
        try:
            for batch in batches:
                name = "images_as_planes" if options["shared_matnet"] else materials.sanitizeNodeName(batch[0]["name"])
                direct = not is_obj
                if direct:
                    container = target
                    origin = _position(container)
                else:
                    origin = _position(target)
                    container = _node(target, "geo", name)
                    created.append(container)
                    container.setPosition(origin)
                    origin = hou.Vector2(0, 0)
                    for child in container.children():
                        child.destroy()
                matnet = reused_matnet if reused_matnet is not None else _node(container, "matnet", "MATNET_images")
                if direct and reused_matnet is None:
                    created.append(matnet)
                materials_before = set(matnet.children())
                _controls(matnet)
                shared = options["material_mode"] == "shared"
                # Separate containers keep independent controls, but use the
                # same full selection and tile indices in shared-material mode.
                shared_material = create_material(matnet, paths, "images", True) if shared else None
                planes = []
                before = set(container.children())
                try:
                    for index, record in enumerate(batch):
                        material = shared_material or create_material(matnet, [record["path"]], record["name"])
                        tile = paths.index(record["path"]) if shared else 0
                        plane = create_plane(container, record, material, tile)
                        planes.append(plane)
                    merge = _node(container, "merge", "MERGE_images")
                    for index, plane in enumerate(planes):
                        merge.setInput(index, plane["out"])
                    layout = _labs_layout(container, merge)
                    out = _node(container, "null", "OUT_images")
                    out.setInput(0, layout if layout is not None else merge)
                    out.setDisplayFlag(True)
                    out.setRenderFlag(True)
                finally:
                    if direct:
                        created.extend(n for n in container.children() if n not in before)
                new_materials = [n for n in matnet.children() if n not in materials_before]
                material_origin = _position(matnet) if materials_before else hou.Vector2(0, 0)
                matnet.layoutChildren(items=new_materials)
                for node in new_materials:
                    node.setPosition(node.position() + material_origin)
                new_nodes = [n for n in container.children() if n not in before]
                if reused_matnet is None:
                    new_nodes.append(matnet)
                container.layoutChildren(items=new_nodes)
                for node in new_nodes:
                    node.setPosition(node.position() + origin)
                results.append({"container": container, "matnet": matnet, "planes": planes,
                                "merge": merge, "layout": layout, "out": out})
        except Exception:
            if reused_matnet is not None:
                for node in reused_matnet.children():
                    if node not in original_materials:
                        node.destroy()
                reused_matnet.setParmTemplateGroup(original_templates)
            for node in reversed(created):
                node.destroy()
            if previous_display is not None:
                previous_display.setDisplayFlag(True)
            if previous_render is not None:
                previous_render.setRenderFlag(True)
            raise
    return results


def choose_options(target):
    try:
        from PySide6 import QtWidgets
    except ImportError:
        from PySide2 import QtWidgets
    saved = normalize_options(openToolsUtils.getToolSettings("ImagesAsPlanesImporter"))
    dialog = QtWidgets.QDialog(hou.qt.mainWindow())
    dialog.setWindowTitle("Images as Planes")
    layout = QtWidgets.QVBoxLayout(dialog)
    mode = QtWidgets.QComboBox()
    mode.addItems(["Individual materials", "Shared material (UV tile file list)"])
    mode.setCurrentIndex(1 if saved["material_mode"] == "shared" else 0)
    layout.addWidget(mode)
    shared = QtWidgets.QCheckBox("Shared matnet for all selected images")
    shared.setChecked(saved["shared_matnet"])
    layout.addWidget(shared)
    existing = QtWidgets.QCheckBox("Use existing matnet (in sopnet only)")
    existing.setChecked(saved["use_existing_matnet"])
    layout.addWidget(existing)
    candidates = existing_matnets(target) if target.childTypeCategory() == hou.sopNodeTypeCategory() else []
    existing_choice = QtWidgets.QComboBox()
    for node in candidates:
        existing_choice.addItem(node.name(), node.path())
        if node.isSelected() or node.path() == saved.get("existing_matnet"):
            existing_choice.setCurrentIndex(existing_choice.count() - 1)
    layout.addWidget(existing_choice)
    def update_existing():
        existing.setEnabled(shared.isChecked() and bool(candidates))
        existing_choice.setEnabled(existing.isEnabled() and existing.isChecked())
    shared.toggled.connect(update_existing)
    existing.toggled.connect(update_existing)
    update_existing()
    label = QtWidgets.QLabel("Shared matnet: one matnet with controls for all images.\n"
                            "Separate matnets: one matnet per image and one object per image in OBJ.\n"
                            "In SOPs, all plane chains stay in the current network.\n"
                            "Shared material uses the selected images as a horizontal UV tile file list.")
    label.setWordWrap(True)
    layout.addWidget(label)
    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    if not (dialog.exec() if hasattr(dialog, "exec") else dialog.exec_()):
        return None
    options = normalize_options({"material_mode": "shared" if mode.currentIndex() else "individual",
                                 "shared_matnet": shared.isChecked(),
                                 "use_existing_matnet": existing.isEnabled() and existing.isChecked(),
                                 "existing_matnet": existing_choice.currentData()})
    openToolsUtils.setToolSettings("ImagesAsPlanesImporter", options)
    return options


def main(kwargs=None):
    kwargs = kwargs or {}
    target = kwargs.get("target")
    target = target if target is not None else find_target_context()
    paths = kwargs.get("image_paths") or kwargs.get("file_paths") or kwargs.get("file_path")
    if not paths:
        paths = hou.ui.selectFile(title="Select images to import as planes", file_type=hou.fileType.Image,
            multiple_select=True, collapse_sequences=False, chooser_mode=hou.fileChooserMode.Read)
    if not paths:
        return []
    options = kwargs.get("options")
    if options is None:
        options = choose_options(target)
    if options is None:
        return []
    try:
        results = import_images(paths, options, target)
    except Exception as error:
        hou.ui.displayMessage(str(error), title="Images as Planes", severity=hou.severityType.Error)
        return []
    hou.ui.displayMessage("Imported {0} image plane(s).".format(sum(len(r["planes"]) for r in results)),
                          title="Images as Planes")
    return results
