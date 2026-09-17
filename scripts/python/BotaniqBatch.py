"""Asset discovery, tree selection and cache decisions without Houdini imports."""
import json
from pathlib import Path

import BotaniqPaths


def discover_assets(root):
    root = Path(BotaniqPaths.remap_unc_to_drive(str(Path(root).expanduser().resolve())))
    if not root.is_dir():
        raise ValueError('Source directory does not exist: ' + str(root))
    model_root = next((p for p in (root/'blends/models', root/'models') if p.is_dir()), root)
    records = []
    for source in sorted(model_root.rglob('*.blend'), key=lambda p: str(p).casefold()):
        relative = source.relative_to(model_root)
        if source.name.casefold().startswith('bq_library'):
            continue
        if any(p.casefold().endswith('_octane') for p in relative.parts[:-1]):
            continue
        if 'blends' in relative.parts and 'models' not in relative.parts:
            continue
        records.append({'source': source.resolve(), 'relative': relative.as_posix(),
                        'category': relative.parent.as_posix() if relative.parent != Path('.') else '',
                        'name': source.stem})
    return records


def expand_selection(records, selected_tree_paths):
    selected = {str(p).replace('\\', '/').strip('/') for p in selected_tree_paths}
    result = []
    seen = set()
    for record in records:
        leaf = str(Path(record['relative']).with_suffix('')).replace('\\', '/')
        source = str(record['source'])
        if source not in seen and any(p == leaf or not p or leaf.startswith(p + '/') for p in selected):
            result.append(record)
            seen.add(source)
    return result


def package_path(record, mode='source', custom_root=None):
    source = Path(record['source'])
    name = source.stem + '_octane'
    if mode == 'source':
        return source.parent / name
    if mode != 'custom' or custom_root is None:
        raise ValueError('Custom output requires a directory.')
    category = Path(record.get('category') or '')
    if category.is_absolute() or '..' in category.parts:
        raise ValueError('Invalid asset category: ' + str(category))
    return Path(custom_root) / category / name


def read_manifest(folder):
    try:
        value = json.loads((Path(folder)/'manifest.json').read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def has_material_metadata(manifest):
    return (manifest.get('material_backend') == 'octane_simple'
            and manifest.get('schema') == 3
            and isinstance(manifest.get('materials'), list)
            and bool(manifest['materials'])
            and all(isinstance(m, dict) and m.get('name') and isinstance(m.get('textures'), dict)
                    and m.get('role') in {'leaf', 'grass', 'bark', 'stem', 'rock', 'basic'}
                    and isinstance(m.get('parameters'), dict)
                    for m in manifest['materials'])
            and isinstance(manifest.get('objects'), list))


def cache_status(folder, source):
    folder, source = Path(folder), Path(source).resolve()
    manifest = read_manifest(folder)
    if manifest.get('source') and BotaniqPaths.path_key(manifest['source']) != BotaniqPaths.path_key(source):
        return 'conflict'
    if not (folder/'geometry.usdc').is_file():
        return 'missing'
    if not has_material_metadata(manifest) or not (folder/'asset.usda').is_file():
        return 'metadata_missing'
    # Missing collected images need repair, without rewriting the geometry.
    for material in manifest['materials']:
        for texture in material['textures'].values():
            if not isinstance(texture, dict) or not texture.get('path') or not BotaniqPaths.exists(folder, texture['path']):
                return 'metadata_missing'
    return 'ready'


def material_textures_present(folder, manifest):
    """Return whether every collected texture in a valid manifest exists."""
    if not has_material_metadata(manifest):
        return False
    return all(isinstance(texture, dict) and texture.get('path')
               and BotaniqPaths.exists(folder, texture['path'])
               for material in manifest['materials']
               for texture in material['textures'].values())
