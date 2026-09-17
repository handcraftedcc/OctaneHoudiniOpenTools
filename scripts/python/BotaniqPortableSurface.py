"""Lower MaterialX closure networks to one portable standard_surface.

The Houdini Octane OBJ plugin accepts ``standard_surface`` but does not accept
the otherwise-valid MaterialX ``mix`` node with a ``surfaceshader`` signature,
nor the ``surface`` constructor used to turn a BSDF into a surface shader.
This pass retains all of the source mask/value networks and replaces only that
unsupported closure composition with a standard-surface parameter composition.
"""


# Defaults from the MaterialX standard_surface definition.  The compiler emits
# only a small subset for botaniq, but a complete stable set makes a mixed
# parameter agree with an unconnected input on the other branch.
_DEFAULTS = {
    'base': (1.0, 'float'), 'base_color': ((0.8, 0.8, 0.8), 'color3'),
    'diffuse_roughness': (0.0, 'float'), 'metalness': (0.0, 'float'),
    'specular': (1.0, 'float'), 'specular_color': ((1.0, 1.0, 1.0), 'color3'),
    'specular_roughness': (0.2, 'float'), 'specular_IOR': (1.5, 'float'),
    'specular_anisotropy': (0.0, 'float'), 'specular_rotation': (0.0, 'float'),
    'transmission': (0.0, 'float'), 'transmission_color': ((1.0, 1.0, 1.0), 'color3'),
    'transmission_depth': (0.0, 'float'), 'transmission_scatter': ((0.0, 0.0, 0.0), 'color3'),
    'transmission_scatter_anisotropy': (0.0, 'float'), 'transmission_dispersion': (0.0, 'float'),
    'transmission_extra_roughness': (0.0, 'float'),
    'subsurface': (0.0, 'float'), 'subsurface_color': ((1.0, 1.0, 1.0), 'color3'),
    'subsurface_radius': ((1.0, 1.0, 1.0), 'color3'), 'subsurface_scale': (1.0, 'float'),
    'subsurface_anisotropy': (0.0, 'float'),
    'sheen': (0.0, 'float'), 'sheen_color': ((1.0, 1.0, 1.0), 'color3'),
    'sheen_roughness': (0.3, 'float'), 'coat': (0.0, 'float'),
    'coat_color': ((1.0, 1.0, 1.0), 'color3'), 'coat_roughness': (0.1, 'float'),
    'coat_anisotropy': (0.0, 'float'), 'coat_rotation': (0.0, 'float'),
    'coat_IOR': (1.5, 'float'), 'thin_film_thickness': (0.0, 'float'),
    'thin_film_IOR': (1.5, 'float'), 'emission': (0.0, 'float'),
    'emission_color': ((1.0, 1.0, 1.0), 'color3'),
    'opacity': ((1.0, 1.0, 1.0), 'color3'), 'thin_walled': (False, 'boolean'),
}


def _index(graph):
    return {node['name']: node for node in graph.nodes}


def _signal_type(value, fallback='float'):
    return value.get('type', fallback)


def _standard_defaults(graph, key):
    if key in {'normal', 'coat_normal'}:
        return graph.add('normal', 'vector3', 'default_' + key,
                         space=graph.literal('world', 'string'))
    value, kind = _DEFAULTS[key]
    return graph.literal(value, kind)


def _mix(graph, background, foreground, factor, kind):
    if background == foreground:
        return background
    if factor.get('value') == 0:
        return background
    if factor.get('value') == 1:
        return foreground
    return graph.add('mix', kind, 'closure_parameter_mix',
                     bg=background, fg=foreground, mix=factor)


def _normalized_mix(graph, background, foreground, factor):
    mixed = _mix(graph, background, foreground, factor, 'vector3')
    return graph.add('normalize', 'vector3', 'closure_normal_mix', **{'in': mixed})


def _fully_transparent(values):
    """Whether a lowered Blender Transparent BSDF is one mix branch."""
    opacity = values.get('opacity')
    if not opacity or 'value' not in opacity:
        return False
    value = opacity['value']
    return value == 0 or (isinstance(value, (list, tuple)) and not any(value))


def _prune(graph):
    """Keep nodes reachable from material outputs after closure lowering."""
    by_name = _index(graph)
    needed = set()

    def visit(value):
        name = value.get('node') if isinstance(value, dict) else None
        if not name or name in needed:
            return
        needed.add(name)
        for child in by_name[name]['inputs'].values():
            visit(child)

    visit(graph.surface)
    if hasattr(graph, 'displacement'):
        visit(graph.displacement)
    graph.nodes[:] = [node for node in graph.nodes if node['name'] in needed]


