"""Pure Python tests for Botaniq batch discovery and cache selection."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "python"))
import BotaniqBatch
import BotaniqImporter


class BotaniqBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="botaniq_batch_test_")
        self.root = Path(self.tmp.name)
        (self.root / "pack" / "blends" / "models" / "trees").mkdir(parents=True)
        (self.root / "pack" / "blends" / "models" / "flowers").mkdir(parents=True)
        for path in (self.root / "pack" / "blends" / "models" / "trees" / "Oak.blend",
                     self.root / "pack" / "blends" / "models" / "flowers" / "Pine.blend",
                     self.root / "pack" / "blends" / "models" / "bq_Library_ignored.blend"):
            path.write_bytes(b"blend")
        (self.root / "pack" / "blends" / "models" / "trees" / "Oak_octane").mkdir()
        (self.root / "pack" / "blends" / "models" / "trees" / "Oak_octane" / "Generated.blend").write_bytes(b"blend")

    def tearDown(self):
        self.tmp.cleanup()

    def test_discovery_filters_library_and_generated_cache_and_reports_categories(self):
        records = BotaniqBatch.discover_assets(self.root / "pack")
        names = {record["name"] for record in records}
        self.assertEqual(names, {"Oak", "Pine"})
        for record in records:
            self.assertIsInstance(record["source"], Path)
            self.assertEqual(record["source"], self.root / "pack" / "blends" / "models" / record["relative"])
            self.assertTrue(record["category"])

    def test_folder_and_asset_selection_deduplicate_overlaps(self):
        records = BotaniqBatch.discover_assets(self.root / "pack")
        oak = next(record for record in records if record["name"] == "Oak")
        pine = next(record for record in records if record["name"] == "Pine")
        category = oak["category"]
        selected = BotaniqBatch.expand_selection(records, [category, oak["relative"].rsplit(".", 1)[0], pine["relative"].rsplit(".", 1)[0]])
        self.assertEqual({record["name"] for record in selected}, {"Oak", "Pine"})
        self.assertEqual(len(selected), 2)

    def test_package_path_source_and_custom_root_are_category_scoped(self):
        record = next(record for record in BotaniqBatch.discover_assets(self.root / "pack") if record["name"] == "Oak")
        self.assertEqual(BotaniqBatch.package_path(record), record["source"].parent / "Oak_octane")
        custom = self.root / "output"
        self.assertEqual(BotaniqBatch.package_path(record, mode="custom", custom_root=custom), custom / record["category"] / "Oak_octane")

    def test_custom_package_collision_is_distinct_between_categories(self):
        records = BotaniqBatch.discover_assets(self.root / "pack")
        oak = next(record for record in records if record["name"] == "Oak")
        fern = next(record for record in records if record["name"] == "Pine")
        custom = self.root / "output"
        self.assertNotEqual(BotaniqBatch.package_path(oak, mode="custom", custom_root=custom), BotaniqBatch.package_path(fern, mode="custom", custom_root=custom))

    def test_cache_status_distinguishes_missing_metadata_and_ready(self):
        record = next(record for record in BotaniqBatch.discover_assets(self.root / "pack") if record["name"] == "Oak")
        folder = self.root / "cache"
        self.assertEqual(BotaniqBatch.cache_status(folder, record["source"]), "missing")
        folder.mkdir()
        (folder / "geometry.usdc").write_bytes(b"usd")
        self.assertEqual(BotaniqBatch.cache_status(folder, record["source"]), "metadata_missing")
        manifest = {"schema": 3, "source": str(record["source"]), "material_backend": "octane_simple",
                    "materials": [{"name": "bark", "role": "bark", "parameters": {}, "textures": {}}], "objects": []}
        (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (folder / "asset.usda").write_text("#usda 1.0", encoding="utf-8")
        self.assertEqual(BotaniqBatch.cache_status(folder, record["source"]), "ready")
        manifest["materials"][0]["textures"] = {"basecolor": {"path": "textures/base.png"}}
        (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        self.assertEqual(BotaniqBatch.cache_status(folder, record["source"]), "metadata_missing")
        manifest["source"] = str(self.root / "other.blend")
        (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        self.assertEqual(BotaniqBatch.cache_status(folder, record["source"]), "conflict")

    @staticmethod
    def _manifest(source):
        return {"schema": 3, "source": str(source), "material_backend": "octane_simple",
                "materials": [{"name": "bark", "role": "bark", "parameters": {}, "textures": {}}], "objects": []}

    def test_ensure_ready_cache_reuses_without_blender_or_bundle_write(self):
        source = self.root / "pack" / "blends" / "models" / "trees" / "Oak.blend"
        output = self.root / "ready"
        output.mkdir()
        (output / "geometry.usdc").write_bytes(b"geometry")
        (output / "asset.usda").write_text("asset", encoding="utf-8")
        (output / "manifest.json").write_text(json.dumps(self._manifest(source)), encoding="utf-8")
        with mock.patch.object(BotaniqImporter, "_run_blender") as run, mock.patch.object(BotaniqImporter.BotaniqPackage, "write_bundle") as bundle:
            result = BotaniqImporter.ensure_package(source, output)
        self.assertEqual(result["status"], "reused")
        run.assert_not_called()
        bundle.assert_not_called()

    def test_ensure_missing_wrapper_repairs_only_wrapper_and_preserves_geometry(self):
        source = self.root / "pack" / "blends" / "models" / "trees" / "Oak.blend"
        output = self.root / "cache"
        output.mkdir()
        geometry = output / "geometry.usdc"
        geometry.write_bytes(b"precious geometry")
        before = (geometry.read_bytes(), geometry.stat().st_mtime_ns)
        (output / "manifest.json").write_text(json.dumps(self._manifest(source)), encoding="utf-8")

        def write_wrapper(folder, options=None):
            Path(folder, "asset.usda").write_text("wrapper", encoding="utf-8")

        with mock.patch.object(BotaniqImporter, "_run_blender") as run, mock.patch.object(BotaniqImporter.BotaniqPackage, "write_bundle", side_effect=write_wrapper):
            result = BotaniqImporter.ensure_package(source, output)
        self.assertEqual(result["status"], "metadata_repaired")
        run.assert_not_called()
        self.assertEqual((geometry.read_bytes(), geometry.stat().st_mtime_ns), before)

    def test_ensure_invalid_metadata_runs_blender_metadata_only_and_preserves_geometry(self):
        source = self.root / "pack" / "blends" / "models" / "trees" / "Oak.blend"
        output = self.root / "cache"
        output.mkdir()
        geometry = output / "geometry.usdc"
        geometry.write_bytes(b"precious geometry")
        before = (geometry.read_bytes(), geometry.stat().st_mtime_ns)
        (output / "manifest.json").write_text(json.dumps({"schema": 2, "source": str(source)}), encoding="utf-8")

        def run_metadata(_source, folder, _blender, _evaluated, metadata_only=False):
            self.assertTrue(metadata_only)
            Path(folder, "textures").mkdir()
            Path(folder, "textures", "base.png").write_bytes(b"texture")
            manifest = self._manifest(source)
            manifest["materials"][0]["textures"] = {"basecolor": {"path": "textures/base.png"}}
            Path(folder, "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            Path(folder, "export.log").write_text("metadata", encoding="utf-8")

        def write_wrapper(folder, options=None):
            Path(folder, "asset.usda").write_text("wrapper", encoding="utf-8")

        with mock.patch.object(BotaniqImporter, "_run_blender", side_effect=run_metadata) as run, mock.patch.object(BotaniqImporter.BotaniqPackage, "write_bundle", side_effect=write_wrapper):
            result = BotaniqImporter.ensure_package(source, output)
        self.assertEqual(result["status"], "metadata_repaired")
        run.assert_called_once()
        self.assertEqual((geometry.read_bytes(), geometry.stat().st_mtime_ns), before)


if __name__ == "__main__":
    unittest.main()
