"""Regression tests for mapped-drive/UNC path handling."""
import sys
import unittest
from pathlib import PureWindowsPath

sys.path.insert(0, 'scripts/python')
import BotaniqPaths


class BotaniqPathTests(unittest.TestCase):
    MAPPINGS = [('X:', r'\\asset-server\asset-share')]

    def test_unc_uses_matching_drive_without_hardcoding_it(self):
        source = r'\\asset-server\asset-share\Resources\plant\textures\leaf.png'
        self.assertEqual(
            BotaniqPaths.remap_unc_to_drive(source, self.MAPPINGS),
            r'X:\Resources\plant\textures\leaf.png')

    def test_longest_mapping_wins(self):
        source = r'\\server\share\package\textures\leaf.png'
        mappings = [('X:', r'\\server\share'), ('Y:', r'\\server\share\package')]
        self.assertEqual(BotaniqPaths.remap_unc_to_drive(source, mappings),
                         r'Y:\textures\leaf.png')

    def test_share_root_maps_to_drive_root(self):
        self.assertEqual(BotaniqPaths.remap_unc_to_drive(
            r'\\asset-server\asset-share', self.MAPPINGS), 'X:\\')

    def test_relative_texture_is_joined_then_remapped(self):
        folder = r'\\asset-server\asset-share\Resources\plant'
        self.assertEqual(BotaniqPaths.resolve_path(folder, 'textures/leaf.png', self.MAPPINGS),
                         r'X:\Resources\plant\textures\leaf.png')

    def test_unmapped_unc_is_preserved(self):
        source = r'\\other-server\share\textures\leaf.png'
        self.assertEqual(BotaniqPaths.remap_unc_to_drive(source, self.MAPPINGS), source)

    def test_path_objects_are_accepted(self):
        source = PureWindowsPath(r'\\asset-server\asset-share\Resources\leaf.png')
        self.assertEqual(BotaniqPaths.remap_unc_to_drive(source, self.MAPPINGS),
                         r'X:\Resources\leaf.png')


if __name__ == '__main__':
    unittest.main()
