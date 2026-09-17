from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts' / 'python'))

import BotaniqCollections


class BotaniqCollectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='botaniq_collections_test_')
        self.pack = Path(self.tmp.name) / 'pack'
        self.collection = self.pack / 'blends' / 'particles' / 'forest' / 'collection.blend'
        self.model = self.pack / 'blends' / 'models' / 'trees' / 'oak.blend'
        self.collection.parent.mkdir(parents=True)
        self.model.parent.mkdir(parents=True)
        self.collection.write_bytes(b'collection')
        self.model.write_bytes(b'model')

    def tearDown(self):
        self.tmp.cleanup()

    def test_discover_collections_uses_particles_hierarchy(self):
        records = BotaniqCollections.discover_collections(self.pack)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['category'], 'forest')
        self.assertEqual(records[0]['kind'], 'collection')

    def test_inspection_writes_adjacent_cache_and_reuses_it(self):
        completed = mock.Mock(returncode=0,
                              stdout='noise\nBOTANIQ_COLLECTION_JSON=' + json.dumps({'models': [str(self.model)]}) + '\n')
        with mock.patch.object(BotaniqCollections.subprocess, 'run', return_value=completed) as run:
            first = BotaniqCollections.inspect_collection(self.collection, 'blender.exe')
            second = BotaniqCollections.inspect_collection(self.collection, 'blender.exe')
        self.assertEqual(first['cache_status'], 'inspected')
        self.assertEqual(second['cache_status'], 'reused')
        self.assertEqual(first['models'], [str(self.model.resolve())])
        self.assertTrue(BotaniqCollections.cache_path(self.collection).is_file())
        run.assert_called_once()


if __name__ == '__main__':
    unittest.main()
