# Material API

Module: `scripts/python/openToolsMaterialUtils.py`

`openToolsMaterialUtils` creates Octane material builder networks from normalized material descriptions. Importers should pass parsed texture and parameter data to this module instead of duplicating node creation logic.

## Entry Point

```python
import openToolsMaterialUtils

result = openToolsMaterialUtils.createOctaneMaterial(
    targetNetwork,
    name="Oak_Bark",
    textures=textures,
    parameters=parameters,
    projection=projection,
    displacement=displacement,
    options={
        "allow_unknown_parameters": True
    },
)

material_node = result["subnet"]
standard_surface = result["standard_surface"]
texture_nodes = result["texture_nodes"]
```

## Texture Format

Textures may be a dictionary of channel to path:

```python
textures = {
    "basecolor": "X:/asset/basecolor.png",
    "roughness": "X:/asset/roughness.png",
    "height": "X:/asset/height.png",
}
```

Textures may also use detailed records:

```python
textures = {
    "basecolor": {
        "path": "X:/asset/basecolor.png",
        "texture_type": "image",
        "color_space": "NAMED_COLOR_SPACE_SRGB",
        "power": 1.0,
        "parameters": {
            "gamma": 1.0
        }
    },
    "roughness": {
        "path": "X:/asset/roughness.png",
        "texture_type": "greyscale",
        "color_space": "NAMED_COLOR_SPACE_OTHER"
    }
}
```

Supported `texture_type` values:

- `image`: RGB image texture.
- `alpha`: alpha image texture.
- `greyscale` or `grayscale`: float image texture.

If `texture_type` or `color_space` is omitted, the utility infers a reasonable default from the channel name.

## Parameters

Parameters are applied to the Octane Standard Surface node by semantic names where possible:

```python
parameters = {
    "base_color": (0.25, 1.0, 0.23),
    "roughness": 0.25,
    "metallic": 0.0,
    "opacity": 1.0,
}
```

Unknown parameter names are tried directly as Houdini parm names by default. Set `options={"allow_unknown_parameters": False}` when importing from broad parsed data, such as USD files where shader-internal values may be mixed into the input list.

## Projection

```python
projection = {
    "mode": "triplanar",
    "coordinate_space": "3",
    "use_rest_attributes": 1,
}
```

Supported projection modes:

- `uv`
- `mesh_uv`
- `linear`
- `box`
- `cylindrical`
- `spherical`
- `perspective`
- `triplanar`

For non-UV projections, the utility creates a shared 3D transform node. For standard texture projections it also creates the relevant Octane projection node. For `triplanar`, it creates triplanar texture map nodes and supports coordinate-space/rest-attribute settings.

## Displacement

```python
displacement = {
    "mode": "texture_displacement",
    "height": 0.001,
    "midlevel": 0.5,
    "create_disconnected": False,
    "displace_rest_position": 0,
}
```

Supported displacement modes:

- `bump`: connect height textures to Standard Surface bump.
- `texture_displacement`: create Octane texture displacement.
- `vertex_displacement`: create Octane vertex displacement.

Height-like texture channels are `height`, `bump`, and `displacement`.

## Return Value

```python
{
    "subnet": material_builder_node,
    "material": material_builder_node,
    "standard_surface": standard_surface_node,
    "texture_nodes": {"basecolor": node, "roughness": node},
    "transform_2d": transform_2d_node_or_none,
    "transform_3d": transform_3d_node_or_none,
    "projection_node": projection_node_or_none,
    "displacement_nodes": [node]
}
```

Use this result when an importer needs to promote parameters, assign material paths, or apply importer-specific post-processing.
