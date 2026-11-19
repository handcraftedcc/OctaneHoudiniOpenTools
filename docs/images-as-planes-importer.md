# Images as Planes

Choose **Images as Planes** from the **Importers** shelf tool, select one or
more images, and choose the grouping and material options. Image sequences are
selected as individual files. The importer works from OBJ and SOP networks.

Each image becomes an editable XY polygon plane facing +Z. Its longest side is
one Houdini unit, with the image aspect ratio preserved. Each plane is centered
at the origin. Its chain ends with an `OUT_ImageName` Null SOP before merging.
Native Grid, UV Attribute Wrangle, Name, Attribute Create, Material, Merge and
Null SOPs remain editable and cook without the importer module. Each plane has
primitive string attributes `name` (the filename without its extension) and
`image_path`, plus vertex UVs and a material assignment.

When SideFX Labs Align and Distribute is installed, a `LAYOUT_images` SOP is
inserted between the merge and final output. It starts bypassed so the imported
planes remain centered. Enable it to spread the merged images into a
row, or edit its layout settings. Without Labs, the merge connects directly to
the final output.

## Grouping

- **Shared matnet for all selected images** creates one matnet whose controls
  affect all imported images. In OBJ it also creates one geometry object.
- With shared grouping disabled, each image gets its own matnet with independent
  controls, and its own geometry object when importing in OBJ.
- In SOPs, all plane chains and matnets are created directly in the current
  network. No SOP subnet is created in either grouping mode.
- **Use existing matnet (in sopnet only)** is available with shared grouping
  when the current SOP network contains a matnet. Choose the matnet from the
  dropdown. The import adds materials to it and uses its shared controls,
  preserving existing material nodes, control values, positions and wiring.
  Each import creates a new merge and output without wiring existing geometry
  into that output. A native Name SOP sets each plane's primitive name;
  Attribute Create sets its source image path.

## Materials and controls

**Individual materials** creates an Octane Standard Surface builder per image.
**Shared material** creates one builder per matnet using Octane UV Tiles in
File List mode. Selection order determines the file list and horizontal UV tile
indices: image 0 uses U=0..1, image 1 uses U=1..2, and so on. The grid is
`number of selected images × 1`. A matching UV Tiles node reads image alpha.
Shared material mode requires an Octane plugin with the UV Tiles File List
controls (tested with Houdini 22.0.368 / Octane 2026.4 Preview 1).

The material choice and grouping choice are independent. With separate
matnets and shared material mode, each matnet gets its own copy of the full
selection's tile material, preserving tile indices and independent controls.

Every matnet exposes:

- **Emission Amount**, initially zero. Emission uses the image after color
  adjustment, independently of the basecolor texture toggle.
- **Basecolor Texture**, initially enabled. Disable it to use **Solid
  Basecolor** (initially black), while keeping image emission available.
- **Image Alpha to Opacity**, initially enabled. It uses the embedded alpha
  channel by default. Disable it for an opaque plane.
- **Use RGB as Alpha** uses grayscale RGB values instead of the embedded alpha,
  so black and white mask images work without an alpha channel. Black is
  transparent and white is opaque.
- **Invert Opacity Mask** reverses the selected mask, including RGB masks.
  Both mask options are off by default and affect Octane opacity only.
- **Ray Switch Opacity** exposes Camera, Shadow, Diffuse, Reflection,
  Refraction, AO and Photon ray multipliers. They default to 1. Set **Shadow
  Ray** to **0** to keep the decal visible while preventing it from casting a
  shadow. The final opacity graph is the existing mask output multiplied by
  an Octane Ray Switch, then connected to Standard Surface opacity. Ray
  multipliers also apply when Image Alpha to Opacity is disabled.
- **Color Adjustment**, initially disabled, with Hue, Saturation, Brightness,
  Gamma, Contrast, Gain, Exposure and Invert. A texture switch bypasses the
  adjustment node when disabled. Adjustments affect basecolor and emission;
  alpha remains independent.

Material builders replace Octane's fixed OpenGL diffuse controls with Houdini's
native **Diffuse Texture Layers** multiparm. Shared materials have one layer
per selected file, with U translations 0, 1, 2, etc. Decal wrapping prevents
layers from repeating across other tiles. All layers explicitly use the `uv`
attribute; blank UV Set fields would select `uv2`, `uv3`, etc. Viewport diffuse
textures stay enabled when Basecolor Texture is disabled, preserving the image
and its viewport alpha while rendering a solid basecolor. Shader emission and color
correction are render controls; the viewport displays the source images.

Use the Vulkan viewport with High Quality Lighting for the full layered
material display (see [SideFX viewport shader documentation](https://www.sidefx.com/docs/houdini/shade/opengl.html)).
The installed legacy batch OpenGL shader samples one diffuse map, so its ROP
preview repeats the first image or omits later tiles. The generated native
layer parameters are covered by automated tests; their live Vulkan appearance
has not been visually verified.

Import options are remembered through the shared settings API. Image files
remain external references. All dimensions are validated before creating nodes;
failed imports remove newly created nodes. A successful import is one undo step.

## Python API

```python
import ImagesAsPlanesImporter

results = ImagesAsPlanesImporter.import_images(
    ["C:/textures/label.png", "C:/textures/screen.png"],
    options={
        "shared_matnet": True,
        "use_existing_matnet": False,
        "material_mode": "shared",  # or "individual"
    },
    target=hou.node("/obj"),
)
```

Each result contains `container`, `matnet`, `planes`, `merge`, `layout` (or
`None` when Labs is unavailable), and `out`. In SOPs,
`container` is the current network. Set `use_existing_matnet` and optionally
`existing_matnet` to a direct child's path to reuse it. Without an explicit
path, the API uses a selected matnet, or the only matnet in the network; an
ambiguous choice raises an error. Older `shared_subnet` / `use_existing_subnet`
option names are migrated to the matnet settings.

Each plane record contains its Grid, UV wrangle, Name SOP (`name`), Attribute
Create SOP (`attributes`), assignment SOP, per-image output Null (`out`), material
builder, and source image path. `main(kwargs)` accepts `image_paths` (or
`file_paths` / `file_path`), `options`, and `target`; omitted inputs use the UI.

Run native geometry and material graph tests with:

```text
hython -m unittest discover -s tests -p test_images_as_planes.py
```
