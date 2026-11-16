# Purge VRAM

The **Purge VRAM** shelf tool frees GPU/unified memory held by the
OctaneRender for Houdini plugin and reports memory usage before and after the
operation. It lives on the **Octane Open Tools** shelf.

Octane does not expose a public "free all VRAM" call. The plugin's own options
UI notes that `OctaneSDK` has no API to clear the texture cache. This tool
therefore uses the same internal cleanup path as the IPR's **Full scene
reload** button, and optionally closes the IPR or clears Houdini's caches.

## Usage

1. Open the **Octane Open Tools** shelf.
2. Click **Purge VRAM**.
3. Choose an action and click **Purge**.

The dialog shows the current GPU and Houdini memory usage. The selected action
and the "Also clear Houdini caches" state are remembered in
`userSettings/OctanePurgeVRAM/settings.json`.

## Actions

| Action | What it does | IPR stays open |
| --- | --- | --- |
| Full IPR reload | Stops rendering, performs a full Octane scene clean, then reloads the IPR scene. | Yes |
| Close IPR | Closes the Octane Qt5 IPR pane, releasing the IPR render engine. | No |
| Houdini caches only | Clears Houdini's own caches. | Yes |
| Report only | Prints memory usage and changes nothing. | Yes |

**Full IPR reload** is the mildest option that releases stale Octane
allocations. If the scene has not changed, the net GPU usage may stay similar
because the reloaded scene needs the same memory again.

**Close IPR** releases everything the IPR render engine holds. Use it when the
IPR is no longer needed or when you want the largest reclaim.

**Houdini caches only** clears:

```text
texcache -c
glcache -c
geocache -c
sopcache -c
objcache -c
copcache -c
```

These caches are owned by Houdini, not Octane, but they can consume several
gigabytes of unified memory on Apple Silicon. Clearing them forces textures
and geometry to be read again on next use.

## Reading the report

- **GPU in use** — `"In use system memory"` from the Apple Silicon GPU driver
  (`ioreg -r -c AGXAccelerator -d 1`).
- **GPU allocated** — `"Alloc system memory"` from the same source. This is the
  driver's allocation pool and is usually larger than the in-use value.
- **Houdini RSS** — resident set size of the running Houdini process.
- **Change** — difference between the before and after snapshots. A negative
  GPU change means memory was released.

These are system-wide counters, not Octane-only counters. Other applications,
the Houdini viewport, and macOS itself are included.

## How the full reload works

`Full IPR reload` calls the plugin's internal `HOctane_IPR_reLoad(bool)` entry
point with `True`. That is the same code path as the IPR's **Full scene
reload** button:

```text
stop rendering -> clean scene (full) -> reload IPR scene
```

The public `Octane_IPR_reload` command calls the same function with `False`,
which performs a partial clean and keeps the node graph and textures. The
tool falls back to that command when the internal symbol cannot be resolved.

The full clean is only attempted when an Octane Qt5 IPR window is open
(`Octane_getIPRQtPointer` is non-zero). Without an open IPR, the standard
`Octane_IPR_reload` command is used, which is safe and usually a no-op.

## Limitations

- On Apple Silicon, "VRAM" is unified system memory. Releasing Octane
  allocations returns memory to macOS, but the OS may keep pages cached or
  compressed for a while, so the reported drop can lag.
- The full-clean entry point is internal and unsupported. A future plugin build
  could rename it; the tool then falls back to the public reload command.
- GPU 0 is the tonemap GPU on single-GPU systems, so disabling it with
  `Octane_setGPU -g 0 -s 0` is ineffective. This tool does not use that trick.
- Clearing Houdini caches can make the next viewport interaction slower while
  textures and geometry are reloaded.
- `sudo purge` only flushes file caches and is not a VRAM purge.
- If memory is still high after closing the IPR, it may be held by the Metal
  driver, the Houdini viewport, Octane material previews, or another
  application. Restarting Houdini is the only guaranteed full release.

## Calling from Python

```python
import OctanePurgeVRAM

OctanePurgeVRAM.main({})                              # dialog
OctanePurgeVRAM.purge("report")                       # print usage only
OctanePurgeVRAM.purge("reload")                       # full IPR reload
OctanePurgeVRAM.purge("close", clear_caches=True)     # close IPR + caches
OctanePurgeVRAM.purge("caches")                       # Houdini caches only
```

`purge()` returns the same report text that is printed to the Python shell.
