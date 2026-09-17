"""Blender-side regression tests for BotaniqGeometry.

Run with Blender, for example::

    blender --background --factory-startup --disable-autoexec \
        --python-exit-code 1 --python tests/test_botaniq_geometry_blender.py
"""

import sys
import unittest
from pathlib import Path

try:
    import bpy
except ImportError:
    raise unittest.SkipTest("Requires Blender")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "python"))
from BotaniqGeometry import add_shader_attributes, pointiness


class BotaniqGeometryTests(unittest.TestCase):
    def setUp(self):
        self.meshes = []
        self.objects = []

    def tearDown(self):
        for obj in self.objects:
            bpy.data.objects.remove(obj, do_unlink=True)
        for mesh in self.meshes:
            bpy.data.meshes.remove(mesh)

    def make_object(self, name, vertices, faces=()):
        mesh = bpy.data.meshes.new(name + "Mesh")
        mesh.from_pydata(vertices, [], faces)
        mesh.update()
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        self.meshes.append(mesh)
        self.objects.append(obj)
        return obj

    def test_planar_mesh_has_half_pointiness(self):
        obj = self.make_object(
            "Planar",
            [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
            [(0, 1, 2, 3)],
        )

        values = pointiness(obj.data)

        self.assertEqual(len(values), 4)
        for value in values:
            self.assertAlmostEqual(value, 0.5, places=6)

    def test_isolated_vertices_have_zero_pointiness(self):
        obj = self.make_object("Isolated", [(0, 0, 0), (1, 2, 3)])

        values = pointiness(obj.data)

        # With no neighbors, the implementation's neutral cosine is one,
        # which maps to zero curvature in the Cycles definition.
        self.assertEqual(values, [0.0, 0.0])

    def test_generated_coordinates_are_normalized_to_bounds(self):
        obj = self.make_object(
            "Bounds",
            [(-2, 10, 4), (2, 10, 8), (2, 14, 8), (-2, 14, 4)],
            [(0, 1, 2), (0, 2, 3)],
        )

        add_shader_attributes(obj, {"bq_generated"})
        attribute = obj.data.attributes["bq_generated"]
        values = [tuple(element.vector) for element in attribute.data]

        self.assertEqual(values[0], (0.0, 0.0, 0.0))
        self.assertEqual(values[1], (1.0, 0.0, 1.0))
        self.assertEqual(values[2], (1.0, 1.0, 1.0))
        self.assertEqual(values[3], (0.0, 1.0, 0.0))
        for value in values:
            self.assertTrue(all(0.0 <= component <= 1.0 for component in value))

    def test_coincident_vertices_share_pointiness(self):
        obj = self.make_object(
            "Coincident",
            [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 0), (1, 1, 0)],
            [(0, 1, 2), (3, 4, 1)],
        )

        values = pointiness(obj.data)

        self.assertAlmostEqual(values[0], values[3], places=7)

    def test_existing_attributes_are_preserved(self):
        obj = self.make_object("Existing", [(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)])
        generated = obj.data.attributes.new("bq_generated", "FLOAT_VECTOR", "POINT")
        generated.data[0].vector = (0.2, 0.3, 0.4)
        generated.data[1].vector = (0.4, 0.5, 0.6)
        generated.data[2].vector = (0.6, 0.7, 0.8)
        point_attr = obj.data.attributes.new("bq_pointiness", "FLOAT", "POINT")
        for element in point_attr.data:
            element.value = 0.25

        add_shader_attributes(obj, {"bq_generated", "bq_pointiness"})

        for element, expected in zip(
            generated.data, [(0.2, 0.3, 0.4), (0.4, 0.5, 0.6), (0.6, 0.7, 0.8)]
        ):
            for actual, target in zip(element.vector, expected):
                self.assertAlmostEqual(actual, target, places=6)
        for element in point_attr.data:
            self.assertAlmostEqual(element.value, 0.25, places=6)


if __name__ == "__main__":
    # Blender leaves its command-line arguments in sys.argv; unittest should
    # only parse the script name when this file is run with --python.
    unittest.main(argv=[sys.argv[0]])
