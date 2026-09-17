"""Run with hython -m unittest discover -s tests -p test_botaniq_materialx.py."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/python'))
import BotaniqMaterialX as bq
import BotaniqImporter
try:
    import MaterialX as mx
    from pxr import Usd, UsdGeom, UsdShade, Sdf
except ImportError:
    mx = None


@unittest.skipIf(mx is None, 'Requires Houdini MaterialX and USD Python modules')
class BotaniqTests(unittest.TestCase):
    def test_alpha_is_extracted_from_four_channel_image(self):
        m = {'name': 'leaf', 'family': 'bq_Vegetation', 'inputs': {
            'Alpha': {'image': 'textures/leaf.png', 'alpha': True},
            'Normal Color': {'image': 'textures/normal.png', 'colorspace': 'Non-Color'}}}
        doc, _ = bq.materialx_document([m])
        self.assertTrue(doc.validate()[0])
        text = mx.writeToXmlString(doc)
        self.assertIn('type="color4"', text)
        self.assertIn('name="index" type="integer" value="3"', text)
        self.assertIn('category', str(bq.material_graph(m).nodes))
        self.assertIn('thin_walled', text)

    def test_all_family_graphs_validate(self):
        for family in ['bq_Vegetation', 'bq_Bark', 'bq_Basic', 'bq_Rock', 'bq_Grass', 'principled']:
            doc, _ = bq.materialx_document([{'name': family, 'family': family, 'inputs': {}}])
            self.assertTrue(doc.validate()[0], family)

    def test_primary_named_uv_uses_portable_texture_coordinate(self):
        g=bq.material_graph({'name':'flower','family':'bq_Vegetation','inputs':{
            'Base Color':{'image':'flower.png','uv':'UVMap','primary_uv':True}}})
        self.assertTrue(any(n['category']=='texcoord' for n in g.nodes))
        self.assertFalse(any(n['category']=='geompropvalue' for n in g.nodes))

    def test_bundle_preserves_face_binding_weights_and_uv_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            stage = Usd.Stage.CreateNew(str(folder / 'geometry.usdc'))
            root = UsdGeom.Xform.Define(stage, '/root')
            stage.SetDefaultPrim(root.GetPrim())
            mesh = UsdGeom.Mesh.Define(stage, '/root/mesh')
            mesh.CreatePointsAttr([(0,0,0),(1,0,0),(1,1,0),(0,1,0)])
            mesh.CreateFaceVertexCountsAttr([3,3])
            mesh.CreateFaceVertexIndicesAttr([0,1,2,0,2,3])
            api = UsdGeom.PrimvarsAPI(mesh)
            api.CreatePrimvar('bq_trunk', Sdf.ValueTypeNames.FloatArray, 'vertex').Set([0,.25,.75,1])
            api.CreatePrimvar('UVMap', Sdf.ValueTypeNames.TexCoord2fArray, 'faceVarying').Set([(0,0),(1,0),(1,1),(0,0),(1,1),(0,1)])
            records=[]
            for i, name in enumerate(['bark','leaf']):
                mat = UsdShade.Material.Define(stage, '/root/_materials/' + name)
                subset = UsdGeom.Subset.CreateGeomSubset(mesh, name, 'face', [i], 'materialBind')
                UsdShade.MaterialBindingAPI.Apply(subset.GetPrim()).Bind(mat)
                records.append({'name':name,'family':'bq_Basic','inputs':{}})
            stage.GetRootLayer().Save()
            manifest={'source':'source.blend','materials':records,'objects':[{'uv':'UVMap'}]}
            (folder/'manifest.json').write_text(json.dumps(manifest))
            asset=bq.write_bundle(folder)
            result=Usd.Stage.Open(asset)
            api=UsdGeom.PrimvarsAPI(result.GetPrimAtPath('/root/mesh'))
            self.assertEqual(list(api.GetPrimvar('bq_trunk').Get()),[0,.25,.75,1])
            self.assertEqual(list(api.GetPrimvar('botaniq_material').Get()),['bark','leaf'])
            self.assertEqual(api.GetPrimvar('st').Get(),api.GetPrimvar('UVMap').Get())
            for name in ['bark','leaf']:
                mat=UsdShade.Material(result.GetPrimAtPath('/root/_materials/'+name))
                self.assertTrue(mat.GetSurfaceOutput('mtlx').HasConnectedSource())
            self.assertTrue((folder/'materials.mtlx').is_file())

    def test_refuses_material_name_collisions(self):
        with self.assertRaises(ValueError):
            bq.materialx_document([{'name':n,'family':'bq_Basic','inputs':{}} for n in ['a-b','a_b']])

    def test_export_refuses_existing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            (path/'source.blend').write_bytes(b'fixture')
            target=path/'output'
            target.mkdir()
            (target/'precious.txt').write_text('keep')
            with self.assertRaises(FileExistsError):
                BotaniqImporter.export_model(path/'source.blend',target)
            self.assertEqual((target/'precious.txt').read_text(),'keep')


if __name__=='__main__':
    unittest.main()
