"""Local catalog cache regression tests."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts' / 'python'))

import BotaniqBatch
import BotaniqCatalogCache
import BotaniqCollections


class BotaniqCatalogCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='botaniq_catalog_cache_')
        self.root = Path(self.temporary.name) / 'pack'
        self.models = self.root / 'blends' / 'models' / 'trees'
        self.previews = self.root / 'previews' / 'models' / 'trees'
        self.models.mkdir(parents=True)
        self.previews.mkdir(parents=True)
        (self.models / 'Oak.blend').write_bytes(b'blend')
        (self.previews / 'Oak.png').write_bytes(b'preview')
        self.cache = Path(self.temporary.name) / 'local_cache'
        self.cache_patch = mock.patch.object(BotaniqCatalogCache, 'cache_root', return_value=self.cache)
        self.cache_patch.start()

    def tearDown(self):
        self.cache_patch.stop()
        self.temporary.cleanup()

    def test_reuses_catalog_and_local_preview_without_rescanning(self):
        records, state = BotaniqCatalogCache.load_or_build(self.root)
        self.assertEqual(state, 'refreshed')
        self.assertEqual([record['name'] for record in records], ['Oak'])
        self.assertTrue(Path(records[0]['preview']).is_file())
        self.assertNotEqual(Path(records[0]['preview']).parent, self.previews)

        with mock.patch.object(BotaniqBatch, 'discover_assets', side_effect=AssertionError('unexpected asset rescan')), \
             mock.patch.object(BotaniqCollections, 'discover_collections', side_effect=AssertionError('unexpected collection rescan')):
            records, state = BotaniqCatalogCache.load_or_build(self.root)
        self.assertEqual(state, 'cached')
        self.assertEqual([record['name'] for record in records], ['Oak'])

    def test_manual_recache_refreshes_catalog(self):
        BotaniqCatalogCache.load_or_build(self.root)
        (self.models / 'Pine.blend').write_bytes(b'blend')
        (self.previews / 'Pine.png').write_bytes(b'preview')
        os.utime(self.models, None)
        os.utime(self.previews, None)
        records, state = BotaniqCatalogCache.load_or_build(self.root, force=True)
        self.assertEqual(state, 'refreshed')
        self.assertEqual([record['name'] for record in records], ['Oak', 'Pine'])


if __name__ == '__main__':
    unittest.main()
