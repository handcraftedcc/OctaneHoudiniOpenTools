"""Composition and shader tests run with Houdini's pxr or the usd-core package."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts", "python")))
import UsdImporter as importer

try:
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade
except ImportError:
    Usd = None


@unittest.skipIf(Usd is None, "OpenUSD Python modules unavailable")
class UsdImporterTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.source = os.path.join(self.folder.name, "model.usda")
        self.stage = Usd.Stage.CreateNew(self.source)
        self.root = UsdGeom.Xform.Define(self.stage, "/Model").GetPrim()
        self.stage.SetDefaultPrim(self.root)
        self.mesh = UsdGeom.Mesh.Define(self.stage, "/Model/Mesh")
        self.material = UsdShade.Material.Define(self.stage, "/Model/Looks/Surface")
        self.shader = UsdShade.Shader.Define(self.stage, "/Model/Looks/Surface/Shader")
        self.shader.CreateIdAttr("UsdPreviewSurface")
        self.shader.CreateOutput("surface", Sdf.ValueTypeNames.Token)
        self.material.CreateSurfaceOutput().ConnectToSource(self.shader.ConnectableAPI(), "surface")
        UsdShade.MaterialBindingAPI.Apply(self.mesh.GetPrim()).Bind(self.material)

    def save(self):
        self.stage.GetRootLayer().Save()

    def records(self, bound_only=True):
        self.save()
        return importer.parse_materials(importer.open_stage(self.source), self.source, bound_only=bound_only)

    def test_material_inputs_are_scoped_to_surface(self):
        self.shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.2, 0.4, 0.6))
        self.shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.35)
        other = UsdShade.Shader.Define(self.stage, "/Model/Looks/Surface/Unconnected")
        other.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.99)
        record = self.records()[0]
        self.assertAlmostEqual(record["inputs"]["specular_roughness"], 0.35)
        self.assertAlmostEqual(record["inputs"]["base_color"][0], 0.2)
        self.assertEqual(record["usdmaterialpath"], "/Model/Looks/Surface")

    def test_bound_materials_include_inheritance_and_face_subsets(self):
        second = UsdShade.Material.Define(self.stage, "/Model/Looks/Other")
        subset = UsdGeom.Subset.Define(self.stage, "/Model/Mesh/Faces")
        subset.CreateElementTypeAttr("face")
        subset.CreateIndicesAttr([0])
        UsdShade.MaterialBindingAPI.Apply(subset.GetPrim()).Bind(second)
        UsdShade.MaterialBindingAPI.Apply(self.root).Bind(self.material)
        UsdShade.MaterialBindingAPI(self.mesh.GetPrim()).UnbindDirectBinding()
        paths = [r["usdmaterialpath"] for r in self.records()]
        self.assertEqual(set(paths), {"/Model/Looks/Surface", "/Model/Looks/Other"})

    def test_materials_only_includes_unbound_materials(self):
        UsdShade.Material.Define(self.stage, "/Model/Looks/Unused")
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(len(self.records(False)), 2)

    def test_variant_selections_use_private_session_and_flatten_consistently(self):
        variants = self.root.GetVariantSets().AddVariantSet("look")
        for name, roughness in (("rough", 0.8), ("smooth", 0.1)):
            variants.AddVariant(name)
            variants.SetVariantSelection(name)
            with variants.GetVariantEditContext():
                self.shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(roughness)
        variants.SetVariantSelection("rough")
        self.save()
        with open(self.source, "rb") as handle:
            original = handle.read()
        stage = importer.open_stage(self.source)
        importer.apply_variants(stage, {"/Model": {"look": "smooth"}})
        record = importer.parse_materials(stage, self.source, bound_only=True)[0]
        self.assertAlmostEqual(record["inputs"]["specular_roughness"], 0.1)
        flattened = os.path.join(self.folder.name, "flattened.usda")
        stage.Flatten().Export(flattened)
        self.assertAlmostEqual(importer.parse_materials(importer.open_stage(flattened), flattened)[0]["inputs"]["specular_roughness"], 0.1)
        with open(self.source, "rb") as handle:
            self.assertEqual(original, handle.read())

    def test_unknown_variant_is_rejected(self):
        with self.assertRaises(ValueError):
            importer.apply_variants(self.stage, {"/Model": {"look": "missing"}})

    def test_binary_usd_and_split_layers(self):
        self.save()
        geo = os.path.join(self.folder.name, "geo.usdc")
        self.stage.GetRootLayer().Export(geo)
        companion = os.path.join(self.folder.name, "materials.usda")
        stage = Usd.Stage.CreateNew(companion)
        shader = UsdShade.Shader(stage.OverridePrim(self.shader.GetPath()))
        shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.75)
        stage.GetRootLayer().Save()
        composed = importer.open_stage(geo, [companion])
        record = importer.parse_materials(composed, geo, bound_only=True)[0]
        self.assertAlmostEqual(record["inputs"]["specular_roughness"], 0.75)

    def test_interface_input_connection(self):
        interface = self.material.CreateInput("roughness", Sdf.ValueTypeNames.Float)
        interface.Set(0.42)
        self.shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).ConnectToSource(interface)
        self.assertAlmostEqual(self.records()[0]["inputs"]["specular_roughness"], 0.42)

    def test_texture_resolves_relative_to_referenced_layer(self):
        library = os.path.join(self.folder.name, "library")
        os.mkdir(library)
        texture = os.path.join(library, "color.png")
        with open(texture, "wb") as handle:
            handle.write(b"texture")
        path = os.path.join(library, "material.usda")
        stage = Usd.Stage.CreateNew(path)
        mat = UsdShade.Material.Define(stage, "/Material")
        shader = UsdShade.Shader.Define(stage, "/Material/Shader")
        shader.CreateIdAttr("UsdPreviewSurface")
        shader.CreateOutput("surface", Sdf.ValueTypeNames.Token)
        image = UsdShade.Shader.Define(stage, "/Material/Image")
        image.CreateIdAttr("UsdUVTexture")
        image.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath("color.png"))
        image.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
        shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(image.ConnectableAPI(), "rgb")
        mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
        stage.GetRootLayer().Save()
        self.material.GetPrim().GetReferences().AddReference(path, "/Material")
        # Remove the stronger local surface connection so the reference wins.
        self.material.GetSurfaceOutput().GetAttr().ClearConnections()
        self.shader.GetPrim().SetActive(False)
        record = self.records()[0]
        spec = next(n for n in record["graph"]["nodes"] if n["id"] == "UsdUVTexture")
        self.assertEqual(os.path.normcase(os.path.normpath(spec["inputs"]["file"]["value"])), os.path.normcase(texture))

    def test_unsupported_nodes_report_warning(self):
        node = UsdShade.Shader.Define(self.stage, "/Model/Looks/Surface/Noise")
        node.CreateIdAttr("CustomUnsupportedNoise")
        node.CreateOutput("out", Sdf.ValueTypeNames.Float)
        self.shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).ConnectToSource(node.ConnectableAPI(), "out")
        record = self.records()[0]
        self.assertNotIn("specular_roughness", record["textures"])
        self.assertTrue(any("CustomUnsupportedNoise" in w for w in record["warnings"]))

    def test_cycles_do_not_recurse_forever(self):
        a = self.material.CreateInput("a", Sdf.ValueTypeNames.Float)
        b = self.material.CreateInput("b", Sdf.ValueTypeNames.Float)
        a.ConnectToSource(b)
        b.ConnectToSource(a)
        self.shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).ConnectToSource(a)
        self.assertTrue(any("Cyclic" in w for w in self.records()[0]["warnings"]))

    def test_packed_components_stay_connected_graph_outputs(self):
        path = os.path.join(self.folder.name, "packed.png")
        with open(path, "wb") as handle:
            handle.write(b"texture")
        image = UsdShade.Shader.Define(self.stage, "/Model/Looks/Surface/Image")
        image.CreateIdAttr("UsdUVTexture")
        image.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(path))
        for channel, output in (("roughness", "g"), ("opacity", "a")):
            image.CreateOutput(output, Sdf.ValueTypeNames.Float)
            self.shader.CreateInput(channel, Sdf.ValueTypeNames.Float).ConnectToSource(image.ConnectableAPI(), output)
        graph = self.records()[0]["graph"]
        self.assertEqual(len(graph["nodes"]), 1)
        self.assertEqual(graph["surface"]["specular_roughness"]["output"], "g")
        self.assertEqual(graph["surface"]["opacity"]["output"], "a")
        self.assertEqual(graph["surface"]["opacity"]["node"], graph["surface"]["specular_roughness"]["node"])

    def test_materialx_texture_multiplier(self):
        self.shader.CreateIdAttr("ND_standard_surface_surfaceshader")
        image = UsdShade.Shader.Define(self.stage, "/Model/Looks/Surface/Image")
        image.CreateIdAttr("ND_image_color3")
        texture_path = os.path.join(self.folder.name, "color.png")
        with open(texture_path, "wb") as handle:
            handle.write(b"texture")
        image.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(texture_path))
        image.CreateOutput("out", Sdf.ValueTypeNames.Color3f)
        multiply = UsdShade.Shader.Define(self.stage, "/Model/Looks/Surface/Multiply")
        multiply.CreateIdAttr("ND_multiply_color3")
        multiply.CreateInput("in1", Sdf.ValueTypeNames.Color3f).ConnectToSource(image.ConnectableAPI(), "out")
        multiply.CreateInput("in2", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.5, 0.25, 1))
        multiply.CreateOutput("out", Sdf.ValueTypeNames.Color3f)
        self.shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).ConnectToSource(multiply.ConnectableAPI(), "out")
        record = self.records()[0]
        graph = record["graph"]
        self.assertEqual(graph["surface"]["base_color"]["node"], str(multiply.GetPath()))
        specs = {node["id"]: node for node in graph["nodes"]}
        self.assertEqual(specs["ND_multiply_color3"]["inputs"]["in2"]["value"], (0.5, 0.25, 1))
        self.assertEqual(specs["ND_multiply_color3"]["inputs"]["in1"]["node"], str(image.GetPath()))


if __name__ == "__main__":
    unittest.main()
