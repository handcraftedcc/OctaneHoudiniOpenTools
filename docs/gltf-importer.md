# GLTF Importer

Choose **GLTF** from the **Importers** shelf tool and select one or more `.gltf`
or `.glb` models (use Ctrl/Shift to select multiple files). The importer creates
a separate Geometry object for each model when run from an OBJ network, or
adds separate node chains to the current Geometry object when run from a SOP
network. Models are placed beside each other in the network, with separate
material networks and unique node names. In a SOP network, the last imported
model receives the display/render flags.

Batch imports show one completion summary. If a file fails, the remaining files
are still imported and the summary lists the failures. Displacement settings
are requested for each model that declares height textures; canceling that
dialog skips that model.

Scripts can call `run(gltf_paths=["/path/model.gltf", "/path/other.glb"])` or
`import_gltf_files(paths, displacement_settings=None)`. Paths may also be a
semicolon-separated Houdini file selection. Duplicate paths are imported once.
`run` returns a network dictionary for one file and a list for multiple files;
`import_gltf_files` always returns a list of successful imports.
When the selected file is generically named `scene.gltf` or `scene.glb`, the
parent directory name is used for the Geometry object and generated node names.

The SOP chain is:

```text
gltf (flattenedgeometry) -> material -> OUT
```

Materials are read directly from the GLTF JSON and rebuilt through
`openToolsMaterialUtils`. The Material SOP assigns each Octane material with
the primitive selection `@gltf_material_name="<GLTF material name>"`. Texture
projection is always UV.

Core metallic/roughness properties and the commonly encountered specular,
clearcoat, transmission, IOR, emissive-strength, and legacy
specular-glossiness extensions are parsed. Base-color and emissive textures
are multiplied by their corresponding GLTF factors in the Octane graph.
Embedded GLTF/GLB images are
extracted beside the source file into a deterministic `<name>_octane_textures`
cache, with the system temporary directory used as a fallback.

GLTF has no core displacement property. If a material declares a recognizable
height or displacement texture in an extension or `extras`, the importer asks
whether to use texture displacement, vertex displacement, or bump. It does not
show this dialog for ordinary GLTF files.

Packed metallic/roughness and specular/glossiness maps require component
extraction that the shared material API does not currently provide. The scalar
factors are applied, the packed texture is left disconnected, and a warning is
printed to the Python shell.
