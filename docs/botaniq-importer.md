# botaniq importer: simple Octane materials

This tool requires a separately licensed local botaniq installation. The
repository contains importer code and its optional Houdini Indie animation HDA,
not botaniq/Engon models, textures, previews, Blender files, pack indexes, or
source code.

Import botaniq model blends as static USD geometry with native Octane materials.
Imported SOP geometry is reoriented from Blender Z-up to Houdini Y-up with a
`reorient_blender_zup` Transform SOP before `OUT_BOTANIQ`.
Primitive material bindings are assigned with a Houdini Material SOP using the
`botaniq_material` attribute, so the assignment remains editable when material
names or paths change.
This uses `openToolsMaterialUtils.createOctaneMaterial()`, compact texture networks,
and controls on the parent material network, following the PlantCatalog importer.

## Use

1. Open **Importers → botaniq**.
2. Choose the pack root (or `blends/models`) once. Later launches open that
   library immediately; use **Change Library** in the browser header when needed.
3. Choose **Assets** to browse the single scrollable thumbnail view. Categories start collapsed so
   the picker can open immediately; expand one to load its local thumbnails.
   Click its header to select every asset inside. Click a thumbnail to select
   it, Ctrl-click to add or remove it, and Shift-click to select the range from
   the last clicked thumbnail. Selected thumbnails are green, and a category
   header turns green only when all of its assets are selected. **Select All**
   and **Deselect All** are at the bottom.
4. Choose **Next to Each Blend** (default), or **Choose Output Folder**, initially
   starting at `$HIP/assets/botaniq`. The custom folder is remembered.
5. Choose whether to add **Botaniq Animation (Indie license only)**. When enabled,
   the bundled animation HDA is inserted after each `reorient_blender_zup` Transform
   SOP and before the asset output.
6. Select an imported geometry's `materials` matnet for Global and material tabs.

Choose **Collections** to browse the particle-system blends with the same previews
and category layout. A collection is a single selection: its particle blend is not
converted or scattered in Houdini. Blender reads the model libraries it actually
loads and the importer performs the same package import for every referenced model.
The resulting models appear in the active OBJ network. When launched from a SOP
network, each model is built directly there as an editable vertical node column:
its material network sits above USD import, unpack, Material SOP, reorient transform,
and output nodes. No Object Merge nodes are created.

The first collection import writes `<particle-blend>.botaniq_collection.json` beside
the particle blend. It records the source timestamp and its model blend paths. It is
reused while the source blend is unchanged, so later imports do not reopen Blender
to discover dependencies.

Source mode creates `<model>_octane` subfolders beside the original blend files.
Custom mode retains categories below the selected output folder. Batch progress
and the final summary distinguish new exports, cache reuse, metadata repairs and
individual failures. Canceling retains completed imports and caches.

The picker keeps a local catalog for both assets and collections, plus local copies
of preview thumbnails, in `userSettings/BotaniqImporter/catalog_cache`. The source
pack is never modified. Reopening the browser reads this cache directly and does
not walk the network library or open Blender files. Use **Recache Library** at the
top right when you add, remove, or replace pack files; it rebuilds the catalog and
local previews immediately.

Requires Blender 5.2+ and Houdini with native Octane. Tested locally with Blender
5.2.1, Houdini 22.0.368 and Octane 2026.4.0.1 Preview 1. Source blends open with
factory settings and automatic script execution disabled, and are never saved.
Engon is not required by the export worker.

## Materials and controls

- Diffuse feeds a base-color corrector. Textureless materials retain their source
  plain color, including steel.
- Real alpha cutouts use Octane alpha-image nodes; opaque alpha outputs are omitted.
- Normals take precedence over height/bump. Without a normal, use an existing
  height map. Woody/rock surfaces with diffuse and neither map get a low-strength
  diffuse-to-bump fallback. Leaves never get this fallback.
- Leaves, flowers and grass use thin wall and a separate subsurface color
  corrector. A backlight map takes precedence; otherwise it uses diffuse.
- Roughness uses an available map or scalar. Direct source roughness, specular
  and metallic values are retained.
- Grass uses shared diffuse/normal maps with original UVs. Snow, moss, seasonal
  masks and multi-image effects are not rebuilt.

Global and material tabs expose hue, saturation, brightness, roughness and
specular. Foliage additionally exposes subsurface amount and independent
subsurface hue/saturation/brightness. Bump strength appears when bump is connected.
Normal maps connect directly; Octane's bump-height parameter is not presented as
a misleading normal-strength control. Controls use relative expressions and the
native subnets remain editable.

The simplified mapping intentionally favors source texture assignments and
editable renderer controls over reproducing proprietary source shader graphs.

## Geometry and packages

`geometry.usdc` retains UV layers, supported attributes, material subsets and
vertex weights as point primvars. Static evaluated export uses render subdivision
levels; `evaluated=False` preserves source topology. Wind, scattering and animation
systems remain outside this workflow.

`asset.usda` adds an `st` UV alias and face material names that survive unpacking.
`manifest.json` stores minimal texture recipes, properties and notices;
`textures/` holds collected images, and `export.log` retains Blender diagnostics.
Keep package files together.

On Windows, Blender can report a mapped network drive as a UNC path. The
importer queries the current session's mapped drives and converts a matching
UNC prefix to its active drive letter (choosing the longest matching share).
The drive letter is never hard-coded. If no matching mapping is available, the
UNC path is retained so a disconnected share is not guessed incorrectly.

`manifest.json` is the material/info sidecar: it holds everything needed to
recreate the native materials without reading Blender. A complete cache skips
Blender entirely and reuses its USD. If the sidecar or its textures are missing,
the worker runs in metadata-only mode and preserves the geometry file. A missing
`asset.usda` wrapper is regenerated from cached metadata without Blender. Packages
record their original source and are not silently reused for a different blend.
Cache reuse is explicit: it does not automatically rebuild geometry after source
edits. Use a different output directory when a fresh geometry export is wanted.

New exports do not compile MaterialX or create a Solaris renderer setup.
Explicit `create_stage=True` gives a geometry reference only for these packages.
Older MaterialX packages remain readable through the API; re-export to obtain the
simple workflow. Existing material nodes are not automatically migrated.

## Python API

```python
import BotaniqImporter

BotaniqImporter.ensure_package(
    source="/assets/botaniq_starter/blends/models/flowers/model.blend",
    output="/exports/model_octane",
    evaluated=True,
)
result = BotaniqImporter.import_package("/exports/model_octane")
```

The shelf reloads the material builder on launch, so subsequent imports pick up
tool updates. Existing nodes retain their previous setup.

## Validation

```text
hython -m unittest discover -s tests -p test_botaniq_octane.py
python -m unittest discover -s tests -p test_botaniq_batch.py
python -m unittest discover -s tests -p test_botaniq_browser.py
python -m unittest discover -s tests -p test_botaniq_paths.py
blender --background --factory-startup --disable-autoexec --python-exit-code 1 --python tests/test_botaniq_geometry_blender.py
```

Tests use temporary fixtures. Private models, textures, HIPs, and renders remain
under ignored local paths and are never part of the distribution.