def lower_graph(graph):
    """Replace the graph's surface closure tree with one standard_surface.

    The source compiler already translates all non-closure texture, ramp,
    attribute, normal, season and snow computations to ordinary MaterialX
    values.  This function leaves those nodes intact and only lowers the
    closure tree.  Add Shader has no equivalent single standard surface and is
    deliberately rejected rather than quietly changing its energy.
    """
    by_name = _index(graph)
    memo = {}

    def lower(signal):
        name = signal.get('node')
        if name in memo:
            return memo[name]
        node = by_name.get(name)
        if not node:
            raise ValueError('Missing closure node ' + str(name))
        category = node['category']
        inputs = node['inputs']

        if category == 'standard_surface':
            result = dict(inputs)
            # MaterialX defines effective subsurface radius as radius * scale.
            # Octane accepts a connected radius but rejects a connected scale,
            # so move that authored scale into the radius before composition.
            if 'subsurface_scale' in result:
                radius = result.get('subsurface_radius', _standard_defaults(graph, 'subsurface_radius'))
                scale = result.pop('subsurface_scale')
                scale_color = graph.add('convert', 'color3', 'subsurface_scale_color', **{'in': scale})
                result['subsurface_radius'] = graph.add('multiply', 'color3', 'effective_subsurface_radius',
                                                         in1=radius, in2=scale_color)
        elif category == 'surface':
            bsdf = by_name.get(inputs['bsdf'].get('node'))
            if not bsdf:
                raise ValueError('Surface has no connected BSDF')
            if bsdf['category'] == 'translucent_bsdf':
                result = {
                    # Keep base at one: when this is blended with a diffuse
                    # closure, standard_surface applies the subsurface mix
                    # weight separately.  A zero here would square the
                    # remaining diffuse contribution at intermediate masks.
                    'base': graph.literal(1.0, 'float'),
                    'subsurface': graph.literal(1.0, 'float'),
                    'subsurface_color': bsdf['inputs']['color'],
                    # The Blender Translucent BSDF contains no specular lobe.
                    'specular': graph.literal(0.0, 'float'),
                    'thin_walled': graph.literal(True, 'boolean'),
                }
                if 'normal' in bsdf['inputs']:
                    result['normal'] = bsdf['inputs']['normal']
            elif bsdf['category'] == 'oren_nayar_diffuse_bsdf':
                result = {'base': bsdf['inputs'].get('weight', graph.literal(1.0, 'float')),
                          'base_color': bsdf['inputs']['color']}
                if 'roughness' in bsdf['inputs']:
                    result['diffuse_roughness'] = bsdf['inputs']['roughness']
                if 'normal' in bsdf['inputs']:
                    result['normal'] = bsdf['inputs']['normal']
            else:
                raise ValueError('Unsupported BSDF closure ' + bsdf['category'])
        elif category == 'mix' and node['type'] == 'surfaceshader':
            background = lower(inputs['bg'])
            foreground = lower(inputs['fg'])
            factor = inputs['mix']
            bg_transparent, fg_transparent = _fully_transparent(background), _fully_transparent(foreground)
            # A transparent closure changes coverage, not the physical values
            # behind the cutout.  Keeping the solid branch avoids tinting or
            # bleaching leaf/flower alpha edges with standard-surface defaults.
            if bg_transparent and not fg_transparent:
                result = dict(foreground)
                result['opacity'] = _mix(graph, background['opacity'],
                                          foreground.get('opacity', _standard_defaults(graph, 'opacity')),
                                          factor, 'color3')
            elif fg_transparent and not bg_transparent:
                result = dict(background)
                result['opacity'] = _mix(graph, background.get('opacity', _standard_defaults(graph, 'opacity')),
                                          foreground['opacity'], factor, 'color3')
            else:
                result = {}
                for key in sorted(set(background) | set(foreground)):
                    if key == 'thin_walled':
                        # A dynamic boolean OR is not a standard MaterialX value node.
                        # Thin walled is the safe, leaf-friendly interpretation whenever
                        # either closure needs transmission through a sheet.
                        a = background.get(key, _standard_defaults(graph, key))
                        b = foreground.get(key, _standard_defaults(graph, key))
                        result[key] = graph.literal(bool(a.get('value')) or bool(b.get('value')), 'boolean')
                        continue
                    default = _standard_defaults(graph, key)
                    a, b = background.get(key, default), foreground.get(key, default)
                    kind = _signal_type(a, _signal_type(b))
                    if key in {'normal', 'coat_normal'}:
                        result[key] = _normalized_mix(graph, a, b, factor)
                    else:
                        result[key] = _mix(graph, a, b, factor, kind)
        elif category == 'add' and node['type'] == 'surfaceshader':
            raise ValueError('Blender Add Shader has no lossless standard_surface lowering')
        else:
            raise ValueError('Unsupported surface closure ' + category + '/' + node['type'])
        memo[name] = result
        return result

    source = graph.surface
    values = lower(source)
    graph.surface = graph.add('standard_surface', 'surfaceshader', 'portable_surface', **values)
    _prune(graph)
    return graph
