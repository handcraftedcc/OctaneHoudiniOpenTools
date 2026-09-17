"""Regression checks for the portable full-graph compiler (no private assets)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/python'))
from BotaniqMaterialX import Graph, materialx_document, create_houdini_material
from BotaniqShaderGraph import Compiler


def evaluate(graph, result):
    values = {}
    def read(value):
        return values[value['node']] if 'node' in value else value['value']
    for n in graph.nodes:
        p = {k: read(v) for k,v in n['inputs'].items()}
        op = n['category']
        if op == 'subtract': v = p['in1']-p['in2']
        elif op == 'divide': v = p['in1']/p['in2']
        elif op == 'multiply': v = p['in1']*p['in2']
        elif op == 'clamp': v = max(p['low'], min(p['high'], p['in']))
        elif op == 'mix': v = p['bg']*(1-p['mix'])+p['fg']*p['mix']
        elif op == 'ifgreatereq': v = p['in1'] if p['value1'] >= p['value2'] else p['in2']
        else: raise AssertionError(op)
        values[n['name']] = v
    return read(result)


class ShaderGraphTests(unittest.TestCase):
    def test_season_ramp_keeps_all_stops_and_clamps(self):
        stops = [(0, .2), (.25, .8), (.75, .4), (1, 1)]
        for x, expected in [(-1,.2),(0,.2),(.25,.8),(.5,.6),(.75,.4),(1,1),(2,1)]:
            graph = Graph()
            compiler = Compiler(graph, {}, {})
            result = compiler.ramp(graph.literal(x,'float'), stops, kind='float')
            self.assertAlmostEqual(evaluate(graph,result), expected)

    def test_constant_ramp_changes_at_stop(self):
        for x, expected in [(.499,.2),(.5,.9),(.9,.9)]:
            graph = Graph()
            result = Compiler(graph,{},{}).ramp(graph.literal(x,'float'),[(0,.2),(.5,.9)],'CONSTANT','float')
            self.assertAlmostEqual(evaluate(graph,result),expected)

    def test_instance_override_replaces_geometry_read(self):
        record = dict(type='Control',name='bq_season_offset',kind='VALUE',value=1,group='instance')
        graph = Graph()
        Compiler(graph,{},{}).compile(record)
        self.assertEqual(graph.nodes[-1]['category'],'geompropvalue')
        graph = Graph()
        Compiler(graph,{}, {'season':2.5}).compile(record)
        self.assertEqual(graph.nodes[-1]['inputs']['value']['value'],2.5)

    def test_unknown_connected_node_fails_with_source_name(self):
        compiler = Compiler(Graph(),{'nodes':[dict(type='Unsupported',name='Original node',kind='RGBA',output='Color')]},{})
        with self.assertRaisesRegex(ValueError, 'Original node'):
            compiler.expr({'ref':0})

    def test_color_to_scalar_is_explicit(self):
        graph = Graph()
        compiler = Compiler(graph,{}, {})
        color = graph.add('image','color3','texture')
        result = compiler.cast(color,'float')
        self.assertEqual(result['type'],'float')
        self.assertEqual([n['category'] for n in graph.nodes],['image','luminance','extract'])

    def test_displacement_has_material_output(self):
        source = {'nodes':[
            dict(type='ShaderNodeBsdfPrincipled',name='surface',kind='SHADER',inputs=[]),
            dict(type='ShaderNodeDisplacement',name='height',kind='VECTOR',inputs=[]),
        ],'surface':{'ref':0},'displacement':{'ref':1}}
        document, _ = materialx_document([dict(name='test',family='principled',source_graph=source)])
        self.assertTrue(document.validate()[0])
        self.assertIsNotNone(document.getNode('test').getInput('displacementshader'))

    def test_native_uv_conversions_keep_intended_types(self):
        import hou
        source = {'nodes':[
            dict(type='ShaderNodeUVMap',name='UV',kind='VECTOR',uv_map='UVMap',inputs=[]),
            dict(type='ShaderNodeTexImage',name='texture',kind='RGBA',output='Color',
                 image='textures/test.png',colorspace='sRGB',extension='REPEAT',projection='FLAT',
                 vector_linked=True,inputs=[dict(name='Vector',signal={'ref':0})]),
            dict(type='ShaderNodeBsdfPrincipled',name='surface',kind='SHADER',
                 inputs=[dict(name='Base Color',signal={'ref':1})]),
        ],'surface':{'ref':2}}
        material = dict(name='signature_regression',family='principled',source_graph=source)
        subnet = create_houdini_material(hou.node('/mat'),material,Path.cwd())
        try:
            converts = [n for n in subnet.children() if n.type().name() == 'mtlxconvert']
            self.assertEqual([n.parm('signature').eval() for n in converts],['vector2vector3','vector3vector2'])
            self.assertEqual([n.outputDataTypes()[0] for n in converts],['vector','vector2'])
        finally:
            subnet.destroy()

    def test_native_fractional_scale_uses_float_conversion_parameter(self):
        import hou
        source = {'nodes':[
            dict(type='ShaderNodeBsdfPrincipled',name='surface',kind='SHADER',inputs=[
                dict(name='Subsurface Scale',signal={'value':.125,'kind':'VALUE'}),
            ]),
        ],'surface':{'ref':0}}
        material = dict(name='fractional_scale_regression',family='principled',source_graph=source)
        subnet = create_houdini_material(hou.node('/mat'),material,Path.cwd())
        try:
            converts = [n for n in subnet.children() if n.type().name()=='mtlxconvert']
            self.assertEqual(len(converts),1)
            self.assertEqual(converts[0].parm('in_floatcolor3').eval(),.125)
            self.assertEqual(converts[0].outputDataTypes()[0],'color')
        finally:
            subnet.destroy()


if __name__ == '__main__':
    unittest.main()
