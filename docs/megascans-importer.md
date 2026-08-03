# Megascans Importer

Choose **Megascans** from the **Importers** shelf tool. On first use, select the
root Megascans library directory. The path and import defaults are stored via
the Open Tools settings API in:

```text
userSettings/MegascansImporter/settings.json
```

The browser scans Bridge-style `Custom` and `Downloaded` asset folders and
shows their preview images in two multi-select tabs:

- **Materials:** surfaces, atlases, and brushes
- **Assets:** 3D assets and 3D plants

After selecting records and choosing **Import Selected**, choose texture
resolution, mesh LOD, fallback behavior, displacement settings, and material
projection. The **Load variants** menu creates FBX variants either in one
geometry object or as separate geometry objects with independent MATNETs. The
Python shell receives a per-asset report showing the requested quality,
available qualities, fallbacks, and skipped records.

With **Closest available**, unavailable resolutions and LODs use the nearest
available level. With **Don't load asset**, a record is skipped if its requested
texture resolution or mesh LOD is unavailable.

Standalone materials are created in the active material network, or `/mat`
when the active editor is elsewhere. Assets imported from an OBJ network create
one geometry object per selected asset, or one per FBX variant when **As
separate objects** is selected. Assets imported from inside a geometry object
always keep variants in that object and never run a parent-wide layout.

Each FBX variant creates its own File, Transform (uniform scale `0.01`),
Material, and `OUT_variant` chain. Same-object imports also merge those chains
into `OUT_asset_ALL`.
