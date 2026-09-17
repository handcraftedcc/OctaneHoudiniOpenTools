"""botaniq model importer: Blender USD geometry + simple native Octane materials."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

import BotaniqMaterialX
import BotaniqPackage
import BotaniqBatch
import BotaniqPaths
import BotaniqCollections


ANIMATION_NODE_TYPE = 'Octane_Open_Tools::botaniqWindAnimator::1.0'
ANIMATION_HDA = Path(__file__).resolve().parents[2] / 'otls' / 'sop_Octane Open Tools--botaniqAnimator-1.0.hdalc'


def find_blender(configured=None):
    if configured and Path(configured).is_file():
        return str(configured)
    executable = shutil.which('blender')
    if executable:
        return executable
    candidates = sorted(Path(os.environ.get('ProgramFiles', 'C:/Program Files')).glob('Blender Foundation/Blender 5.*/blender.exe'), reverse=True)
    if candidates:
        return str(candidates[0])
    raise FileNotFoundError('Set the Blender 5.2+ executable in the botaniq importer settings.')


def _run_blender(source, output, blender, evaluated, metadata_only=False):
    command = [find_blender(blender), '--background', '--factory-startup', '--disable-autoexec',
               '--python-exit-code', '1', '--python', str(Path(__file__).with_name('BotaniqBlenderExport.py')),
               '--', '--source', str(source), '--output', str(output)]
    if evaluated:
        command.append('--evaluated')
    if metadata_only:
        command.append('--metadata-only')
    environment = os.environ.copy()
    environment.pop('OCIO', None)
    with (output / 'export.log').open('w', encoding='utf-8') as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, env=environment,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode or not (output / 'manifest.json').is_file():
        raise RuntimeError('Blender export failed. See ' + str(output / 'export.log'))


def export_model(source, output, blender=None, evaluated=True, options=None):
    """Create an asset package. Refuse nonempty destinations to protect existing work."""
    source = Path(BotaniqPaths.remap_unc_to_drive(str(Path(source).resolve())))
    output = Path(BotaniqPaths.remap_unc_to_drive(str(Path(output).resolve())))
    if not source.is_file() or source.suffix.lower() != '.blend':
        raise ValueError('Select an existing botaniq model .blend file.')
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('Output directory must be empty: ' + str(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    # Publish only after Blender and geometry package preparation complete.
    staging = output.parent / ('botaniq_' + uuid.uuid4().hex[:12])
    staging.mkdir()
    try:
        _run_blender(source, staging, blender, evaluated)
        BotaniqPackage.write_bundle(staging, options)
        if output.exists():
            output.rmdir()  # Previously checked empty; fails if another writer added files.
        staging.rename(output)
    except Exception as exc:
        raise RuntimeError('{}\nDiagnostic files retained in {}'.format(exc, staging)) from exc
    return str(output / 'asset.usda')


def ensure_package(source, output, blender=None, evaluated=True):
    """Reuse cached USD/JSON; repair only missing material metadata when possible."""
    source = Path(BotaniqPaths.remap_unc_to_drive(str(Path(source).resolve())))
    output = Path(BotaniqPaths.remap_unc_to_drive(str(Path(output).resolve())))
    status = BotaniqBatch.cache_status(output, source)
    if status == 'conflict':
        raise FileExistsError('This package belongs to another source: ' + str(output))
    if status == 'ready':
        return {'asset': str(output/'asset.usda'), 'status': 'reused'}
    if status == 'missing':
        return {'asset': export_model(source, output, blender, evaluated), 'status': 'exported'}
    manifest = BotaniqBatch.read_manifest(output)
    textures_present = BotaniqBatch.material_textures_present(output, manifest)
    if not textures_present:
        staging = output.parent / ('botaniq_metadata_' + uuid.uuid4().hex[:12])
        staging.mkdir()
        try:
            _run_blender(source, staging, blender, manifest.get('evaluated', evaluated), metadata_only=True)
            refreshed = BotaniqBatch.read_manifest(staging)
            if not BotaniqBatch.has_material_metadata(refreshed):
                raise ValueError('Blender did not produce valid material metadata.')
            if (staging/'textures').exists():
                shutil.copytree(staging/'textures', output/'textures', dirs_exist_ok=True)
            shutil.copy2(staging/'export.log', output/'metadata.log')
            os.replace(staging/'manifest.json', output/'manifest.json')
        except Exception as exc:
            raise RuntimeError('{}\nMetadata diagnostics retained in {}'.format(exc, staging)) from exc
        # Only remove the unique staging directory created above, never the cache.
        if staging.resolve().parent == output.parent.resolve() and staging.name.startswith('botaniq_metadata_'):
            shutil.rmtree(staging)
    BotaniqPackage.write_bundle(output)
    return {'asset': str(output/'asset.usda'), 'status': 'metadata_repaired'}


def _create_animation_node(parent, input_node, name):
    """Insert the bundled Indie-only wind HDA after the import transform."""
    import hou
    node_type = hou.nodeType(hou.sopNodeTypeCategory(), ANIMATION_NODE_TYPE)
    if node_type is None:
        if not ANIMATION_HDA.is_file():
            raise RuntimeError('Botaniq Animation HDA is missing: {}'.format(ANIMATION_HDA))
        try:
            hou.hda.installFile(str(ANIMATION_HDA), change_oplibraries_file=False)
        except hou.OperationFailed as exc:
            raise RuntimeError('Botaniq Animation is available with a Houdini Indie license only.') from exc
    node = parent.createNode(ANIMATION_NODE_TYPE, 'botaniq_animation_' + name)
    node.setInput(0, input_node)
    return node


def _create_direct_sop_asset(parent, folder, manifest, name, column, add_animation=False):
    """Build one editable import column directly inside a SOP network."""
    import hou
    simple_octane = manifest.get('material_backend') == 'octane_simple'
    asset = folder / 'asset.usda'
    read = parent.createNode('usdimport', 'read_' + name)
    read.parm('filepath1').set(asset.as_posix())
    read.parm('primpattern').set('%type:Mesh')
    read.parm('unpack').set(True) if read.parm('unpack') else None
    unpack = parent.createNode('unpackusd', 'unpack_' + name)
    unpack.setInput(0, read)
    unpack.parm('output').set(1)
    if unpack.parm('primvarpattern'):
        unpack.parm('primvarpattern').set('*')
    matnet = parent.createNode('matnet', 'materials_' + name)
    if simple_octane:
        import BotaniqOctane
        materials = {m['name']: BotaniqOctane.create_material(matnet, m, folder) for m in manifest['materials']}
    else:
        materials = {m['name']: BotaniqMaterialX.create_houdini_material(
            matnet, m, folder, manifest.get('materialx_options')) for m in manifest['materials']}
    assign = parent.createNode('material', 'assign_' + name)
    assign.setInput(0, unpack)
    if assign.parm('num_materials'):
        assign.parm('num_materials').set(len(materials))
    for index, (original, node) in enumerate(materials.items(), 1):
        group = assign.parm('group{}'.format(index))
        path = assign.parm('shop_materialpath{}'.format(index))
        if group:
            group.set('@botaniq_material={}'.format(BotaniqMaterialX.identifier(original)))
        if path:
            path.set(assign.relativePathTo(node))
    reorient = parent.createNode('xform', 'reorient_' + name)
    reorient.setInput(0, assign)
    if reorient.parm('rx'):
        reorient.parm('rx').set(-90.0)
    final_input = _create_animation_node(parent, reorient, name) if add_animation else reorient
    output = parent.createNode('null', 'OUT_BOTANIQ_' + name)
    output.setInput(0, final_input)
    output.setDisplayFlag(True)
    output.setRenderFlag(True)
    # Columns are deliberately placed, never auto-laid out with the user's SOPs.
    x = float(column) * 5.0
    matnet.setPosition(hou.Vector2(x, 2))
    read.setPosition(hou.Vector2(x, 0))
    unpack.setPosition(hou.Vector2(x, -1))
    assign.setPosition(hou.Vector2(x, -2))
    reorient.setPosition(hou.Vector2(x, -3))
    if add_animation:
        final_input.setPosition(hou.Vector2(x, -4))
    output.setPosition(hou.Vector2(x, -5 if add_animation else -4))
    return {'output': output, 'materials': materials, 'matnet': matnet}


def import_package(folder, create_obj=True, create_stage=None, obj_parent=None, sop_parent=None, sop_column=0,
                   add_animation=False):
    """Create native Octane materials; older MaterialX packages remain readable."""
    import hou
    from pxr import Usd, UsdGeom
    folder = Path(BotaniqPaths.remap_unc_to_drive(str(Path(folder).resolve())))
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    simple_octane = manifest.get('material_backend') == 'octane_simple'
    if create_stage is None:
        create_stage = not simple_octane
    asset = folder / 'asset.usda'
    if not asset.is_file():
        raise FileNotFoundError(str(asset))
    name = BotaniqMaterialX.identifier(Path(manifest['source']).stem)
    result = {'package': str(folder), 'manifest': manifest}
    if create_stage:
        ref = hou.node('/stage').createNode('reference', name)
        ref.parm('filepath1').set(asset.as_posix())
        ref.setDisplayFlag(True)
        result['reference'] = ref
    if create_obj and sop_parent is not None:
        try:
            result.update(_create_direct_sop_asset(sop_parent, folder, manifest, name, sop_column, add_animation))
        except Exception:
            if result.get('reference') is not None:
                result['reference'].destroy()
            raise
        return result
    if create_obj:
        parent = obj_parent or hou.node('/obj')
        geometry = parent.createNode('geo', name)
        try:
            for child in geometry.children():
                child.destroy()
            read = geometry.createNode('usdimport', 'read_botaniq_usd')
            read.parm('filepath1').set(asset.as_posix())
            # Import all mesh prims; USD Import expands geometry with its primvars.
            read.parm('primpattern').set('%type:Mesh')
            read.parm('unpack').set(True) if read.parm('unpack') else None
            unpack = geometry.createNode('unpackusd', 'unpack_with_attributes')
            unpack.setInput(0, read)
            unpack.parm('output').set(1)
            if unpack.parm('primvarpattern'):
                unpack.parm('primvarpattern').set('*')
            matnet = geometry.createNode('matnet', 'materials')
            if simple_octane:
                import BotaniqOctane
                materials = {m['name']: BotaniqOctane.create_material(matnet, m, folder) for m in manifest['materials']}
            else:
                materials = {m['name']: BotaniqMaterialX.create_houdini_material(matnet, m, folder, manifest.get('materialx_options')) for m in manifest['materials']}
            # Material SOP evaluates the attribute groups dynamically and owns the
            # assignment slots. This keeps material path/name changes editable
            # without embedding a generated VEX snippet.
            assign = geometry.createNode('material', 'assign_materials')
            assign.setInput(0, unpack)
            if assign.parm('num_materials'):
                assign.parm('num_materials').set(len(materials))
            for index, (original, node) in enumerate(materials.items(), 1):
                token = BotaniqMaterialX.identifier(original)
                group = assign.parm('group{}'.format(index))
                path = assign.parm('shop_materialpath{}'.format(index))
                if group:
                    group.set('@botaniq_material={}'.format(token))
                if path:
                    path.set(assign.relativePathTo(node))
            # Blender assets are authored Z-up; Houdini's object/SOP convention is Y-up.
            # Rx=-90 maps Blender +Z (up) to Houdini +Y while preserving +X.
            reorient = geometry.createNode('xform', 'reorient_blender_zup')
            reorient.setInput(0, assign)
            if reorient.parm('rx'):
                reorient.parm('rx').set(-90.0)
            final_input = _create_animation_node(geometry, reorient, name) if add_animation else reorient
            output = geometry.createNode('null', 'OUT_BOTANIQ')
            output.setInput(0, final_input)
            output.setDisplayFlag(True)
            output.setRenderFlag(True)
            geometry.layoutChildren()
            result.update(geometry=geometry, output=output, materials=materials)
        except Exception:
            geometry.destroy()
            if result.get('reference') is not None:
                result['reference'].destroy()
            raise
    return result


def _active_import_context():
    """Return the visible OBJ or SOP network, falling back to /obj."""
    import hou
    editor = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
    node = editor.pwd() if editor is not None else hou.node('/obj')
    while node is not None:
        category = node.childTypeCategory()
        if category in (hou.objNodeTypeCategory(), hou.sopNodeTypeCategory()):
            return node
        node = node.parent()
    return hou.node('/obj')


def _first_sop_column(parent):
    """Reserve columns to the right of the visible SOP network."""
    try:
        rightmost = max((child.position().x() for child in parent.children()), default=-5.0)
        return max(0, int(rightmost // 5.0) + 1)
    except Exception:
        return 0


def _choose_animation():
    """Ask once per import operation whether to append the Indie HDA."""
    import hou
    choice = hou.ui.displayMessage(
        'Add Botaniq Animation after the reorientation Transform SOP?\n\n'
        'Botaniq Animation requires a Houdini Indie license.',
        buttons=('Add Animation (Indie license only)', 'Skip Animation'),
        default_choice=1, close_choice=1, title='Botaniq Animation')
    return choice == 0


def import_collection(root, collection_record, placement, blender=None, add_animation=False):
    """Inspect one particle blend and import each referenced model package."""
    import hou
    collection = BotaniqCollections.read_cached(collection_record['source'])
    if collection is None:
        collection = BotaniqCollections.inspect_collection(collection_record['source'], find_blender(blender))
    else:
        collection['cache_status'] = 'reused'
    all_records = BotaniqBatch.discover_assets(root)
    by_source = {BotaniqPaths.path_key(record['source']): record for record in all_records}
    missing, records = [], []
    for source in collection['models']:
        record = by_source.get(BotaniqPaths.path_key(source))
        if record is None:
            missing.append(source)
        else:
            records.append(record)
    if not records:
        raise ValueError('The collection has no model blends in this botaniq source root.')
    context = _active_import_context()
    sop_parent = context if context.childTypeCategory() == hou.sopNodeTypeCategory() else None
    obj_parent = context if sop_parent is None else hou.node('/obj')
    first_column = _first_sop_column(sop_parent) if sop_parent is not None else 0
    results, failures = [], []
    with hou.InterruptableOperation('Import botaniq collection', open_interrupt_dialog=True) as operation:
        for index, record in enumerate(records):
            operation.updateProgress(float(index) / len(records))
            try:
                folder = BotaniqBatch.package_path(record, **placement)
                cache = ensure_package(record['source'], folder, blender)
                result = import_package(folder, obj_parent=obj_parent, sop_parent=sop_parent,
                                        sop_column=first_column + index, add_animation=add_animation)
                result['cache_status'] = cache['status']
                results.append(result)
            except hou.OperationInterrupted:
                raise
            except Exception as exc:
                failures.append('{}: {}'.format(record['relative'], exc))
        operation.updateProgress(1.0)
    return {'results': results, 'failures': failures, 'missing': missing,
            'collection': collection, 'sop_output': None}


def main(kwargs=None):
    import importlib
    import hou
    import openToolsUtils
    import BotaniqOctane
    import BotaniqBrowser
    import BotaniqCatalogCache
    for module in (BotaniqPaths, BotaniqMaterialX, BotaniqOctane, BotaniqCatalogCache,
                   BotaniqPackage, BotaniqBatch, BotaniqCollections, BotaniqBrowser):
        importlib.reload(module)
    settings = openToolsUtils.getToolSettings('BotaniqImporter')
    selection = BotaniqBrowser.choose_import(settings)
    if not selection:
        return None
    root, selection_kind, records = selection
    placement = BotaniqBrowser.choose_output(settings)
    if not placement:
        return None
    add_animation = _choose_animation()
    if selection_kind == 'collections':
        blender = settings.get('blender')
        # Cached manifests can import without Blender.  A first-time inspection
        # needs an executable, using the same remembered setting as model export.
        if BotaniqCollections.read_cached(records[0]['source']) is None:
            try:
                blender = find_blender(blender)
            except FileNotFoundError:
                blender = hou.ui.selectFile(title='Locate Blender 5.2+ executable', pattern='blender.exe')
                if not blender:
                    return None
                blender = hou.expandString(blender)
            settings['blender'] = blender
            openToolsUtils.setToolSettings('BotaniqImporter', settings)
        try:
            result = import_collection(root, records[0], placement, blender, add_animation=add_animation)
        except hou.OperationInterrupted:
            return None
        except Exception as exc:
            hou.ui.displayMessage('Collection import failed: {}'.format(exc), title='Botaniq Collection Import')
            return None
        details = list(result['failures']) + ['Not found in this pack: ' + path for path in result['missing']]
        cache_state = result['collection'].get('cache_status', 'reused')
        message = 'Imported {} models from {} (collection manifest {}).'.format(
            len(result['results']), records[0]['name'], cache_state)
        if result['sop_output'] is not None:
            message += '\nCreated {} in the active SOP network.'.format(result['sop_output'].path())
        if details:
            message += '\n{} item(s) need attention; see details.'.format(len(details))
        hou.ui.displayMessage(message, title='Botaniq Collection Import', details='\n'.join(details))
        return result

    targets = [(r, BotaniqBatch.package_path(r, **placement)) for r in records]
    blender = settings.get('blender')
    needs_blender = False
    for record, folder in targets:
        status = BotaniqBatch.cache_status(folder, record['source'])
        if status == 'missing':
            needs_blender = True
        elif status == 'metadata_missing':
            manifest = BotaniqBatch.read_manifest(folder)
            complete = BotaniqBatch.material_textures_present(folder, manifest)
            needs_blender = needs_blender or not complete
    if needs_blender:
        try:
            blender = find_blender(blender)
        except FileNotFoundError:
            blender = hou.ui.selectFile(title='Locate Blender 5.2+ executable', pattern='blender.exe')
            if not blender:
                return None
            blender = hou.expandString(blender)
        settings['blender'] = blender
        openToolsUtils.setToolSettings('BotaniqImporter', settings)
    results, failures = [], []
    context = _active_import_context()
    sop_parent = context if context.childTypeCategory() == hou.sopNodeTypeCategory() else None
    obj_parent = context if sop_parent is None else None
    first_column = _first_sop_column(sop_parent) if sop_parent is not None else 0
    cancelled = False
    try:
        with hou.InterruptableOperation('Import botaniq assets', open_interrupt_dialog=True) as operation:
            for index, (record, folder) in enumerate(targets):
                operation.updateProgress(float(index) / len(targets))
                try:
                    cache = ensure_package(record['source'], folder, blender)
                    result = import_package(folder, obj_parent=obj_parent, sop_parent=sop_parent,
                                            sop_column=first_column + index, add_animation=add_animation)
                    result['cache_status'] = cache['status']
                    results.append(result)
                except hou.OperationInterrupted:
                    raise
                except Exception as exc:
                    failures.append('{}: {}'.format(record['relative'], exc))
            operation.updateProgress(1.0)
    except hou.OperationInterrupted:
        cancelled = True
    counts = {status: sum(r['cache_status'] == status for r in results)
              for status in ('exported', 'reused', 'metadata_repaired')}
    message = 'Imported {} botaniq assets.\n{} new exports, {} cached, {} metadata repairs.'.format(
        len(results), counts['exported'], counts['reused'], counts['metadata_repaired'])
    if cancelled:
        message += '\nBatch cancelled; completed imports and caches were retained.'
    if failures:
        message += '\n{} failed; see details.'.format(len(failures))
    notices = [w for result in results for w in result['manifest'].get('warnings', [])]
    if notices:
        message += '\n{} material notices; see details.'.format(len(notices))
    hou.ui.displayMessage(message, title='Botaniq Import', details='\n'.join(failures + notices))
    return {'results': results, 'failures': failures, 'cancelled': cancelled}
