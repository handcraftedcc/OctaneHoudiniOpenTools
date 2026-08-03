# Cargo Live Import

Use the **Cargo Listener** button on the **Octane Open Tools** shelf to open
the listener control panel.

## Installation

Copy `OctaneHoudiniOpenTools.json` from the repository root into the Houdini
package directory:

```text
%USERPROFILE%\Documents\houdini21.0\packages
```

Disable the original listener by renaming:

```text
kitbash3d-cargo-houdini.json
```

to:

```text
kitbash3d-cargo-houdini.jsonx
```

Then fully restart Houdini. Only one plugin can listen on Cargo's configured
Houdini port.

## Listener Panel

**Copy to Destination** is enabled by default and the destination defaults to
`$HIP/cargo/`. Disable it to import directly from Cargo's source files; the
destination controls become unavailable. Copying is skipped only when the
root USD and every resolved dependency for the selected texture variant
already exist inside the saved Houdini project's `$HIP` directory.

Choose a 1K, 2K, or 4K texture resolution; 2K is the default. The panel
selections are saved through the Open Tools user settings API in:

```text
userSettings/CargoOctaneListener/settings.json
```

Choose **Start Listener**, then use Cargo's normal **Send to Houdini** command.
The panel reports either **Listener Running** or **Listener Not Running** and
provides a **Stop Listener** button.

The Import Settings section mirrors the manual Cargo importer:

- Texture displacement, vertex displacement, or bump
- Default displacement height
- Connected or disconnected height/displacement nodes
- UV, triplanar, box, XYZ to UVW, cylindrical, spherical, or perspective
  projection for standalone material imports

- Cargo materials are rebuilt as Octane materials in `/mat`.
- Cargo models are imported as geometry objects in `/obj`, with their Cargo
  material assignments rebuilt as Octane materials.
- Cargo's requested texture variant is preferred. The normal
  `CargoImporter.py` fallback order is used when that variant is unavailable.
- Height maps use the importer's default texture-displacement settings and
  live imports use UV projection.

The listener only adapts Cargo's `usdFilePath` and `textureVariant` message to
the records consumed by `scripts/python/CargoImporter.py`. Asset parsing and
Houdini/Octane node creation remain centralized in that importer.

Only one plugin can listen on Cargo's configured Houdini port. Disable or
remove the KitBash3D Solaris/USD Houdini plugin before enabling this package,
otherwise the second listener will report that the port is already in use.

For optional manual control from Houdini's Python shell:

```python
import CargoOctaneListener

CargoOctaneListener.stop()
CargoOctaneListener.start("$HIP/cargo/", "2k", True)
```
