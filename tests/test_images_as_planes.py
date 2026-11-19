"""Pure selection/option tests and native Houdini + Octane network tests.

Run with hython -m unittest discover -s tests -p test_images_as_planes.py.
"""
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "python"))
import ImagesAsPlanesImporter as importer

try:
    import hou
except ImportError:
    hou = None


class SelectionTests(unittest.TestCase):
    def test_preserves_tile_order_and_deduplicates_selection(self):
        paths = importer.normalize_paths(" first.png ; second.png ; first.png ; ")
        self.assertEqual([Path(p).name for p in paths], ["first.png", "second.png"])

    def test_existing_container_requires_shared_grouping(self):
        self.assertFalse(importer.normalize_options({"shared_matnet": False,
                                                     "use_existing_matnet": True})["use_existing_matnet"])
        migrated = importer.normalize_options({"shared_subnet": False, "use_existing_subnet": True})
        self.assertFalse(migrated["shared_matnet"])
        self.assertFalse(migrated["use_existing_matnet"])
        self.assertNotIn("shared_subnet", migrated)
        with self.assertRaises(ValueError):
            importer.normalize_options({"material_mode": "unknown"})


def _png(path, width, height):
    def chunk(name, data):
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))
    # Semitransparent RGBA pixels exercise alpha setup without shipping assets.
    raw = (b"\0" + bytes([200, 80, 30, 128]) * width) * height
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">2I5B", width, height, 8, 6, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


