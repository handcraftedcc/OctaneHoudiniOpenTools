"""Read a botaniq particle blend and print the model libraries it actually loads."""
import argparse
import json
from pathlib import Path
import sys

import bpy


def is_model_library(path):
    parts = [part.casefold() for part in Path(path).parts]
    return 'blends' in parts and 'models' in parts and Path(path).suffix.casefold() == '.blend'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    bpy.ops.wm.open_mainfile(filepath=args.source, load_ui=False)
    models = []
    for library in bpy.data.libraries:
        path = bpy.path.abspath(library.filepath)
        if is_model_library(path) and not Path(path).name.casefold().startswith('bq_library'):
            models.append(str(Path(path).resolve()))
    print('BOTANIQ_COLLECTION_JSON=' + json.dumps({'models': sorted(set(models), key=str.casefold)}))


if __name__ == '__main__':
    main()
