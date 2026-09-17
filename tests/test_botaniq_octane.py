"""Native Octane Botaniq material builder regressions.

Run with Houdini's hython, for example::

    hython -m unittest tests.test_botaniq_octane
"""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "python"))

import hou
import BotaniqOctane


def _label(node):
    return (node.name() + " " + node.type().name()).lower()


def _nodes(container):
    return list(container.allSubChildren())


def _surface(container):
    candidates = [n for n in _nodes(container) if "standard" in _label(n) and "surface" in _label(n)]
    if not candidates:
        raise AssertionError("native standard surface was not created")
    return candidates[0]


def _input_source(node, words):
    words = tuple(w.replace("_", "").lower() for w in words)
    for index, name in enumerate(node.inputNames()):
        if any(word in name.replace("_", "").lower() for word in words):
            source = node.input(index)
            if source is not None:
                return source
    return None


def _material(name, family, textures=None, parameters=None):
    return {
        "name": name,
        "family": family,
        "role": family,
        "textures": textures or {},
        "parameters": parameters or {
            "base_color": [0.35, 0.55, 0.2],
            "roughness": 0.63,
            "metallic": 0.0,
            "specular": 0.45,
        },
    }


class BotaniqOctaneTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="botaniq_octane_test_"))
        self.matnet = hou.node("/mat").createNode("subnet", "botaniq_octane_test_matnet")

    def tearDown(self):
        if self.matnet is not None:
            self.matnet.destroy()
        for child in self.folder.iterdir():
            child.unlink()
        self.folder.rmdir()

    def _build(self, material):
        for texture in material.get("textures", {}).values():
            relative = texture.get("path") if isinstance(texture, dict) else texture
            path = self.folder / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture")
        return BotaniqOctane.create_material(self.matnet, material, self.folder)

    def test_builds_native_octane_graph_without_materialx_nodes(self):
        subnet = self._build(_material("basic_native", "basic", {
            "basecolor": {"path": "base.png", "type": "color"},
            "normal": {"path": "normal.png", "type": "data"},
            "opacity": {"path": "opacity.png", "type": "data"},
        }))
        self.assertIsNotNone(subnet)
        self.assertTrue(_nodes(subnet))
        self.assertFalse(any("mtlx" in _label(n) or "materialx" in _label(n) for n in _nodes(subnet)))

    def test_opacity_uses_native_alpha_texture_node(self):
        subnet = self._build(_material("alpha_native", "basic", {
            "basecolor": {"path": "base.png", "type": "color"},
            "opacity": {"path": "opacity.png", "type": "alpha"},
        }))
        opacity = next(n for n in _nodes(subnet) if "alpha" in _label(n) and "image" in _label(n))
        self.assertIn("alpha", _label(opacity))
        self.assertIsNotNone(_input_source(_surface(subnet), ("opacity",)))

    def test_leaf_has_separate_subsurface_color_correction_and_no_diffuse_bump(self):
        subnet = self._build(_material("leaf_native", "leaf", {
            "basecolor": {"path": "leaf.png", "type": "color"},
            "opacity": {"path": "leaf_opacity.png", "type": "alpha"},
        }))
        surface = _surface(subnet)
        base = next((n for n in _nodes(subnet) if "tex_image" in _label(n) and "floatimage" not in _label(n)), None)
        subsurface = _input_source(surface, ("subsurfacecolor", "subsurface_color"))
        self.assertIsNotNone(subsurface)
        self.assertIsNot(subsurface, base)
        self.assertIn("correction", _label(subsurface))
        self.assertIsNone(_input_source(surface, ("bump", "height")))
        self.assertIsNone(_input_source(surface, ("normal",)))

    def test_normal_texture_drives_normal_input_without_bump_connection(self):
        subnet = self._build(_material("normal_native", "bark", {
            "basecolor": {"path": "base.png", "type": "color"},
            "normal": {"path": "normal.png", "type": "data"},
        }))
        surface = _surface(subnet)
        self.assertIsNotNone(_input_source(surface, ("normal",)))
        self.assertIsNone(_input_source(surface, ("bump", "height")))

    def test_bark_without_normal_gets_nonzero_height_bump(self):
        subnet = self._build(_material("bark_native", "bark", {
            "basecolor": {"path": "bark.png", "type": "color"},
        }))
        surface = _surface(subnet)
        base = next(n for n in _nodes(subnet) if "tex_image" in _label(n) and "floatimage" not in _label(n))
        bump_source = _input_source(surface, ("bump", "height"))
        self.assertIsNotNone(bump_source)
        self.assertIn("floatimage", _label(bump_source))
        filename = lambda node: next(p.eval() for p in node.parms() if p.name().lower() in ("a_filename", "filename", "file"))
        self.assertEqual(Path(filename(bump_source)).name, Path(filename(base)).name)
        bump_height = next(p for p in surface.parms() if "bumpheight" in p.name().lower())
        self.assertIn("ch(", bump_height.expression())
        control = next(p for p in self.matnet.parms() if p.name().lower().endswith("_bump"))
        control.set(0.37)
        self.assertGreater(float(bump_height.eval()), 0.0)

    def test_promoted_base_color_controls_drive_native_surface_by_expression(self):
        subnet = self._build(_material("promoted_color", "basic", parameters={
            "base_color": [0.2, 0.3, 0.4], "roughness": 0.4, "metallic": 0.1, "specular": 0.5,
        }))
        surface = _surface(subnet)
        corrector = next(n for n in _nodes(subnet) if "colorcorrection" in _label(n))
        control = self.matnet.parm("promoted_color_hue")
        self.assertIsNotNone(control)
        target = corrector.parm("hue")
        self.assertIn("ch(", target.expression())
        control.set(0.81)
        self.assertAlmostEqual(float(target.eval()), 0.81, places=4)


if __name__ == "__main__":
    unittest.main()
