"""Cargo network placement regressions without Houdini or Octane."""
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "python"))
import CargoImporter as cargo
import CargoOctaneListener as listener


class Vector2(tuple):
    def __new__(cls, x, y):
        return super().__new__(cls, (x, y))

    def x(self):
        return self[0]

    def y(self):
        return self[1]

    def __add__(self, other):
        return Vector2(self.x() + other.x(), self.y() + other.y())


class CargoLayoutTests(unittest.TestCase):
    def setUp(self):
        self.hou = mock.Mock(Vector2=Vector2)
        self.parent = self.hou.node.return_value
        self.existing = self.make_node("existing", Vector2(10, -6), Vector2(3, 2))
        self.parent.children.return_value = [self.existing]
        self.record = {"name": "imported", "path": "material.usd"}

    def make_node(self, name, position, size):
        node = mock.Mock()
        node.position.return_value = position
        node.size.return_value = size
        node.path.return_value = "/mat/" + name

        def set_position(point):
            node.position.return_value = point

        node.setPosition.side_effect = set_position
        return node

    def test_manual_import_lays_out_only_new_materials_and_moves_them_as_a_group(self):
        first = self.make_node("first", Vector2(0, 0), Vector2(1, 1))
        second = self.make_node("second", Vector2(0, 0), Vector2(1, 1))

        def layout(*, items):
            items[0].setPosition(Vector2(-8, 5))
            items[1].setPosition(Vector2(-5, 3))

        def create(*args):
            node = next(new_nodes)
            self.parent.children.return_value.append(node)
            return node

        new_nodes = iter([first, second])
        self.parent.layoutChildren.side_effect = layout
        with mock.patch.object(cargo, "hou", self.hou), \
                mock.patch.object(cargo, "scan_material_usds", return_value=[self.record, self.record]), \
                mock.patch.object(cargo, "print_material_list"), \
                mock.patch.object(cargo, "find_target_material_context", return_value=self.parent), \
                mock.patch.object(cargo, "material_record_from_usd", return_value=self.record), \
                mock.patch.object(cargo, "create_octane_material", side_effect=create):
            result = cargo.run_material_import(".", [], [0, 1], "bump", "uv")

        self.assertEqual(result, [first, second])
        self.parent.layoutChildren.assert_called_once_with(items=[first, second])
        self.existing.setPosition.assert_not_called()
        self.assertEqual(first.position(), Vector2(15, -4.5))
        self.assertEqual(second.position(), Vector2(18, -6.5))

    def test_live_material_import_appends_to_the_right(self):
        new_node = self.make_node("imported", Vector2(40, 20), Vector2(1, 1))

        def create(*args):
            self.parent.children.return_value.append(new_node)
            return new_node

        with mock.patch.object(cargo, "hou", self.hou), \
                mock.patch.object(listener, "hou", self.hou), \
                mock.patch.object(listener, "_asset_kind_and_record", return_value=("material", self.record)), \
                mock.patch.object(cargo, "material_record_from_usd", return_value=self.record), \
                mock.patch.object(cargo, "create_octane_material", side_effect=create):
            result = listener.import_received_asset("material.usd")

        self.assertIs(result, new_node)
        self.parent.layoutChildren.assert_not_called()
        self.existing.setPosition.assert_not_called()
        self.assertEqual(new_node.position(), Vector2(15, -5.5))

    def test_live_model_import_uses_its_existing_placement_without_relayout(self):
        with mock.patch.object(cargo, "hou", self.hou), \
                mock.patch.object(listener, "hou", self.hou), \
                mock.patch.object(listener, "_asset_kind_and_record", return_value=("model", self.record)), \
                mock.patch.object(cargo, "create_model_import") as create:
            result = listener.import_received_asset("model.usd")

        self.assertIs(result, create.return_value)
        self.assertEqual(create.call_args.args[-1], Vector2(15, -5))
        self.parent.layoutChildren.assert_not_called()
