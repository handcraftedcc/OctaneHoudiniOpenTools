"""Native batch network tests; run with Houdini 22+ hython (Octane is not required)."""
import base64
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "python"))
import GltfImporter as importer

try:
    import hou
except ImportError:
    hou = None


@unittest.skipIf(hou is None or hou.applicationVersion()[0] < 22, "Requires Houdini 22+")
class HoudiniGltfBatchTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        binary = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
        document = {
            "asset": {"version": "2.0"}, "scene": 0,
            "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "material": 0}]}],
            "materials": [{"name": "Shared"}],
            "buffers": [{"byteLength": len(binary)}],
            "bufferViews": [{"buffer": 0, "byteLength": len(binary), "target": 34962}],
            "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3,
                           "type": "VEC3", "min": [0, 0, 0], "max": [1, 1, 0]}],
        }
        # Same model/material names exercise collision handling within one SOP network.
        self.paths = []
        for index, extension in enumerate(("gltf", "glb")):
            folder = Path(self.folder.name) / str(index)
            folder.mkdir()
            path = folder / ("model." + extension)
            if extension == "glb":
                payload = json.dumps(document).encode("utf-8")
                payload += b" " * (-len(payload) % 4)
                length = 12 + 8 + len(payload) + 8 + len(binary)
                path.write_bytes(struct.pack("<4sII", b"glTF", 2, length)
                                 + struct.pack("<II", len(payload), 0x4E4F534A) + payload
                                 + struct.pack("<II", len(binary), 0x004E4942) + binary)
            else:
                text_doc = dict(document)
                text_doc["buffers"] = [{"byteLength": len(binary),
                    "uri": "data:application/octet-stream;base64," + base64.b64encode(binary).decode("ascii")}]
                path.write_text(json.dumps(text_doc), encoding="utf-8")
            self.paths.append(str(path))
        self.parent = hou.node("/obj").createNode("subnet", "gltf_batch_test")
        self.addCleanup(self.parent.destroy)

    def import_into(self, target):
        def placeholder(parent, record, settings):
            return parent.createNode("subnet", record["name"])
        with patch.object(importer, "find_target_context", return_value=target), \
                patch.object(importer, "create_octane_material", placeholder), \
                patch.object(hou, "ui", create=True) as ui:
            results = importer.import_gltf_files(self.paths)
        ui.displayMessage.assert_called_once()
        self.assertEqual(len(results), 2)
        for result, path in zip(results, self.paths):
            self.assertEqual(result["import"].parm("gltffile").eval(), path)
            geometry = result["out"].geometry()
            self.assertFalse(result["out"].errors())
            self.assertEqual(len(geometry.prims()), 1)
            self.assertEqual(geometry.primStringAttribValues("shop_materialpath"),
                             (result["materials"][0],),
                             [(a.name(), geometry.prims()[0].attribValue(a)) for a in geometry.primAttribs()])
        self.assertNotEqual(results[0]["materials"], results[1]["materials"])
        return results

    def test_obj_batch_creates_separate_geometry_objects(self):
        results = self.import_into(self.parent)
        first, second = [result["geo"] for result in results]
        self.assertNotEqual(first.name(), second.name())
        self.assertGreater(second.position().x(), first.position().x())

    def test_sop_batch_keeps_chains_separate_and_preserves_existing_nodes(self):
        geo = self.parent.createNode("geo")
        existing = geo.createNode("null", "existing")
        existing.setPosition(hou.Vector2(-5, 7))
        results = self.import_into(geo)
        self.assertEqual(existing.position(), hou.Vector2(-5, 7))
        self.assertGreater(results[1]["import"].position().x(), results[0]["import"].position().x())
        self.assertNotEqual(results[0]["out"].name(), results[1]["out"].name())
        self.assertEqual(geo.displayNode(), results[-1]["out"])


if __name__ == "__main__":
    unittest.main()
