"""Standard Python UI contract tests for BotaniqBrowser."""
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "python"))
import BotaniqBatch
import BotaniqBrowser
import BotaniqCatalogCache


class BotaniqBrowserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="botaniq_browser_test_")
        self.root = Path(self.tmp.name) / "pack" / "blends" / "models"
        (self.root / "trees").mkdir(parents=True)
        (self.root / "flowers").mkdir()
        (self.root / "trees" / "Oak.blend").write_bytes(b"blend")
        (self.root / "flowers" / "Pine.blend").write_bytes(b"blend")
        self.new_root = Path(self.tmp.name) / "other" / "blends" / "models"
        (self.new_root / "shrubs").mkdir(parents=True)
        (self.new_root / "shrubs" / "Fern.blend").write_bytes(b"blend")
        self.hou = types.SimpleNamespace(
            fileType=types.SimpleNamespace(Directory="directory"),
            expandString=lambda value: str(value).replace("$HIP", "C:/hip"),
            ui=mock.Mock(),
        )
        self.hou.ui.selectFile.return_value = str(self.new_root)
        self.hou.ui.selectFromTree.return_value = None
        self.hou.ui.displayMessage.return_value = 0
        self.hou_patch = mock.patch.dict(sys.modules, {"hou": self.hou})
        self.hou_patch.start()
        self.settings_patch = mock.patch.object(BotaniqBrowser.openToolsUtils, "setToolSettings")
        self.save = self.settings_patch.start()
        self.cache_patch = mock.patch.object(BotaniqCatalogCache, "cache_root", return_value=Path(self.tmp.name) / "catalog_cache")
        self.cache_patch.start()

    def tearDown(self):
        self.settings_patch.stop()
        self.cache_patch.stop()
        self.hou_patch.stop()
        self.tmp.cleanup()

    def test_saved_root_use_skips_folder_picker_and_preserves_other_preferences(self):
        settings = {"source_root": str(self.root), "custom_root": "keep-me", "other": 7}
        records = BotaniqBatch.discover_assets(self.root)
        self.hou.ui.selectFromTree.return_value = [records[0]["relative"].rsplit(".", 1)[0]]
        result = BotaniqBrowser.choose_assets(settings)
        self.assertEqual(result[0], self.root.resolve())
        self.hou.ui.selectFile.assert_not_called()
        self.save.assert_called_once()
        self.assertEqual(self.save.call_args.args[1]["custom_root"], "keep-me")
        self.assertEqual(self.save.call_args.args[1]["other"], 7)

    def test_change_root_action_returns_new_cached_library(self):
        settings = {"source_root": str(self.root), "custom_root": "keep-me"}
        result = BotaniqBrowser._change_library(self.new_root)
        self.assertEqual(result[0], self.new_root.resolve())
        self.hou.ui.selectFile.assert_called_once()
        self.assertEqual([record['name'] for record in result[1]['assets']], ['Fern'])

    def test_category_and_leaf_tree_selection_is_deduplicated(self):
        settings = {"source_root": str(self.root)}
        records = BotaniqBatch.discover_assets(self.root)
        self.hou.ui.selectFromTree.return_value = ["trees", "trees/Oak"]
        result = BotaniqBrowser.choose_assets(settings)
        self.assertEqual([record["name"] for record in result[1]], ["Oak"])
        self.assertEqual(self.hou.ui.selectFromTree.call_args.args[0], ["flowers/Pine", "trees/Oak"])

    def test_folder_paths_and_pack_preview_resolution(self):
        records = BotaniqBatch.discover_assets(self.root)
        self.assertEqual(BotaniqBrowser._folder_paths(records), ["", "flowers", "trees"])
        preview = BotaniqBrowser._preview_path(self.root, records[0])
        self.assertIsNone(preview)

        pack = ROOT / "testfiles" / "botaniq_starter"
        records = BotaniqBatch.discover_assets(pack)
        record = next(r for r in records if r["name"] == "bq_Tree_Aesculus-hippocastanum_A_summer")
        preview = BotaniqBrowser._preview_path(pack, record)
        self.assertTrue(preview and preview.name.endswith(".png"))

    def test_sections_group_assets_by_top_level_folder(self):
        records = BotaniqBatch.discover_assets(self.root)
        nested = dict(next(record for record in records if record["name"] == "Oak"))
        nested["category"] = "trees/evergreen"
        sections = BotaniqBrowser._sections(records + [nested])
        self.assertEqual([name for name, _items in sections], ["flowers", "trees"])
        self.assertEqual([record["name"] for record in sections[1][1]], ["Oak", "Oak"])

    def test_source_output_default_does_not_open_folder_picker(self):
        settings = {}
        result = BotaniqBrowser.choose_output(settings)
        self.assertEqual(result, {"mode": "source", "custom_root": None})
        self.hou.ui.selectFile.assert_not_called()

    def test_custom_output_uses_expanded_default_and_persists_root(self):
        settings = {}
        chosen = Path(self.tmp.name) / "exports"
        chosen.mkdir()
        self.hou.ui.displayMessage.return_value = 1
        self.hou.ui.selectFile.return_value = str(chosen)
        result = BotaniqBrowser.choose_output(settings)
        self.assertEqual(result, {"mode": "custom", "custom_root": chosen.resolve()})
        self.assertEqual(self.hou.ui.selectFile.call_args.kwargs["start_directory"], "C:/hip/assets/botaniq")
        self.assertEqual(self.save.call_args.args[1]["custom_root"], str(chosen.resolve()))


if __name__ == "__main__":
    unittest.main()
