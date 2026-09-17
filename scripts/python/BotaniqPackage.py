"""Geometry package preparation independent of the material renderer."""
import json
from pathlib import Path


def write_bundle(folder, options=None):
    """Retain named geometry data and expose bindings through USD unpacking."""
    from pxr import Usd, UsdGeom, UsdShade, Sdf
    folder = Path(folder)
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    stage = Usd.Stage.CreateNew(str(folder / 'asset.usda'))
    stage.GetRootLayer().subLayerPaths = ['geometry.usdc']
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        mesh = UsdGeom.Mesh(prim)
        api = UsdGeom.PrimvarsAPI(prim)
        bound, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
        assignments = [bound.GetPrim().GetName() if bound else ''] * len(mesh.GetFaceVertexCountsAttr().Get())
        for subset in UsdGeom.Subset.GetAllGeomSubsets(mesh):
            bound, _ = UsdShade.MaterialBindingAPI(subset.GetPrim()).ComputeBoundMaterial()
            if bound:
                for index in subset.GetIndicesAttr().Get():
                    assignments[index] = bound.GetPrim().GetName()
        api.CreatePrimvar('botaniq_material', Sdf.ValueTypeNames.StringArray, 'uniform').Set(assignments)
        if not api.GetPrimvar('st'):
            for obj in manifest['objects']:
                uv = api.GetPrimvar(obj['uv']) if obj.get('uv') else None
                if uv:
                    api.CreatePrimvar('st', Sdf.ValueTypeNames.TexCoord2fArray, uv.GetInterpolation()).Set(uv.ComputeFlattened())
                    break
    stage.GetRootLayer().Save()
    manifest['import_options'] = options or {}
    (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return str(folder / 'asset.usda')
