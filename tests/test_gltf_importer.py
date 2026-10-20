"""Batch selection and orchestration tests without a Houdini installation."""
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "python"))
import GltfImporter as importer


class GltfBatchTests(unittest.TestCase):
    def setUp(self):
        self.hou = mock.Mock()
        self.hou.expandString.side_effect = lambda path: path.replace("$HIP", "C:/project")
        patch = mock.patch.object(importer, "hou", self.hou)
        patch.start()
        self.addCleanup(patch.stop)

    def test_selection_handles_spaces_mixed_extensions_and_duplicates(self):
        self.hou.ui.selectFile.return_value = "$HIP/my model.gltf; $HIP/other.GLB; $HIP/my model.gltf"
        paths = importer.select_gltf_files()
        self.assertEqual(paths, [os.path.abspath("C:/project/my model.gltf"),
                                 os.path.abspath("C:/project/other.GLB")])
        self.assertTrue(self.hou.ui.selectFile.call_args.kwargs["multiple_select"])

    def test_cancel_and_invalid_selection_do_not_import(self):
        for selection in ("", "model.gltf;texture.png"):
            self.hou.ui.selectFile.return_value = selection
            with mock.patch.object(importer, "import_gltf") as load:
                self.assertIsNone(importer.run())
                load.assert_not_called()

    def test_batch_continues_after_failure_and_skip_with_one_summary(self):
        first = {"materials": ["/obj/first/mat"]}
        last = {"materials": ["/obj/last/mat"]}
        target = object()
        settings = {"mode": "bump"}
        with mock.patch.object(importer, "find_target_context", return_value=target) as context, \
                mock.patch.object(importer, "import_gltf", side_effect=[first, ValueError("bad JSON"), None, last]) as load:
            results = importer.import_gltf_files(
                ["first.gltf", "broken.glb", "skip.gltf", "last.glb"], settings
            )
        self.assertEqual(results, [first, last])
        context.assert_called_once()
        self.assertEqual(load.call_count, 4)
        for call in load.call_args_list:
            self.assertEqual(call.kwargs, dict(displacement_settings=settings,
                                               show_summary=False, target=target))
        self.hou.ui.displayMessage.assert_called_once()
        message = self.hou.ui.displayMessage.call_args.args[0]
        self.assertIn("Imported 2 of 4", message)
        self.assertIn("2 Octane material(s)", message)
        self.assertIn("Skipped 1", message)
        self.assertIn("broken.glb: bad JSON", message)

    def test_single_file_run_retains_dictionary_result(self):
        result = {"materials": []}
        with mock.patch.object(importer, "import_gltf", return_value=result) as load:
            self.assertIs(importer.run(gltf_path="single.gltf"), result)
        load.assert_called_once_with(os.path.abspath("single.gltf"), displacement_settings=None)

    def test_main_accepts_plural_file_paths(self):
        paths = ["a.gltf", "b.glb"]
        with mock.patch.object(importer, "import_gltf_files", return_value=[]) as load:
            importer.main({"file_paths": paths})
        load.assert_called_once_with([os.path.abspath(path) for path in paths], displacement_settings=None)

    def test_displacement_cancel_creates_no_network(self):
        with mock.patch.object(importer, "parse_materials", return_value=[{"has_displacement": True}]), \
                mock.patch.object(importer, "choose_displacement_settings", return_value=None), \
                mock.patch.object(importer, "create_gltf_network") as create:
            self.assertIsNone(importer.import_gltf("model.gltf", show_summary=False))
        create.assert_not_called()
        self.hou.ui.displayMessage.assert_not_called()

    def test_empty_batch_returns_without_dialog(self):
        self.assertEqual(importer.import_gltf_files([]), [])
        self.hou.ui.displayMessage.assert_not_called()


if __name__ == "__main__":
    unittest.main()
