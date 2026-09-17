"""Local catalog and thumbnail cache for the Botaniq asset picker."""
from __future__ import print_function

import hashlib
import json
import os
from pathlib import Path
import shutil

import openToolsUtils


SCHEMA = 2
TOOL_NAME = 'BotaniqImporter'


def cache_root():
    return Path(openToolsUtils.settingsDirectory()) / TOOL_NAME / 'catalog_cache'


def _model_root(root):
    root = Path(root)
    return next((path for path in (root / 'blends' / 'models', root / 'models') if path.is_dir()), root)


def _pack_root(root, model_root):
    if model_root.name.lower() == 'models' and model_root.parent.name.lower() == 'blends':
        return model_root.parent.parent
    return Path(root)


def _key(root):
    return hashlib.sha256(str(Path(root)).casefold().encode('utf-8')).hexdigest()[:16]


def _path_data(path, label):
    try:
        stat = path.stat()
    except OSError:
        return [label, None, None]
    return [label, stat.st_mtime_ns, stat.st_size]


def _directory_snapshot(root, model_root):
    """Cheap change signal: directories and the optional pack index only.

    Directory mtimes detect added/removed/renamed assets without opening any
    Blender files or preview images.  Traversing directories is deliberately
    much cheaper than building a fresh asset catalog or decoding thumbnails.
    """
    pack_root = _pack_root(root, model_root)
    preview_root = pack_root / 'previews' / 'models'
    rows = [_path_data(pack_root / 'mapr_index.json', 'mapr_index.json')]
    for label, start in (('models', model_root), ('previews', preview_root)):
        if not start.is_dir():
            rows.append([label, None, None])
            continue
        pending = [start]
        while pending:
            directory = pending.pop()
            try:
                relative = directory.relative_to(start).as_posix()
                stat = directory.stat()
                rows.append([label + '/' + relative, stat.st_mtime_ns, 0])
                with os.scandir(str(directory)) as entries:
                    children = sorted((directory / entry.name for entry in entries if entry.is_dir()),
                                      key=lambda child: child.name.casefold())
            except OSError:
                rows.append([label + '/unavailable', None, None])
                continue
            pending.extend(reversed(children))
    return rows


def _read(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding='utf-8')
    temporary.replace(path)


def _restore_records(records):
    result = []
    for record in records:
        if not isinstance(record, dict) or not record.get('source') or not record.get('relative'):
            return None
        restored = dict(record)
        restored['source'] = Path(restored['source'])
        result.append(restored)
    return result


def _preview_source(pack_root, record):
    relative = Path(record['relative']).with_suffix('.png')
    kind = 'particles' if record.get('kind') == 'collection' else 'models'
    base = pack_root / 'previews' / kind / relative
    for candidate in (base, base.with_suffix('.jpg'), base.with_suffix('.jpeg'), base.with_suffix('.webp')):
        if candidate.is_file():
            return candidate
    return None


def _cache_preview(source, thumbnail_root, relative, kind='models'):
    if source is None:
        return ''
    key = hashlib.sha256((kind + '/' + str(relative)).casefold().encode('utf-8')).hexdigest()
    target = thumbnail_root / (key + source.suffix.lower())
    try:
        source_stat = source.stat()
        target_stat = target.stat() if target.is_file() else None
        if target_stat is None or target_stat.st_size != source_stat.st_size or target_stat.st_mtime_ns != source_stat.st_mtime_ns:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source), str(target))
        return str(target)
    except OSError:
        return ''


def _with_local_previews(records, pack_root, thumbnail_root):
    result = []
    for record in records:
        value = dict(record)
        source = _preview_source(pack_root, value)
        value['preview'] = _cache_preview(source, thumbnail_root, value['relative'],
                                          'particles' if value.get('kind') == 'collection' else 'models')
        value['preview_cached'] = True
        result.append(value)
    return result


def load_library(root, force=False):
    """Load the complete local picker catalog without touching the source pack.

    The cache is intentionally trusted between explicit refreshes.  This makes
    repeated browser launches fast on large or network-hosted libraries.
    """
    import BotaniqBatch
    import BotaniqCollections

    root = Path(root).expanduser().resolve()
    model_root = _model_root(root)
    key = _key(root)
    folder = cache_root()
    catalog = folder / (key + '.json')
    thumbnails = folder / key
    cached = _read(catalog)
    if (not force and cached and cached.get('schema') == SCHEMA and cached.get('root') == str(root)):
        assets = _restore_records(cached.get('assets', []))
        collections = _restore_records(cached.get('collections', []))
        if assets is not None and collections is not None:
            return {'assets': assets, 'collections': collections}, 'cached'

    assets = _with_local_previews(list(BotaniqBatch.discover_assets(root)),
                                  _pack_root(root, model_root), thumbnails)
    collections = _with_local_previews(list(BotaniqCollections.discover_collections(root)),
                                       _pack_root(root, model_root), thumbnails)
    def serialize(records):
        return [dict(record, source=str(record['source'])) for record in records]
    _write(catalog, {'schema': SCHEMA, 'root': str(root), 'assets': serialize(assets),
                     'collections': serialize(collections)})
    return {'assets': assets, 'collections': collections}, 'refreshed'


def load_or_build(root, force=False):
    """Return ``(records, cache_state)`` for a source root.

    ``cache_state`` is ``'cached'`` for the inexpensive local path and
    ``'refreshed'`` after the source catalog or local thumbnails are rebuilt.
    """
    library, state = load_library(root, force=force)
    return library['assets'], state
