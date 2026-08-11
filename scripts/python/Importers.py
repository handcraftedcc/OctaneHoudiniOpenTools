"""
Importer launcher for Octane Houdini Open Tools.

Import this module from a Houdini shelf tool and call main(kwargs). Add future
importers to IMPORTERS with a label, module name, and optional description.
"""

from __future__ import print_function

import importlib

try:
    import hou
except ImportError:
    hou = None


IMPORTERS = [
    {
        "label": "Cargo",
        "module": "CargoImporter",
        "description": "Import Cargo USD materials or models.",
    },
    {
        "label": "Megascans",
        "module": "MegascansImporter",
        "description": "Browse and import Megascans materials and assets.",
    },
    {
        "label": "GLTF",
        "module": "GltfImporter",
        "description": "Import GLTF models and rebuild their materials for Octane.",
    },
    {
        "label": "PlantFactory",
        "module": "PlantCatalogImporter",
        "description": "Import PlantFactory FBX assets and build Octane materials.",
    },
]


def choose_importer():
    if hou is None:
        raise RuntimeError("This script must run inside Houdini.")

    selected = hou.ui.selectFromList(
        [item["label"] for item in IMPORTERS],
        message="Choose an importer:",
        title="Octane Importers",
        exclusive=True,
        clear_on_cancel=True,
    )
    if not selected:
        return None
    return IMPORTERS[selected[0]]


def run(importer=None, kwargs=None):
    if hou is None:
        raise RuntimeError("This script must run inside Houdini.")

    kwargs = kwargs or {}
    importer = importer or choose_importer()
    if importer is None:
        return None

    module = importlib.import_module(importer["module"])
    module = importlib.reload(module)
    return module.main(kwargs)


def main(kwargs=None):
    return run(kwargs=kwargs)


def should_auto_run():
    if hou is None:
        return __name__ == "__main__"
    if __name__ in ("__main__", "__builtin__", "builtins"):
        return True
    return False


if should_auto_run():
    run()
