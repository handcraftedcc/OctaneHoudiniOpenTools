"""Blender-side shader attributes on the exported (possibly evaluated) mesh."""
import hashlib
import math


def pointiness(mesh):
    """Angle-based curvature with a neighbor blur, following Cycles' definition.

    Reference: blender/blender intern/cycles/blender/mesh.cpp,
    attr_create_pointiness. Coincident vertices share normals and neighbors.
    """
    from mathutils import Vector
    from mathutils.kdtree import KDTree
    count = len(mesh.vertices)
    tree = KDTree(count)
    for v in mesh.vertices:
        tree.insert(v.co, v.index)
    tree.balance()
    parent = list(range(count))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for v in mesh.vertices:
        for _, j, _ in tree.find_range(v.co, math.sqrt(1.1920929e-7)):
            if abs(sum(mesh.vertices[j].co) - sum(v.co)) <= 3 * 1.1920929e-7:
                parent[root(j)] = root(v.index)
    roots = [root(i) for i in range(count)]
    normals = [Vector() for _ in roots]
    adjacent = [set() for _ in roots]
    for v, i in zip(mesh.vertices, roots):
        normals[i] += v.normal
    for edge in mesh.edges:
        a, b = (roots[i] for i in edge.vertices)
        if a != b:
            adjacent[a].add(b)
            adjacent[b].add(a)
    raw = []
    for i, neighbors in enumerate(adjacent):
        direction = sum(((mesh.vertices[j].co-mesh.vertices[i].co).normalized() for j in neighbors), Vector())
        cosine = normals[i].normalized().dot(direction / len(neighbors)) if neighbors else 1
        raw.append(math.acos(max(-1, min(1, cosine))) / math.pi)
    smooth = [(raw[i] + sum(raw[j] for j in n)) / (len(n)+1) for i,n in enumerate(adjacent)]
    return [smooth[i] for i in roots]


def add_shader_attributes(obj, required):
    mesh = obj.data
    def attribute(name, kind, values):
        existing = mesh.attributes.get(name)
        if existing:
            return
        data = mesh.attributes.new(name, kind, 'POINT').data
        field = 'vector' if kind == 'FLOAT_VECTOR' else 'color' if kind == 'FLOAT_COLOR' else 'value'
        for element, value in zip(data, values):
            setattr(element, field, value)
    if 'bq_generated' in required:
        lo = [min(v.co[i] for v in mesh.vertices) for i in range(3)]
        span = [max(v.co[i] for v in mesh.vertices)-lo[i] for i in range(3)]
        attribute('bq_generated','FLOAT_VECTOR', [tuple((v.co[i]-lo[i])/span[i] if span[i] else 0 for i in range(3)) for v in mesh.vertices])
    if 'bq_pointiness' in required:
        attribute('bq_pointiness','FLOAT',pointiness(mesh))
    random = int(hashlib.sha256(obj.name.encode()).hexdigest()[:8],16)/4294967295
    attribute('bq_object_random','FLOAT',[random]*len(mesh.vertices))
    attribute('bq_object_location','FLOAT_VECTOR',[tuple(obj.matrix_world.translation)]*len(mesh.vertices))
    attribute('bq_object_color','FLOAT_COLOR',[tuple(obj.color)]*len(mesh.vertices))
    for name, value in obj.items():
        if name.startswith('bq_') and isinstance(value, (float, int)):
            attribute(name, 'FLOAT', [float(value)]*len(mesh.vertices))
