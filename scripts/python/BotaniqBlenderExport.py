"""Background Blender worker for BotaniqImporter; never saves source blend files."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

import bpy

try:
    from BotaniqPaths import remap_unc_to_drive
except ImportError:
    # Blender executes this file directly, so its directory is not guaranteed
    # to be on sys.path until after the source file has been opened.
    sys.path.insert(0, str(Path(__file__).parent))
    from BotaniqPaths import remap_unc_to_drive

FAMILIES = {'bq_Vegetation', 'bq_Bark', 'bq_Grass', 'bq_Basic', 'bq_Rock'}


def plain(value):
    if isinstance(value, (int, float, str, bool)):
        return value
    try:
        return list(value)
    except TypeError:
        return None


class Extractor:
    def __init__(self, output):
        self.output = output
        self.warnings = []
        self.primary_uvs = set()

    def image(self, image):
        source = Path(remap_unc_to_drive(bpy.path.abspath(image.filepath, library=image.library)))
        key = hashlib.sha256(str(source).encode()).hexdigest()[:10]
        target = self.output / 'textures' / (key + '_' + source.name)
        target.parent.mkdir(exist_ok=True)
        if image.packed_file:
            target.write_bytes(image.packed_file.data)
        elif source.is_file():
            shutil.copy2(source, target)
        else:
            raise FileNotFoundError('Missing texture: ' + str(source))
        return target.relative_to(self.output).as_posix()


def export(source, output, evaluated=False, metadata_only=False):
    if bpy.app.version < (5, 2, 0):
        raise RuntimeError('The first importer version requires Blender 5.2+ for tested USD weight export.')
    source = Path(remap_unc_to_drive(str(Path(source).resolve())))
    output = Path(remap_unc_to_drive(str(Path(output).resolve())))
    if output == source.parent:
        raise ValueError('Use a separate package subfolder, not the blend directory itself.')
    output.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.open_mainfile(filepath=str(source), load_ui=False)
    objects = [o for o in bpy.context.scene.objects if o.type == 'MESH']
    if not objects:
        raise ValueError('No mesh objects in ' + str(source))
    extractor = Extractor(output)
    sys.path.insert(0, str(Path(__file__).parent))
    from BotaniqSimpleExtract import extract_material
    from BotaniqGeometry import add_shader_attributes
    materials = {slot.material.name: slot.material for o in objects for slot in o.material_slots if slot.material}
    extracted = []
    for material in materials.values():
        extractor.primary_uvs = {o.data.uv_layers.active.name for o in objects
                                if o.data.uv_layers.active and any(s.material == material for s in o.material_slots)}
        extracted.append(extract_material(material, extractor))
    manifest = {'schema': 3, 'material_backend': 'octane_simple',
                'source': str(source), 'blender': bpy.app.version_string,
                'geometry': 'geometry.usdc', 'evaluated': evaluated,
                'materials': extracted, 'objects': []}
    bpy.ops.object.select_all(action='DESELECT')
    for obj in objects:
        obj.select_set(True)
        manifest['objects'].append({'name': obj.name, 'vertices': len(obj.data.vertices),
                                    'faces': len(obj.data.polygons), 'uv': obj.data.uv_layers.active.name if obj.data.uv_layers.active else '',
                                    'groups': [g.name for g in obj.vertex_groups],
                                    'properties': {k: plain(v) for k, v in obj.items() if k.startswith('bq_')},
                                    'modifiers': [m.name for m in obj.modifiers]})
        if metadata_only:
            continue
        if not evaluated:
            obj.data = obj.data.copy()
            for mod in obj.modifiers:
                mod.show_render = False
                mod.show_viewport = False
        else:
            for mod in obj.modifiers:
                mod.show_viewport = mod.show_render
                if mod.type == 'SUBSURF':
                    mod.levels = mod.render_levels
            bpy.context.view_layer.update()
            evaluated_obj = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
            obj.data = bpy.data.meshes.new_from_object(evaluated_obj, preserve_all_data_layers=True,
                                                      depsgraph=bpy.context.evaluated_depsgraph_get())
            obj.modifiers.clear()
        add_shader_attributes(obj, set())
    if not metadata_only:
        bpy.ops.wm.usd_export(filepath=str(output / 'geometry.usdc'), selected_objects_only=True,
                          export_materials=True, generate_materialx_network=False,
                          generate_preview_surface=False, export_textures_mode='KEEP',
                          export_animation=False, export_armatures=False, export_shapekeys=False,
                          export_subdivision='IGNORE', rename_uvmaps=False,
                          author_blender_name=True, relative_paths=True)
    manifest['warnings'] = extractor.warnings + [warning for m in extracted for warning in m.get('warnings', [])]
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print('BOTANIQ_EXPORT_COMPLETE', str(output), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--evaluated', action='store_true')
    parser.add_argument('--metadata-only', action='store_true')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    export(args.source, args.output, args.evaluated, args.metadata_only)
