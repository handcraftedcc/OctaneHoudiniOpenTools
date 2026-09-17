"""Portable closure composition regressions, independent of private assets."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/python'))
from BotaniqMaterialX import Graph
from BotaniqPortableSurface import lower_graph


class PortableSurfaceTests(unittest.TestCase):
    def test_transparent_mask_does_not_bleach_surface_parameters(self):
        graph = Graph()
        color = graph.literal([.1,.4,.05],'color3')
        transparent = graph.add('standard_surface','surfaceshader','clear',opacity=graph.literal(0,'color3'))
        leaf = graph.add('standard_surface','surfaceshader','leaf',base_color=color,
                         specular_roughness=graph.literal(.65,'float'))
        graph.surface = graph.add('mix','surfaceshader','alpha',bg=transparent,fg=leaf,mix=graph.literal(.25,'float'))
        lower_graph(graph)
        surface = next(n for n in graph.nodes if n['name']==graph.surface['node'])
        self.assertEqual(surface['inputs']['base_color'],color)
        self.assertEqual(surface['inputs']['specular_roughness']['value'],.65)
        self.assertEqual(sum(n['type']=='surfaceshader' for n in graph.nodes),1)

    def test_translucent_surface_has_nonzero_base_and_no_specular(self):
        graph = Graph()
        bsdf = graph.add('translucent_bsdf','BSDF','leaf',color=graph.literal([.1,.4,.1],'color3'))
        graph.surface = graph.add('surface','surfaceshader','closure',bsdf=bsdf)
        lower_graph(graph)
        surface = next(n for n in graph.nodes if n['name']==graph.surface['node'])
        self.assertEqual(surface['inputs']['base']['value'],1)
        self.assertEqual(surface['inputs']['specular']['value'],0)
        self.assertTrue(surface['inputs']['thin_walled']['value'])
        self.assertFalse(any(n['type']=='BSDF' or n['category']=='surface' for n in graph.nodes))

    def test_normal_coat_and_film_defaults_survive_layering(self):
        graph = Graph()
        normal = graph.add('normal','vector3','normal')
        a = graph.add('standard_surface','surfaceshader','a',normal=normal,coat_normal=normal,
                      thin_film_thickness=graph.literal(300,'float'))
        b = graph.add('standard_surface','surfaceshader','b',base_color=graph.literal([1,1,1],'color3'))
        graph.surface = graph.add('mix','surfaceshader','layer',bg=a,fg=b,mix=graph.literal(.5,'float'))
        lower_graph(graph)
        surface = next(n for n in graph.nodes if n['name']==graph.surface['node'])
        self.assertIn('normal',surface['inputs'])
        self.assertIn('coat_normal',surface['inputs'])
        self.assertIn('thin_film_thickness',surface['inputs'])
        self.assertFalse(any(n['category']=='mix' and n['type']=='surfaceshader' for n in graph.nodes))


if __name__ == '__main__':
    unittest.main()
