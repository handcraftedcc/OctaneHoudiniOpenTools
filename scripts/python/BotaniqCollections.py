"""Discovery and cached Blender-library inspection for botaniq collections."""
from __future__ import print_function

import json
import os
from pathlib import Path
import subprocess

import BotaniqPaths


SCHEMA = 1


def discover_collections(root):
    """Return particle-system blend files as thumbnail-browser records."""
    root = Path(BotaniqPaths.remap_unc_to_drive(str(Path(root).expanduser().resolve())))
    particle_root = next((path for path in (root / 'blends' / 'particles', root / 'particles',
                                             root.parent / 'particles' if root.name.casefold() == 'models' else root / '__missing__')
                          if path.is_dir()), None)
    if particle_root is None:
        return []
    records = []
    for source in sorted(particle_root.rglob('*.blend'), key=lambda path: str(path).casefold()):
        relative = source.relative_to(particle_root)
        records.append({'source': source.resolve(), 'relative': relative.as_posix(),
                        'category': relative.parent.as_posix() if relative.parent != Path('.') else '',
                        'name': source.stem, 'kind': 'collection'})
    return records


def cache_path(source):
    source = Path(source)
    return source.with_suffix('.botaniq_collection.json')


def _fingerprint(source):
    stat = Path(source).stat()
    return {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}


def _valid(manifest, source):
    return (isinstance(manifest, dict) and manifest.get('schema') == SCHEMA
            and manifest.get('fingerprint') == _fingerprint(source)
            and isinstance(manifest.get('models'), list)
            and all(isinstance(path, str) for path in manifest['models']))


def read_cached(source):
    try:
        manifest = json.loads(cache_path(source).read_text(encoding='utf-8'))
        return manifest if _valid(manifest, source) else None
    except (OSError, ValueError):
        return None


def inspect_collection(source, blender):
    """Open one particle blend in Blender and persist its linked model blends."""
    source = Path(BotaniqPaths.remap_unc_to_drive(str(Path(source).resolve())))
    cached = read_cached(source)
    if cached is not None:
        cached['cache_status'] = 'reused'
        return cached
    worker = Path(__file__).with_name('BotaniqBlenderCollection.py')
    command = [str(blender), '--background', '--factory-startup', '--disable-autoexec',
               '--python-exit-code', '1', '--python', str(worker), '--', '--source', str(source)]
    environment = os.environ.copy()
    environment.pop('OCIO', None)
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            encoding='utf-8', errors='replace', env=environment,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('Blender could not inspect {}:\n{}'.format(source.name, result.stdout[-3000:]))
    try:
        payload = json.loads(result.stdout.split('BOTANIQ_COLLECTION_JSON=', 1)[1].splitlines()[0])
    except (IndexError, ValueError) as exc:
        raise RuntimeError('Blender did not return a collection manifest for {}.'.format(source.name)) from exc
    models = []
    seen = set()
    for value in payload.get('models', []):
        path = Path(BotaniqPaths.remap_unc_to_drive(value))
        if path.is_file() and path.suffix.lower() == '.blend':
            key = BotaniqPaths.path_key(path)
            if key not in seen:
                seen.add(key)
                models.append(str(path.resolve()))
    manifest = {'schema': SCHEMA, 'source': str(source), 'fingerprint': _fingerprint(source),
                'models': models}
    cache_path(source).write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding='utf-8')
    manifest['cache_status'] = 'inspected'
    return manifest
