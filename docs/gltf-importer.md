# GLTF Importer

Choose **GLTF** from the **Importers** shelf tool and select a `.gltf` or `.glb`
model. The importer creates a Geometry object when run from an OBJ network, or
adds its nodes to the current Geometry object when run from a SOP network.
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
