# USD Importer

Choose **USD** from the **Importers** shelf tool and select one or more `.usd`,
`.usda`, `.usdc`, or `.usdz` files. Choose **Models and materials** or
**Materials only**. Materials use native Houdini MaterialX texture and utility
nodes connected to an Octane standard surface inside an Octane material builder.

Model imports create a Geometry object in an OBJ network, or add a column to
the current SOP network. Each asset gets a local material network and this chain:

```text
usdimport -> unpackusd (recursive polygons) -> material -> OUT
```

Existing nodes keep their positions. USD material bindings, including inherited
bindings and face subsets, are read through OpenUSD. The Material SOP selects
faces by `usdmaterialpath` and assigns the corresponding Octane builders. The
render purpose is imported, together with USD's default purpose; proxy-only
geometry is excluded. USD `st` primvars are translated to Houdini UVs by unpacking.
Materials-only imports use the current material network or `/mat`.

Use the asset's top-level entry file when it references separate geometry,
material, or payload layers. For files named `geo`, `geometry`, `mtl`, `materials`,
or `payload`, the importer asks whether to use the selected file alone or add
companion USD layers. It does not infer sibling composition from filenames.
Companion layers are composed as stronger sublayers and must use matching USD
scene paths; they do not remap material libraries onto unrelated geometry paths.
Multiple files selected in the first dialog are imported as separate assets.

When variants exist, use authored defaults or choose a variant for each available
set. This includes material, texture-resolution, geometry, and animation variants.
After each choice, newly introduced variant sets are discovered. One selected
configuration is imported per file; the importer does not expand every variant
combination. Companion layers and changed variants are flattened to unique
cached USD files, retaining geometry time samples and leaving the sources untouched.

The importer follows UsdShade connections and interface inputs for USD Preview
Surface and MaterialX standard surface terminals. MaterialX shading graphs are
copied directly to their matching `mtlx` VOP nodes, preserving input types,
overload signatures, values, output names, and shared connections. Blends,
texture multiplication, alpha clipping, normal reconstruction, coordinate
networks, and displacement remain MaterialX operations. Only the standard surface
terminal is replaced by its Octane equivalent. External `.mtlx` references
depend on Houdini's USD MaterialX plugin and standard libraries.

USD Preview Surface texture graphs use MaterialX image, channel extraction,
conversion, multiply, and add nodes, including their authored scale/bias.
Packed texture channels remain connected graph outputs; no Pillow dependency or
channel baking is needed.

USDZ textures are read from their packages into a `<source-name>_octane` cache
beside the asset, with a system temporary cache as a fallback. Cached files must
remain available while the imported network uses them.

Missing textures, unavailable MaterialX node types or pins, and unsupported
renderer-specific shader nodes are reported in the completion dialog's details
and the Python shell. Source surface controls without Octane equivalents, such
as `coat_affect_color` and `coat_affect_roughness`, are reported separately from
upstream MaterialX graph conversion. Missing textures retain their authoring-layer paths for
manual repair. Shader parameters are sampled once at the current frame (or the
supplied `time`); geometry animation remains file-driven. Authored MaterialX
displacement nodes retain their scale and midpoint operations. The earlier
`height_mode` argument is accepted for compatibility but does not override these
authored MaterialX controls.

For the supplied Cargo samples, the authored selection is `png4k`, while the test
folder contains `png2k` and `exr2k`. Choose `png2k` on the model and its material
variant sets to use the supplied textures.

Programmatic calls can suppress the dialogs:

```python
import UsdImporter
UsdImporter.main({
    "file_paths": ["/assets/model.usdz"],
    "import_mode": "models",  # or "materials"
    "variant_selections": {"/Model": {"look": "Blue"}},
    "additional_layers": [],
    "time": 1,  # optional USD time code for shaders and geometry
})
```

An empty `variant_selections` dictionary keeps authored defaults. An empty
`additional_layers` list imports only the selected files. `additional_layers` can
also be a dictionary keyed by absolute source path with a layer list for each
asset. Variant selections supplied programmatically apply to every selected file.

The composition tests in `tests/test_usd_importer.py` require `pxr` (Houdini's
Python environment or `usd-core`). They generate their own USD fixtures and do
not require the third-party sample assets.
`tests/test_usd_importer_houdini.py` additionally verifies native polygon import,
face-subset assignments, and preservation of existing network positions with
`hython`. Geometry tests use placeholders when Octane is unavailable. With Octane
installed, an additional test verifies native MaterialX mix, multiply, normal,
and alpha-clip connections to the actual Octane surface.
