"""Emission fallback must respect source colors and unavailable maps."""
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "python"))
import CargoImporter as cargo
import openToolsMaterialUtils as materials


class EmissionFallbackTests(unittest.TestCase):
    def assert_fallback(self, material, expected, emission_node=False):
        for owner, function, connector in (
            (materials, materials.connectEmissionColorFallback, "connectToFirstNamedInput"),
            (cargo, cargo.connect_emission_color_fallback, "connect_to_first_named_input"),
        ):
            with self.subTest(builder=owner.__name__):
                surface, base = mock.Mock(), mock.Mock()
                nodes = {"base_color": base}
                if emission_node:
                    nodes["emission_color"] = mock.Mock()
                with mock.patch.object(owner, connector) as connect:
                    function(surface, material, nodes)
                if expected:
                    connect.assert_called_once_with(surface, base, ["emissionColor"])
                else:
                    connect.assert_not_called()

    def test_strength_only_keeps_legacy_fallback(self):
        self.assert_fallback({"inputs": {"emission": 1}}, True)
        self.assert_fallback({"inputs": {"emission": 0}}, False)

    def test_constant_emission_including_black_is_preserved(self):
        for color in ((1, 0.1, 0.2), (0, 0, 0)):
            for key in ("inputs", "parameters"):
                self.assert_fallback({key: {"emission": 1, "emission_color": color}}, False)

    def test_explicit_or_uncreated_emission_texture_blocks_fallback(self):
        self.assert_fallback({"inputs": {"emission": 1}}, False, emission_node=True)
        for textures in (
            {"emission_color": "/missing/emission.png"},
            [{"channel": "emissive", "path": ""}],
        ):
            self.assert_fallback({"inputs": {"emission": 1}, "textures": textures}, False)

    def test_cargo_missing_map_intent_survives_file_filtering(self):
        text = '''float inputs:emission = 1
"png4k" {
    over "image_basecolor" {
        asset inputs:file = @../Textures/png4k/base.png@
    }
    over "image_emission" {
        asset inputs:file = @../Textures/png4k/missing_emission.png@
    }
}
'''
        with mock.patch.object(cargo, "read_text", return_value=text), \
                mock.patch.object(cargo.os.path, "exists", side_effect=lambda p: p.endswith("base.png")):
            material = cargo.material_record_from_usd({"path": "/cargo/Materials/test.usd"}, ["png4k"])
        self.assertEqual(material["texture_variant"], "png4k")
        self.assertEqual(set(material["textures"]), {"base_color"})
        self.assertIn("emission_color", material["authored_texture_channels"])
        self.assert_fallback(material, False)


if __name__ == "__main__":
    unittest.main()
