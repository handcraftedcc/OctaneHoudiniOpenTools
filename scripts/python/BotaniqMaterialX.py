"""Portable MaterialX surface templates shared by USD and Houdini VOP import.

No Octane-specific shading nodes are used. New exports translate source graphs;
legacy packages retain the first-version family templates.
"""
from pathlib import Path
import re

import BotaniqPaths


def identifier(name):
    name = re.sub(r'[^A-Za-z0-9_]', '_', name)
    return ('bq_' + name) if not name or name[0].isdigit() else name


class Graph:
    def __init__(self):
        self.nodes = []

    def add(self, category, kind, name, **inputs):
        name = identifier(name) + '_' + str(len(self.nodes))
        self.nodes.append({'name': name, 'category': category, 'type': kind, 'inputs': inputs})
        return {'node': name, 'type': kind}

    @staticmethod
    def literal(value, kind):
        if kind in ('color3', 'vector3'):
            value = list(value[:3]) if isinstance(value, (list, tuple)) else [float(value or 0)] * 3
        elif kind == 'float':
            value = float(value[0] if isinstance(value, (list, tuple)) else value or 0)
        elif kind == 'integer':
            value = int(value or 0)
        return {'value': value, 'type': kind}

    def signal(self, signal, kind='color3', name='input'):
        if 'image' in signal:
            uv = self.add('geompropvalue', 'vector2', name + '_uv', geomprop=self.literal(signal['uv'], 'string')) if signal.get('uv') and not signal.get('primary_uv') else self.add('texcoord', 'vector2', name + '_uv', index=self.literal(0, 'integer'))
            image_type = 'color4' if signal.get('alpha') else kind
            image = self.add('image', image_type, name + '_image', file=self.literal(signal['image'], 'filename'), texcoord=uv)
            self.nodes[-1]['colorspace'] = 'srgb_texture' if kind == 'color3' and not signal.get('alpha') and signal.get('colorspace') == 'sRGB' else 'raw'
            if signal.get('alpha'):
                image = self.add('extract', 'float', name + '_alpha', **{'in': image, 'index': self.literal(3, 'integer')})
                if kind != 'float':
                    image = self.add('convert', kind, name + '_convert', **{'in': image})
            return image
        if 'attribute' in signal:
            return self.add('geompropvalue', kind, name + '_attribute', geomprop=self.literal(signal['attribute'], 'string'))
        if signal.get('op') == 'hsv':
            color = self.signal(signal['color'], 'color3', name + '_color')
            hue = self.add('subtract', 'float', name + '_hue', in1=self.signal(signal['hue'], 'float'), in2=self.literal(.5, 'float'))
            amount = self.add('combine3', 'vector3', name + '_adjustment', in1=hue, in2=self.signal(signal['saturation'], 'float'), in3=self.signal(signal['value'], 'float'))
            adjusted = self.add('hsvadjust', 'color3', name + '_hsv', **{'in': color, 'amount': amount})
            return self.add('mix', 'color3', name + '_mix', bg=color, fg=adjusted, mix=self.signal(signal['factor'], 'float'))
        if signal.get('op') in ('mix', 'multiply'):
            a, b = self.signal(signal['a'], kind), self.signal(signal['b'], kind)
            result = self.add('multiply', kind, name + '_mul', in1=a, in2=b) if signal['op'] == 'multiply' else b
            return self.add('mix', kind, name + '_mix', bg=a, fg=result, mix=self.signal(signal['factor'], 'float'))
        return self.literal(signal.get('value', 0), kind)


