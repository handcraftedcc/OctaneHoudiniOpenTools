"""Material imports must preserve existing material-network positions and wiring."""
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "python"))
import MegascansImporter as importer

try:
    import hou
except ImportError:
    hou = None


class Vector2(tuple):
    def __new__(cls, x, y):
        return tuple.__new__(cls, (x, y))

    def x(self):
        return self[0]

    def y(self):
        return self[1]


def resolved(record, preferences):
    return {"record": record, "textures": {}, "skipped": False}


class PlacementTests(unittest.TestCase):
    def test_standalone_materials_append_without_relayout(self):
        parent = mock.Mock()
        existing = mock.Mock()
        existing.position.return_value = Vector2(-10, -6)
        children = [existing]
        parent.children.side_effect = lambda: tuple(children)
        created = []

        def create_material(parent, record, *args):
            node = mock.Mock()
            node.position.return_value = Vector2(0, 0)
            node.setPosition.side_effect = lambda position: setattr(node.position, "return_value", position)
            children.append(node)
            created.append(node)
            return node

        records = [{"name": name, "kind": "material"} for name in ("first", "second")]
        with mock.patch.object(importer, "hou", mock.Mock(Vector2=Vector2)), \
                mock.patch.object(importer, "resolve_record", side_effect=resolved), \
                mock.patch.object(importer, "print_quality_report"), \
                mock.patch.object(importer, "find_target_material_context", return_value=parent), \
                mock.patch.object(importer, "create_octane_material", side_effect=create_material):
            result = importer.import_records(records, {})

        self.assertEqual(result["created"], created)
        self.assertEqual([node.position() for node in created], [Vector2(-7, -6), Vector2(-4, -6)])
        existing.setPosition.assert_not_called()
        existing.setInput.assert_not_called()
        parent.layoutChildren.assert_not_called()


@unittest.skipIf(hou is None, "Requires Houdini")
class HoudiniPlacementTests(unittest.TestCase):
    def test_repeated_import_preserves_nodes_wiring_boxes_and_notes(self):
        geo = hou.node("/obj").createNode("geo", "megascans_layout_test")
        self.addCleanup(geo.destroy)
        sop_matnet = geo.createNode("matnet", "existing_materials")
        mat_context = hou.node("/mat").createNode("subnet", "megascans_layout_test")
        self.addCleanup(mat_context.destroy)
        for target in (sop_matnet, mat_context):
            with self.subTest(target=target.path()):
                first = target.createNode("subnet", "keep_first")
                second = target.createNode("subnet", "keep_second")
                first.setPosition(hou.Vector2(-12, -8))
                second.setPosition(hou.Vector2(-5, -6))
                second.setInput(0, first)
                box = target.createNetworkBox()
                box.addItem(first)
                box.fitAroundContents()
                note = target.createStickyNote()
                note.setPosition(hou.Vector2(3, 10))
                protected = (first, second, box, note)
                positions = {item: item.position() for item in protected}
                old_inputs = second.inputs()
                records = [{"name": name, "kind": "material"} for name in ("new_a", "new_b")]
                with mock.patch.object(importer, "resolve_record", side_effect=resolved), \
                        mock.patch.object(importer, "print_quality_report"), \
                        mock.patch.object(importer, "find_target_material_context", return_value=target), \
                        mock.patch.object(importer, "create_octane_material",
                                          side_effect=lambda parent, record, *args: parent.createNode("subnet", record["name"])):
                    initial = importer.import_records(records, {})["created"]
                    initial_positions = {node: node.position() for node in initial}
                    repeated = importer.import_records(records[:1], {})["created"]
                self.assertEqual({item: item.position() for item in protected}, positions)
                self.assertEqual(second.inputs(), old_inputs)
                self.assertEqual({node: node.position() for node in initial}, initial_positions)
                self.assertGreater(initial[0].position().x(), second.position().x())
                self.assertGreater(initial[1].position().x(), initial[0].position().x())
                self.assertGreater(repeated[0].position().x(), initial[1].position().x())


if __name__ == "__main__":
    unittest.main()
