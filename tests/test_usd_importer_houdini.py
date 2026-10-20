"""Native SOP integration tests; run with hython (Octane is not required)."""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts", "python")))
import UsdImporter as importer

try:
    import hou
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade
except ImportError:
    hou = None


@unittest.skipIf(hou is None, "Requires Houdini")
class HoudiniUsdImporterTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.source = os.path.join(self.folder.name, "asset.usda")
        stage = Usd.Stage.CreateNew(self.source)
        root = UsdGeom.Xform.Define(stage, "/Asset").GetPrim()
        stage.SetDefaultPrim(root)
        UsdGeom.Xform.Define(stage, "/Asset/Nested")
        mesh = UsdGeom.Mesh.Define(stage, "/Asset/Nested/Mesh")
        mesh.CreatePointsAttr([Gf.Vec3f(0, 0, 0), Gf.Vec3f(1, 0, 0), Gf.Vec3f(0, 1, 0), Gf.Vec3f(1, 1, 0)])
        mesh.CreateFaceVertexCountsAttr([3, 3])
        mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 1, 3, 2])
        for index, name in enumerate(("First", "Second")):
            mat = UsdShade.Material.Define(stage, "/Asset/Looks/" + name)
            shader = UsdShade.Shader.Define(stage, str(mat.GetPath()) + "/Shader")
            shader.CreateIdAttr("UsdPreviewSurface")
            shader.CreateOutput("surface", Sdf.ValueTypeNames.Token)
            mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
            subset = UsdGeom.Subset.Define(stage, str(mesh.GetPath()) + "/" + name)
            subset.CreateElementTypeAttr("face")
            subset.CreateFamilyNameAttr("materialBind")
            subset.CreateIndicesAttr([index])
            UsdShade.MaterialBindingAPI.Apply(subset.GetPrim()).Bind(mat)
        stage.GetRootLayer().Save()
        self.parent = hou.node("/obj").createNode("geo", "usd_importer_test")
        self.addCleanup(self.parent.destroy)

    @staticmethod
    def placeholder(parent, record, settings):
        # Exercise native USD/Material SOP behavior without an Octane installation.
        return parent.createNode("subnet", record["name"])

    def test_polygons_and_face_assignments_and_existing_positions(self):
        existing = self.parent.createNode("null", "existing")
        existing.setPosition(hou.Vector2(-5, 7))
        records = importer.parse_materials(importer.open_stage(self.source), self.source, bound_only=True)
        with patch.object(importer, "create_material", self.placeholder):
            result = importer.create_network(self.parent, self.source, records, None, hou.Vector2(5, 0), "asset")
        geometry = result["out"].geometry()
        self.assertFalse(result["out"].errors())
        self.assertEqual(len(geometry.prims()), 2)
        self.assertTrue(all(p.type() == hou.primType.Polygon for p in geometry.prims()))
        assignments = geometry.primStringAttribValues("shop_materialpath")
        self.assertEqual(set(assignments), {result["matnet"].node(name).path() for name in ("First", "Second")})
        self.assertEqual(existing.position(), hou.Vector2(-5, 7))
        self.assertEqual(result["unpack"].parm("limititerations").eval(), 0)

    def test_materialx_graph_connects_to_real_octane_surface(self):
        if "octane::NT_MAT_STANDARD_SURFACE" not in hou.vopNodeTypeCategory().nodeTypes():
            self.skipTest("Octane is not installed")
        import UsdMaterialX
        def value(val, kind="float"):
            return {"value": val, "type": kind}
        def link(path, kind="color3"):
            return {"node": path, "output": "out", "type": kind}
        def node(path, ident, kind, inputs):
            return {"path": path, "name": path, "id": ident, "outputs": {"out": kind}, "inputs": inputs}
        graph = {"nodes": [
            node("a", "ND_constant_color3", "color3", {"value": value((.2, .4, .6), "color3")}),
            node("b", "ND_mix_color3", "color3", {"bg": link("a"), "fg": value((1., 0., 0.), "color3"), "mix": value(.3)}),
            node("multiply", "ND_multiply_color3", "color3", {"in1": link("a"), "in2": link("b")}),
            node("encoded", "ND_add_vector3", "vector3", {"in1": value((.25, .25, .5), "vector3"), "in2": value((.25, .25, .5), "vector3")}),
            node("normal", "ND_normalmap", "vector3", {"in": link("encoded", "vector3"), "scale": value(1.)}),
            node("clip", "ND_ifgreater_float", "float", {"value1": value(.75), "value2": value(.5), "in1": value(1.), "in2": value(0.)}),
        ], "surface": {"base_color": link("multiply"), "normal": link("normal", "vector3"), "opacity": link("clip", "float")}}
        parent = self.parent.createNode("matnet")
        record = {"name": "native_graph", "graph": graph, "warnings": []}
        subnet = importer.create_material(parent, record, None)
        surface = importer.materials.findStandardSurfaceNode(subnet)
        for pin, expected in (("baseColor", "mtlxmultiply"), ("normal", "mtlxnormalmap"), ("opacity", "mtlxifgreater")):
            upstream = surface.inputs()[surface.inputIndex(pin)]
            self.assertEqual(upstream.type().name().split("::")[0], expected)
        multiply = subnet.node("multiply")
        self.assertEqual({n.name() for n in multiply.inputs()}, {"a", "b"})
        self.assertFalse(record["warnings"])
        self.assertFalse(any(n.type().name().startswith("octane::NT_TEX_") for n in subnet.children()))

    def test_usd_texture_channels_use_native_materialx_nodes(self):
        import UsdMaterialX
        subnet = self.parent.createNode("matnet").createNode("subnet")
        spec = {"id": "UsdUVTexture", "name": "packed", "path": "/packed",
                "inputs": {}, "outputs": {"rgb": "color3", "g": "float", "a": "float"}}
        warnings = []
        nodes = UsdMaterialX.build_graph(subnet, {"nodes": [spec]}, warnings)
        for output, expected in (("rgb", "mtlxconvert"), ("g", "mtlxextract"), ("a", "mtlxextract")):
            node, name = UsdMaterialX.endpoint(nodes, {"node": "/packed", "output": output})
            self.assertEqual(node.type().name(), expected)
            self.assertEqual(name, "out")
        self.assertFalse(warnings)


if __name__ == "__main__":
    unittest.main()