def material_graph(material, tint=(1, 1, 1), brightness=1.0, translucency=None, **options):
    """Expand one reusable family template with source material instance inputs."""
    g = Graph()
    if 'source_graph' in material:
        from BotaniqShaderGraph import compile_material
        options.setdefault('primary_uvs', material.get('primary_uvs', ['UVMap']))
        options.update(tint=tint, brightness=brightness)
        if translucency is not None:
            options.setdefault('controls', {}).setdefault('Translucency Factor', translucency)
        return compile_material(g, material, options)
    inputs = material['inputs']
    family = material['family']
    def sig(name, default, kind='float'):
        return g.signal(inputs.get(name, {'value': default}), kind, identifier(name))
    base_key = 'Diffuse Texture' if family == 'bq_Rock' else 'Base Color'
    base = sig(base_key, (.18, .35, .08) if family == 'bq_Grass' else (.5, .5, .5), 'color3')
    base = g.add('multiply', 'color3', 'tint', in1=base, in2=g.literal([v * brightness for v in tint], 'color3'))
    if family == 'bq_Vegetation':
        hue = g.add('subtract', 'float', 'hue_offset', in1=sig('Hue', .5), in2=g.literal(.5, 'float'))
        amount = g.add('combine3', 'vector3', 'hsv_controls', in1=hue, in2=sig('Saturation', 1), in3=sig('Value', 1))
        base = g.add('hsvadjust', 'color3', 'vegetation_color', **{'in': base, 'amount': amount})
    if family == 'bq_Basic':
        base = g.add('multiply', 'color3', 'source_tint', in1=base, in2=sig('Multiply Color', (1, 1, 1), 'color3'))
    leaf = family in ('bq_Vegetation', 'bq_Grass')
    values = {'base': g.literal(1., 'float'), 'base_color': base,
              'specular': sig('Specular' if family != 'principled' else 'Specular IOR Level', .5),
              'specular_roughness': sig('Roughness Texture' if family == 'bq_Rock' else 'Roughness', .5),
              'metalness': sig('Metallic', 0), 'opacity': sig('Alpha', 1, 'color3'),
              'thin_walled': g.literal(leaf, 'boolean')}
    if leaf:
        # Thin-walled Standard Surface subsurface gives diffuse leaf transmission.
        values['subsurface'] = sig('Translucency Factor', .5) if translucency is None else g.literal(translucency, 'float')
        values['subsurface_color'] = base
    normal_key = 'Normal Map' if family == 'bq_Rock' else 'Normal Color'
    if normal_key in inputs and 'image' in inputs[normal_key]:
        normal = g.signal(inputs[normal_key], 'vector3', 'normal_texture')
        values['normal'] = g.add('normalmap', 'vector3', 'normal_map', **{'in': normal, 'scale': sig('Normal Strength', 1)})
    surface = g.add('standard_surface', 'surfaceshader', family + '_surface', **values)
    g.surface = surface
    return g


def materialx_document(materials, options=None):
    import MaterialX as mx
    doc = mx.createDocument()
    library = mx.createDocument()
    mx.loadLibraries(mx.getDefaultDataLibraryFolders(), mx.getDefaultDataSearchPath(), library)
    # Resolve standard definitions without embedding/validating unrelated host libraries.
    doc.setDataLibrary(library)
    graphs = {}
    names = set()
    for material in materials:
        name = identifier(material['name'])
        if name in names:
            raise ValueError('Material names collide after sanitization: ' + name)
        names.add(name)
        g = material_graph(material, **(options or {}))
        ng = doc.addNodeGraph(name + '_graph')
        for node in g.nodes:
            n = ng.addNode(node['category'], node['name'], node['type'])
            if node.get('colorspace') and node['colorspace'] != 'raw':
                n.setColorSpace(node['colorspace'])
            for key, val in node['inputs'].items():
                p = n.addInput(key, val['type'])
                if 'node' in val:
                    p.setNodeName(val['node'])
                else:
                    value = val['value']
                    if isinstance(value, bool): value = 'true' if value else 'false'
                    elif isinstance(value, (list, tuple)): value = ', '.join(str(v) for v in value)
                    p.setValueString(str(value))
            nd = n.getNodeDef()
            if nd is None:
                raise ValueError('No MaterialX definition for ' + node['category'] + ' / ' + node['type'])
            node['nodedef'] = nd.getName()
        output = ng.addOutput('out', 'surfaceshader')
        output.setNodeName(g.surface['node'])
        m = doc.addNode('surfacematerial', name, 'material')
        m.addInput('surfaceshader', 'surfaceshader').setConnectedOutput(output)
        if hasattr(g, 'displacement'):
            displacement = ng.addOutput('displacement', 'displacementshader')
            displacement.setNodeName(g.displacement['node'])
            m.addInput('displacementshader', 'displacementshader').setConnectedOutput(displacement)
        graphs[material['name']] = g
    valid, errors = doc.validate()
    if not valid:
        raise ValueError('MaterialX validation failed: ' + errors)
    return doc, graphs


