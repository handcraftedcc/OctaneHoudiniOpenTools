"""File parameter regressions without a Houdini installation or NAS access."""
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "python"))
import CargoImporter as cargo
import openToolsMaterialUtils as materials


NAS_ROOT = r"\\kuiper\kuiper_production\03_Client_Work\Fuse\01_Projects\bluejay_campaign\cargo\kb3d_midmanhattan\7.0.2"
TEXTURE = NAS_ROOT + r"\Textures\png4k\KB3D_MIM_ConcretePolishBlocksGrayB_basecolor.png"
EXPECTED_TEXTURE = "//kuiper/kuiper_production/03_Client_Work/Fuse/01_Projects/bluejay_campaign/cargo/kb3d_midmanhattan/7.0.2/Textures/png4k/KB3D_MIM_ConcretePolishBlocksGrayB_basecolor.png"


class CargoPathTests(unittest.TestCase):
    def test_file_paths_preserve_unc_prefix_and_houdini_tokens(self):
        for source, expected in (
            (TEXTURE, EXPECTED_TEXTURE),
            (EXPECTED_TEXTURE, EXPECTED_TEXTURE),
            (r"C:\Cargo\texture.png", "C:/Cargo/texture.png"),
            (r"$HIP\cargo\texture.$F4.<UDIM>.exr", "$HIP/cargo/texture.$F4.<UDIM>.exr"),
            ("/mnt/cargo/texture.png", "/mnt/cargo/texture.png"),
        ):
            with self.subTest(source=source):
                self.assertEqual(materials.houdiniFilePath(source), expected)

    def test_cargo_resolved_texture_reaches_octane_with_forward_slashes(self):
        # Exercise the same resolver and shared texture builder used by live
        # and manual imports, including Windows normpath on Windows hosts.
        usd_path = NAS_ROOT.replace("\\", "/") + "/Materials/concrete.usd"
        resolved = cargo.resolve_asset_path(usd_path, "../Textures/png4k/KB3D_MIM_ConcretePolishBlocksGrayB_basecolor.png")
        image = mock.Mock()
        with mock.patch.object(materials, "hou", mock.Mock()), \
                mock.patch.object(materials, "createNode", return_value=image), \
                mock.patch.object(materials, "createTransform2DNode"), \
                mock.patch.object(materials, "createProjectionSetup", return_value=(None, None)), \
                mock.patch.object(materials, "connectToFirstNamedInput"), \
                mock.patch.object(materials, "setFirstExistingParm") as set_parm:
            materials.createTextureNodes(
                mock.Mock(), mock.Mock(),
                {"textures": {"base_color": resolved}}, {}, {},
            )
        set_parm.assert_any_call(image, ["A_FILENAME", "filename", "file"], EXPECTED_TEXTURE)

    def test_viewport_preview_uses_forward_slashes(self):
        for setter, owner, parm_setter in (
            (materials.setPreviewTexture, materials, "setFirstExistingParm"),
            (cargo.set_preview_texture, cargo, "set_first_existing_parm"),
        ):
            subnet = mock.Mock()
            textures = {"base_color": TEXTURE}
            argument = {"textures": textures} if owner is cargo else textures
            with mock.patch.object(owner, parm_setter) as set_parm:
                setter(subnet, argument)
            set_parm.assert_any_call(subnet, ["ogl_tex1"], EXPECTED_TEXTURE)

    def test_model_file_parameter_uses_forward_slashes(self):
        source = NAS_ROOT + r"\Models\building\geo.usd"
        nodes = [mock.Mock() for _ in range(4)]
        with mock.patch.object(cargo, "hou", mock.Mock()), \
                mock.patch.object(cargo, "create_node", side_effect=nodes), \
                mock.patch.object(cargo, "set_first_existing_parm") as set_parm:
            cargo.create_usd_import_column(
                mock.Mock(), {"name": "building", "path": source}, [],
                "bump", "uv", ["png4k"], mock.Mock(x=lambda: 0.0, y=lambda: 0.0),
            )
        set_parm.assert_any_call(nodes[1], ["filepath1", "filepath", "file"], source.replace("\\", "/"))


if __name__ == "__main__":
    unittest.main()