@unittest.skipIf(hou is None, "Requires Houdini and Octane")
class NetworkTests(unittest.TestCase):
    def setUp(self):
        if "octane::NT_MAT_STANDARD_SURFACE" not in hou.vopNodeTypeCategory().nodeTypes():
            self.skipTest("Requires Octane")
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.paths = []
        for name, size in [("wide.png", (8, 4)), ("tall.png", (2, 8))]:
            path = Path(folder.name) / name
            _png(path, *size)
            self.paths.append(str(path).replace("\\", "/"))
        self.parent = hou.node("/obj").createNode("subnet", "images_as_planes_tests")
        self.addCleanup(self.parent.destroy)

    def check_planes(self, results, shared):
        planes = [p for r in results for p in r["planes"]]
        self.assertEqual(len(planes), 2)
        for index, plane in enumerate(planes):
            node = plane["assignment"]
            geometry = node.geometry()
            self.assertFalse(node.errors())
            self.assertEqual(plane["name"].type().name(), "name")
            self.assertEqual(plane["name"].evalParm("name1"), Path(self.paths[index]).stem)
            self.assertEqual(len(geometry.prims()), 1)
            self.assertEqual(geometry.primStringAttribValues("name"), (Path(self.paths[index]).stem,))
            self.assertEqual(geometry.primStringAttribValues("image_path"), (self.paths[index],))
            self.assertEqual(geometry.primStringAttribValues("shop_materialpath"),
                             (plane["material"].path(),))
            size = geometry.boundingBox().sizevec()
            self.assertAlmostEqual(size.x() / size.y(), (2, 0.25)[index])
            self.assertAlmostEqual(size.z(), 0)
            for coordinate in geometry.boundingBox().center():
                self.assertAlmostEqual(coordinate, 0)
            self.assertEqual(plane["out"].type().name(), "null")
            self.assertTrue(plane["out"].name().startswith("OUT_" + Path(self.paths[index]).stem))
            self.assertEqual(plane["out"].inputs(), (node,))
            u = [v.attribValue("uv")[0] for v in geometry.prims()[0].vertices()]
            self.assertEqual((min(u), max(u)), (index, index + 1) if shared else (0, 1))
        return planes

    def test_optional_labs_layout_and_per_image_outputs(self):
        result = importer.import_images(self.paths, target=self.parent)[0]
        self.check_planes([result], False)
        self.assertEqual(result["merge"].inputs(), tuple(p["out"] for p in result["planes"]))
        layout = result["layout"]
        if layout is not None:
            self.assertTrue(layout.isBypassed())
            self.assertEqual(layout.inputs(), (result["merge"],))
            self.assertEqual(result["out"].inputs(), (layout,))
            self.assertAlmostEqual(result["out"].geometry().boundingBox().sizevec().x(), 1)
            layout.bypass(False)
            geometry = result["out"].geometry()
            self.assertFalse(result["out"].errors())
            self.assertEqual(len(geometry.prims()), 2)
            self.assertGreater(geometry.boundingBox().sizevec().x(), 1)
        else:
            self.assertEqual(result["out"].inputs(), (result["merge"],))
        before = set(result["container"].children())
        with patch.object(hou, "sopNodeTypeCategory") as category:
            category.return_value.nodeTypes.return_value = {}
            self.assertIsNone(importer._labs_layout(result["container"], result["merge"]))
        self.assertEqual(set(result["container"].children()), before)

    def test_obj_and_sop_grouping_in_both_material_modes(self):
        for sop in (False, True):
            target = self.parent.createNode("geo") if sop else self.parent
            for shared_matnet in (False, True):
                for mode in ("individual", "shared"):
                    with self.subTest(sop=sop, shared_matnet=shared_matnet, mode=mode):
                        results = importer.import_images(self.paths, {"shared_matnet": shared_matnet,
                            "material_mode": mode}, target)
                        self.assertEqual(len(results), 1 if shared_matnet else 2)
                        planes = self.check_planes(results, mode == "shared")
                        self.assertEqual(planes[0]["material"] == planes[1]["material"],
                                         shared_matnet and mode == "shared")
                        for result in results:
                            self.assertEqual(result["container"], target) if sop else self.assertEqual(result["container"].parent(), target)
                            self.assertEqual(result["matnet"].parent(), result["container"])
                            for plane in result["planes"]:
                                self.assertEqual(plane["grid"].parent(), result["container"])
                            if sop:
                                self.assertFalse(any(n.type().name() == "subnet" for n in target.children()))
                            else:
                                self.assertEqual(result["container"].type().name(), "geo")

    def test_shared_texture_file_list_alpha_and_native_viewport_layers(self):
        result = importer.import_images(self.paths, {"material_mode": "shared"}, self.parent)[0]
        material = result["planes"][0]["material"]
        for node_name in ("images", "image_alpha", "rgb_mask"):
            image = material.node(node_name)
            self.assertEqual(image.evalParm("mode"), "1")
            self.assertEqual(image.evalParm("filelist"), 2)
            self.assertEqual(image.evalParm("A_GRID_SIZEmin"), 2)
            self.assertEqual(image.evalParm("A_GRID_SIZEmax"), 1)
            self.assertEqual([image.evalParm("imageFile%d" % i) for i in range(2)], self.paths)
        self.assertEqual(material.node("image_alpha").evalParm("A_IMAGE_COLOR_FORMAT"), 3)
        self.assertEqual(material.node("rgb_mask").evalParm("A_IMAGE_COLOR_FORMAT"), 2)
        self.assertEqual(material.parmTemplateGroup().find("ogl_numtex").folderType(),
                         hou.folderType.MultiparmBlock)
        self.assertEqual(material.evalParm("ogl_numtex"), 2)
        for index in range(2):
            suffix = str(index + 1)
            self.assertEqual(material.evalParm("ogl_tex" + suffix), self.paths[index])
            self.assertEqual(material.evalParm("ogl_texuvset" + suffix), "uv")
            self.assertEqual(material.parmTuple("ogl_tex_uvtranslate" + suffix).eval(), (index, 0))
            self.assertEqual(material.evalParm("ogl_tex_wrap" + suffix), "decal")

    def test_opacity_mask_controls_in_both_material_modes(self):
        for mode in ("individual", "shared"):
            with self.subTest(mode=mode):
                result = importer.import_images(self.paths, {"material_mode": mode}, self.parent)[0]
                matnet = result["matnet"]
                self.assertEqual(matnet.evalParm("use_rgb_as_alpha"), 0)
                self.assertEqual(matnet.evalParm("invert_image_alpha"), 0)
                for plane in result["planes"]:
                    material = plane["material"]
                    alpha, rgb_mask = material.node("image_alpha"), material.node("rgb_mask")
                    source_switch = material.node("opacity_mask_source")
                    opacity_switch = material.node("enable_image_alpha")
                    surface = importer.materials.findStandardSurfaceNode(material)
                    self.assertEqual(source_switch.inputs()[1:], (alpha, rgb_mask))
                    self.assertEqual(opacity_switch.inputs()[1:], (material.node("opaque"), source_switch))
                    multiply = material.node("opacity_multiply")
                    ray_switch = material.node("opacity_ray_switch")
                    self.assertEqual(multiply.inputs(), (opacity_switch, ray_switch))
                    self.assertEqual(surface.inputs()[surface.inputIndex("opacity")], multiply)
                    for name, _, parm in importer.OPACITY_RAYS:
                        self.assertEqual(matnet.evalParm("opacity_ray_" + name), 1)
                        self.assertEqual(ray_switch.evalParm(parm), 1)
                    if mode == "individual":
                        self.assertEqual(rgb_mask.type().name(), importer.materials.OCTANE_FLOAT_IMAGE_NODE_TYPE)
                        self.assertEqual(rgb_mask.evalParm("A_FILENAME"), plane["image"])
                    for use_rgb, invert, enabled in ((0, 0, 1), (1, 1, 1), (0, 1, 1), (1, 1, 0)):
                        matnet.setParms({"use_rgb_as_alpha": use_rgb, "invert_image_alpha": invert,
                                         "image_alpha_to_opacity": enabled})
                        self.assertEqual(source_switch.evalParm("index"), 1 + use_rgb)
                        self.assertEqual(opacity_switch.evalParm("index"), 1 + enabled)
                        for texture in (alpha, rgb_mask):
                            self.assertEqual(texture.evalParm("invert"), invert)
                            self.assertEqual(texture.evalParm("colorSpace"), importer.materials.COLOR_SPACE_NON_COLOR)
                    self.assertEqual(material.node("color_adjustment").evalParm("invert"), 0)
                # Ray control remains active when image alpha is disabled.
                matnet.setParms({"image_alpha_to_opacity": 0, "opacity_ray_shadow": 0,
                                 "use_basecolor_texture": 0})
                for plane in result["planes"]:
                    material = plane["material"]
                    self.assertEqual(material.node("opacity_ray_switch").evalParm("texture1"), 0)
                    self.assertEqual(material.node("opacity_ray_switch").evalParm("texture"), 1)
                    for index in range(1, material.evalParm("ogl_numtex") + 1):
                        self.assertEqual(material.evalParm("ogl_use_tex" + str(index)), 1)
                    self.assertEqual(material.evalParm("ogl_tex1"), self.paths[0] if mode == "shared" else plane["image"])

    def test_controls_drive_all_materials_and_leave_emission_connected(self):
        result = importer.import_images(self.paths, target=self.parent)[0]
        matnet = result["matnet"]
        matnet.setParms({"emission_amount": 3, "use_basecolor_texture": 0,
                         "image_alpha_to_opacity": 0, "color_adjustment": 1,
                         "hue": 0.2, "saturation": 0.6, "basecolorr": 0.3})
        for plane in result["planes"]:
            material = plane["material"]
            surface = importer.materials.findStandardSurfaceNode(material)
            self.assertEqual(surface.evalParm("emissionWeight"), 3)
            self.assertEqual(material.node("enable_basecolor_texture").evalParm("index"), 1)
            self.assertEqual(material.node("enable_image_alpha").evalParm("index"), 1)
            self.assertEqual(material.node("enable_color_adjustment").evalParm("index"), 2)
            self.assertAlmostEqual(material.node("color_adjustment").evalParm("hue"), 0.2)
            self.assertAlmostEqual(material.node("solid_basecolor").evalParm("A_VALUEr"), 0.3)
            self.assertEqual(surface.inputs()[surface.inputIndex("emissionColor")],
                             material.node("enable_color_adjustment"))
            self.assertEqual(material.evalParm("ogl_use_tex1"), 1)

    def test_reuse_sop_network_preserves_existing_nodes_and_repeated_imports(self):
        geo = self.parent.createNode("geo")
        subnet = geo.createNode("subnet")
        existing = subnet.createNode("null", "existing")
        existing.setPosition(hou.Vector2(-4, 8))
        matnet = subnet.createNode("matnet", "existing_materials")
        old_material = matnet.createNode("subnet", "keep_material")
        old_material.setPosition(hou.Vector2(-3, 4))
        importer._controls(matnet)
        old_group = matnet.parmTemplateGroup()
        old_group.remove("use_rgb_as_alpha")
        old_group.remove("invert_image_alpha")
        old_group.remove("opacity_ray_switch")
        matnet.setParmTemplateGroup(old_group)
        matnet.parm("emission_amount").set(7)
        options = {"use_existing_matnet": True, "material_mode": "shared"}
        first = importer.import_images(self.paths, options, subnet)[0]
        matnet.parm("opacity_ray_shadow").set(0)
        second = importer.import_images(self.paths, options, subnet)[0]
        self.assertEqual(existing.position(), hou.Vector2(-4, 8))
        self.assertEqual(first["container"], subnet)
        self.assertEqual(second["container"], subnet)
        self.assertEqual(first["matnet"], matnet)
        self.assertEqual(second["matnet"], matnet)
        self.assertEqual(old_material.position(), hou.Vector2(-3, 4))
        self.assertEqual(matnet.evalParm("emission_amount"), 7)
        self.assertEqual(matnet.evalParm("use_rgb_as_alpha"), 0)
        self.assertEqual(matnet.evalParm("invert_image_alpha"), 0)
        self.assertEqual(matnet.evalParm("opacity_ray_shadow"), 0)
        for result in (first, second):
            surface = importer.materials.findStandardSurfaceNode(result["planes"][0]["material"])
            self.assertEqual(surface.evalParm("emissionWeight"), 7)
            self.assertEqual(result["planes"][0]["material"].node("opacity_ray_switch").evalParm("texture1"), 0)
        self.assertNotEqual(first["planes"][0]["material"], second["planes"][0]["material"])
        self.check_planes([first], True)
        self.check_planes([second], True)

    def test_failed_reuse_rolls_back_new_nodes_and_display_flags(self):
        geo = self.parent.createNode("geo")
        existing = geo.createNode("null", "existing")
        existing.setDisplayFlag(True)
        existing.setRenderFlag(True)
        matnet = geo.createNode("matnet", "keep_matnet")
        old_material = matnet.createNode("subnet", "keep_material")
        original_group = matnet.parmTemplateGroup().asDialogScript()
        before = set(geo.children())
        original = importer.create_plane
        def fail_after_first(*args, **kwargs):
            result = original(*args, **kwargs)
            raise RuntimeError("Simulated geometry failure")
        with patch.object(importer, "create_plane", fail_after_first):
            with self.assertRaisesRegex(RuntimeError, "Simulated geometry failure"):
                importer.import_images(self.paths, {"use_existing_matnet": True}, geo)
        self.assertEqual(set(geo.children()), before)
        self.assertEqual(matnet.children(), (old_material,))
        self.assertEqual(matnet.parmTemplateGroup().asDialogScript(), original_group)
        self.assertEqual(geo.displayNode(), existing)
        self.assertEqual(geo.renderNode(), existing)

    def test_invalid_image_does_not_create_nodes(self):
        before = set(self.parent.children())
        with self.assertRaises(ValueError):
            importer.import_images(self.paths + [os.path.join(tempfile.gettempdir(), "missing_image.png")],
                                   target=self.parent)
        self.assertEqual(set(self.parent.children()), before)


if __name__ == "__main__":
    unittest.main()