def write_bundle(folder, options=None):
    """Author portable MaterialX XML and explicit USD MaterialX shader networks."""
    import json
    import MaterialX as mx
    from pxr import Usd, UsdGeom, UsdShade, Sdf, Gf
    folder = Path(folder)
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    if options is None:
        options = manifest.get('materialx_options', {})
    doc, graphs = materialx_document(manifest['materials'], options)
    mx.writeToXmlFile(doc, str(folder / 'materials.mtlx'))
    stage = Usd.Stage.CreateNew(str(folder / 'asset.usda'))
    stage.GetRootLayer().subLayerPaths = ['geometry.usdc']
    types = {'float': Sdf.ValueTypeNames.Float, 'integer': Sdf.ValueTypeNames.Int,
             'boolean': Sdf.ValueTypeNames.Bool, 'string': Sdf.ValueTypeNames.String,
             'filename': Sdf.ValueTypeNames.Asset, 'color3': Sdf.ValueTypeNames.Color3f,
             'color4': Sdf.ValueTypeNames.Color4f, 'vector3': Sdf.ValueTypeNames.Vector3f,
             'vector2': Sdf.ValueTypeNames.Float2, 'surfaceshader': Sdf.ValueTypeNames.Token,
             'BSDF': Sdf.ValueTypeNames.Token, 'displacementshader': Sdf.ValueTypeNames.Token}
    for prim in list(stage.Traverse()):
        if prim.IsA(UsdGeom.Mesh):
            api = UsdGeom.PrimvarsAPI(prim)
            mesh = UsdGeom.Mesh(prim)
            bound, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
            assignments = [bound.GetPrim().GetName() if bound else ''] * len(mesh.GetFaceVertexCountsAttr().Get())
            for subset in UsdGeom.Subset.GetAllGeomSubsets(mesh):
                bound, _ = UsdShade.MaterialBindingAPI(subset.GetPrim()).ComputeBoundMaterial()
                if bound:
                    for index in subset.GetIndicesAttr().Get():
                        assignments[index] = bound.GetPrim().GetName()
            api.CreatePrimvar('botaniq_material', Sdf.ValueTypeNames.StringArray, 'uniform').Set(assignments)
            if not api.GetPrimvar('st'):
                # An alias supports conventional renderer UV lookup without deleting source layers.
                uvnames = [o['uv'] for o in manifest['objects'] if o['uv']]
                uv = next((api.GetPrimvar(n) for n in uvnames if api.GetPrimvar(n)), None)
                if uv:
                    api.CreatePrimvar('st', Sdf.ValueTypeNames.TexCoord2fArray, uv.GetInterpolation()).Set(uv.ComputeFlattened())
        if prim.GetTypeName() != 'Material':
            continue
        original = next((m for m in graphs if identifier(m) == prim.GetName()), None)
        if original is None:
            raise ValueError('Cannot match USD material ' + str(prim.GetPath()))
        g = graphs[original]
        mat = UsdShade.Material(prim)
        shaders = {}
        for node in g.nodes:
            shader = UsdShade.Shader.Define(stage, prim.GetPath().AppendChild(node['name']))
            shader.CreateIdAttr(node['nodedef'])
            shader.CreateOutput('out', types[node['type']])
            shaders[node['name']] = shader
        for node in g.nodes:
            shader = shaders[node['name']]
            for key, val in node['inputs'].items():
                inp = shader.CreateInput(key, types[val['type']])
                if 'node' in val:
                    inp.ConnectToSource(shaders[val['node']].ConnectableAPI(), 'out')
                else:
                    value = val['value']
                    if val['type'] in ('color3', 'vector3'): value = Gf.Vec3f(*value)
                    elif val['type'] == 'filename': value = Sdf.AssetPath(value)
                    inp.Set(value)
                if val['type'] == 'filename' and node.get('colorspace') and node['colorspace'] != 'raw':
                    inp.GetAttr().SetColorSpace(node['colorspace'])
        mat.CreateSurfaceOutput('mtlx').ConnectToSource(shaders[g.surface['node']].ConnectableAPI(), 'out')
        if hasattr(g, 'displacement'):
            mat.CreateDisplacementOutput('mtlx').ConnectToSource(shaders[g.displacement['node']].ConnectableAPI(), 'out')
    stage.GetRootLayer().Save()
    manifest['materialx_options'] = options or {}
    (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return str(folder / 'asset.usda')


def create_houdini_material(parent, material, folder, options=None):
    """Editable native MaterialX subnet, also understood by the Octane OBJ plugin."""
    import hou
    g = material_graph(material, **(options or {}))
    subnet = parent.createNode('subnet', identifier(material['name']))
    subnet.setMaterialFlag(True)
    subnet.setComment('botaniq ' + material['family'] + '\nTranslated source shader graph.\n' + '\n'.join(material.get('warnings', [])))
    nodes = {}
    for spec in g.nodes:
        node = subnet.createNode('mtlx' + spec['category'], spec['name'])
        if node.parm('signature'):
            signature = spec['type']
            if spec['category'] == 'convert':
                signature = spec['inputs']['in']['type'] + spec['type']
            node.parm('signature').set(signature)
        nodes[spec['name']] = node
        for key, val in spec['inputs'].items():
            if 'node' in val:
                node.setNamedInput(key, nodes[val['node']], 'out')
                continue
            value = val['value']
            if val['type'] == 'filename':
                value = BotaniqPaths.resolve_path(folder, value).replace('\\', '/')
            signature = node.parm('signature').evalAsString() if node.parm('signature') else ''
            parm = node.parm(key + '_' + signature) or node.parm(key + '_' + val['type']) or node.parm(key)
            pt = node.parmTuple(key + '_' + signature) or node.parmTuple(key + '_' + val['type']) or node.parmTuple(key)
            if val['type'] in ('color3', 'vector3', 'vector2'):
                if pt is None: raise ValueError('Missing vector input: ' + node.path() + '/' + key)
                pt.set(value)
            elif parm:
                try:
                    parm.set(value)
                except (TypeError, hou.Error) as exc:
                    raise ValueError('Cannot set {} = {!r} ({}): {}'.format(
                        parm.path(), value, val['type'], exc)) from exc
            else:
                raise ValueError('Missing parameter: ' + node.path() + '/' + key)
        if spec.get('colorspace'):
            for key, value in spec['inputs'].items():
                parm = node.parm(key + 'colorspace') if value['type'] == 'filename' else None
                if parm:
                    parm.set('Raw' if spec['colorspace'] == 'raw' else spec['colorspace'])
    connector = subnet.createNode('subnetconnector', 'surface_output')
    connector.parm('connectorkind').set('output')
    connector.parm('parmname').set('surface')
    connector.parm('parmlabel').set('Surface')
    connector.parm('parmtype').set('surface')
    connector.setInput(0, nodes[g.surface['node']])
    if hasattr(g, 'displacement'):
        connector = subnet.createNode('subnetconnector', 'displacement_output')
        connector.parm('connectorkind').set('output')
        connector.parm('parmname').set('displacement')
        connector.parm('parmlabel').set('Displacement')
        connector.parm('parmtype').set('displace')
        connector.setInput(0, nodes[g.displacement['node']])
    subnet.layoutChildren()
    return subnet
